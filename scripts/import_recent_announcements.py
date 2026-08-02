#!/usr/bin/env python3
"""Incrementally import only the most recent N days of announcements from cache.
Lightweight enough to run alongside the every-minute download cron."""
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from config import EXISTING_DATA_DIR
from data.storage.db import get_db
from import_all_announcements import build_company_maps, match_company, parse_bse, parse_nse

RECENT_DAYS = 3


def import_recent(days=RECENT_DAYS):
    conn = get_db()
    nse_map, bse_map, name_map = build_company_maps(conn)
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    dates = sorted([
        d.name for d in EXISTING_DATA_DIR.iterdir()
        if d.is_dir() and d.name.startswith("20") and d.name >= cutoff
    ])
    print(f"Processing {len(dates)} dates since {cutoff}")

    total_imported = 0
    total_matched = 0
    total_skipped = 0

    for date_str in dates:
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

    conn.close()
    print(f"Recent import: {total_imported} new, {total_matched} matched, {total_skipped} skipped")


if __name__ == "__main__":
    import_recent()
