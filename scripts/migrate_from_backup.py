#!/usr/bin/env python3
import sqlite3
from pathlib import Path

BACKUP = Path("/home/ubuntu/FinEng/AI_Equity_Research_Analyst_Aug_2026/backend/data/equity_research.db.bak")
NEW_DB = Path("/home/ubuntu/FinEng/AI_Equity_Research_Analyst_Aug_2026/backend/data/equity_research.db")

conn_old = sqlite3.connect(str(BACKUP))
conn_old.row_factory = sqlite3.Row
conn_new = sqlite3.connect(str(NEW_DB))
conn_new.row_factory = sqlite3.Row

old_cursor = conn_old.cursor()
new_cursor = conn_new.cursor()

old_cursor.execute("SELECT * FROM companies")
rows = old_cursor.fetchall()

count = 0
for row in rows:
    try:
        new_cursor.execute('''
            INSERT OR REPLACE INTO companies 
            (bse_code, nse_symbol, company_name, sector, industry, market_cap, pe_ratio,
             pb_ratio, dividend_yield, eps, book_value, debt_to_equity, roe, roce,
             current_price, sma_50, sma_200, beta, week_52_high, week_52_low)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            row['bse_code'], row['nse_symbol'], row['company_name'],
            row['sector'], row['industry'], row['market_cap'],
            row['pe_ratio'], row['pb_ratio'], row['dividend_yield'],
            row['eps'], row['book_value'], row['debt_to_equity'],
            row['roe'], row['roce'], row['current_price'],
            row['sma_50'], row['sma_200'], row['beta'],
            row['week_52_high'], row['week_52_low']
        ))
        count += 1
    except Exception as e:
        print(f"Error: {row['nse_symbol']}: {e}")

conn_new.commit()

# Also migrate announcements (need to remap company_id using nse_symbol)
old_cursor.execute("SELECT id, nse_symbol FROM companies")
id_map = {row['id']: row['nse_symbol'] for row in old_cursor.fetchall()}

new_cursor.execute("SELECT id, nse_symbol FROM companies")
new_id_map = {row['nse_symbol']: row['id'] for row in new_cursor.fetchall()}

old_cursor.execute("SELECT * FROM announcements")
anns = old_cursor.fetchall()
ann_count = 0
for a in anns:
    new_company_id = new_id_map.get(id_map.get(a['company_id']))
    if new_company_id:
        try:
            new_cursor.execute('''
                INSERT OR IGNORE INTO announcements
                (company_id, exchange, category, subcategory, headline, description,
                 attachment_url, announcement_date, announcement_time, is_critical, sentiment_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                new_company_id, a['exchange'], a['category'],
                a['subcategory'] if 'subcategory' in a.keys() else None,
                a['headline'], a['description'], a['attachment_url'],
                a['announcement_date'], a['announcement_time'],
                a['is_critical'], a['sentiment_score'] if 'sentiment_score' in a.keys() else None
            ))
            if new_cursor.rowcount > 0:
                ann_count += 1
        except Exception as e:
            continue

# Migrate price_history
old_cursor.execute("SELECT * FROM price_history")
prices = old_cursor.fetchall()
price_count = 0
for p in prices:
    new_company_id = new_id_map.get(id_map.get(p['company_id']))
    if new_company_id:
        try:
            new_cursor.execute('''
                INSERT OR IGNORE INTO price_history
                (company_id, trade_date, open_price, high_price, low_price, close_price, volume, delivery_pct, turnover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                new_company_id, p['trade_date'], p['open_price'], p['high_price'],
                p['low_price'], p['close_price'], p['volume'],
                p['delivery_pct'] if 'delivery_pct' in p.keys() else None,
                p['turnover'] if 'turnover' in p.keys() else None
            ))
            if new_cursor.rowcount > 0:
                price_count += 1
        except:
            continue

conn_new.commit()
conn_old.close()
conn_new.close()

print(f"Migrated {count} companies, {ann_count} announcements, {price_count} price records from backup")
