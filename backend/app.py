from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from data.storage.db import get_db, init_db
from typing import Optional
from pathlib import Path
import json
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
    """Generate AI summaries for all announcements of a company that don't have one yet."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM announcements
        WHERE company_id = ? AND (ai_summary IS NULL OR ai_summary = '')
        ORDER BY announcement_date DESC LIMIT 20
    """, (company_id,))
    ids = [r["id"] for r in cursor.fetchall()]
    conn.close()

    results = []
    for ann_id in ids:
        try:
            r = generate_announcement_summary(ann_id)
            results.append(r)
        except Exception:
            pass

    return {"company_id": company_id, "generated": len(results), "results": results}


@app.get("/api/companies/{company_id}/ai_summary")
def generate_ai_summary(company_id: int):
    """Generate company-level AI research summary from all stored per-announcement summaries."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
    company = cursor.fetchone()
    if not company:
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")

    # Get announcements WITH ai_summary (stored structured data)
    cursor.execute("""
        SELECT ai_summary, announcement_date, category, headline, exchange
        FROM announcements WHERE company_id = ? AND ai_summary IS NOT NULL AND ai_summary != ''
        ORDER BY announcement_date DESC
    """, (company_id,))
    rows = [dict(r) for r in cursor.fetchall()]

    conn.close()

    if not rows:
        return {"summary": "No AI summaries generated yet for this company's announcements. Click company to generate per-announcement summaries first.", "company_id": company_id}

    # Build context from stored summaries
    ann_text = ""
    for r in rows:
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

    prompt = f"""You are an equity research analyst. Synthesize the following per-announcement summaries for an Indian small-cap company into a structured research report.

Company: {dict(company)['company_name']}
NSE: {dict(company)['nse_symbol'] or 'N/A'} | BSE: {dict(company)['bse_code'] or 'N/A'}
Sector: {dict(company)['sector'] or 'N/A'} | Industry: {dict(company)['industry'] or 'N/A'}
Market Cap: Rs.{dict(company)['market_cap'] / 1e7:.0f} Cr

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
    """, (company_id, summary, len(rows)))
    conn.commit()
    conn.close()

    return {"summary": summary, "company_id": company_id, "announcements_used": len(rows)}


@app.get("/")
def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
