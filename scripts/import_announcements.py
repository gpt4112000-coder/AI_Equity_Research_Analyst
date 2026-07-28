#!/usr/bin/env python3
"""
Step 2: Import all announcements from cached JSON files.
Handles both BSE and NSE announcement formats.
"""
import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path("/home/ubuntu/FinEng")))

from data.storage.db import get_db
from config import EXISTING_DATA_DIR

# Build lookup maps from DB
COMPANY_MAP = {}  # bse_code -> id, nse_symbol -> id, company_name -> id


def build_company_maps(conn):
    """Build lookup maps for matching announcements to companies."""
    global COMPANY_MAP
    cursor = conn.cursor()
    cursor.execute("SELECT id, bse_code, nse_symbol, company_name FROM companies WHERE is_active = 1")
    for row in cursor.fetchall():
        if row["bse_code"]:
            COMPANY_MAP[str(row["bse_code"])] = row["id"]
        if row["nse_symbol"]:
            COMPANY_MAP[row["nse_symbol"].upper()] = row["id"]
        if row["company_name"]:
            name = row["company_name"].upper().strip()
            if name not in COMPANY_MAP:
                COMPANY_MAP[name] = row["id"]
    print(f"  Company lookup maps: {len(COMPANY_MAP)} entries")


def parse_bse_date(dt_str):
    """Parse BSE datetime string like '2025-07-15T23:55:48.01'."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "")).isoformat()
    except Exception:
        try:
            return datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S").isoformat()
        except Exception:
            return None


def parse_nse_date(dt_str):
    """Parse NSE datetime string. Handles both '2025-08-04 22:40:55' and '04-Aug-2025 22:40:55'."""
    if not dt_str:
        return None
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").isoformat()
    except Exception:
        try:
            return datetime.strptime(dt_str, "%d-%b-%Y %H:%M:%S").isoformat()
        except Exception:
            return None


def parse_nse_dt(dt_str):
    """Parse NSE dt field like '04082025224055'."""
    if not dt_str:
        return None
    try:
        return datetime.strptime(dt_str, "%d%m%Y%H%M%S").isoformat()
    except Exception:
        return None


def match_company(item, source):
    """Match announcement to a company in DB. Returns (company_id, matched_by)."""
    if source == "bse":
        scrp_cd = str(item.get("SCRIP_CD", ""))
        if scrp_cd in COMPANY_MAP:
            return COMPANY_MAP[scrp_cd], "bse_code"

        name = (item.get("SLONGNAME") or "").upper().strip()
        if name and name in COMPANY_MAP:
            return COMPANY_MAP[name], "company_name"

    elif source == "nse":
        sym = (item.get("symbol") or "").upper().strip()
        if sym in COMPANY_MAP:
            return COMPANY_MAP[sym], "nse_symbol"

        name = (item.get("sm_name") or "").upper().strip()
        if name and name in COMPANY_MAP:
            return COMPANY_MAP[name], "company_name"

        isin = (item.get("sm_isin") or "").strip()
        if isin:
            for key, cid in COMPANY_MAP.items():
                if key == isin:
                    return cid, "isin"

    return None, None


def import_bse_announcement(item, conn):
    """Import a single BSE announcement."""
    company_id, matched_by = match_company(item, "bse")
    if not company_id:
        return False

    cursor = conn.cursor()
    news_id = item.get("NEWSID", "")
    dt = parse_bse_date(item.get("DT_TM"))
    headline = item.get("HEADLINE") or ""
    newssub = item.get("NEWSSUB") or ""
    category = item.get("CATEGORYNAME") or ""
    content = f"{newssub}. {headline}".strip(". ")
    attachment = item.get("ATTACHMENTNAME")
    url = item.get("NSURL") or ""

    try:
        date_part = dt[:10] if dt else ""
        time_part = dt[11:] if dt and len(dt) > 10 else ""
        cursor.execute("""
            INSERT INTO announcements
            (company_id, exchange, category, headline, description,
             attachment_url, announcement_date, announcement_time, raw_data)
            VALUES (?, 'BSE', ?, ?, ?, ?, ?, ?, ?)
        """, (
            company_id, category, newssub or headline, content,
            url, date_part, time_part,
            json.dumps(item, default=str)[:5000]
        ))
        return True
    except Exception:
        return False


def import_nse_announcement(item, conn):
    """Import a single NSE announcement."""
    company_id, matched_by = match_company(item, "nse")
    if not company_id:
        return False

    cursor = conn.cursor()
    seq_id = item.get("seq_id", "")
    dt = parse_nse_date(item.get("sort_date") or item.get("an_dt"))
    desc = item.get("desc") or ""
    attchmnt_text = item.get("attchmntText") or ""
    sm_name = item.get("sm_name") or ""
    category = "General"
    attachment = item.get("attchmntFile")
    content = attchmnt_text or desc

    try:
        date_part = dt[:10] if dt else ""
        time_part = dt[11:] if dt and len(dt) > 10 else ""
        cursor.execute("""
            INSERT INTO announcements
            (company_id, exchange, category, headline, description,
             attachment_url, announcement_date, announcement_time, raw_data)
            VALUES (?, 'NSE', ?, ?, ?, ?, ?, ?, ?)
        """, (
            company_id, category, desc or sm_name, content,
            attachment or "", date_part, time_part,
            json.dumps(item, default=str)[:5000]
        ))
        return True
    except Exception:
        return False


def process_file(filepath, conn):
    """Process a single announcement JSON file."""
    try:
        data = json.loads(filepath.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0, 0

    if not isinstance(data, list):
        return 0, 0

    filename = filepath.name.lower()
    is_bse = "bse" in filename and "nse" not in filename
    is_nse = "nse" in filename and "sme" not in filename and "bse" not in filename

    # Handle combined file name pattern: announcements_2025-07-15.json (BSE only)
    if filename.startswith("announcements_") and not is_nse:
        is_bse = True

    imported = 0
    skipped = 0

    for item in data:
        if not isinstance(item, dict):
            continue
        if is_bse:
            if import_bse_announcement(item, conn):
                imported += 1
            else:
                skipped += 1
        elif is_nse:
            if import_nse_announcement(item, conn):
                imported += 1
            else:
                skipped += 1

    return imported, skipped


def main():
    print("=" * 60)
    print("STEP 2: Import Announcements from Cached JSON")
    print("=" * 60)

    conn = get_db()
    build_company_maps(conn)

    # Scan all date folders
    if not EXISTING_DATA_DIR.exists():
        print(f"  ERROR: Data dir not found: {EXISTING_DATA_DIR}")
        return

    date_dirs = sorted([d for d in EXISTING_DATA_DIR.iterdir() if d.is_dir()])
    print(f"  Date folders found: {len(date_dirs)}")

    total_imported = 0
    total_skipped = 0
    total_files = 0

    for i, date_dir in enumerate(date_dirs):
        json_files = list(date_dir.glob("*.json"))
        # Exclude filtered_json_output files
        json_files = [f for f in json_files if "filtered_json" not in f.name]

        for f in json_files:
            imported, skipped = process_file(f, conn)
            total_imported += imported
            total_skipped += skipped
            total_files += 1

        if (i + 1) % 50 == 0:
            conn.commit()
            print(f"  Processed {i+1}/{len(date_dirs)} dates: {total_imported} imported, {total_skipped} skipped")

    conn.commit()
    conn.close()

    print(f"\n{'=' * 60}")
    print(f"RESULTS:")
    print(f"  Files processed: {total_files}")
    print(f"  Announcements imported: {total_imported}")
    print(f"  Announcements skipped (no match): {total_skipped}")

    # Stats by company
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM announcements")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT company_id) FROM announcements")
    companies = cursor.fetchone()[0]
    cursor.execute("SELECT exchange, COUNT(*) FROM announcements GROUP BY exchange")
    by_source = cursor.fetchall()
    conn.close()

    print(f"\n  Total in DB: {total} announcements across {companies} companies")
    for src, cnt in by_source:
        print(f"    {src}: {cnt}")

    # Debug: show sample BSE SCRIP_CD vs DB bse_code
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT bse_code FROM companies WHERE bse_code IS NOT NULL LIMIT 5")
    sample_bse = [r[0] for r in cursor.fetchall()]
    print(f"\n  Sample DB bse_codes: {sample_bse}")
    conn.close()


if __name__ == "__main__":
    main()
