#!/usr/bin/env python3
"""Run the full per-company pipeline: backfill 5yr -> download -> analyze.

Orchestrates the three stages for a company (or all companies):
  1. backfill_5yr_announcements.py   - fetch 5 years from BSE + NSE into DB
  2. download_attachments.py         - download attachment files to company folder
  3. analyze_documents.py            - extract text + AI insights into document_insights

Usage:
  python run_pipeline.py --company 516
  python run_pipeline.py --start 516 --count 10
  python run_pipeline.py --all --only-backfill   # just fetch announcements
  python run_pipeline.py --all --skip-download
"""
import sys
import time
import argparse
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db

SCRIPTS = Path(__file__).parent


def run_script(name, args_list):
    cmd = [sys.executable, str(SCRIPTS / name)] + args_list
    print(f"  $ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout:
        for line in r.stdout.strip().splitlines()[-5:]:
            print(f"    {line}", flush=True)
    if r.returncode != 0 and r.stderr:
        for line in r.stderr.strip().splitlines()[-8:]:
            print(f"    ERR {line}", flush=True)
    return r.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", type=int)
    parser.add_argument("--start", type=int, help="first company id (for --all chunked runs)")
    parser.add_argument("--count", type=int, default=1, help="companies to process in this run")
    parser.add_argument("--only-backfill", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-analyze", action="store_true")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if args.company:
        ids = [args.company]
    else:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM companies WHERE is_active=1 ORDER BY id")
        all_ids = [r[0] for r in cursor.fetchall()]
        conn.close()
        start_idx = 0
        if args.start:
            start_idx = next((i for i, x in enumerate(all_ids) if x >= args.start), 0)
        ids = all_ids[start_idx:start_idx + args.count]

    print(f"Pipeline: {len(ids)} companies (years={args.years}, resume={args.resume})")

    for i, cid in enumerate(ids):
        print(f"\n=== [{i+1}/{len(ids)}] Company {cid} ===", flush=True)
        # Stage 1: backfill (only if no done status for both exchanges)
        done = set()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT exchange, status FROM backfill_status WHERE company_id=?", (cid,))
        done = {r[0]: r[1] for r in cursor.fetchall()}
        conn.close()
        if args.resume and done.get("bse") == "done" and done.get("nse") == "done":
            print("  backfill already done, skipping", flush=True)
        else:
            rc = run_script("backfill_5yr_announcements.py",
                            [f"--company={cid}", f"--years={args.years}"])
            if rc != 0:
                print(f"  backfill FAILED for {cid}, skipping", flush=True)
                continue

        if args.only_backfill:
            continue

        # Stage 2: download attachments (important kinds)
        if not args.skip_download:
            run_script("download_attachments.py", [f"--company={cid}", "--limit=2000"])

        # Stage 3: analyze with AI
        if not args.skip_analyze:
            run_script("analyze_documents.py", [f"--company={cid}", "--limit=2000"])

        print(f"  done in {time.strftime('%H:%M:%S')}", flush=True)

    print("\n=== Pipeline Complete ===", flush=True)


if __name__ == "__main__":
    main()
