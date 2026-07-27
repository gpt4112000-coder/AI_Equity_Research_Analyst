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
import json

app = FastAPI(title="AI Equity Research API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/sectors")
def get_sectors():
    return {"sectors": list(SECTORS.keys())}


@app.get("/api/sectors/{sector}/stocks")
def get_sector_stocks(sector: str):
    if sector not in SECTORS:
        raise HTTPException(404, f"Sector '{sector}' not found")
    return {"sector": sector, "stocks": SECTORS[sector]}


@app.get("/api/companies")
def list_companies(sector: str = None):
    conn = get_db()
    cursor = conn.cursor()
    if sector:
        cursor.execute("SELECT * FROM companies WHERE sector = ?", (sector,))
    else:
        cursor.execute("SELECT * FROM companies")
    companies = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"companies": companies, "count": len(companies)}


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
    if not row or not row["nse_symbol"]:
        raise HTTPException(404, "Company not found or no NSE symbol")
    return analyze_fundamentals(row["nse_symbol"])


@app.get("/api/announcements")
def get_announcements(
    date: str = None,
    exchange: str = None,
    category: str = None,
    important_only: bool = False,
    limit: int = Query(100, le=500),
):
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT a.*, c.company_name, c.nse_symbol, c.bse_code FROM announcements a JOIN companies c ON a.company_id = c.id WHERE 1=1"
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


@app.get("/api/cache/{date}/stats")
def get_cache_stats(date: str):
    announcements = load_announcements_from_cache(date)
    return get_announcement_stats(announcements)


@app.post("/api/companies/{company_id}/import-announcements")
def import_announcements(company_id: int, date: str):
    from data.storage.db import get_db
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT bse_code, nse_symbol FROM companies WHERE id = ?", (company_id,))
    company = cursor.fetchone()
    if not company:
        raise HTTPException(404, "Company not found")

    announcements = load_announcements_from_cache(date)
    imported = 0

    for ann in announcements:
        matched = False
        if company["bse_code"] and ann.get("bse_code") == company["bse_code"]:
            matched = True
        if company["nse_symbol"] and ann.get("nse_symbol") == company["nse_symbol"]:
            matched = True

        if matched:
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
                cursor.execute("""
                    INSERT OR IGNORE INTO companies (nse_symbol, company_name, sector, industry, market_cap, pe_ratio, pb_ratio, dividend_yield, eps, debt_to_equity, roe)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol, info.get("company_name", symbol), sec_name,
                    info.get("industry"), info.get("market_cap"),
                    info.get("pe_ratio"), info.get("pb_ratio"),
                    info.get("dividend_yield"), info.get("eps"),
                    info.get("debt_to_equity"), info.get("roe"),
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
    total_companies = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM announcements")
    total_announcements = cursor.fetchone()["cnt"]

    cursor.execute("SELECT sector, COUNT(*) as cnt FROM companies GROUP BY sector")
    sector_distribution = {r["sector"]: r["cnt"] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT category, COUNT(*) as cnt 
        FROM announcements 
        GROUP BY category 
        ORDER BY cnt DESC 
        LIMIT 10
    """)
    top_categories = {r["category"]: r["cnt"] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT a.announcement_date, COUNT(*) as cnt 
        FROM announcements a 
        GROUP BY a.announcement_date 
        ORDER BY a.announcement_date DESC 
        LIMIT 30
    """)
    daily_counts = {r["announcement_date"]: r["cnt"] for r in cursor.fetchall()}

    conn.close()

    return {
        "total_companies": total_companies,
        "total_announcements": total_announcements,
        "sector_distribution": sector_distribution,
        "top_categories": top_categories,
        "daily_announcement_counts": daily_counts,
    }
