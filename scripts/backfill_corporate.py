#!/usr/bin/env python3
"""Enrich each company with structured corporate events on top of announcements.

Pulls from the same exchange APIs as the announcement backfill (BseIndiaApi /
NseIndiaApi siblings) and stores:

  * Corporate actions (dividend / bonus / split / buyback / delisting)
    -> mirrored into the `announcements` table so the EXISTING AI pipeline
       (per-announcement summary -> ai_insights -> company Research Summary)
       analyzes them exactly once. `event_key` + a same-day/family match check
       prevent the AI from reading the same event twice (e.g. a dividend that
       is BOTH a BSE announcement and a corporate-action row).
  * Board meetings            -> `board_meetings` table
  * Result calendar (BSE)     -> `result_calendar` table
  * Annual reports (NSE)      -> `company_files` (kind='annual_report'), with
       optional download so they flow into the Document Insights AI pipeline
       (dedup via the existing UNIQUE(company_id, source_url) index + analyzed flag).

Progress tracked in `enrich_status` (resumable with --resume).

Usage:
  python backfill_corporate.py                       # all companies, both exchanges
  python backfill_corporate.py --company 12          # single company
  python backfill_corporate.py --exchanges bse       # only BSE side
  python backfill_corporate.py --resume              # skip completed companies
  python backfill_corporate.py --no-download         # record annual-reports links only
  python backfill_corporate.py --limit 20
"""
import sys
import json
import argparse
import datetime as dt
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, "/home/ubuntu/FinEng/BseIndiaApi/src")
sys.path.insert(0, "/home/ubuntu/FinEng/NseIndiaApi/src")

from config import COMPANY_FILES_DIR, BACKFILL_YEARS
from data.storage.db import get_db

CHUNK_DAYS = 365


def chunk_dates(from_date, to_date, max_days=CHUNK_DAYS):
    chunks = []
    cur = from_date
    while cur < to_date:
        end = min(cur + dt.timedelta(days=max_days - 1), to_date)
        chunks.append((cur, end))
        cur = end + dt.timedelta(days=1)
    return chunks


# ---------------------------------------------------------------------------
# Date parsing helpers (NSE "17-Oct-2023", BSE "25 Oct 2023", BSE yyyymmdd)
# ---------------------------------------------------------------------------
def parse_dt(value):
    if not value:
        return None
    v = str(value).strip()
    if not v or v in ("-", "NA", "n/a"):
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%b-%Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    # tolerate trailing time
    try:
        return dt.datetime.fromisoformat(v[:10]).date()
    except ValueError:
        return None


def purpose_family(purpose):
    """Map a corporate-action purpose string to (family_keyword, metric)."""
    p = (purpose or "").lower()
    if "dividend" in p:
        return "dividend", "dividend"
    if "bonus" in p:
        return "bonus", "dividend"
    if "split" in p or "sub-division" in p or "subdivision" in p:
        return "split", "dividend"
    if "buyback" in p or "buy back" in p:
        return "buyback", "acquisition"
    if "delist" in p:
        return "delisting", "regulatory"
    if "rights" in p:
        return "rights", "regulatory"
    return "corporate-action", "corporate_action"


def _has_matching_announcement(conn, company_id, date, family_keyword):
    """True if an announcement already covers this event on the same date for
    the same family — used so the AI never reads an event twice (e.g. a
    dividend published both as a BSE announcement and as a corporate action)."""
    cur = conn.cursor()
    cur.execute(
        """SELECT 1 FROM announcements
           WHERE company_id = ? AND announcement_date = ?
           AND LOWER(COALESCE(category,'') || ' ' || COALESCE(subcategory,'') || ' ' || COALESCE(headline,''))
               LIKE ? LIMIT 1""",
        (company_id, date, f"%{family_keyword}%"),
    )
    return cur.fetchone() is not None


def mirror_corporate_action(conn, company_id, exchange, purpose, ex_date, extra):
    """Insert one corporate action as an announcement row (dedup-safe)."""
    d = parse_dt(ex_date) if ex_date else None
    if not d:
        return 0
    family, metric = purpose_family(purpose)
    event_key = f"ca:{exchange}:{family}:{d.isoformat()}"
    sub = (purpose or "").strip()
    cat = f"Corporate Action - {family.title()}"
    headline = f"Corporate Action - {sub} (Ex-date {d.isoformat()})"

    cur = conn.cursor()
    # Skip if we already mirrored this exact event
    cur.execute("SELECT 1 FROM announcements WHERE company_id=? AND event_key=? LIMIT 1",
                (company_id, event_key))
    if cur.fetchone():
        return 0
    # Skip if a real announcement already covers it same-day/same-family
    if _has_matching_announcement(conn, company_id, d.isoformat(), family):
        return 0

    try:
        cur.execute("""
            INSERT OR IGNORE INTO announcements
            (company_id, exchange, category, subcategory, headline, description,
             announcement_date, announcement_time, is_critical, ai_summary, event_key, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, '', 1, NULL, ?, ?)
        """, (
            company_id, exchange, cat, sub, headline,
            json.dumps(extra, default=str), d.isoformat(), event_key,
            json.dumps({"source": "corporate_action", "metric": metric, **extra}, default=str),
        ))
        return 1 if cur.rowcount > 0 else 0
    except Exception:
        return 0


def fetch_bse_actions(bse_code, from_date, to_date, bse):
    out = []
    for fd, td in chunk_dates(from_date, to_date):
        try:
            rows = bse.actions(scripcode=str(bse_code), from_date=fd, to_date=td)
            if isinstance(rows, list):
                out.extend(rows)
        except Exception:
            pass
        time.sleep(0.3)
    return out


def fetch_bse_result_calendar(bse_code, from_date, to_date, bse):
    out = []
    for fd, td in chunk_dates(from_date, to_date):
        try:
            rows = bse.resultCalendar(scripcode=str(bse_code), from_date=fd, to_date=td)
            if isinstance(rows, list):
                out.extend(rows)
        except Exception:
            pass
        time.sleep(0.3)
    # resultCalendar only lists forthcoming results; a historical window returns
    # nothing, so also grab upcoming dates for this scrip.
    if not out:
        try:
            rows = bse.resultCalendar(scripcode=str(bse_code))
            if isinstance(rows, list):
                out.extend(rows)
        except Exception:
            pass
    return out


def fetch_nse_actions(nse_symbol, from_date, to_date, nse):
    out = []
    for fd, td in chunk_dates(from_date, to_date):
        try:
            rows = nse.actions(symbol=nse_symbol, from_date=fd, to_date=td)
            if isinstance(rows, list):
                out.extend(rows)
        except Exception:
            pass
        time.sleep(0.5)
    return out


def fetch_nse_board_meetings(nse_symbol, from_date, to_date, nse):
    out = []
    for fd, td in chunk_dates(from_date, to_date):
        try:
            rows = nse.boardMeetings(symbol=nse_symbol, from_date=fd, to_date=td)
            if isinstance(rows, list):
                out.extend(rows)
        except Exception:
            pass
        time.sleep(0.5)
    return out


def fetch_nse_annual_reports(nse_symbol, nse):
    try:
        res = nse.annual_reports(symbol=nse_symbol)
        return (res or {}).get("data", []) or []
    except Exception:
        return []


def store_board_meetings(conn, company_id, rows):
    n = 0
    cur = conn.cursor()
    for r in rows:
        date = parse_dt(r.get("bm_date"))
        purpose = r.get("bm_purpose") or r.get("bm_desc") or ""
        if not date or not purpose:
            continue
        try:
            cur.execute("""
                INSERT OR IGNORE INTO board_meetings
                (company_id, exchange, meeting_date, purpose, description, raw_data)
                VALUES (?, 'NSE', ?, ?, ?, ?)
            """, (company_id, date.isoformat(), purpose[:500], r.get("bm_desc", ""),
                  json.dumps(r, default=str)))
            n += cur.rowcount if cur.rowcount else 0
        except Exception:
            continue
    return n


def store_result_calendar(conn, company_id, rows):
    n = 0
    cur = conn.cursor()
    for r in rows:
        date = parse_dt(r.get("meeting_date") or r.get("result_date"))
        event = r.get("short_name") or r.get("event") or "Result"
        if not date:
            continue
        try:
            cur.execute("""
                INSERT OR IGNORE INTO result_calendar
                (company_id, exchange, result_date, event, period, raw_data)
                VALUES (?, 'BSE', ?, ?, ?, ?)
            """, (company_id, date.isoformat(), event, "", json.dumps(r, default=str)))
            n += cur.rowcount if cur.rowcount else 0
        except Exception:
            continue
    return n


def store_annual_reports(conn, company_id, rows, download, nse):
    n = 0
    cur = conn.cursor()
    attach_dir = COMPANY_FILES_DIR / str(company_id) / "attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        url = r.get("fileName") or r.get("fileUrl")
        if not url:
            continue
        kind = "annual_report"
        local_path = None
        status = "pending"
        if download and nse is not None:
            try:
                f = nse.download_document(url, folder=attach_dir)
                local_path = str(f)
                status = "done"
            except Exception:
                status = "failed"
        try:
            cur.execute("""
                INSERT OR IGNORE INTO company_files
                (company_id, announcement_id, kind, local_path, source_url,
                 source_exchange, status, analyzed, downloaded_at)
                VALUES (?, NULL, ?, ?, ?, 'NSE', ?, 0, datetime('now'))
            """, (company_id, kind, local_path, url, status))
            n += cur.rowcount if cur.rowcount else 0
        except Exception:
            continue
    return n


def enrich_company(conn, cursor, company, exchanges, years, resume, download):
    company_id = company["id"]
    bse_code = company["bse_code"]
    nse_symbol = company["nse_symbol"]
    from_date = dt.date.today() - dt.timedelta(days=365 * years)
    to_date = dt.date.today()

    stats = {"actions_found": 0, "actions_mirrored": 0, "board_meetings_found": 0,
             "result_calendar_found": 0, "annual_reports_found": 0}
    results = {}

    for ex in exchanges:
        if ex == "bse" and not bse_code:
            results["bse"] = "no_bse_code"
            continue
        if ex == "nse" and not nse_symbol:
            results["nse"] = "no_nse_symbol"
            continue
        if resume:
            cursor.execute(
                "SELECT status FROM enrich_status WHERE company_id=? AND exchange=?",
                (company_id, ex))
            row = cursor.fetchone()
            if row and row["status"] == "done":
                results[ex] = "already_done"
                continue

        cursor.execute(
            """INSERT OR REPLACE INTO enrich_status
               (company_id, exchange, status, started_at)
               VALUES (?, ?, 'running', datetime('now'))""",
            (company_id, ex))
        conn.commit()

        try:
            if ex == "bse":
                from bse import BSE
                with BSE('/tmp/opencode/bse_enrich') as bse:
                    actions = fetch_bse_actions(bse_code, from_date, to_date, bse)
                    mirrored = sum(
                        mirror_corporate_action(conn, company_id, "BSE", r.get("Purpose"),
                                                r.get("Ex_date") or r.get("exdate"), r)
                        for r in actions if isinstance(r, dict)
                    )
                    stats["actions_found"] += len(actions)
                    stats["actions_mirrored"] += mirrored
                    rc = fetch_bse_result_calendar(bse_code, from_date, to_date, bse)
                    stats["result_calendar_found"] += store_result_calendar(conn, company_id, rc)
                conn.commit()
                results[ex] = f"ok actions={len(actions)} mirrored={mirrored} rc={stats['result_calendar_found']}"
            else:
                from nse.NSE import NSE
                with NSE('/tmp/opencode/nse_enrich', server=False) as nse:
                    actions = fetch_nse_actions(nse_symbol, from_date, to_date, nse)
                    mirrored = sum(
                        mirror_corporate_action(conn, company_id, "NSE", r.get("subject"),
                                                r.get("exDate"), r)
                        for r in actions if isinstance(r, dict)
                    )
                    stats["actions_found"] += len(actions)
                    stats["actions_mirrored"] += mirrored
                    bm = fetch_nse_board_meetings(nse_symbol, from_date, to_date, nse)
                    stats["board_meetings_found"] += store_board_meetings(conn, company_id, bm)
                    ar = fetch_nse_annual_reports(nse_symbol, nse)
                    stats["annual_reports_found"] += store_annual_reports(
                        conn, company_id, ar, download, nse)
                conn.commit()
                results[ex] = f"ok actions={len(actions)} mirrored={mirrored} bm={len(bm)} ar={len(ar)}"

            cursor.execute(
                """UPDATE enrich_status SET status='done',
                   actions_found=?, actions_mirrored=?, board_meetings_found=?,
                   result_calendar_found=?, annual_reports_found=?,
                   finished_at=datetime('now') WHERE company_id=? AND exchange=?""",
                (stats["actions_found"], stats["actions_mirrored"],
                 stats["board_meetings_found"], stats["result_calendar_found"],
                 stats["annual_reports_found"], company_id, ex))
            conn.commit()
        except Exception as e:
            cursor.execute(
                """UPDATE enrich_status SET status='error', error=?,
                   finished_at=datetime('now') WHERE company_id=? AND exchange=?""",
                (str(e)[:500], company_id, ex))
            conn.commit()
            results[ex] = f"error:{str(e)[:120]}"

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", type=int)
    parser.add_argument("--exchanges", default="both", choices=["both", "bse", "nse"])
    parser.add_argument("--years", type=int, default=BACKFILL_YEARS)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-download", action="store_true",
                        help="Record annual-report links only; skip downloading PDFs")
    args = parser.parse_args()

    conn = get_db()
    cursor = conn.cursor()
    if args.company:
        cursor.execute("SELECT id, bse_code, nse_symbol FROM companies WHERE id=?", (args.company,))
    else:
        cursor.execute("""
            SELECT id, bse_code, nse_symbol FROM companies
            WHERE is_active=1 ORDER BY id
        """)
    companies = [dict(r) for r in cursor.fetchall()]
    if args.limit:
        companies = companies[:args.limit]
    conn.close()

    exchanges = ["bse", "nse"] if args.exchanges == "both" else [args.exchanges]
    download = not args.no_download
    print(f"Enriching {len(companies)} companies, {args.years} years, exchanges={exchanges}, download_ar={download}")

    start = time.time()
    for i, comp in enumerate(companies):
        conn = get_db()
        cursor = conn.cursor()
        results = enrich_company(conn, cursor, comp, exchanges, args.years, args.resume, download)
        conn.close()
        print(f"[{i+1}/{len(companies)}] {comp['nse_symbol'] or comp['bse_code']}: {results}", flush=True)

    print(f"\n=== Enrich Complete in {time.time()-start:.0f}s ===")


if __name__ == "__main__":
    main()
