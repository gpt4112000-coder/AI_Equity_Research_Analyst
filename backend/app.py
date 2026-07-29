from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from data.storage.db import get_db, init_db
from typing import Optional
from pathlib import Path
import json
import re
import httpx
import uvicorn

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
    from_date: str = None,
    to_date: str = None,
    sentiment: str = None,
    min_amount: float = None,
    max_amount: float = None,
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
    if from_date or to_date:
        # Use announcements table for date range filtering (has all historical dates)
        date_subquery = "SELECT DISTINCT company_id FROM announcements WHERE 1=1"
        date_params = []
        if from_date:
            date_subquery += " AND announcement_date >= ?"
            date_params.append(from_date)
        if to_date:
            date_subquery += " AND announcement_date <= ?"
            date_params.append(to_date)
        query += f" AND c.id IN ({date_subquery})"
        params.extend(date_params)
    if sentiment == "positive":
        query += " AND cs.sentiment_positive > cs.sentiment_negative AND cs.sentiment_positive > cs.sentiment_neutral"
    elif sentiment == "negative":
        query += " AND cs.sentiment_negative > cs.sentiment_positive AND cs.sentiment_negative > cs.sentiment_neutral"
    elif sentiment == "neutral":
        query += " AND cs.sentiment_neutral >= cs.sentiment_positive AND cs.sentiment_neutral >= cs.sentiment_negative"
    if min_amount is not None or max_amount is not None:
        query += " AND c.id IN (SELECT company_id FROM announcement_insights WHERE 1=1"
        if min_amount is not None:
            query += " AND amount >= ?"
            params.append(min_amount)
        if max_amount is not None:
            query += " AND amount <= ?"
            params.append(max_amount)
        query += " GROUP BY company_id HAVING COUNT(*) > 0)"

    # Sorting
    sort_map = {
        "market_cap": "c.market_cap",
        "announcements": "cs.total_announcements",
        "company": "c.company_name",
        "latest": "cs.latest_announcement_date",
        "sentiment": "(COALESCE(cs.sentiment_positive,0) - COALESCE(cs.sentiment_negative,0)) * 1.0 / MAX(COALESCE(cs.sentiment_positive,0) + COALESCE(cs.sentiment_negative,0) + COALESCE(cs.sentiment_neutral,0), 1)",
    }
    # For insight-specific sorts, add a subquery SELECT column
    insight_sort_map = {
        "guidance_count": "guidance_count",
        "order_count": "order_count",
        "capex_count": "capex_count",
    }
    if sort_by in insight_sort_map:
        # Add subquery counts to SELECT and use them for ORDER BY
        ai_table = f"(SELECT company_id, COUNT(*) as cnt FROM announcement_insights WHERE insight_type = '{sort_by.replace('_count','')}' GROUP BY company_id) ai_{sort_by.replace('_count','')}"
        query = query.replace("LEFT JOIN company_summary cs ON c.id = cs.company_id",
                              f"LEFT JOIN company_summary cs ON c.id = cs.company_id LEFT JOIN {ai_table} ON ai_{sort_by.replace('_count','')}.company_id = c.id")
        query = query.replace("SELECT c.id,", f"SELECT c.id, COALESCE(ai_{sort_by.replace('_count','')}.cnt, 0) as {sort_by},")
        sort_map[sort_by] = sort_by
    sort_col = sort_map.get(sort_by, "c.market_cap")
    sort_dir = "ASC" if sort_order.lower() == "asc" else "DESC"
    query += f" ORDER BY {sort_col} {sort_dir} NULLS LAST"

    # Count - extract FROM...WHERE...ORDER BY from the main query
    from_match = re.search(r'(FROM\s+companies\s+c.*)', query, re.DOTALL | re.IGNORECASE)
    if from_match:
        count_query = "SELECT COUNT(*) " + from_match.group(1)
        # Remove ORDER BY and any added columns from count
        count_query = re.sub(r'\s+ORDER\s+BY.*$', '', count_query, flags=re.DOTALL | re.IGNORECASE)
        count_query = re.sub(r'\s+LIMIT\s+.*$', '', count_query, flags=re.DOTALL | re.IGNORECASE)
        cursor.execute(count_query, params)
    else:
        # Fallback: use simple count
        cursor.execute("SELECT COUNT(*) FROM companies c LEFT JOIN company_summary cs ON c.id = cs.company_id WHERE c.is_active = 1", params)
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

    # Get stored company-level AI summary
    conn2 = get_db()
    cursor2 = conn2.cursor()
    cursor2.execute("SELECT summary, announcements_used, generated_at FROM company_ai_summary WHERE company_id = ?", (company_id,))
    stored_summary = cursor2.fetchone()
    conn2.close()

    return {
        "company": company,
        "announcements": announcements,
        "insight_summary": insight_summary,
        "company_ai_summary": {
            "summary": stored_summary["summary"] if stored_summary else None,
            "announcements_used": stored_summary["announcements_used"] if stored_summary else 0,
            "generated_at": stored_summary["generated_at"] if stored_summary else None,
        } if stored_summary else None,
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


@app.post("/api/announcements/{ann_id}/ai_summary")
def generate_announcement_summary(ann_id: int):
    """Generate and store AI summary for a single announcement."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.*, c.company_name, c.nse_symbol, c.bse_code, c.sector, c.industry
        FROM announcements a JOIN companies c ON a.company_id = c.id
        WHERE a.id = ?
    """, (ann_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Announcement not found")

    row = dict(row)

    # If already has valid summary, return it
    if row.get("ai_summary") and row["ai_summary"].strip():
        try:
            parsed = json.loads(row["ai_summary"])
            conn.close()
            return {"ann_id": ann_id, "summary": parsed}
        except json.JSONDecodeError:
            pass  # Fall through to regenerate

    prompt = f"""Analyze this Indian stock market filing and extract structured insights.

Company: {row['company_name']} ({row['nse_symbol'] or row['bse_code'] or 'N/A'})
Sector: {row['sector'] or 'N/A'} | Exchange: {row['exchange'] or 'N/A'}
Date: {row['announcement_date'] or 'Unknown'}
Category: {row['category'] or 'N/A'}
Title: {row['headline'] or ''}
Details: {(row['description'] or '')[:600]}

Respond in EXACTLY this JSON format (no other text):
{{
  "date": "{row['announcement_date'] or ''}",
  "headline": "{(row['headline'] or '')[:100]}",
  "summary": "2-3 sentence factual summary with specific numbers, dates, amounts",
  "categories": ["guidance" or "capex" or "orders" or "financials" or "dividend" or "acquisition" or "management" or "regulatory" or "general"],
  "sentiment": "positive" or "negative" or "neutral",
  "key_numbers": ["list of specific amounts, percentages, dates mentioned"],
  "importance": "high" or "medium" or "low"
}}"""

    try:
        response = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": "qwen2.5:3b", "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.2, "num_predict": 300}},
            timeout=90.0
        )
        raw = response.json().get("response", "")
        # Extract JSON from response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            summary = json.loads(raw[start:end])
        else:
            summary = {"summary": raw.strip(), "categories": ["general"], "sentiment": "neutral", "key_numbers": [], "importance": "medium", "date": row['announcement_date'] or '', "headline": (row['headline'] or '')[:100]}

        # Ensure required fields
        summary.setdefault("date", row['announcement_date'] or '')
        summary.setdefault("headline", (row['headline'] or '')[:100])
        summary.setdefault("summary", raw.strip()[:300])
        summary.setdefault("categories", ["general"])
        summary.setdefault("sentiment", "neutral")
        summary.setdefault("key_numbers", [])
        summary.setdefault("importance", "medium")
    except Exception as e:
        summary = {"summary": f"Error: {str(e)[:100]}", "categories": ["general"], "sentiment": "neutral", "key_numbers": [], "importance": "low", "date": row['announcement_date'] or '', "headline": (row['headline'] or '')[:100]}

    cursor.execute("UPDATE announcements SET ai_summary = ? WHERE id = ?", (json.dumps(summary), ann_id))
    conn.commit()
    conn.close()

    return {"ann_id": ann_id, "summary": summary}


@app.post("/api/companies/{company_id}/generate_summaries")
def generate_missing_summaries(company_id: int):
    """Generate AI summaries for announcements that don't have one yet. Skips existing."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM announcements
        WHERE company_id = ? AND (ai_summary IS NULL OR ai_summary = '')
        ORDER BY announcement_date DESC
    """, (company_id,))
    ids = [r["id"] for r in cursor.fetchall()]

    # Also count how many already have summaries
    cursor.execute("""
        SELECT COUNT(*) FROM announcements
        WHERE company_id = ? AND ai_summary IS NOT NULL AND ai_summary != ''
    """, (company_id,))
    already_done = cursor.fetchone()[0]
    conn.close()

    results = []
    for ann_id in ids:
        try:
            r = generate_announcement_summary(ann_id)
            results.append(r)
        except Exception:
            pass

    return {
        "company_id": company_id,
        "generated": len(results),
        "already_done": already_done,
        "remaining": len(ids) - len(results),
        "results": results,
    }


@app.get("/api/companies/{company_id}/ai_summary")
def generate_ai_summary(company_id: int):
    """Generate or update company-level AI summary. Only processes new announcements."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
    company = cursor.fetchone()
    if not company:
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")

    # Get ALL announcements WITH ai_summary
    cursor.execute("""
        SELECT id, ai_summary, announcement_date, category, headline, exchange
        FROM announcements WHERE company_id = ? AND ai_summary IS NOT NULL AND ai_summary != ''
        ORDER BY announcement_date DESC
    """, (company_id,))
    all_rows = [dict(r) for r in cursor.fetchall()]

    if not all_rows:
        conn.close()
        return {"summary": "No AI summaries generated yet. Click 'Analyze All Announcements' first.", "company_id": company_id, "announcements_used": 0}

    # Check existing summary
    cursor.execute("SELECT summary, announcements_used FROM company_ai_summary WHERE company_id = ?", (company_id,))
    existing = cursor.fetchone()
    conn.close()

    # If summary exists and all announcements are already included, return it
    if existing and existing["announcements_used"] >= len(all_rows):
        return {"summary": existing["summary"], "company_id": company_id, "announcements_used": existing["announcements_used"]}

    # Build context from ALL announcement summaries
    ann_text = ""
    for r in all_rows:
        try:
            s = json.loads(r["ai_summary"])
            cats = ", ".join(s.get("categories", []))
            sentiment = s.get("sentiment", "neutral")
            key_nums = "; ".join(s.get("key_numbers", [])[:3])
            ann_text += f"- [{r['announcement_date']}] ({r['exchange']}) {r['category']}: {s.get('headline', r['headline'] or '')}\n"
            ann_text += f"  Summary: {s.get('summary', '')}\n"
            ann_text += f"  Categories: {cats} | Sentiment: {sentiment} | Key numbers: {key_nums}\n\n"
        except Exception:
            continue

    # If existing summary, add context about it
    existing_context = ""
    if existing and existing["summary"]:
        existing_context = f"""

EXISTING SUMMARY (already generated from {existing['announcements_used']} announcements):
{existing['summary']}

Update the above summary with any NEW information from the announcements below. Keep existing sections that are still valid. Only add/update what's new. Do not repeat information."""

    prompt = f"""You are an equity research analyst. Synthesize per-announcement summaries for an Indian small-cap company into a structured research report.

Company: {dict(company)['company_name']}
NSE: {dict(company)['nse_symbol'] or 'N/A'} | BSE: {dict(company)['bse_code'] or 'N/A'}
Sector: {dict(company)['sector'] or 'N/A'} | Industry: {dict(company)['industry'] or 'N/A'}
Market Cap: Rs.{(dict(company)['market_cap'] or 0) / 1e7:.0f} Cr
{existing_context}

PER-ANNOUNCEMENT SUMMARIES (chronological):
{ann_text}

Generate a structured summary with these EXACT sections (use ## for headers). Include dates in brackets:

## RECENT FILING NEWS
Summarize the 3-5 most important announcements with specific dates, numbers, amounts. Format: [Date] description.

## FUTURE OUTLOOK
Based on all announcements, what is the company's near-term outlook? Include order book, capex plans, guidance with dates and numbers.

## KEY RED FLAGS
List concerns with dates. If none, say "No significant red flags."

## POSITIVE SIGNALS
List positive developments with dates.

## COMPANY DESCRIPTION
1-2 sentence description.

Keep total under 500 words. Be factual. Use Indian conventions (Cr, FY)."""

    try:
        response = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": "qwen2.5:3b", "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.3, "num_predict": 800}},
            timeout=120.0
        )
        summary = response.json().get("response", "")
    except Exception as e:
        summary = f"Error: {str(e)}"

    # Save to database
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO company_ai_summary (company_id, summary, announcements_used, generated_at)
        VALUES (?, ?, ?, datetime('now'))
    """, (company_id, summary, len(all_rows)))
    conn.commit()
    conn.close()

    return {"summary": summary, "company_id": company_id, "announcements_used": len(all_rows)}


@app.get("/api/sentiment-timeline/{company_id}")
def get_sentiment_timeline(company_id: int):
    """Get sentiment counts grouped by month for a company."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            substr(a.announcement_date, 1, 7) as month,
            ai.sentiment,
            COUNT(*) as cnt
        FROM announcement_insights ai
        JOIN announcements a ON ai.announcement_id = a.id
        WHERE ai.company_id = ? AND ai.sentiment IS NOT NULL
        GROUP BY month, ai.sentiment
        ORDER BY month
    """, (company_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    timeline = {}
    for r in rows:
        m = r['month']
        if m not in timeline:
            timeline[m] = {'month': m, 'positive': 0, 'negative': 0, 'neutral': 0}
        timeline[m][r['sentiment']] = r['cnt']

    return {"timeline": list(timeline.values())}


@app.get("/api/stock-info/{symbol}")
def get_stock_info(symbol: str):
    """Fetch live stock info from yfinance."""
    try:
        from data.collectors.yfinance_data import get_stock_info as fetch_info
        info = fetch_info(symbol.upper())
        return {"symbol": symbol.upper(), "info": info}
    except Exception as e:
        return {"symbol": symbol.upper(), "info": None, "error": str(e)}


@app.get("/api/search-stock")
def search_stock(q: str):
    """Search for stocks by name or symbol from existing DB or BSE code."""
    conn = get_db()
    cursor = conn.cursor()
    q_upper = q.upper()
    q_like = f"%{q}%"

    # First search existing DB
    cursor.execute("""
        SELECT id, company_name, nse_symbol, bse_code, sector
        FROM companies
        WHERE UPPER(company_name) LIKE ? OR UPPER(nse_symbol) LIKE ? OR UPPER(bse_code) LIKE ?
        LIMIT 10
    """, (q_like, q_like, q_like))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    results = []
    for r in rows:
        results.append({
            "symbol": r["nse_symbol"] or r["bse_code"] or "",
            "company_name": r["company_name"],
            "bse_code": r["bse_code"] or "",
            "in_db": True,
            "company_id": r["id"],
        })

    return {"results": results}


@app.get("/")
def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/fetch")
def serve_fetch_page():
    return FileResponse(str(FRONTEND_DIR / "fetch.html"))


@app.get("/api/fetch")
def fetch_stock(symbol: str, years: int = 5):
    """Fetch announcements for any stock from BSE/NSE."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

    try:
        from fetch_stock_announcements import fetch_stock as do_fetch
        result = do_fetch(symbol, years)
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/fetched-stocks")
def list_fetched_stocks():
    """List all fetched stocks."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT fs.*, c.company_name, c.sector, c.market_cap,
               cs.total_announcements, cs.has_guidance, cs.has_order_news,
               cs.has_capex_news, cs.has_dividend_news, cs.has_financial_results
        FROM fetched_stock fs
        LEFT JOIN companies c ON fs.company_id = c.id
        LEFT JOIN company_summary cs ON fs.company_id = cs.company_id
        ORDER BY fs.fetched_at DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"stocks": rows}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
