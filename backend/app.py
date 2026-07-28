from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from data.storage.db import get_db, init_db, ensure_schema
from data.processors.profile_builder import build_company_profile
from config import SECTORS, IMPORTANT_CATEGORIES
from typing import Optional
import json

app = FastAPI(title="AI Equity Research API", version="3.0.0")

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
    else:
        ensure_schema()
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
    has_guidance: int = None,
    has_capex: int = None,
    has_orders: int = None,
    search: str = None,
    sort_by: str = "market_cap",
    sort_order: str = "desc",
    limit: int = Query(100, le=500),
    offset: int = 0,
):
    conn = get_db()
    cursor = conn.cursor()

    query = """
        SELECT c.*, cs.total_announcements, cs.important_announcements,
               cs.has_guidance, cs.guidance_text, cs.has_capex_news, cs.capex_text,
               cs.has_order_news, cs.order_text, cs.has_financial_results,
               cs.has_dividend_news, cs.dividend_text, cs.sentiment_positive,
               cs.sentiment_negative, cs.key_themes, cs.latest_announcement_date
        FROM companies c
        LEFT JOIN company_summary cs ON c.id = cs.company_id
        WHERE c.is_active = 1
    """
    params = []

    if sector:
        query += " AND c.sector = ?"
        params.append(sector)
    if min_mcap:
        query += " AND c.market_cap >= ?"
        params.append(min_mcap)
    if max_mcap:
        query += " AND c.market_cap <= ?"
        params.append(max_mcap)
    if min_pe:
        query += " AND c.pe_ratio >= ?"
        params.append(min_pe)
    if max_pe:
        query += " AND c.pe_ratio <= ?"
        params.append(max_pe)
    if min_roe:
        query += " AND c.roe >= ?"
        params.append(min_roe)
    if has_guidance is not None:
        query += " AND cs.has_guidance = ?"
        params.append(has_guidance)
    if has_capex is not None:
        query += " AND cs.has_capex_news = ?"
        params.append(has_capex)
    if has_orders is not None:
        query += " AND cs.has_order_news = ?"
        params.append(has_orders)
    if search:
        query += " AND (c.company_name LIKE ? OR c.nse_symbol LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    count_query = "SELECT COUNT(*) FROM (" + query + ")"
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    valid_sorts = ['market_cap', 'pe_ratio', 'roe', 'company_name', 'nse_symbol', 'total_announcements']
    if sort_by in valid_sorts:
        table = 'cs' if sort_by == 'total_announcements' else 'c'
        order = "DESC" if sort_order.lower() == "desc" else "ASC"
        query += f" ORDER BY {table}.{sort_by} {order}"

    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    companies = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"companies": companies, "total": total, "limit": limit, "offset": offset}


@app.get("/api/companies/{company_id}")
def get_company(company_id: int):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
    company = cursor.fetchone()
    if not company:
        conn.close()
        raise HTTPException(404, "Company not found")

    cursor.execute("SELECT * FROM company_summary WHERE company_id = ?", (company_id,))
    summary = cursor.fetchone()

    cursor.execute("""
        SELECT * FROM announcement_insights WHERE company_id = ?
        ORDER BY extracted_at DESC LIMIT 20
    """, (company_id,))
    recent_insights = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT COUNT(*) as total, 
               SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) as positive,
               SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) as negative
        FROM announcement_insights WHERE company_id = ?
    """, (company_id,))
    sentiment_stats = dict(cursor.fetchone())

    conn.close()

    return {
        "company": dict(company),
        "summary": dict(summary) if summary else None,
        "recent_insights": recent_insights,
        "sentiment_stats": sentiment_stats,
    }


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


@app.get("/api/companies/{company_id}/insights")
def get_company_insights(
    company_id: int,
    insight_type: str = None,
    limit: int = Query(50, le=200),
):
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM announcement_insights WHERE company_id = ?"
    params = [company_id]

    if insight_type:
        query += " AND insight_type = ?"
        params.append(insight_type)

    query += " ORDER BY extracted_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    insights = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"insights": insights, "count": len(insights)}


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
        placeholders = ",".join("?" * len(IMPORTANT_CATEGORIES))
        query += f" AND a.category IN ({placeholders})"
        params.extend(IMPORTANT_CATEGORIES)

    query += " ORDER BY a.announcement_date DESC, a.announcement_time DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    announcements = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"announcements": announcements, "count": len(announcements)}


@app.get("/api/insights/filter")
def filter_by_insights(
    insight_type: str = None,
    sentiment: str = None,
    sector: str = None,
    min_mcap: float = None,
    max_mcap: float = None,
    limit: int = Query(100, le=500),
):
    conn = get_db()
    cursor = conn.cursor()

    query = """
        SELECT DISTINCT c.id, c.nse_symbol, c.company_name, c.sector, c.market_cap,
               c.current_price, c.pe_ratio, cs.has_guidance, cs.has_capex_news,
               cs.has_order_news, cs.guidance_text, cs.capex_text, cs.order_text,
               i.insight_type, i.summary as latest_insight, i.sentiment
        FROM companies c
        JOIN announcement_insights i ON c.id = i.company_id
        LEFT JOIN company_summary cs ON c.id = cs.company_id
        WHERE 1=1
    """
    params = []

    if insight_type:
        query += " AND i.insight_type = ?"
        params.append(insight_type)
    if sentiment:
        query += " AND i.sentiment = ?"
        params.append(sentiment)
    if sector:
        query += " AND c.sector = ?"
        params.append(sector)
    if min_mcap:
        query += " AND c.market_cap >= ?"
        params.append(min_mcap)
    if max_mcap:
        query += " AND c.market_cap <= ?"
        params.append(max_mcap)

    query += " ORDER BY c.market_cap DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    results = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"results": results, "count": len(results)}


@app.get("/api/dashboard")
def get_dashboard():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as cnt FROM companies")
    total_companies = cursor.fetchone()['cnt']

    cursor.execute("SELECT COUNT(*) as cnt FROM announcements")
    total_announcements = cursor.fetchone()['cnt']

    cursor.execute("SELECT COUNT(*) as cnt FROM announcement_insights")
    total_insights = cursor.fetchone()['cnt']

    cursor.execute("SELECT sector, COUNT(*) as cnt FROM companies GROUP BY sector ORDER BY cnt DESC")
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
        SELECT insight_type, COUNT(*) as cnt
        FROM announcement_insights
        GROUP BY insight_type
        ORDER BY cnt DESC
    """)
    insight_types = {r['insight_type']: r['cnt'] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT
            SUM(has_guidance) as with_guidance,
            SUM(has_capex_news) as with_capex,
            SUM(has_order_news) as with_orders,
            SUM(has_financial_results) as with_financials
        FROM company_summary
    """)
    summary_stats = dict(cursor.fetchone())

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
        "total_insights": total_insights,
        "sector_distribution": sector_distribution,
        "top_categories": top_categories,
        "insight_types": insight_types,
        "summary_stats": summary_stats,
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
