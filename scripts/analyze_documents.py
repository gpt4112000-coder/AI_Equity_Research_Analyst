#!/usr/bin/env python3
"""Extract text from downloaded company documents and analyze with Ollama.

Reads downloaded attachments (from download_attachments.py), extracts text
(PDF via pymupdf, plus plain-text/xlsx fallbacks), sends to Ollama for
structured insight extraction, and stores the results in the
`document_insights` table (filterable by metric/amount/sentiment/importance).

Marks company_files.analyzed=1 when done so re-runs skip completed files.

Usage:
  python analyze_documents.py                  # all done-but-unanalyzed files
  python analyze_documents.py --company 516    # single company
  python analyze_documents.py --kind transcript
  python analyze_documents.py --limit 20
  python analyze_documents.py --max-pages 30   # cap PDF pages read
"""
import sys
import json
import time
import argparse
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from config import COMPANY_FILES_DIR
from data.storage.db import get_db

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:3b"
MAX_CHARS = 12000  # approx 3k tokens per chunk


def extract_text(path):
    """Extract readable text from a downloaded file."""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext in (".txt", ".htm", ".html"):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        return raw
    return ""


def _extract_pdf(path, max_pages=40):
    import fitz  # PyMuPDF
    text = []
    try:
        doc = fitz.open(path)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text.append(page.get_text())
        doc.close()
    except Exception:
        return ""
    return "\n".join(text)


def chunk_text(text, max_chars=MAX_CHARS):
    """Split long text into overlapping-ish chunks of roughly max_chars."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    while len(text) > max_chars:
        cut = text.rfind("\n", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


PROMPT_TEMPLATE = """Analyze this Indian corporate document content and extract structured investment insights.

Company: {company_name} ({symbol})
Document kind: {kind}
Announcement date: {ann_date}
Headline: {headline}

CONTENT:
{content}

Respond in EXACTLY this JSON (no other text):
{{
  "metrics": [
    {{
      "metric": "capex" | "guidance" | "orders" | "financials" | "dividend" | "acquisition" | "management" | "regulatory" | "credit_rating",
      "summary": "1-2 sentence factual summary with specific numbers, dates, amounts",
      "amount": number in crore (or null),
      "amount_text": "raw amount text as stated",
      "sentiment": "positive" | "negative" | "neutral",
      "importance": "high" | "medium" | "low"
    }}
  ]
}}

Rules:
- Extract only facts stated in the content. Do not invent.
- If the document mentions revenue/profit/orders/capex amounts, capture them.
- Use Indian crore scale for amounts (convert lakh->0.01, million->0.1, billion->100).
- Keep total under 400 words.
"""


def _call_ollama(prompt):
    import httpx
    resp = httpx.post(
        OLLAMA_URL,
        json={"model": MODEL, "prompt": prompt, "stream": False,
              "options": {"temperature": 0.2, "num_predict": 700}},
        timeout=120.0,
    )
    return resp.json().get("response", "")


def _parse_ollama_json(raw):
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(raw[start:end])
    return None


def analyze_file(conn, row, max_pages):
    """Analyze one company_files row. Returns list of metric insights + summary."""
    file_id = row["id"]
    company_id = row["company_id"]
    kind = row["kind"] or "general"
    local_path = row["local_path"]
    if not local_path or not Path(local_path).exists():
        return []

    cursor = conn.cursor()
    # company + announcement context
    cursor.execute("SELECT company_name, nse_symbol, bse_code FROM companies WHERE id=?", (company_id,))
    c = cursor.fetchone()
    if not c:
        return []
    company_name = c["company_name"]
    symbol = c["nse_symbol"] or c["bse_code"] or ""

    ann_date = headline = ""
    if row["announcement_id"]:
        cursor.execute("SELECT announcement_date, headline FROM announcements WHERE id=?", (row["announcement_id"],))
        a = cursor.fetchone()
        if a:
            ann_date = a["announcement_date"] or ""
            headline = (a["headline"] or "")[:300]

    text = extract_text(local_path)
    if not text or len(text) < 50:
        return []

    insights = []
    chunks = chunk_text(text)
    for chunk in chunks:
        prompt = PROMPT_TEMPLATE.format(
            company_name=company_name, symbol=symbol, kind=kind,
            ann_date=ann_date or "Unknown", headline=headline or "(none)",
            content=chunk)
        try:
            raw = _call_ollama(prompt)
            parsed = _parse_ollama_json(raw)
            if parsed and isinstance(parsed.get("metrics"), list):
                for m in parsed["metrics"]:
                    if not isinstance(m, dict) or not m.get("metric"):
                        continue
                    insights.append({
                        "metric": str(m["metric"]).strip().lower(),
                        "summary": (m.get("summary") or "")[:600],
                        "amount": m.get("amount"),
                        "amount_text": (m.get("amount_text") or "")[:100],
                        "sentiment": (m.get("sentiment") or "neutral"),
                        "importance": (m.get("importance") or "medium"),
                    })
            time.sleep(0.5)
        except Exception as e:
            print(f"    ollama err: {str(e)[:120]}", flush=True)
            time.sleep(1)

    return insights


def process_company(conn, company_id, kind_filter, limit, max_pages, dry_run):
    cursor = conn.cursor()
    cursor.execute("SELECT nse_symbol, bse_code FROM companies WHERE id=?", (company_id,))
    comp = cursor.fetchone()
    if not comp:
        return 0

    cursor.execute(
        """SELECT id, company_id, announcement_id, kind, local_path, analyzed, status
           FROM company_files WHERE company_id=? AND status='done' AND analyzed=0
           ORDER BY id LIMIT ?""",
        (company_id, limit))
    rows = [dict(r) for r in cursor.fetchall()]

    processed = 0
    for row in rows:
        if kind_filter and row["kind"] != kind_filter:
            continue
        if dry_run:
            print(f"  would analyze file {row['id']} ({row['kind']})")
            processed += 1
            continue
        try:
            insights = analyze_file(conn, row, max_pages)
            cursor.execute("UPDATE company_files SET analyzed=1 WHERE id=?", (row["id"],))
            for ins in insights:
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO document_insights
                        (company_id, file_id, announcement_id, metric, insight_date,
                         headline, summary, amount, amount_text, sentiment, importance, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (row["company_id"], row["id"], row["announcement_id"], ins["metric"],
                          None, headline_from(row, cursor), ins["summary"],
                          ins["amount"], ins["amount_text"], ins["sentiment"],
                          ins["importance"], json.dumps(ins)))
                except Exception:
                    continue
            conn.commit()
            processed += 1
            print(f"  file {row['id']} ({row['kind']}): {len(insights)} insights", flush=True)
            time.sleep(0.5)
        except Exception as e:
            print(f"  ERR file {row['id']}: {str(e)[:120]}", flush=True)
            traceback.print_exc()

    conn.commit()
    return processed


def headline_from(row, cursor):
    if row["announcement_id"]:
        cursor.execute("SELECT headline FROM announcements WHERE id=?", (row["announcement_id"],))
        r = cursor.fetchone()
        if r:
            return (r["headline"] or "")[:300]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", type=int)
    parser.add_argument("--kind", help="Only analyze one kind")
    parser.add_argument("--limit", type=int, default=500, help="Max files per company")
    parser.add_argument("--companies", type=int, help="Max companies to process")
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_db()
    if args.company:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT company_id FROM company_files WHERE status='done' AND analyzed=0 AND company_id=?",
            (args.company,))
    else:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT company_id FROM company_files WHERE status='done' AND analyzed=0 ORDER BY company_id")
    companies = [r[0] for r in cursor.fetchall()]
    if args.companies:
        companies = companies[:args.companies]
    conn.close()

    print(f"Analyzing {len(companies)} companies (files: done + unanalyzed)")
    total = 0
    for i, cid in enumerate(companies):
        conn = get_db()
        p = process_company(conn, cid, args.kind, args.limit, args.max_pages, args.dry_run)
        conn.close()
        total += p
        print(f"[{i+1}/{len(companies)}] company {cid}: {p} files", flush=True)

    print(f"\n=== Analysis Complete: {total} files processed ===")


if __name__ == "__main__":
    main()
