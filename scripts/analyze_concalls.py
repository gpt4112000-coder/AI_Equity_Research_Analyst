#!/usr/bin/env python3
"""Analyze downloaded concall transcripts and store AI summaries per company.

Thin CLI wrapper over backend.concall_pipeline (llm backend selected by the
CONCALL_LLM_BACKEND env var: 'opencode' default, 'ollama' local).

Status transitions: downloaded -> done | error; transient failures stay
'downloaded' so re-runs resume where left off.

Usage:
  python analyze_concalls.py                    # all downloaded transcripts
  python analyze_concalls.py --company 681
  python analyze_concalls.py --companies 20 --limit 10
  python analyze_concalls.py --dry-run
  CONCALL_LLM_BACKEND=ollama python analyze_concalls.py
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db
from concall_pipeline import analyze_downloaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", type=int)
    parser.add_argument("--companies", type=int, help="Max companies to process")
    parser.add_argument("--limit", type=int, help="Max concalls per company")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_db()
    cursor = conn.cursor()
    if args.company:
        cursor.execute("SELECT DISTINCT company_id FROM concalls WHERE status='downloaded' AND company_id=?",
                       (args.company,))
    else:
        cursor.execute("SELECT DISTINCT company_id FROM concalls WHERE status='downloaded' ORDER BY company_id")
    companies = [r["company_id"] for r in cursor.fetchall()]
    if args.companies:
        companies = companies[:args.companies]
    conn.close()
    print(f"Companies with downloaded transcripts to analyze: {len(companies)}")

    if args.dry_run:
        total = 0
        for cid in companies:
            conn = get_db()
            cursor = conn.cursor()
            n = cursor.execute(
                "SELECT COUNT(*) n FROM concalls WHERE company_id=? AND status='downloaded'",
                (cid,)).fetchone()["n"]
            conn.close()
            total += n
            print(f"  company {cid}: would analyze {n} transcripts")
        print(f"\n=== Dry run: {total} transcripts queued ===")
        return

    total = 0
    for i, cid in enumerate(companies):
        conn = get_db()
        p = analyze_downloaded(conn, cid, args.limit)
        conn.close()
        total += p
        print(f"[{i+1}/{len(companies)}] company {cid}: {p} analyzed", flush=True)

    print(f"\n=== Analysis Complete: {total} transcripts processed ===")


if __name__ == "__main__":
    main()
