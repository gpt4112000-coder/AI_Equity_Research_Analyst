from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from data.storage.db import get_db, init_db
from data.processors.announcement_parser import (
    load_announcements_from_cache, filter_important_announcements,
    group_by_company, get_announcement_stats, load_all_cached_dates
)
from data.processors.profile_builder import build_company_profile
from analysis.fundamental import analyze_fundamentals
from config import SECTORS
from typing import Optional, List
import json

app = FastAPI(title="AI Equity Research API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    from data.storage.db import get_db as _get_db
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='companies'")
    if not cursor.fetchone():
        init_db()
    conn.close()


@app.get("/api/sectors")
def get_sectors():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT sector, COUNT(*) as cnt FROM companies GROUP BY sector ORDER BY cnt DESC")
    sectors = {r['sector']: r['cnt'] for r in cursor.fetchall()}
    conn.close()
    return {"sectors": sectors}


@app.get("/api/companies")
def list_companies(
    sector: str = None,
    min_mcap: float = None,
    max_mcap: float = None,
    min_pe: float = None,
    max_pe: float = None,
    min_roe: float = None,
    max_roe: float = None,
    search: str = None,
    sort_by: str = "market_cap",
    sort_order: str = "desc",
    limit: int = Query(100, le=500),
    offset: int = 0,
):
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM companies WHERE 1=1"
    params = []

    if sector:
        query += " AND sector = ?"
        params.append(sector)
    if min_mcap:
        query += " AND market_cap >= ?"
        params.append(min_mcap)
    if max_mcap:
        query += " AND market_cap <= ?"
        params.append(max_mcap)
    if min_pe:
        query += " AND pe_ratio >= ?"
        params.append(min_pe)
    if max_pe:
        query += " AND pe_ratio <= ?"
        params.append(max_pe)
    if min_roe:
        query += " AND roe >= ?"
        params.append(min_roe)
    if max_roe:
        query += " AND roe <= ?"
        params.append(max_roe)
    if search:
        query += " AND (company_name LIKE ? OR nse_symbol LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    count_query = query.replace("SELECT *", "SELECT COUNT(*)")
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    valid_sorts = ['market_cap', 'pe_ratio', 'roe', 'company_name', 'nse_symbol']
    if sort_by in valid_sorts:
        order = "DESC" if sort_order.lower() == "desc" else "ASC"
        query += f" ORDER BY {sort_by} {order}"

    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    companies = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"companies": companies, "total": total, "limit": limit, "offset": offset}


@app.get("/api/companies/{company_id}")
def get_company(company_id: int):
    profile = build_company_profile(company_id)
    if not profile.get("company"):
        raise HTTPException(404, "Company not found")
    return profile


@app.get("/api/companies/{company_id}/analysis")
def get_company_analysis(company_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT nse_symbol FROM companies WHERE id = ?", (company_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row['nse_symbol']:
        raise HTTPException(404, "Company not found or no NSE symbol")
    return analyze_fundamentals(row['nse_symbol'])


@app.get("/api/companies/{company_id}/announcements")
def get_company_announcements(
    company_id: int,
    category: str = None,
    start_date: str = None,
    end_date: str = None,
    limit: int = Query(50, le=200),
):
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM announcements WHERE company_id = ?"
    params = [company_id]

    if category:
        query += " AND category = ?"
        params.append(category)
    if start_date:
        query += " AND announcement_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND announcement_date <= ?"
        params.append(end_date)

    query += " ORDER BY announcement_date DESC, announcement_time DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    announcements = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"announcements": announcements, "count": len(announcements)}


@app.get("/api/companies/{company_id}/financials")
def get_company_financials(company_id: int, period: str = None):
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM financials WHERE company_id = ?"
    params = [company_id]

    if period:
        query += " AND period = ?"
        params.append(period)

    query += " ORDER BY report_date DESC LIMIT 20"

    cursor.execute(query, params)
    financials = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"financials": financials, "count": len(financials)}


@app.get("/api/companies/{company_id}/technicals")
def get_company_technicals(company_id: int):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM technical_indicators 
        WHERE company_id = ? 
        ORDER BY indicator_date DESC 
        LIMIT 1
    """, (company_id,))
    tech = cursor.fetchone()

    cursor.execute("""
        SELECT * FROM price_history 
        WHERE company_id = ? 
        ORDER BY trade_date DESC 
        LIMIT 30
    """, (company_id,))
    prices = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        "indicators": dict(tech) if tech else None,
        "prices": prices
    }


@app.get("/api/announcements")
def get_announcements(
    date: str = None,
    exchange: str = None,
    category: str = None,
    company_id: int = None,
    important_only: bool = False,
    limit: int = Query(100, le=500),
):
    conn = get_db()
    cursor = conn.cursor()

    query = """
        SELECT a.*, c.company_name, c.nse_symbol 
        FROM announcements a 
        JOIN companies c ON a.company_id = c.id 
        WHERE 1=1
    """
    params = []

    if date:
        query += " AND a.announcement_date = ?"
        params.append(date)
    if exchange:
        query += " AND a.exchange = ?"
        params.append(exchange)
    if category:
        query += " AND a.category = ?"
        params.append(category)
    if company_id:
        query += " AND a.company_id = ?"
        params.append(company_id)
    if important_only:
        from config import IMPORTANT_CATEGORIES
        placeholders = ",".join("?" * len(IMPORTANT_CATEGORIES))
        query += f" AND a.category IN ({placeholders})"
        params.extend(IMPORTANT_CATEGORIES)

    query += " ORDER BY a.announcement_date DESC, a.announcement_time DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    announcements = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"announcements": announcements, "count": len(announcements)}


@app.get("/api/cache/dates")
def get_cached_dates():
    dates = load_all_cached_dates()
    return {"dates": dates}


@app.get("/api/cache/{date}/announcements")
def get_cached_announcements(date: str, important_only: bool = False):
    announcements = load_announcements_from_cache(date)
    if important_only:
        announcements = filter_important_announcements(announcements)
    stats = get_announcement_stats(announcements)
    grouped = group_by_company(announcements)
    return {
        "date": date,
        "stats": stats,
        "by_company": grouped,
        "total": len(announcements),
    }


@app.post("/api/companies/{company_id}/import-announcements")
def import_announcements(company_id: int, date: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT bse_code, nse_symbol, company_name FROM companies WHERE id = ?", (company_id,))
    company = cursor.fetchone()
    if not company:
        raise HTTPException(404, "Company not found")

    announcements = load_announcements_from_cache(date)
    imported = 0

    for ann in announcements:
        matched = False
        if company['bse_code'] and ann.get('bse_code') == company['bse_code']:
            matched = True
        if company['nse_symbol'] and ann.get('nse_symbol') == company['nse_symbol']:
            matched = True
        if company['company_name'] and ann.get('company_name') == company['company_name']:
            matched = True

        if matched:
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
                imported += 1
            except Exception:
                continue

    conn.commit()
    conn.close()
    return {"imported": imported, "date": date}


@app.post("/api/seed-companies")
def seed_companies(sector: str = None):
    from data.collectors.yfinance_data import get_stock_info
    conn = get_db()
    cursor = conn.cursor()

    sectors_to_seed = {sector: SECTORS[sector]} if sector else SECTORS
    added = 0

    for sec_name, symbols in sectors_to_seed.items():
        for symbol in symbols:
            try:
                info = get_stock_info(symbol)
                cursor.execute('''
                    INSERT OR REPLACE INTO companies 
                    (nse_symbol, company_name, sector, industry, market_cap, pe_ratio, 
                     pb_ratio, dividend_yield, eps, debt_to_equity, roe, roce,
                     current_price, sma_50, sma_200, beta, week_52_high, week_52_low)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol, info.get("company_name", symbol), sec_name,
                    info.get("industry"), info.get("market_cap"),
                    info.get("pe_ratio"), info.get("pb_ratio"),
                    info.get("dividend_yield"), info.get("eps"),
                    info.get("debt_to_equity"), info.get("roe"),
                    info.get("roce"), info.get("current_price"),
                    info.get("sma_50"), info.get("sma_200"),
                    info.get("beta"), info.get("52w_high"), info.get("52w_low"),
                ))
                added += 1
            except Exception as e:
                print(f"Error seeding {symbol}: {e}")
                continue

    conn.commit()
    conn.close()
    return {"added": added, "sectors": list(sectors_to_seed.keys())}


@app.get("/api/dashboard")
def get_dashboard():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as cnt FROM companies")
    total_companies = cursor.fetchone()['cnt']

    cursor.execute("SELECT COUNT(*) as cnt FROM announcements")
    total_announcements = cursor.fetchone()['cnt']

    cursor.execute("SELECT sector, COUNT(*) as cnt FROM companies GROUP BY sector")
    sector_distribution = {r['sector']: r['cnt'] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT category, COUNT(*) as cnt 
        FROM announcements 
        GROUP BY category 
        ORDER BY cnt DESC 
        LIMIT 15
    """)
    top_categories = {r['category']: r['cnt'] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT a.announcement_date, COUNT(*) as cnt 
        FROM announcements a 
        GROUP BY a.announcement_date 
        ORDER BY a.announcement_date DESC 
        LIMIT 30
    """)
    daily_counts = {r['announcement_date']: r['cnt'] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT 
            ROUND(AVG(market_cap)/1e7, 0) as avg_mcap,
            ROUND(AVG(pe_ratio), 2) as avg_pe,
            ROUND(AVG(roe)*100, 2) as avg_roe
        FROM companies WHERE market_cap IS NOT NULL
    """)
    averages = dict(cursor.fetchone())

    conn.close()

    return {
        "total_companies": total_companies,
        "total_announcements": total_announcements,
        "sector_distribution": sector_distribution,
        "top_categories": top_categories,
        "daily_announcement_counts": daily_counts,
        "averages": averages,
    }


@app.get("/api/watchlist")
def get_watchlist():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT w.*, c.nse_symbol, c.company_name, c.market_cap, c.current_price
        FROM watchlist w
        JOIN companies c ON w.company_id = c.id
        ORDER BY w.added_date DESC
    """)
    watchlist = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"watchlist": watchlist, "count": len(watchlist)}


@app.post("/api/watchlist/{company_id}")
def add_to_watchlist(company_id: int, notes: str = ""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO watchlist (company_id, notes)
        VALUES (?, ?)
    """, (company_id, notes))
    conn.commit()
    conn.close()
    return {"status": "added"}


@app.delete("/api/watchlist/{company_id}")
def remove_from_watchlist(company_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE company_id = ?", (company_id,))
    conn.commit()
    conn.close()
    return {"status": "removed"}
