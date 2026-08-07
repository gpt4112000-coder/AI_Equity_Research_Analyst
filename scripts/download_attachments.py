#!/usr/bin/env python3
"""Download announcement attachments (PDF/transcripts) into per-company folders.

Fetches the attachment URLs tracked in `company_files` (populated by
backfill_5yr_announcements.py), saves files into
  backend/data/company_files/{company_id}/attachments/
and updates the DB row status to done/error + kind.

By default only downloads important content (financial results, transcripts,
concalls, annual reports, orders, credit ratings, presentations). Use
--all to download everything, or --kind to filter.

Usage:
  python download_attachments.py                     # pending files, important kinds only
  python download_attachments.py --company 516       # single company
  python download_attachments.py --all               # all kinds
  python download_attachments.py --kind transcript   # only transcripts/concalls
  python download_attachments.py --limit 50          # cap per run
"""
import sys
import re
import time
import argparse
import mimetypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from config import COMPANY_FILES_DIR
from data.storage.db import get_db

IMPORTANT_KINDS = [
    "transcript", "concall", "annual_report", "credit_rating",
    "financial_result", "order", "presentation", "dividend",
]

KIND_PATTERNS = {
    "transcript": [r"transcript", r"con.?call", r"earnings.?call", r"investor.?meet",
                   r"analyst.?meet", r"institutional.?investor"],
    "annual_report": [r"annual.?report", r"_ar", r"/annual_reports/"],
    "credit_rating": [r"credit.?rating", r"rating"],
    "financial_result": [r"financial.?result", r"result", r"quarterly"],
    "order": [r"order", r"contract", r"bagging"],
    "presentation": [r"ppt", r"presentation", r"deck"],
    "dividend": [r"dividend", r"bonus"],
    "regulatory": [r"reg\s*\.?\s*\d", r"compliance", r"board.?meeting"],
    "general": [],
}

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".zip": "application/zip", ".htm": "text/html", ".html": "text/html",
}


def classify_kind(headline, url):
    """Guess the content kind from headline + URL."""
    text = f"{headline or ''} {url or ''}".lower()
    for kind, patterns in KIND_PATTERNS.items():
        if kind == "general":
            continue
        if any(re.search(p, text) for p in patterns):
            return kind
    return "general"


def safe_filename(url, ann_id):
    """Derive a safe local filename from the URL."""
    stem = url.rstrip("/").split("/")[-1]
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)
    if not stem or len(stem) < 4:
        stem = f"ann_{ann_id}"
    # Some NSE links have no extension but are PDFs
    if "." not in Path(stem).suffix:
        stem = f"{stem}.pdf"
    return stem


def _bse_alt_url(url):
    """Old BSE attachments live under /AttachHis/ instead of /AttachLive/."""
    if "corpfiling/AttachLive/" in url:
        return url.replace("corpfiling/AttachLive/", "corpfiling/AttachHis/")
    if "corpfiling/AttachHis/" in url:
        return url.replace("corpfiling/AttachHis/", "corpfiling/AttachLive/")
    return None


def download_one(url, dest_path, session=None):
    import httpx
    if session is None:
        session = httpx.Client(follow_redirects=True, timeout=60)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/138.0",
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Referer": "https://www.nseindia.com/" if "nseindia.com" in url else "https://www.bseindia.com/",
    }
    resp = session.get(url, headers=headers)
    if resp.status_code == 404:
        # Try alternate BSE archive path for old attachments
        alt = _bse_alt_url(url)
        if alt:
            resp = session.get(alt, headers=headers)
    resp.raise_for_status()
    content = resp.content
    if not content:
        raise RuntimeError("empty response")
    # NSE sometimes serves an HTML page instead of the file
    if content[:5] not in (b"%PDF-", b"PK\x03\x04") and b"<!DOCTYPE" in content[:500]:
        raise RuntimeError("server returned HTML page instead of document")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(content)
    return len(content)


def process_company(conn, company_id, all_kinds, kind_filter, limit, dry_run):
    cursor = conn.cursor()
    cursor.execute("SELECT nse_symbol, bse_code FROM companies WHERE id=?", (company_id,))
    comp = cursor.fetchone()
    if not comp:
        return 0, 0
    folder = COMPANY_FILES_DIR / str(company_id) / "attachments"
    folder.mkdir(parents=True, exist_ok=True)

    # Pick pending rows, optionally filtered by kind
    if kind_filter:
        cursor.execute(
            """SELECT id, announcement_id, source_url, source_exchange, status
               FROM company_files WHERE company_id=? AND status='pending' LIMIT ?""",
            (company_id, limit))
    else:
        cursor.execute(
            """SELECT id, announcement_id, source_url, source_exchange, status
               FROM company_files WHERE company_id=? AND status='pending' LIMIT ?""",
            (company_id, limit))
    rows = [dict(r) for r in cursor.fetchall()]

    # Attach headline for classification
    headline_map = {}
    if rows:
        cursor.execute(
            f"""SELECT id, headline FROM announcements WHERE id IN ({','.join('?' for _ in rows)})""",
            [r["announcement_id"] for r in rows])
        headline_map = {r["id"]: r["headline"] for r in cursor.fetchall()}

    done = failed = 0
    import httpx
    with httpx.Client(follow_redirects=True, timeout=60) as session:
        for row in rows:
            url = row["source_url"]
            if not url:
                continue
            headline = headline_map.get(row["announcement_id"], "")
            kind = classify_kind(headline, url)
            if kind_filter and kind_filter not in (kind, "general"):
                continue
            if not all_kinds and kind not in IMPORTANT_KINDS:
                # still mark as skipped to avoid re-checking every run
                if not dry_run:
                    cursor.execute("UPDATE company_files SET status='skipped' WHERE id=?", (row["id"],))
                continue

            fname = safe_filename(url, row["announcement_id"])
            dest = folder / fname
            if dest.exists() and dest.stat().st_size > 0:
                if not dry_run:
                    cursor.execute(
                        """UPDATE company_files SET status='done', kind=?, local_path=?,
                           file_size=?, downloaded_at=datetime('now') WHERE id=?""",
                        (kind, str(dest), dest.stat().st_size, row["id"]))
                done += 1
                continue

            if dry_run:
                print(f"  would download {kind}: {fname}")
                continue

            try:
                size = download_one(url, dest)
                cursor.execute(
                    """UPDATE company_files SET status='done', kind=?, local_path=?,
                       file_size=?, downloaded_at=datetime('now') WHERE id=?""",
                    (kind, str(dest), size, row["id"]))
                done += 1
                print(f"  [{kind}] {fname} ({size:,} bytes)", flush=True)
                time.sleep(0.3)
            except Exception as e:
                cursor.execute(
                    "UPDATE company_files SET status='error' WHERE id=?", (row["id"],))
                failed += 1
                print(f"  ERR {fname}: {str(e)[:120]}", flush=True)

        conn.commit()
    return done, failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", type=int)
    parser.add_argument("--all", action="store_true", help="Download all kinds (not just important)")
    parser.add_argument("--kind", help="Only download one kind (transcript, annual_report, ...)")
    parser.add_argument("--limit", type=int, default=1000, help="Max pending files per company")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--companies", type=int, help="Max companies to process")
    args = parser.parse_args()

    conn = get_db()
    if args.company:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT company_id FROM company_files WHERE status='pending' AND company_id=?",
                       (args.company,))
    else:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT company_id FROM company_files WHERE status='pending' ORDER BY company_id")
    companies = [r[0] for r in cursor.fetchall()]

    if args.companies:
        companies = companies[:args.companies]
    conn.close()
    print(f"Processing {len(companies)} companies")

    total_done = total_failed = 0
    for i, cid in enumerate(companies):
        conn = get_db()
        d, f = process_company(conn, cid, args.all, args.kind, args.limit, args.dry_run)
        conn.close()
        total_done += d
        total_failed += f
        print(f"[{i+1}/{len(companies)}] company {cid}: {d} done, {f} failed", flush=True)

    print(f"\n=== Download Complete: {total_done} downloaded, {total_failed} failed ===")


if __name__ == "__main__":
    main()
