#!/usr/bin/env python3
"""Import ALL announcements from BSE/NSE cache into DB for our tracked companies."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from config import EXISTING_DATA_DIR, IMPORTANT_CATEGORIES
from data.storage.db import get_db


def build_company_maps(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT id, nse_symbol, bse_code, company_name FROM companies")
    companies = cursor.fetchall()

    nse_map = {}
    bse_map = {}
    name_map = {}
    for c in companies:
        if c['nse_symbol']:
            nse_map[c['nse_symbol'].upper()] = c['id']
        if c['bse_code']:
            bse_map[str(c['bse_code']).strip()] = c['id']
        if c['company_name']:
            name_map[c['company_name'].upper().strip()] = c['id']
            # Also map first 15 chars for fuzzy match
            short = c['company_name'].upper().strip()[:15]
            if short not in name_map:
                name_map[short] = c['id']
    return nse_map, bse_map, name_map


def match_company(raw, exchange, nse_map, bse_map, name_map):
    if exchange == "NSE":
        sym = (raw.get("symbol") or "").upper().strip()
        if sym in nse_map:
            return nse_map[sym]
        name = (raw.get("sm_name") or "").upper().strip()
        if name in name_map:
            return name_map[name]
        short = name[:15]
        if short in name_map:
            return name_map[short]
    else:  # BSE
        code = str(raw.get("SCRIP_CD") or "").strip()
        if code in bse_map:
            return bse_map[code]
        name = (raw.get("SLONGNAME") or "").upper().strip()
        if name in name_map:
            return name_map[name]
        short = name[:15]
        if short in name_map:
            return name_map[short]
    return None


def parse_bse(raw):
    dt = raw.get("DT_TM", "")
    return {
        "exchange": "BSE",
        "category": raw.get("SUBCATNAME", ""),
        "headline": raw.get("NEWSSUB", ""),
        "description": raw.get("HEADLINE", ""),
        "announcement_date": dt[:10] if dt else "",
        "announcement_time": dt[11:19] if len(dt) > 11 else "",
        "is_critical": raw.get("CRITICALNEWS", 0),
        "attachment_url": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{raw['ATTACHMENTNAME']}" if raw.get("ATTACHMENTNAME") else None,
        "raw_data": json.dumps(raw, default=str),
    }


def parse_nse(raw):
    sort_date = raw.get("sort_date", "")
    return {
        "exchange": "NSE",
        "category": raw.get("desc", ""),
        "headline": raw.get("attchmntText") or raw.get("desc", ""),
        "description": raw.get("attchmntText", ""),
        "announcement_date": sort_date[:10] if sort_date else "",
        "announcement_time": sort_date[11:19] if len(sort_date) > 11 else "",
        "is_critical": 0,
        "attachment_url": raw.get("attchmntFile"),
        "raw_data": json.dumps(raw, default=str),
    }


def import_all():
    conn = get_db()
    nse_map, bse_map, name_map = build_company_maps(conn)
    print(f"Company maps: {len(nse_map)} NSE, {len(bse_map)} BSE, {len(name_map)} name")

    cursor = conn.cursor()
    dates = sorted([
        d.name for d in EXISTING_DATA_DIR.iterdir()
        if d.is_dir() and d.name.startswith("20")
    ])
    print(f"Found {len(dates)} cached dates")

    total_imported = 0
    total_matched = 0
    total_skipped = 0

    for i, date_str in enumerate(dates):
        date_dir = EXISTING_DATA_DIR / date_str
        for json_file in date_dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue

            exchange = "BSE" if "Bse_" in json_file.name else "NSE"

            for raw in data:
                if not isinstance(raw, dict):
                    continue
                company_id = match_company(raw, exchange, nse_map, bse_map, name_map)
                if not company_id:
                    total_skipped += 1
                    continue

                total_matched += 1
                parsed = parse_bse(raw) if exchange == "BSE" else parse_nse(raw)

                try:
                    cursor.execute('''
                        INSERT OR IGNORE INTO announcements
                        (company_id, exchange, category, headline, description,
                         attachment_url, announcement_date, announcement_time,
                         is_critical, raw_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        company_id, parsed['exchange'], parsed['category'],
                        parsed['headline'], parsed['description'],
                        parsed['attachment_url'], parsed['announcement_date'],
                        parsed['announcement_time'], parsed['is_critical'],
                        parsed['raw_data'],
                    ))
                    if cursor.rowcount > 0:
                        total_imported += 1
                except Exception:
                    continue

        conn.commit()
        if (i + 1) % 30 == 0:
            print(f"  Progress: {i+1}/{len(dates)} dates, {total_imported} imported, {total_matched} matched, {total_skipped} skipped")

    cursor.execute("SELECT COUNT(*) FROM announcements")
    final_count = cursor.fetchone()[0]
    conn.close()

    print(f"\n=== Import Complete ===")
    print(f"Dates processed: {len(dates)}")
    print(f"Matched to companies: {total_matched}")
    print(f"New announcements imported: {total_imported}")
    print(f"Skipped (no company match): {total_skipped}")
    print(f"Total announcements in DB: {final_count}")


if __name__ == "__main__":
    import_all()
