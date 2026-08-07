"""Shared concall pipeline: queue, download, and analyze transcripts.

Used by scripts/fetch_concall_transcripts.py, scripts/analyze_concalls.py, and
the backend batch job (/api/concalls/batch/*).

Status flow on `concalls` rows:
  (insert) pending -> downloaded -> done | error
Transient LLM failures leave rows 'downloaded' so re-runs retry them (resume).
"""

import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta

from config import COMPANY_FILES_DIR
from llm import generate

TRANSCRIPT_LIKE = """
    (lower(headline) LIKE '%concall%' OR lower(headline) LIKE '%transcript%'
     OR lower(headline) LIKE '%con. call%' OR lower(headline) LIKE '%earnings call%'
     OR category IN ('Earnings Call Transcript',
                     'Analyst / Investor Meet',
                     'Analysts/Institutional Investor Meet/Con. Call Updates',
                     'Schedule of Analysts/Institutional Investor Meet/Con. Call'))
"""

MAX_TEXT = 50000
CHUNK = 45000
MAX_PAGES = 80


def recent_cutoff(years=2):
    return (datetime.now() - timedelta(days=365 * years)).strftime("%Y-%m-%d")


def quarter_of(date_str):
    d = date_str or ""
    year = d[:4]
    month = int(d[5:7]) if len(d) >= 7 else 0
    if not year:
        return None
    if 1 <= month <= 3:
        q = "Q4"
    elif 4 <= month <= 6:
        q = "Q1"
    elif 7 <= month <= 9:
        q = "Q2"
    else:
        q = "Q3"
    return f"{year} {q}"


def safe_filename(url, ann_id):
    stem = (url or "").rstrip("/").split("/")[-1]
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)
    if not stem or len(stem) < 4:
        stem = f"ann_{ann_id}"
    if "." not in Path(stem).suffix:
        stem = f"{stem}.pdf"
    return stem


def _bse_alt_url(url):
    if "corpfiling/AttachLive/" in url:
        return url.replace("corpfiling/AttachLive/", "corpfiling/AttachHis/")
    if "corpfiling/AttachHis/" in url:
        return url.replace("corpfiling/AttachHis/", "corpfiling/AttachLive/")
    return None


def download_one(url, dest_path, session):
    import httpx
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/138.0",
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Referer": "https://www.nseindia.com/" if "nseindia.com" in url else "https://www.bseindia.com/",
    }
    resp = session.get(url, headers=headers)
    if resp.status_code == 404:
        alt = _bse_alt_url(url)
        if alt:
            resp = session.get(alt, headers=headers)
    resp.raise_for_status()
    content = resp.content
    if not content:
        raise RuntimeError("empty response")
    if content[:5] not in (b"%PDF-", b"PK\x03\x04") and b"<!DOCTYPE" in content[:500]:
        raise RuntimeError("server returned HTML page instead of document")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(content)
    return len(content)


def transcript_announcements(conn, company_id, cutoff, limit=None):
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT id, announcement_date, headline, attachment_url, exchange
        FROM announcements
        WHERE company_id = ? AND announcement_date >= ?
              AND {TRANSCRIPT_LIKE} AND attachment_url IS NOT NULL AND attachment_url != ''
        ORDER BY announcement_date DESC
        """,
        (company_id, cutoff))
    rows = [dict(r) for r in cursor.fetchall()]
    if limit:
        rows = rows[:limit]
    return rows


def ensure_queued(conn, company_id, cutoff, limit=None):
    """Insert a `concalls` row (status pending) for every transcript announcement
    not yet tracked. Returns number inserted."""
    cursor = conn.cursor()
    inserted = 0
    for row in transcript_announcements(conn, company_id, cutoff, limit):
        if not (row["attachment_url"] or "").lower().endswith(".pdf"):
            continue
        cursor.execute(
            """INSERT OR IGNORE INTO concalls
               (company_id, announcement_id, call_date, quarter, title)
               VALUES (?, ?, ?, ?, ?)""",
            (company_id, row["id"], row["announcement_date"],
             quarter_of(row["announcement_date"]), (row["headline"] or "")[:400]))
        inserted += cursor.rowcount
    conn.commit()
    return inserted


def _upsert_company_file(cursor, company_id, ann_id, url, exchange, status, local_path, size=0):
    cursor.execute(
        """INSERT OR IGNORE INTO company_files
           (company_id, announcement_id, kind, local_path, source_url, source_exchange,
            file_size, status, downloaded_at)
           VALUES (?, ?, 'transcript', ?, ?, ?, ?, ?, datetime('now'))""",
        (company_id, ann_id, local_path, url, exchange, size, status))
    cursor.execute(
        """UPDATE company_files SET status=?, local_path=?, kind='transcript',
           file_size=?, downloaded_at=datetime('now')
           WHERE company_id=? AND announcement_id=? AND source_url=?""",
        (status, local_path, size, company_id, ann_id, url))


def download_pending(conn, company_id, cutoff, limit=None, stop_cb=None):
    """Download files for concalls rows that are 'pending' (or missing file).
    Returns (downloaded, already, failed)."""
    import httpx
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, company_id, announcement_id, call_date, quarter, title,
                  transcript_path, status
           FROM concalls WHERE company_id=? AND status IN ('pending','downloaded','error')
           ORDER BY call_date DESC LIMIT ?""",
        (company_id, limit or -1))
    rows = [dict(r) for r in cursor.fetchall()]

    folder = COMPANY_FILES_DIR / str(company_id) / "concalls"
    folder.mkdir(parents=True, exist_ok=True)

    downloaded = already = failed = 0
    with httpx.Client(follow_redirects=True, timeout=60) as session:
        for cc in rows:
            if stop_cb and stop_cb():
                break
            if cc["status"] == "done":
                continue
            ann_id = cc["announcement_id"]
            cursor.execute(
                "SELECT attachment_url, exchange FROM announcements WHERE id=?", (ann_id,))
            a = cursor.fetchone()
            if not a or not a["attachment_url"]:
                continue
            url = a["attachment_url"]

            if cc["transcript_path"] and Path(cc["transcript_path"]).exists() and \
                    Path(cc["transcript_path"]).stat().st_size > 0:
                if cc["status"] != "downloaded":
                    cursor.execute(
                        "UPDATE concalls SET status='downloaded', error=NULL WHERE id=?",
                        (cc["id"],))
                    conn.commit()
                already += 1
                continue

            fname = f"{ann_id}_{safe_filename(url, ann_id)}"
            dest = folder / fname
            if dest.exists() and dest.stat().st_size > 0:
                cursor.execute(
                    "UPDATE concalls SET transcript_path=?, status='downloaded', error=NULL WHERE id=?",
                    (str(dest), cc["id"]))
                _upsert_company_file(cursor, company_id, ann_id, url, a["exchange"], "done", str(dest))
                conn.commit()
                already += 1
                continue

            try:
                size = download_one(url, dest, session)
                cursor.execute(
                    "UPDATE concalls SET transcript_path=?, status='downloaded', error=NULL WHERE id=?",
                    (str(dest), cc["id"]))
                _upsert_company_file(cursor, company_id, ann_id, url, a["exchange"], "done", str(dest), size)
                conn.commit()
                downloaded += 1
                time.sleep(0.3)
            except Exception as e:
                cursor.execute(
                    "UPDATE concalls SET status='error', error=? WHERE id=?",
                    (str(e)[:200], cc["id"]))
                conn.commit()
                failed += 1
    return downloaded, already, failed


# ---------------- Analysis ----------------

SYSTEM = (
    "You are an expert financial analyst. Extract structured insights from Indian "
    "earnings-call transcripts. Use only facts stated in the transcript. Amounts in "
    "Indian Rupees (crore). Reply with ONLY valid JSON, no markdown fences."
)

DIGEST_PROMPT = """Analyze this excerpt of an earnings-call transcript for {company} ({symbol}).

ANNOUNCEMENT: {headline}
DATE: {date}

TRANSCRIPT EXCERPT:
{content}

Return EXACTLY this JSON (no other text):
{{
  "summary": "2-3 sentence overview of this part of the call, with numbers",
  "guidance": "any forward-looking guidance/revenue targets stated, or empty string",
  "management_views": "key management commentary / strategy / outlook, or empty string",
  "qna_summary": "notable analyst questions and management answers, or empty string",
  "key_topics": ["short topic labels"],
  "key_numbers": ["specific numbers/dates mentioned"]
}}
Keep total under 250 words."""

MERGE_PROMPT = """You are consolidating partial digests of one earnings-call transcript for {company} ({symbol}).

HEADLINE: {headline}
DATE: {date}

PARTIAL DIGESTS:
{content}

Return ONE consolidated JSON (no other text):
{{
  "summary": "4-6 sentence overview of the whole call with key numbers",
  "guidance": "management guidance for the upcoming period with specific figures, or empty string",
  "management_views": "key management commentary / strategy / outlook",
  "qna_summary": "highlights from the Q&A session (topics analysts probed, management responses)",
  "key_topics": ["short topic labels"],
  "key_numbers": ["specific numbers/dates mentioned"],
  "sentiment": "positive" | "neutral" | "negative",
  "importance": "high" | "medium" | "low"
}}
Keep under 450 words."""

SINGLE_PROMPT = """Analyze this earnings-call transcript for {company} ({symbol}).

ANNOUNCEMENT: {headline}
DATE: {date}

TRANSCRIPT:
{content}

Return EXACTLY this JSON (no other text):
{{
  "summary": "4-6 sentence overview of the call with key numbers",
  "guidance": "management guidance for the upcoming period with specific figures, or empty string",
  "management_views": "key management commentary / strategy / outlook",
  "qna_summary": "highlights from the Q&A session (topics analysts probed, management responses)",
  "key_topics": ["short topic labels"],
  "key_numbers": ["specific numbers/dates mentioned"],
  "sentiment": "positive" | "neutral" | "negative",
  "importance": "high" | "medium" | "low"
}}
Keep under 450 words."""


def _parse_json(raw):
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(raw[start:end])
    return None


def extract_text(path):
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        import fitz
        text = []
        try:
            doc = fitz.open(path)
            for i, page in enumerate(doc):
                if i >= MAX_PAGES:
                    break
                text.append(page.get_text())
            doc.close()
        except Exception:
            return ""
        return "\n".join(text)
    if ext in (".txt", ".htm", ".html"):
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def chunk_text(text, size=CHUNK):
    text = text.strip()
    if not text:
        return []
    chunks = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def analyze_row(conn, cc):
    """Analyze one concall row. Returns (status, message, parsed)."""
    company_id = cc["company_id"]
    path = cc["transcript_path"]
    if not path or not Path(path).exists():
        return "error", "transcript file missing", None

    cursor = conn.cursor()
    cursor.execute("SELECT company_name, nse_symbol, bse_code FROM companies WHERE id=?", (company_id,))
    comp = cursor.fetchone()
    if not comp:
        return "error", "company not found", None
    company = comp["company_name"]
    symbol = comp["nse_symbol"] or comp["bse_code"] or ""
    headline = cc["title"] or ""
    date = cc["call_date"] or ""

    text = extract_text(path)
    if len(text) < 50:
        return "error", "no extractable text (scanned/image PDF)", None

    chunks = chunk_text(text)
    if len(chunks) == 1:
        prompt = SINGLE_PROMPT.format(company=company, symbol=symbol,
                                      headline=headline, date=date, content=chunks[0][:MAX_TEXT])
        raw = generate(SYSTEM, prompt, max_tokens=2000)
        parsed = _parse_json(raw)
        if not parsed:
            return "retry", "could not parse LLM JSON", None
        return "done", "", parsed

    digests = []
    for chunk in chunks:
        prompt = DIGEST_PROMPT.format(company=company, symbol=symbol,
                                      headline=headline, date=date, content=chunk)
        raw = generate(SYSTEM, prompt, max_tokens=900)
        p = _parse_json(raw)
        if p:
            digests.append(json.dumps(p, ensure_ascii=False))
        time.sleep(0.4)
    if not digests:
        return "retry", "no digest parsed", None

    merged = "\n---\n".join(digests)
    prompt = MERGE_PROMPT.format(company=company, symbol=symbol,
                                 headline=headline, date=date, content=merged)
    raw = generate(SYSTEM, prompt, max_tokens=2000)
    parsed = _parse_json(raw)
    if not parsed:
        return "retry", "could not parse merged JSON", None
    return "done", "", parsed


def analyze_downloaded(conn, company_id, limit=None, stop_cb=None, on_progress=None):
    """Analyze concalls rows with status='downloaded'. Returns processed count."""
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, company_id, announcement_id, call_date, quarter, title,
                  transcript_path, status
           FROM concalls WHERE company_id=? AND status='downloaded'
           ORDER BY call_date DESC LIMIT ?""",
        (company_id, limit or -1))
    rows = [dict(r) for r in cursor.fetchall()]

    processed = 0
    for cc in rows:
        if stop_cb and stop_cb():
            break
        try:
            status, msg, parsed = analyze_row(conn, cc)
            if status == "done":
                cursor.execute(
                    """UPDATE concalls SET status='done', analyzed_at=datetime('now'),
                       error=NULL, summary=?, guidance=?, management_views=?, qna_summary=?,
                       key_topics=?, key_numbers=?, sentiment=?, importance=?
                       WHERE id=?""",
                    (parsed.get("summary") or "", parsed.get("guidance") or "",
                     parsed.get("management_views") or "", parsed.get("qna_summary") or "",
                     json.dumps(parsed.get("key_topics") or []),
                     json.dumps(parsed.get("key_numbers") or []),
                     parsed.get("sentiment") or "neutral",
                     parsed.get("importance") or "medium", cc["id"]))
            elif status == "retry":
                cursor.execute(
                    "UPDATE concalls SET status='downloaded', error=? WHERE id=?",
                    (msg[:300], cc["id"]))
            else:
                cursor.execute(
                    "UPDATE concalls SET status='error', error=? WHERE id=?",
                    (msg[:300], cc["id"]))
            conn.commit()
            processed += 1
            if on_progress:
                on_progress(cc["id"], status, msg)
            time.sleep(0.4)
        except Exception as e:
            # Transient failure -> stays 'downloaded', retried next run
            if on_progress:
                on_progress(cc["id"], "retry", str(e)[:120])
            time.sleep(1)
    return processed
