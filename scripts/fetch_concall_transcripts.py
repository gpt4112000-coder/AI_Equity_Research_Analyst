#!/usr/bin/env python3
"""Download concall/transcript PDFs for each company and queue them for analysis.

Thin CLI wrapper over backend.concall_pipeline. See that module for details.

Usage:
  python fetch_concall_transcripts.py                 # last 2 years, all companies
  python fetch_concall_transcripts.py --company 681   # single company
  python fetch_concall_transcripts.py --years 5       # wider window
  python fetch_concall_transcripts.py --companies 20  # first N companies
  python fetch_concall_transcripts.py --dry-run
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db
from concall_pipeline import recent_cutoff, transcript_announcements, ensure_queued, download_pending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", type=int)
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--companies", type=int, help="Max companies to process")
    parser.add_argument("--limit", type=int, help="Max transcripts per company")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cutoff = recent_cutoff(args.years)
    print(f"Window: transcripts >= {cutoff}")

    conn = get_db()
    cursor = conn.cursor()
    if args.company:
        companies = [args.company]
    else:
        cursor.execute(
            "SELECT DISTINCT company_id FROM announcements WHERE announcement_date >= ? "
            "AND attachment_url IS NOT NULL AND attachment_url != '' "
            "AND (lower(headline) LIKE '%concall%' OR lower(headline) LIKE '%transcript%' "
            "OR lower(headline) LIKE '%con. call%' OR lower(headline) LIKE '%earnings call%' "
            "OR category IN ('Earnings Call Transcript','Analyst / Investor Meet',"
            "'Analysts/Institutional Investor Meet/Con. Call Updates',"
            "'Schedule of Analysts/Institutional Investor Meet/Con. Call')) ORDER BY company_id",
            (cutoff,))
        companies = [r["company_id"] for r in cursor.fetchall()]
    conn.close()
    if args.companies:
        companies = companies[:args.companies]
    print(f"Companies with transcripts in window: {len(companies)}")

    td = ta = tf = 0
    for i, cid in enumerate(companies):
        conn = get_db()
        if args.dry_run:
            rows = transcript_announcements(conn, cid, cutoff, args.limit)
            queued = len(rows)
            print(f"[{i+1}/{len(companies)}] company {cid}: would queue {queued} transcripts")
        else:
            ensure_queued(conn, cid, cutoff, args.limit)
            d, a, f = download_pending(conn, cid, cutoff, args.limit)
            td += d; ta += a; tf += f
            print(f"[{i+1}/{len(companies)}] company {cid}: {d} downloaded, {a} existing, {f} failed",
                  flush=True)
        conn.close()

    print(f"\n=== Done: {td} downloaded, {ta} already present, {tf} failed ===")


if __name__ == "__main__":
    main()
