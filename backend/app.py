from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from data.storage.db import get_db, init_db
from typing import Optional
from pathlib import Path
import json
import httpx

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="AI Equity Research API", version="4.0.0")

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
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT sector, COUNT(*) as cnt FROM companies WHERE is_active=1 GROUP BY sector ORDER BY cnt DESC")
    sectors = {r['sector']: r['cnt'] for r in cursor.fetchall()}
    conn.close()
    return {"sectors": sectors}


@app.get("/api/companies")
def list_companies(
    sector: str = None,
    min_mcap: float = None,
    max_mcap: float = None,
    has_guidance: int = None,
    has_capex: int = None,
    has_orders: int = None,
    has_dividend: int = None,
    has_financials: int = None,
    insight_type: str = None,
    search: str = None,
    sort_by: str = "market_cap",
    sort_order: str = "desc",
    limit: int = Query(50, le=500),
    offset: int = 0,
):
    conn = get_db()
    cursor = conn.cursor()

    query = """
        SELECT c.id, c.bse_code, c.nse_symbol, c.company_name, c.sector, c.industry,
               c.market_cap, c.group_name, c.isin,
               cs.total_announcements, cs.has_guidance, cs.guidance_text,
               cs.has_capex_news, cs.capex_text, cs.has_order_news, cs.order_text,
               cs.has_financial_results, cs.financial_results_text,
               cs.has_dividend_news, cs.dividend_text,
               cs.sentiment_positive, cs.sentiment_negative, cs.sentiment_neutral,
               cs.key_themes, cs.latest_announcement_date
        FROM companies c
        LEFT JOIN company_summary cs ON c.id = cs.company_id
        WHERE c.is_active = 1
    """
    params = []

    if sector:
        query += " AND c.sector = ?"
        params.append(sector)
    if min_mcap is not None:
        query += " AND c.market_cap >= ?"
        params.append(min_mcap * 1e7)
    if max_mcap is not None:
        query += " AND c.market_cap <= ?"
        params.append(max_mcap * 1e7)
    if has_guidance is not None:
        query += " AND cs.has_guidance = ?"
        params.append(has_guidance)
    if has_capex is not None:
        query += " AND cs.has_capex_news = ?"
        params.append(has_capex)
    if has_orders is not None:
        query += " AND cs.has_order_news = ?"
        params.append(has_orders)
    if has_dividend is not None:
        query += " AND cs.has_dividend_news = ?"
        params.append(has_dividend)
    if has_financials is not None:
        query += " AND cs.has_financial_results = ?"
        params.append(has_financials)
    if insight_type:
        query += " AND cs.key_themes LIKE ?"
        params.append(f'%"{insight_type}"%')
    if search:
        query += " AND (c.company_name LIKE ? OR c.nse_symbol LIKE ? OR c.bse_code LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    # Sorting
    sort_map = {
        "market_cap": "c.market_cap",
        "announcements": "cs.total_announcements",
        "company": "c.company_name",
        "latest": "cs.latest_announcement_date",
    }
    sort_col = sort_map.get(sort_by, "c.market_cap")
    sort_dir = "ASC" if sort_order.lower() == "asc" else "DESC"
    query += f" ORDER BY {sort_col} {sort_dir} NULLS LAST"

    # Count
    count_query = query.replace(
        "SELECT c.id, c.bse_code, c.nse_symbol, c.company_name, c.sector, c.industry,\n               c.market_cap, c.group_name, c.isin,\n               cs.total_announcements, cs.has_guidance, cs.guidance_text,\n               cs.has_capex_news, cs.capex_text, cs.has_order_news, cs.order_text,\n               cs.has_financial_results, cs.financial_results_text,\n               cs.has_dividend_news, cs.dividend_text,\n               cs.sentiment_positive, cs.sentiment_negative, cs.sentiment_neutral,\n               cs.key_themes, cs.latest_announcement_date",
        "SELECT COUNT(*)"
    )
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    companies = []
    for r in rows:
        mcap_cr = r['market_cap'] / 1e7 if r['market_cap'] else 0
        companies.append({
            "id": r['id'],
            "bse_code": r['bse_code'],
            "nse_symbol": r['nse_symbol'],
            "company_name": r['company_name'],
            "sector": r['sector'],
            "industry": r['industry'],
            "market_cap_cr": mcap_cr,
            "group_name": r['group_name'],
            "isin": r['isin'],
            "total_announcements": r['total_announcements'] or 0,
            "has_guidance": r['has_guidance'] or 0,
            "guidance_text": r['guidance_text'],
            "has_capex": r['has_capex_news'] or 0,
            "capex_text": r['capex_text'],
            "has_orders": r['has_order_news'] or 0,
            "order_text": r['order_text'],
            "has_financials": r['has_financial_results'] or 0,
            "financial_text": r['financial_results_text'],
            "has_dividend": r['has_dividend_news'] or 0,
            "dividend_text": r['dividend_text'],
            "sentiment_positive": r['sentiment_positive'] or 0,
            "sentiment_negative": r['sentiment_negative'] or 0,
            "sentiment_neutral": r['sentiment_neutral'] or 0,
            "key_themes": json.loads(r['key_themes']) if r['key_themes'] else [],
            "latest_announcement": r['latest_announcement_date'],
        })

    return {"companies": companies, "total": total, "limit": limit, "offset": offset}


@app.get("/api/companies/{company_id}")
def get_company(company_id: int):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.*, cs.total_announcements, cs.has_guidance, cs.guidance_text,
               cs.has_capex_news, cs.capex_text, cs.has_order_news, cs.order_text,
               cs.has_financial_results, cs.financial_results_text,
               cs.has_dividend_news, cs.dividend_text,
               cs.sentiment_positive, cs.sentiment_negative, cs.sentiment_neutral,
               cs.key_themes, cs.latest_announcement_date
        FROM companies c
        LEFT JOIN company_summary cs ON c.id = cs.company_id
        WHERE c.id = ?
    """, (company_id,))
    r = cursor.fetchone()
    if not r:
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")

    company = {
        "id": r['id'],
        "bse_code": r['bse_code'],
        "nse_symbol": r['nse_symbol'],
        "company_name": r['company_name'],
        "sector": r['sector'],
        "industry": r['industry'],
        "market_cap_cr": r['market_cap'] / 1e7 if r['market_cap'] else 0,
        "group_name": r['group_name'],
        "isin": r['isin'],
        "total_announcements": r['total_announcements'] or 0,
        "has_guidance": r['has_guidance'] or 0,
        "guidance_text": r['guidance_text'],
        "has_capex": r['has_capex_news'] or 0,
        "capex_text": r['capex_text'],
        "has_orders": r['has_order_news'] or 0,
        "order_text": r['order_text'],
        "has_financials": r['has_financial_results'] or 0,
        "financial_text": r['financial_results_text'],
        "has_dividend": r['has_dividend_news'] or 0,
        "dividend_text": r['dividend_text'],
        "sentiment_positive": r['sentiment_positive'] or 0,
        "sentiment_negative": r['sentiment_negative'] or 0,
        "sentiment_neutral": r['sentiment_neutral'] or 0,
        "key_themes": json.loads(r['key_themes']) if r['key_themes'] else [],
        "latest_announcement": r['latest_announcement_date'],
    }

    # Get recent announcements
    cursor.execute("""
        SELECT id, exchange, category, headline, description, announcement_date, ai_summary
        FROM announcements WHERE company_id = ?
        ORDER BY announcement_date DESC LIMIT 20
    """, (company_id,))
    announcements = [dict(row) for row in cursor.fetchall()]

    # Get insights by type
    cursor.execute("""
        SELECT insight_type, COUNT(*) as cnt,
               GROUP_CONCAT(DISTINCT sentiment) as sentiments
        FROM announcement_insights WHERE company_id = ?
        GROUP BY insight_type ORDER BY cnt DESC
    """, (company_id,))
    insight_summary = [dict(row) for row in cursor.fetchall()]

    conn.close()

    return {
        "company": company,
        "announcements": announcements,
        "insight_summary": insight_summary,
    }


@app.get("/api/dashboard")
def get_dashboard():
    return get_stats()


@app.get("/api/stats")
def get_stats():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM companies WHERE is_active=1")
    total_companies = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM announcements")
    total_announcements = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM announcement_insights")
    total_insights = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT company_id) FROM announcements")
    companies_with_announcements = cursor.fetchone()[0]

    cursor.execute("SELECT exchange, COUNT(*) FROM announcements GROUP BY exchange")
    by_exchange = {r[0]: r[1] for r in cursor.fetchall()}

    cursor.execute("SELECT insight_type, COUNT(*) FROM announcement_insights GROUP BY insight_type ORDER BY COUNT(*) DESC")
    by_type = {r[0]: r[1] for r in cursor.fetchall()}

    conn.close()

    return {
        "total_companies": total_companies,
        "total_announcements": total_announcements,
        "total_insights": total_insights,
        "companies_with_announcements": companies_with_announcements,
        "by_exchange": by_exchange,
        "by_insight_type": by_type,
    }


@app.get("/api/companies/{company_id}/ai_summary")
def generate_ai_summary(company_id: int):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
    company = cursor.fetchone()
    if not company:
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")

    cursor.execute("""
        SELECT headline, description, category, exchange, announcement_date
        FROM announcements WHERE company_id = ?
        ORDER BY announcement_date DESC LIMIT 50
    """, (company_id,))
    announcements = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT insight_type, sentiment, summary, amount, period
        FROM announcement_insights WHERE company_id = ?
        ORDER BY extracted_at DESC LIMIT 50
    """, (company_id,))
    insights = [dict(r) for r in cursor.fetchall()]

    conn.close()

    ann_text = ""
    for a in announcements:
        ann_text += f"- [{a['exchange']}] {a['announcement_date'] or 'No date'} | {a['category'] or ''}: {a['headline'] or ''}. {(a['description'] or '')[:300]}\n"

    insight_text = ""
    for i in insights:
        amt = f" (₹{i['amount']:.0f} Cr)" if i.get('amount') else ""
        per = f" [{i['period']}]" if i.get('period') else ""
        insight_text += f"- {i['insight_type']} | {i['sentiment']}{amt}{per}: {i['summary'] or ''}\n"

    prompt = f"""You are an equity research analyst. Analyze the following Indian small-cap company and generate a structured research summary.

Company: {company['company_name']}
NSE: {company['nse_symbol'] or 'N/A'} | BSE: {company['bse_code'] or 'N/A'}
Sector: {company['sector'] or 'N/A'} | Industry: {company['industry'] or 'N/A'}
Market Cap: ₹{company['market_cap'] / 1e7:.0f} Cr (if available)

ANNOUNCEMENTS (recent filings):
{ann_text}

EXTRACTED INSIGHTS:
{insight_text}

Generate a structured summary with these EXACT sections (use ## for headers):

## RECENT FILING NEWS
Summarize the 2-3 most important recent announcements. Be specific with numbers, dates, and details.

## FUTURE OUTLOOK
Based on order book, capex plans, guidance, and recent activity, what is the company's near-term outlook? Include specific numbers where available.

## KEY RED FLAGS
List any concerns: governance issues, declining financials, management changes, rising debt, etc. Be specific. If none found, say "No significant red flags identified in recent filings."

## COMPANY DESCRIPTION
1-2 sentence description of what the company does.

Keep the total response under 400 words. Be factual, not speculative. Use Indian financial conventions (Cr for crores, FY for financial year)."""

    try:
        response = httpx.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "qwen2.5:3b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 800}
            },
            timeout=120.0
        )
        result = response.json()
        summary = result.get("response", "")
    except Exception as e:
        summary = f"Error generating summary: {str(e)}"

    return {"summary": summary, "company_id": company_id}


@app.get("/")
def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
