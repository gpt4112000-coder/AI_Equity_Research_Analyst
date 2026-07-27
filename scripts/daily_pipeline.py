import sys
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from config import EXISTING_DATA_DIR, SECTORS
from data.storage.db import get_db, init_db
from data.collectors.yfinance_data import get_stock_info, get_price_history
from data.processors.announcement_parser import (
    load_announcements_from_cache, filter_important_announcements, group_by_company
)


def run_daily_pipeline():
    print(f"[{datetime.now()}] Starting daily pipeline...")
    init_db()

    india_tz = ZoneInfo("Asia/Kolkata")
    now = datetime.now(india_tz)
    date_str = now.strftime("%Y-%m-%d")

    print(f"[{date_str}] Processing announcements...")
    announcements = load_announcements_from_cache(date_str)
    print(f"  Found {len(announcements)} total announcements")

    important = filter_important_announcements(announcements)
    print(f"  {len(important)} important announcements")

    grouped = group_by_company(important)
    print(f"  Across {len(grouped)} companies")

    conn = get_db()
    cursor = conn.cursor()

    for key, comp_data in grouped.items():
        symbol = comp_data.get("nse_symbol")
        bse_code = comp_data.get("bse_code")
        company_name = comp_data.get("company_name")

        if symbol:
            cursor.execute("SELECT id FROM companies WHERE nse_symbol = ?", (symbol,))
        elif bse_code:
            cursor.execute("SELECT id FROM companies WHERE bse_code = ?", (bse_code,))
        else:
            continue

        row = cursor.fetchone()
        if row:
            company_id = row["id"]
        else:
            try:
                info = get_stock_info(symbol) if symbol else {}
                cursor.execute("""
                    INSERT INTO companies (nse_symbol, bse_code, company_name, sector, market_cap, pe_ratio)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (symbol, bse_code, company_name, None, info.get("market_cap"), info.get("pe_ratio")))
                company_id = cursor.lastrowid
                print(f"  Added new company: {company_name} ({symbol})")
            except Exception as e:
                print(f"  Error adding {company_name}: {e}")
                continue

        for ann in comp_data["announcements"]:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO announcements
                    (company_id, exchange, category, headline, description, announcement_date, announcement_time, is_critical, attachment_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    company_id, ann["exchange"], ann["category"],
                    ann.get("headline"), ann.get("description"),
                    ann["announcement_date"], ann.get("announcement_time"),
                    ann.get("is_critical", 0), ann.get("attachment_url"),
                ))
            except Exception:
                continue

    conn.commit()
    print(f"  Database updated.")

    print(f"\n[{date_str}] Fetching price data for tracked companies...")
    cursor.execute("SELECT nse_symbol FROM companies WHERE nse_symbol IS NOT NULL")
    symbols = [r["nse_symbol"] for r in cursor.fetchall()]

    for symbol in symbols[:20]:
        try:
            prices = get_price_history(symbol, period="5d")
            if prices:
                cursor.execute("SELECT id FROM companies WHERE nse_symbol = ?", (symbol,))
                company_row = cursor.fetchone()
                if company_row:
                    for p in prices:
                        cursor.execute("""
                            INSERT OR IGNORE INTO price_history (company_id, trade_date, open_price, high_price, low_price, close_price, volume)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (company_row["id"], p["trade_date"], p["open_price"], p["high_price"], p["low_price"], p["close_price"], p["volume"]))
        except Exception as e:
            print(f"  Error fetching price for {symbol}: {e}")
            continue

    conn.commit()
    conn.close()

    print(f"\n[{datetime.now()}] Pipeline complete.")


if __name__ == "__main__":
    run_daily_pipeline()
