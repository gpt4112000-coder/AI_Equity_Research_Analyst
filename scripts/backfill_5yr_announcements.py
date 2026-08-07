#!/usr/bin/env python3
"""Backfill 5 years of announcements for every company from BSE + NSE.

Fetches per-company historical announcements directly from the exchanges
(the daily cache only holds ~13 months), stores them into the `announcements`
table (dedup-safe via INSERT OR IGNORE), tracks progress in `backfill_status`,
and writes a per-company announcements.json + attachments/ layout.

Usage:
  python backfill_5yr_announcements.py                  # all companies, both exchanges
  python backfill_5yr_announcements.py --company 12     # single company
  python backfill_5yr_announcements.py --exchanges bse  # only BSE
  python backfill_5yr_announcements.py --years 5        # default 5
  python backfill_5yr_announcements.py --resume         # skip done companies
"""
import sys
import json
import argparse
import datetime as dt
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, "/home/ubuntu/FinEng/BseIndiaApi/src")

from config import COMPANY_FILES_DIR, BACKFILL_YEARS
from data.storage.db import get_db
from import_all_announcements import parse_bse, parse_nse

CHUNK_DAYS = 365  # 1-year chunks; BSE archives respond fine over 1yr ranges


def chunk_dates(from_date, to_date, max_days=CHUNK_DAYS):
    """Split a date range into overlapping-safe sequential chunks."""
    chunks = []
    cur = from_date
    while cur < to_date:
        end = min(cur + dt.timedelta(days=max_days - 1), to_date)
        chunks.append((cur, end))
        cur = end + dt.timedelta(days=1)
    return chunks


def fetch_bse_company(bse_code, from_date, to_date, bse=None):
    """Fetch all BSE announcements for a scrip over a date range (paginated 50/page)."""
    results = []
    page = 1
    while True:
        res = bse.announcements(
            page_no=page,
            from_date=from_date,
            to_date=to_date,
            scripcode=str(bse_code),
        )
        table = res.get("Table") or []
        results.extend(table)
        try:
            total = int(res["Table1"][0]["ROWCNT"])
        except (KeyError, IndexError, TypeError, ValueError):
            total = len(results)
        if len(results) >= total or not table:
            break
        page += 1
        time.sleep(0.3)
    return results


def fetch_nse_company(nse_symbol, from_date, to_date, nse=None):
    """Fetch all NSE announcements for a symbol over a date range."""
    res = nse.announcements(
        index="equities",
        symbol=nse_symbol,
        from_date=from_date,
        to_date=to_date,
    )
    return res or []


def company_dir(company_id):
    return COMPANY_FILES_DIR / str(company_id)


def write_company_announcements(company_id, rows):
    """Write all announcements metadata for a company to its folder."""
    folder = company_dir(company_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "attachments").mkdir(exist_ok=True)
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "exchange": r["exchange"],
            "category": r["category"],
            "headline": r["headline"],
            "description": r["description"],
            "announcement_date": r["announcement_date"],
            "announcement_time": r["announcement_time"],
            "is_critical": r["is_critical"],
            "attachment_url": r["attachment_url"],
        })
    (folder / "announcements.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    return len(out)


def backfill_company(conn, cursor, company, exchange, years, resume):
    company_id = company["id"]
    bse_code = company["bse_code"]
    nse_symbol = company["nse_symbol"]

    if exchange == "bse" and not bse_code:
        return 0, 0, "no_bse_code"
    if exchange == "nse" and not nse_symbol:
        return 0, 0, "no_nse_symbol"

    # Resume: skip if already completed
    if resume:
        cursor.execute(
            "SELECT status FROM backfill_status WHERE company_id=? AND exchange=?",
            (company_id, exchange))
        row = cursor.fetchone()
        if row and row["status"] == "done":
            return 0, 0, "already_done"

    from_date = dt.date.today() - dt.timedelta(days=365 * years)
    to_date = dt.date.today()

    cursor.execute(
        """INSERT OR REPLACE INTO backfill_status
           (company_id, exchange, status, from_date, to_date, started_at)
           VALUES (?, ?, 'running', ?, ?, datetime('now'))""",
        (company_id, exchange, from_date, to_date))
    conn.commit()

    try:
        if exchange == "bse":
            from bse import BSE
            with BSE('/tmp/opencode/bse_backfill') as bse:
                raw_rows = []
                for fd, td in chunk_dates(from_date, to_date):
                    raw_rows.extend(fetch_bse_company(bse_code, fd, td, bse))
                    time.sleep(0.3)
            parsed = [parse_bse(r) for r in raw_rows if isinstance(r, dict)]
        else:
            from nse.NSE import NSE
            with NSE('/tmp/opencode/nse_backfill', server=False) as nse:
                raw_rows = []
                for fd, td in chunk_dates(from_date, to_date):
                    raw_rows.extend(fetch_nse_company(nse_symbol, fd, td, nse))
                    time.sleep(0.5)
            parsed = [parse_nse(r) for r in raw_rows if isinstance(r, dict)]

        # Insert (dedup-safe via unique index on company+date+headline)
        inserted = 0
        seen_dates = set()
        for p in parsed:
            if not p["announcement_date"]:
                continue
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO announcements
                    (company_id, exchange, category, subcategory, headline, description,
                     attachment_url, announcement_date, announcement_time,
                     is_critical, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    company_id, p['exchange'], p['category'], p.get('subcategory', ''),
                    p['headline'], p['description'], p['attachment_url'],
                    p['announcement_date'], p['announcement_time'],
                    p['is_critical'], p['raw_data'],
                ))
                if cursor.rowcount > 0:
                    inserted += 1
                if p["announcement_date"]:
                    seen_dates.add(p["announcement_date"])
            except Exception:
                continue

        # Track source files for later download
        for p in parsed:
            if p.get("attachment_url"):
                try:
                    cursor.execute("SELECT id FROM announcements WHERE company_id=? AND attachment_url=? LIMIT 1",
                                   (company_id, p["attachment_url"]))
                    arow = cursor.fetchone()
                    aid = arow["id"] if arow else None
                    cursor.execute('''
                        INSERT OR IGNORE INTO company_files
                        (company_id, announcement_id, source_url, source_exchange, status)
                        VALUES (?, ?, ?, ?, 'pending')
                    ''', (company_id, aid, p["attachment_url"], p["exchange"]))
                except Exception:
                    continue

        cursor.execute(
            """UPDATE backfill_status SET status='done',
               announcements_fetched=?, announcements_inserted=?,
               finished_at=datetime('now') WHERE company_id=? AND exchange=?""",
            (len(parsed), inserted, company_id, exchange))
        conn.commit()
        return len(parsed), inserted, "ok"
    except Exception as e:
        cursor.execute(
            """UPDATE backfill_status SET status='error', error=?,
               finished_at=datetime('now') WHERE company_id=? AND exchange=?""",
            (str(e)[:500], company_id, exchange))
        conn.commit()
        return 0, 0, f"error:{str(e)[:200]}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", type=int, help="Only backfill this company_id")
    parser.add_argument("--exchanges", default="both", choices=["both", "bse", "nse"])
    parser.add_argument("--years", type=int, default=BACKFILL_YEARS)
    parser.add_argument("--resume", action="store_true", help="Skip already-done companies")
    parser.add_argument("--limit", type=int, help="Max number of companies to process")
    args = parser.parse_args()

    conn = get_db()
    cursor = conn.cursor()
    if args.company:
        cursor.execute("SELECT id, bse_code, nse_symbol FROM companies WHERE id=?", (args.company,))
    else:
        cursor.execute("""
            SELECT id, bse_code, nse_symbol FROM companies
            WHERE is_active=1
            ORDER BY id
        """)
    companies = [dict(r) for r in cursor.fetchall()]
    if args.limit:
        companies = companies[:args.limit]
    conn.close()

    print(f"Backfilling {len(companies)} companies, {args.years} years, exchanges={args.exchanges}")

    stats = {"ok": 0, "error": 0, "skip": 0}
    start = time.time()
    for i, comp in enumerate(companies):
        exchanges = ["bse", "nse"] if args.exchanges == "both" else [args.exchanges]
        for ex in exchanges:
            conn = get_db()
            cursor = conn.cursor()
            fetched, inserted, status = backfill_company(conn, cursor, comp, ex, args.years, args.resume)
            conn.close()
            if status == "ok":
                stats["ok"] += 1
            elif status.startswith("error"):
                stats["error"] += 1
            else:
                stats["skip"] += 1
            print(f"[{i+1}/{len(companies)}] {comp['nse_symbol'] or comp['bse_code']} "
                  f"{ex}: {status} fetched={fetched} inserted={inserted}", flush=True)
        # Per-company announcements.json dump
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, exchange, category, headline, description, announcement_date,
                   announcement_time, is_critical, attachment_url
            FROM announcements WHERE company_id=? ORDER BY announcement_date
        """, (comp["id"],))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        n = write_company_announcements(comp["id"], rows)
        if i % 10 == 0 or n:
            print(f"    wrote {n} rows -> company_files/{comp['id']}/announcements.json", flush=True)

    elapsed = time.time() - start
    print(f"\n=== Backfill Complete: {stats['ok']} ok, {stats['error']} error, {stats['skip']} skip in {elapsed:.0f}s ===")


if __name__ == "__main__":
    main()
