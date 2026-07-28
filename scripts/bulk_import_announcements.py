import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from config import EXISTING_DATA_DIR, IMPORTANT_CATEGORIES
from data.storage.db import get_db
from data.processors.announcement_parser import (
    load_announcements_from_cache, filter_important_announcements, load_all_cached_dates
)


def bulk_import_announcements():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT id, nse_symbol, company_name FROM companies')
    companies = cursor.fetchall()

    nse_map = {}
    name_map = {}
    for c in companies:
        if c['nse_symbol']:
            nse_map[c['nse_symbol'].upper()] = c['id']
        if c['company_name']:
            name_map[c['company_name'].upper()] = c['id']

    all_dates = load_all_cached_dates()
    print(f"Found {len(all_dates)} cached dates to import")

    total_imported = 0
    for i, date_str in enumerate(all_dates):
        announcements = load_announcements_from_cache(date_str)
        important = filter_important_announcements(announcements)

        date_imported = 0
        for ann in important:
            company_id = None
            nse_sym = ann.get('nse_symbol', '').upper()
            comp_name = ann.get('company_name', '').upper().strip()

            if nse_sym in nse_map:
                company_id = nse_map[nse_sym]
            elif comp_name in name_map:
                company_id = name_map[comp_name]
            else:
                for key, cid in name_map.items():
                    if key and comp_name and len(key) > 5 and (key[:8] in comp_name or comp_name[:8] in key):
                        company_id = cid
                        break

            if company_id:
                try:
                    cursor.execute('''
                        INSERT OR IGNORE INTO announcements
                        (company_id, exchange, category, headline, description, 
                         announcement_date, announcement_time, is_critical, attachment_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        company_id, ann['exchange'], ann['category'],
                        ann.get('headline'), ann.get('description'),
                        ann['announcement_date'], ann.get('announcement_time'),
                        ann.get('is_critical', 0), ann.get('attachment_url'),
                    ))
                    if cursor.rowcount > 0:
                        date_imported += 1
                except Exception:
                    continue

        conn.commit()
        total_imported += date_imported
        if (i + 1) % 10 == 0:
            print(f"  Progress: {i+1}/{len(all_dates)} dates, {total_imported} total imported")

    cursor.execute('SELECT COUNT(*) FROM announcements')
    final_count = cursor.fetchone()[0]
    conn.close()

    print(f"\n=== Import Complete ===")
    print(f"Dates processed: {len(all_dates)}")
    print(f"New announcements imported: {total_imported}")
    print(f"Total announcements in DB: {final_count}")


if __name__ == "__main__":
    bulk_import_announcements()
