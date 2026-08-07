from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from data.storage.db import get_db, init_db
from concall_pipeline import (
    recent_cutoff, ensure_queued, download_pending, analyze_downloaded,
)
from typing import Optional
from pathlib import Path
import json
import re
import httpx
import uvicorn
import threading
import time

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
    corporate_actions: int = None,
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
               cs.key_themes, cs.latest_announcement_date,
               COALESCE(ca.ca_count, 0) as ca_count, ca.latest_ca_date
        FROM companies c
        LEFT JOIN company_summary cs ON c.id = cs.company_id
        LEFT JOIN (
            SELECT company_id, COUNT(*) as ca_count, MAX(announcement_date) as latest_ca_date
            FROM announcements WHERE event_key IS NOT NULL
            GROUP BY company_id
        ) ca ON ca.company_id = c.id
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
    if corporate_actions:
        query += " AND ca.ca_count > 0"

    # Sorting
    sort_map = {
        "market_cap": "c.market_cap",
        "announcements": "cs.total_announcements",
        "company": "c.company_name",
        "latest": "cs.latest_announcement_date",
        "ca_latest": "ca.latest_ca_date",
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
            "ca_count": r['ca_count'] or 0,
            "latest_ca_date": r['latest_ca_date'],
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

    # Analysis status (used by UI to auto-start/reflect stored insights)
    cursor.execute("""
        SELECT COUNT(*) FROM announcements
        WHERE company_id = ? AND (ai_summary IS NULL OR ai_summary = '')
    """, (company_id,))
    ai_pending = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM company_files
        WHERE company_id = ? AND status = 'done' AND analyzed = 0
    """, (company_id,))
    doc_pending = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM company_files
        WHERE company_id = ? AND status = 'done'
    """, (company_id,))
    doc_done = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM ai_insights WHERE company_id = ?", (company_id,))
    ai_insight_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM document_insights WHERE company_id = ?", (company_id,))
    doc_insight_count = cursor.fetchone()[0]

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
        "analysis_status": {
            "ai_pending": ai_pending,
            "doc_pending": doc_pending,
            "doc_done": doc_done,
            "ai_insight_count": ai_insight_count,
            "doc_insight_count": doc_insight_count,
        },
        "company_ai_summary": {
            "summary": stored_summary["summary"] if stored_summary else None,
            "announcements_used": stored_summary["announcements_used"] if stored_summary else 0,
            "generated_at": stored_summary["generated_at"] if stored_summary else None,
        } if stored_summary else None,
    }


# ---- Documents section (Screener-style) ----
_DOCS_CACHE = {}
_DOCS_CACHE_TTL = 600  # 10 minutes


def _classify_announcement(row):
    """Classify an announcement into documents buckets (annual report, credit
    rating, concall). Prefers structured API category/subcategory/event_key;
    falls back to headline + attachment-URL regex for legacy rows."""
    cat = (row.get("category") or "").lower()
    sub = (row.get("subcategory") or "").lower()
    event = (row.get("event_key") or "").lower()
    text = f"{row.get('headline') or ''} {row.get('attachment_url') or ''}".lower()
    kinds = set()
    if "annual_report" in event or cat == "annual report" or "annual report" in sub \
            or "annual_report" in text or "_ar" in text or "/annual_reports/" in text:
        kinds.add("annual_report")
    if "credit_rating" in event or "credit rating" in cat or "credit rating" in sub \
            or "credit rating" in text or "rating update" in text:
        kinds.add("credit_rating")
    if "concall" in event or any(k in text for k in ["concall", "con call", "investor meet",
                                                     "institutional investor meet", "earnings call",
                                                     "analyst meet", "transcript", "ppt", "presentation"]):
        kinds.add("concall")
    return kinds


def _parse_agency(attachment_url):
    """Extract rating agency from attachment URL."""
    u = (attachment_url or "").upper()
    for agency in ["CRISIL", "CARE", "ICRA", "BRICKWORK", "INDIA RATINGS", "FITCH"]:
        if agency in u:
            return agency.title()
    return None


@app.get("/api/companies/{company_id}/documents")
def company_documents(company_id: int):
    """Return Screener-style documents: announcements (recent/important/search/all),
    annual reports (NSE + classified attachments), credit ratings, concalls."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT id, company_name, nse_symbol, bse_code FROM companies WHERE id = ?", (company_id,))
    company = cursor.fetchone()
    if not company:
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")

    company = dict(company)
    symbol = company["nse_symbol"] or company["bse_code"] or ""

    # ---- Announcements ----
    cursor.execute("""
        SELECT id, exchange, category, subcategory, headline, description, announcement_date,
               attachment_url, ai_summary, event_key
        FROM announcements WHERE company_id = ?
        ORDER BY announcement_date DESC, id DESC
        LIMIT 200
    """, (company_id,))
    anns = []
    for r in cursor.fetchall():
        row = dict(r)
        parsed = None
        if row.get("ai_summary"):
            try:
                parsed = json.loads(row["ai_summary"])
            except Exception:
                parsed = None
        anns.append({
            "id": row["id"],
            "exchange": row["exchange"],
            "category": row["category"],
            "subcategory": row["subcategory"],
            "headline": row["headline"],
            "description": row["description"],
            "date": row["announcement_date"],
            "attachment_url": row["attachment_url"],
            "event_key": row["event_key"],
            "ai_summary": parsed,
            "importance": (parsed or {}).get("importance") if parsed else None,
            "sentiment": (parsed or {}).get("sentiment") if parsed else None,
        })
    # ---- Announcement-type breakdown (full DB counts, for the filter chips) ----
    cursor.execute("""
        SELECT
          CASE
            WHEN event_key IS NOT NULL AND event_key LIKE 'ca:%' THEN 'corporate_action'
            WHEN LOWER(COALESCE(category,'')) LIKE '%result%'
                 OR LOWER(COALESCE(category,'')) LIKE '%financial%' THEN 'financial_result'
            WHEN LOWER(COALESCE(category,'')) LIKE '%board%' THEN 'board_meeting'
            WHEN LOWER(COALESCE(category,'')) LIKE '%credit%' THEN 'credit_rating'
            WHEN LOWER(COALESCE(category,'')) LIKE '%general%' THEN 'general'
            ELSE 'other'
          END AS family,
          COUNT(*) AS cnt
        FROM announcements
        WHERE company_id = ?
        GROUP BY family
    """, (company_id,))
    type_counts = {r["family"]: r["cnt"] for r in cursor.fetchall()}

    # ---- Corporate actions (mirrored event_key announcements, full list) ----
    cursor.execute("""
        SELECT id, exchange, category, subcategory, headline, announcement_date, ai_summary
        FROM announcements
        WHERE company_id = ? AND event_key IS NOT NULL AND event_key LIKE 'ca:%'
        ORDER BY announcement_date DESC, id DESC LIMIT 100
    """, (company_id,))
    corporate_actions = []
    for r in cursor.fetchall():
        row = dict(r)
        parsed = None
        if row.get("ai_summary"):
            try:
                parsed = json.loads(row["ai_summary"])
            except Exception:
                parsed = None
        corporate_actions.append({
            "id": row["id"],
            "exchange": row["exchange"],
            "category": row["category"],
            "family": (row["category"] or "").replace("Corporate Action - ", "").strip(),
            "subcategory": row["subcategory"],
            "headline": row["headline"],
            "date": row["announcement_date"],
            "ai_summary": parsed,
        })

    # ---- Annual reports: NSE API (cached) + classified announcement attachments ----
    annual_reports = []
    nse_symbol = company.get("nse_symbol")
    if nse_symbol:
        cache_key = f"ar:{nse_symbol}"
        now = __import__("time").time()
        cached = _DOCS_CACHE.get(cache_key)
        if cached and (now - cached["ts"]) < _DOCS_CACHE_TTL:
            annual_reports = cached["data"]
        else:
            try:
                import sys
                sys.path.insert(0, "/home/ubuntu/FinEng/NseIndiaApi/src")
                from nse.NSE import NSE
                with NSE("/tmp/opencode/nse_docs") as nse:
                    resp = nse.annual_reports(nse_symbol)
                data = resp.get("data") or []
                for item in data:
                    fy = item.get("fromYr") or ""
                    to = item.get("toYr") or ""
                    annual_reports.append({
                        "fy": f"FY {fy}-{to}" if fy and to else fy,
                        "fromYr": fy,
                        "toYr": to,
                        "file_name": item.get("fileName"),
                        "size": item.get("attFileSize"),
                        "source": "nse",
                    })
                _DOCS_CACHE[cache_key] = {"ts": now, "data": annual_reports}
            except Exception:
                annual_reports = []

    # Merge classified annual-report attachments from announcements (source: bse)
    seen_fy = set()
    for r in annual_reports:
        if r["fromYr"]:
            seen_fy.add(r["fromYr"])
    ann_ars = [a for a in anns if "annual_report" in _classify_announcement(a) and a["attachment_url"]]
    for a in ann_ars:
        annual_reports.append({
            "fy": (a["date"] or "")[:4],
            "fromYr": (a["date"] or "")[:4],
            "toYr": "",
            "file_name": a["attachment_url"],
            "size": None,
            "source": "bse",
            "date": a["date"],
        })
    # De-dupe by source+fromYr
    dedup = {}
    for r in annual_reports:
        key = (r["source"], r["fromYr"])
        dedup[key] = r
    annual_reports = [dedup[k] for k in sorted(dedup, reverse=True)]

    # ---- Credit ratings (from announcement DB) ----
    credit_ratings = []
    for a in anns:
        if "credit_rating" in _classify_announcement(a):
            credit_ratings.append({
                "date": a["date"],
                "headline": a["headline"],
                "attachment_url": a["attachment_url"],
                "agency": _parse_agency(a["attachment_url"]),
                "ai_summary": a["ai_summary"],
            })

    # ---- Concalls (from announcement DB + stored AI summaries), grouped by quarter ----
    cc_map = {}
    cursor = conn.cursor()
    cursor.execute(
        """SELECT announcement_id, summary, guidance, management_views, qna_summary,
                  key_topics, sentiment, importance, status
           FROM concalls WHERE company_id = ?""",
        (company_id,))
    for r in cursor.fetchall():
        cc_map[r["announcement_id"]] = {
            "summary": r["summary"], "guidance": r["guidance"],
            "management_views": r["management_views"], "qna_summary": r["qna_summary"],
            "key_topics": _safe_json_list(r["key_topics"]),
            "sentiment": r["sentiment"], "importance": r["importance"],
            "status": r["status"],
        }
    concall_anns = [a for a in anns if "concall" in _classify_announcement(a)]
    quarter_map = {}
    for a in concall_anns:
        d = a["date"] or ""
        year = d[:4]
        month = int(d[5:7]) if len(d) >= 7 else 0
        if not year:
            continue
        if 1 <= month <= 3:
            q = "Q4"
        elif 4 <= month <= 6:
            q = "Q1"
        elif 7 <= month <= 9:
            q = "Q2"
        else:
            q = "Q3"
        key = f"{year} {q}"
        if key not in quarter_map:
            quarter_map[key] = []
        quarter_map[key].append(a)
    concalls = []
    for key in sorted(quarter_map, reverse=True):
        assets = {"transcript": [], "ppt": [], "record": [], "other": []}
        analyzed = 0
        for a in quarter_map[key]:
            url = (a["attachment_url"] or "").lower()
            cc = cc_map.get(a["id"], {})
            if cc.get("status") == "done":
                analyzed += 1
            item = {"id": a["id"], "headline": a["headline"], "date": a["date"],
                    "attachment_url": a["attachment_url"], "ai_summary": a["ai_summary"],
                    "concall": cc}
            if "transcript" in url:
                assets["transcript"].append(item)
            elif "ppt" in url or "presentation" in url:
                assets["ppt"].append(item)
            elif "rec" in url or url.endswith(".mp3") or url.endswith(".wav"):
                assets["record"].append(item)
            else:
                assets["other"].append(item)
        concalls.append({"quarter": key, **assets, "total": len(quarter_map[key]),
                         "analyzed": analyzed})

    # ---- Announcement-type breakdown (full DB counts, for the filter chips) ----
    conn.close()
    return {
        "company": {"id": company_id, "name": company["company_name"], "symbol": symbol},
        "announcements": anns,
        "annual_reports": annual_reports,
        "credit_ratings": credit_ratings,
        "concalls": concalls,
        "corporate_actions": corporate_actions,
        "type_counts": type_counts,
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


def _parse_key_numbers(key_numbers):
    """Extract (amount_in_crore, raw_text) from AI key_numbers list."""
    if not key_numbers:
        return None, None
    for kn in key_numbers:
        if not kn:
            continue
        t = str(kn)
        m = re.search(r"([\d][\d,\.]*)\s*(cr|crore|lakh|lac|mn|million|bn|billion|k|thousand|%|x)?\b", t, re.I)
        if not m:
            continue
        try:
            num = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        mult = {"cr": 1, "crore": 1, "lakh": 0.01, "lac": 0.01,
                "mn": 0.1, "million": 0.1, "bn": 100, "billion": 100,
                "k": 0.0001, "thousand": 0.0001}.get(unit, 1)
        return round(num * mult, 4), t
    return None, None


def _store_ai_insights(cursor, row, summary):
    """Insert one ai_insights row per category (metric) with date + parsed amount."""
    company_id = row["company_id"]
    ann_id = row["id"]
    ann_date = row["announcement_date"] or ""
    headline = (row["headline"] or "")[:300]
    s = summary.get("summary") or ""
    sentiment = summary.get("sentiment") or "neutral"
    importance = summary.get("importance") or "medium"
    categories = summary.get("categories") or ["general"]
    amount, amount_text = _parse_key_numbers(summary.get("key_numbers") or [])
    seen = set()
    for cat in categories:
        metric = str(cat).lower().strip()
        if not metric or metric in seen:
            continue
        seen.add(metric)
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO ai_insights
                (company_id, announcement_id, metric, announcement_date, headline,
                 summary, amount, amount_text, sentiment, importance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (company_id, ann_id, metric, ann_date, headline, s,
                  amount, amount_text, sentiment, importance))
        except Exception:
            continue


def _generate_announcement_summary_core(conn, ann_id):
    """Generate + store AI summary and metric-wise insights for one announcement.
    Returns (result_dict, is_new). Returns (None, False) if announcement not found."""
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.*, c.company_name, c.nse_symbol, c.bse_code, c.sector, c.industry
        FROM announcements a JOIN companies c ON a.company_id = c.id
        WHERE a.id = ?
    """, (ann_id,))
    row = cursor.fetchone()
    if not row:
        return None, False

    row = dict(row)

    # If already has valid summary, return it
    if row.get("ai_summary") and row["ai_summary"].strip():
        try:
            parsed = json.loads(row["ai_summary"])
            return {"ann_id": ann_id, "summary": parsed}, False
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
    except Exception:
        # Leave ai_summary NULL so the announcement stays pending and is
        # retried on the next batch run (resume-where-left-off).
        return None, False

    cursor.execute("UPDATE announcements SET ai_summary = ? WHERE id = ?", (json.dumps(summary), ann_id))
    _store_ai_insights(cursor, row, summary)
    conn.commit()

    return {"ann_id": ann_id, "summary": summary}, True


@app.post("/api/announcements/{ann_id}/ai_summary")
def generate_announcement_summary(ann_id: int):
    """Generate and store AI summary for a single announcement."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM announcements WHERE id = ?", (ann_id,))
        exists = cursor.fetchone() is not None
        result, _is_new = _generate_announcement_summary_core(conn, ann_id)
    finally:
        conn.close()
    if not exists:
        raise HTTPException(status_code=404, detail="Announcement not found")
    if result is None:
        return {"ok": False, "announcement_id": ann_id,
                "message": "AI generation failed (Ollama unreachable or timeout). It stays pending and will be retried on the next batch run."}
    return result


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


# ---------------------------------------------------------------------------
# Peer Comparison (Screener-style)
# ---------------------------------------------------------------------------

COMPARE_METRICS = [
    # ---- Valuation ----
    {"id": "market_cap_cr", "label": "Market Cap (Cr)", "category": "Valuation",
     "desc": "Market capitalization in ₹ Crore", "format": "cr"},
    {"id": "current_price", "label": "Current Price", "category": "Valuation",
     "desc": "Latest traded price (₹)", "format": "money"},
    {"id": "pe_ratio", "label": "P/E Ratio", "category": "Valuation",
     "desc": "Price to Earnings (trailing)", "format": "decimal2"},
    {"id": "forward_pe", "label": "Forward P/E", "category": "Valuation",
     "desc": "Forward price to earnings", "format": "decimal2"},
    {"id": "peg_ratio", "label": "PEG", "category": "Valuation",
     "desc": "P/E divided by earnings growth", "format": "decimal2"},
    {"id": "pb_ratio", "label": "P/B Ratio", "category": "Valuation",
     "desc": "Price to Book", "format": "decimal2"},
    {"id": "ps_ratio", "label": "P/S Ratio", "category": "Valuation",
     "desc": "Price to Sales", "format": "decimal2"},
    {"id": "dividend_yield", "label": "Div Yield (%)", "category": "Valuation",
     "desc": "Dividend yield as percentage", "format": "percent"},
    {"id": "target_price", "label": "Analyst Target (₹)", "category": "Valuation",
     "desc": "Average analyst price target", "format": "money"},
    # ---- Profitability ----
    {"id": "roe", "label": "ROE (%)", "category": "Profitability",
     "desc": "Return on Equity", "format": "percent"},
    {"id": "roce", "label": "ROCE (%)", "category": "Profitability",
     "desc": "Return on Capital Employed", "format": "percent"},
    {"id": "net_margin", "label": "Net Margin (%)", "category": "Profitability",
     "desc": "Net profit margin", "format": "percent"},
    {"id": "operating_margin", "label": "Op Margin (%)", "category": "Profitability",
     "desc": "Operating profit margin", "format": "percent"},
    {"id": "eps", "label": "EPS (₹)", "category": "Profitability",
     "desc": "Earnings per share", "format": "money"},
    {"id": "book_value", "label": "Book Value (₹)", "category": "Profitability",
     "desc": "Book value per share", "format": "money"},
    # ---- Leverage / Financial Health ----
    {"id": "debt_to_equity", "label": "Debt/Equity", "category": "Leverage",
     "desc": "Total debt to equity ratio", "format": "decimal2"},
    {"id": "total_debt_cr", "label": "Total Debt (Cr)", "category": "Leverage",
     "desc": "Total debt in ₹ Crore", "format": "cr"},
    {"id": "total_cash_cr", "label": "Total Cash (Cr)", "category": "Leverage",
     "desc": "Total cash in ₹ Crore", "format": "cr"},
    # ---- Growth ----
    {"id": "revenue_growth", "label": "Revenue Growth (%)", "category": "Growth",
     "desc": "YoY revenue growth", "format": "percent"},
    {"id": "earnings_growth", "label": "Earnings Growth (%)", "category": "Growth",
     "desc": "YoY earnings growth", "format": "percent"},
    # ---- Market / Technical ----
    {"id": "beta", "label": "Beta", "category": "Market",
     "desc": "Volatility vs market", "format": "decimal2"},
    {"id": "sma_50", "label": "SMA 50 (₹)", "category": "Market",
     "desc": "50 day moving average", "format": "money"},
    {"id": "sma_200", "label": "SMA 200 (₹)", "category": "Market",
     "desc": "200 day moving average", "format": "money"},
    {"id": "week_52_high", "label": "52W High (₹)", "category": "Market",
     "desc": "52 week high", "format": "money"},
    {"id": "week_52_low", "label": "52W Low (₹)", "category": "Market",
     "desc": "52 week low", "format": "money"},
    {"id": "volume", "label": "Volume", "category": "Market",
     "desc": "Trading volume", "format": "count"},
    {"id": "avg_volume", "label": "Avg Volume", "category": "Market",
     "desc": "Average trading volume", "format": "count"},
    {"id": "promoter_holding", "label": "Promoter Hold (%)", "category": "Market",
     "desc": "Promoter shareholding", "format": "percent"},
    # ---- Company Activity (from our insights) ----
    {"id": "total_announcements", "label": "Announcements", "category": "Activity",
     "desc": "Total announcements in DB", "format": "count"},
    {"id": "guidance_count", "label": "Guidance News", "category": "Activity",
     "desc": "Guidance announcements", "format": "count"},
    {"id": "order_count", "label": "Order Wins", "category": "Activity",
     "desc": "Order announcements", "format": "count"},
    {"id": "capex_count", "label": "Capex News", "category": "Activity",
     "desc": "Capex announcements", "format": "count"},
    {"id": "dividend_count", "label": "Dividend News", "category": "Activity",
     "desc": "Dividend announcements", "format": "count"},
    {"id": "sentiment_score", "label": "Sentiment Score", "category": "Activity",
     "desc": "Positive minus negative (weighted)", "format": "decimal0"},
]

# In-memory cache for yfinance metrics (avoids hammering API)
_compare_cache = {}
_COMPARE_CACHE_TTL = 600  # 10 minutes


@app.get("/api/compare/metrics")
def compare_metrics_catalog():
    """Return the catalog of available comparison metrics."""
    categories = {}
    for m in COMPARE_METRICS:
        categories.setdefault(m["category"], []).append(m)
    return {"categories": categories, "total": len(COMPARE_METRICS)}


def _fetch_compare_metrics_for_symbol(symbol):
    """Fetch metrics for a single symbol (yfinance + DB activity). Cached."""
    global _compare_cache
    now = __import__("time").time()
    cached = _compare_cache.get(symbol)
    if cached and (now - cached["ts"]) < _COMPARE_CACHE_TTL:
        return cached["data"]

    data = {"symbol": symbol.upper(), "company_name": symbol.upper(), "metrics": {}}

    # 1) Live metrics from yfinance
    try:
        from data.collectors.yfinance_data import get_stock_info as fetch_info
        info = fetch_info(symbol.upper())
        if info:
            m = data["metrics"]
            if info.get("company_name"):
                data["company_name"] = info["company_name"]
            mc = info.get("market_cap")
            m["market_cap_cr"] = round(mc / 1e7, 2) if mc else None
            m["current_price"] = info.get("current_price") or info.get("previous_close")
            m["pe_ratio"] = info.get("pe_ratio")
            m["pb_ratio"] = info.get("pb_ratio")
            # Dividend yield: yfinance returns it already as percentage
            m["dividend_yield"] = info.get("dividend_yield")
            m["eps"] = info.get("eps")
            m["book_value"] = info.get("book_value")
            m["debt_to_equity"] = info.get("debt_to_equity")
            m["roe"] = round(info.get("roe", 0) * 100, 2) if info.get("roe") is not None else None
            m["roce"] = round(info.get("roce", 0) * 100, 2) if info.get("roce") is not None else None
            m["promoter_holding"] = round(info.get("promoter_holding", 0) * 100, 2) if info.get("promoter_holding") is not None else None
            m["beta"] = info.get("beta")
            m["sma_50"] = info.get("sma_50")
            m["sma_200"] = info.get("sma_200")
            m["week_52_high"] = info.get("52w_high")
            m["week_52_low"] = info.get("52w_low")
            m["volume"] = info.get("volume")
            m["avg_volume"] = info.get("avg_volume")
    except Exception:
        pass

    # 2) DB activity metrics (only for companies in our DB)
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.nse_symbol, cs.total_announcements
            FROM companies c
            LEFT JOIN company_summary cs ON c.id = cs.company_id
            WHERE UPPER(COALESCE(c.nse_symbol,'')) = ? OR UPPER(COALESCE(c.bse_code,'')) = ?
        """, (symbol.upper(), symbol.upper()))
        row = cursor.fetchone()
        if row:
            cid = row["id"]
            m = data["metrics"]
            m["total_announcements"] = row["total_announcements"] or 0
            cursor.execute("SELECT insight_type, COUNT(*) as cnt FROM announcement_insights WHERE company_id = ? GROUP BY insight_type", (cid,))
            counts = {r["insight_type"]: r["cnt"] for r in cursor.fetchall()}
            m["guidance_count"] = counts.get("guidance", 0)
            m["order_count"] = counts.get("order", 0)
            m["capex_count"] = counts.get("capex", 0)
            m["dividend_count"] = counts.get("dividend", 0)
            m["sentiment_score"] = counts.get("financial", 0)  # placeholder
            cursor.execute("""
                SELECT COALESCE(sentiment_positive,0) - COALESCE(sentiment_negative,0) as score
                FROM company_summary WHERE company_id = ?
            """, (cid,))
            srow = cursor.fetchone()
            if srow and srow["score"] is not None:
                m["sentiment_score"] = srow["score"]
            # company name from DB if yfinance failed
            if not data["company_name"] or data["company_name"] == symbol.upper():
                cursor.execute("SELECT company_name FROM companies WHERE id = ?", (cid,))
                cname = cursor.fetchone()
                if cname and cname["company_name"]:
                    data["company_name"] = cname["company_name"]
        conn.close()
    except Exception:
        pass

    _compare_cache[symbol] = {"ts": now, "data": data}
    return data


@app.get("/api/compare/peers")
def compare_peers(symbols: str):
    """Compare metrics across multiple symbols. Fetch live from yfinance, cache 10 min."""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    results = []
    for sym in sym_list[:15]:
        results.append(_fetch_compare_metrics_for_symbol(sym))
    return {"companies": results, "count": len(results)}


@app.get("/api/compare/sector-peers")
def sector_peers(company_id: int, limit: int = 5):
    """Return same-sector companies (with tradeable symbols) for auto peer comparison."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT sector, nse_symbol, bse_code, company_name, market_cap FROM companies WHERE id = ?", (company_id,))
    base = cursor.fetchone()
    if not base:
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")

    sector = base["sector"]
    base_sym = (base["nse_symbol"] or base["bse_code"] or "").upper()
    peers = []
    if sector:
        cursor.execute("""
            SELECT nse_symbol, bse_code, company_name, market_cap
            FROM companies
            WHERE sector = ? AND id != ?
              AND ((nse_symbol IS NOT NULL AND nse_symbol != '') OR (bse_code IS NOT NULL AND bse_code != ''))
            ORDER BY CASE WHEN nse_symbol IS NOT NULL AND nse_symbol != '' THEN 0 ELSE 1 END, market_cap DESC
            LIMIT ?
        """, (sector, company_id, max(1, min(limit, 10))))
        for r in cursor.fetchall():
            sym = (r["nse_symbol"] or r["bse_code"] or "").upper()
            if not sym or sym == base_sym:
                continue
            peers.append({
                "symbol": sym,
                "company_name": r["company_name"],
                "market_cap_cr": round(r["market_cap"] / 1e7, 2) if r["market_cap"] else 0,
                "sector": sector,
            })
    conn.close()
    return {"base_symbol": base_sym, "base_name": base["company_name"], "sector": sector, "peers": peers}


# ---- AI Batch Analysis Job (background, incremental) ----
_AI_BATCH = {
    "running": False,
    "company_id": None,
    "scope": "all",
    "total": 0,
    "processed": 0,
    "generated": 0,
    "skipped": 0,
    "failed": 0,
    "current": "",
    "started_at": None,
    "finished_at": None,
    "stop_requested": False,
    "message": "",
}
_AI_BATCH_LOCK = threading.Lock()


def _ai_batch_worker(company_id):
    conn = get_db()
    try:
        cursor = conn.cursor()
        # Snapshot pending ids up front so pagination can't skip rows as the
        # result set shrinks while we assign ai_summary.
        if company_id:
            cursor.execute("""
                SELECT id FROM announcements
                WHERE company_id = ? AND (ai_summary IS NULL OR ai_summary = '')
                ORDER BY announcement_date DESC
            """, (company_id,))
        else:
            cursor.execute("""
                SELECT id FROM announcements
                WHERE (ai_summary IS NULL OR ai_summary = '')
                ORDER BY announcement_date DESC
            """)
        pending_ids = [r["id"] for r in cursor.fetchall()]
        with _AI_BATCH_LOCK:
            _AI_BATCH["total"] = len(pending_ids)
            _AI_BATCH["processed"] = 0
            _AI_BATCH["generated"] = 0
            _AI_BATCH["skipped"] = 0
            _AI_BATCH["failed"] = 0
            _AI_BATCH["message"] = "Starting..."

        processed = generated = skipped = failed = 0
        for aid in pending_ids:
            with _AI_BATCH_LOCK:
                if _AI_BATCH["stop_requested"]:
                    _AI_BATCH["message"] = "Stopped by user"
                    _AI_BATCH["stop_requested"] = False
                    break
                _AI_BATCH["current"] = str(aid)
            try:
                result, is_new = _generate_announcement_summary_core(conn, aid)
                if result is None:
                    failed += 1
                elif is_new:
                    generated += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1
            processed += 1
            with _AI_BATCH_LOCK:
                _AI_BATCH["processed"] = processed
                _AI_BATCH["generated"] = generated
                _AI_BATCH["skipped"] = skipped
                _AI_BATCH["failed"] = failed
                _AI_BATCH["message"] = f"{processed}/{len(pending_ids)} processed"

        with _AI_BATCH_LOCK:
            _AI_BATCH["running"] = False
            _AI_BATCH["finished_at"] = time.time()
            if _AI_BATCH["message"] != "Stopped by user":
                _AI_BATCH["message"] = f"Done: {generated} new, {skipped} existing, {failed} failed"
    finally:
        conn.close()


@app.post("/api/ai/batch/start")
def ai_batch_start(company_id: Optional[int] = None):
    """Start a background batch AI analysis job. Only processes announcements that
    do NOT yet have an ai_summary (incremental — never re-runs on already-read docs)."""
    with _AI_BATCH_LOCK:
        if _AI_BATCH["running"]:
            return {"ok": False, "message": "A batch job is already running", "status": dict(_AI_BATCH)}
        _AI_BATCH["running"] = True
        _AI_BATCH["company_id"] = company_id
        _AI_BATCH["scope"] = "company" if company_id else "all"
        _AI_BATCH["started_at"] = time.time()
        _AI_BATCH["finished_at"] = None
        _AI_BATCH["stop_requested"] = False
        _AI_BATCH["message"] = "Starting..."

    t = threading.Thread(target=_ai_batch_worker, args=(company_id,), daemon=True)
    t.start()
    return {"ok": True, "message": "Batch started", "status": dict(_AI_BATCH)}


@app.get("/api/ai/batch/status")
def ai_batch_status():
    with _AI_BATCH_LOCK:
        return dict(_AI_BATCH)


@app.post("/api/ai/batch/stop")
def ai_batch_stop():
    with _AI_BATCH_LOCK:
        if not _AI_BATCH["running"]:
            return {"ok": False, "message": "No batch job is running"}
        _AI_BATCH["stop_requested"] = True
    return {"ok": True, "message": "Stop requested"}


# ---- Document AI Analysis Job (background, on-demand per company) ----
_DOC_BATCH = {
    "running": False,
    "company_id": None,
    "total": 0,
    "processed": 0,
    "generated": 0,
    "failed": 0,
    "current": "",
    "started_at": None,
    "finished_at": None,
    "stop_requested": False,
    "message": "",
}
_DOC_BATCH_LOCK = threading.Lock()

DOC_PROMPT = """Analyze this Indian corporate document content and extract structured investment insights.

Company: {company_name} ({symbol})
Document kind: {kind}
Announcement date: {ann_date}
Headline: {headline}

CONTENT:
{content}

Respond in EXACTLY this JSON (no other text):
{{
  "metrics": [
    {{
      "metric": "capex" | "guidance" | "orders" | "financials" | "dividend" | "acquisition" | "management" | "regulatory" | "credit_rating",
      "summary": "1-2 sentence factual summary with specific numbers, dates, amounts",
      "amount": number in crore (or null),
      "amount_text": "raw amount text as stated",
      "sentiment": "positive" | "negative" | "neutral",
      "importance": "high" | "medium" | "low"
    }}
  ]
}}

Rules:
- Extract only facts stated in the content. Do not invent.
- If the document mentions revenue/profit/orders/capex amounts, capture them.
- Use Indian crore scale for amounts (convert lakh->0.01, million->0.1, billion->100).
- Keep total under 400 words.
"""


def _doc_extract_text(path, max_pages=40):
    """Extract readable text from a downloaded PDF/txt/html file."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        try:
            import fitz
            parts = []
            doc = fitz.open(str(p))
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                parts.append(page.get_text())
            doc.close()
            return "\n".join(parts)
        except Exception:
            return ""
    if ext in (".txt", ".htm", ".html"):
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def _doc_chunk_text(text, max_chars=12000):
    text = text.strip()
    if not text:
        return []
    chunks = []
    while len(text) > max_chars:
        cut = text.rfind("\n", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def _doc_call_ollama(prompt):
    resp = httpx.post(
        "http://localhost:11434/api/generate",
        json={"model": "qwen2.5:3b", "prompt": prompt, "stream": False,
              "options": {"temperature": 0.2, "num_predict": 700}},
        timeout=120.0,
    )
    raw = resp.json().get("response", "")
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(raw[start:end])
    return None


def _analyze_doc_file(conn, row):
    """Analyze one company_files row. Returns (insights_list, had_content, headline, ann_date)."""
    cursor = conn.cursor()
    local_path = row["local_path"]
    if not local_path or not Path(local_path).exists():
        return [], False, None, None
    company_id = row["company_id"]

    cursor.execute("SELECT company_name, nse_symbol, bse_code FROM companies WHERE id=?", (company_id,))
    c = cursor.fetchone()
    if not c:
        return [], False, None, None
    company_name = c["company_name"]
    symbol = c["nse_symbol"] or c["bse_code"] or ""

    ann_date = headline = ""
    if row["announcement_id"]:
        cursor.execute("SELECT announcement_date, headline FROM announcements WHERE id=?", (row["announcement_id"],))
        a = cursor.fetchone()
        if a:
            ann_date = a["announcement_date"] or ""
            headline = (a["headline"] or "")[:300]

    text = _doc_extract_text(local_path)
    if not text or len(text) < 50:
        return [], False, headline or None, ann_date or None

    insights = []
    for chunk in _doc_chunk_text(text):
        prompt = DOC_PROMPT.format(
            company_name=company_name, symbol=symbol, kind=row.get("kind") or "general",
            ann_date=ann_date or "Unknown", headline=headline or "(none)", content=chunk)
        try:
            parsed = _doc_call_ollama(prompt)
            if parsed and isinstance(parsed.get("metrics"), list):
                for m in parsed["metrics"]:
                    if not isinstance(m, dict) or not m.get("metric"):
                        continue
                    insights.append({
                        "metric": str(m["metric"]).strip().lower(),
                        "summary": (m.get("summary") or "")[:600],
                        "amount": m.get("amount"),
                        "amount_text": (m.get("amount_text") or "")[:100],
                        "sentiment": (m.get("sentiment") or "neutral"),
                        "importance": (m.get("importance") or "medium"),
                    })
            time.sleep(0.5)
        except Exception:
            time.sleep(1)
    return insights, True, headline or None, ann_date or None


def _doc_batch_worker(company_id):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, company_id, announcement_id, kind, local_path, status
            FROM company_files
            WHERE company_id = ? AND status = 'done' AND analyzed = 0
            ORDER BY id
        """, (company_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        with _DOC_BATCH_LOCK:
            _DOC_BATCH["total"] = len(rows)
            _DOC_BATCH["processed"] = 0
            _DOC_BATCH["generated"] = 0
            _DOC_BATCH["failed"] = 0
            _DOC_BATCH["message"] = "Starting..."

        processed = generated = failed = 0
        for row in rows:
            with _DOC_BATCH_LOCK:
                if _DOC_BATCH["stop_requested"]:
                    _DOC_BATCH["message"] = "Stopped by user"
                    _DOC_BATCH["stop_requested"] = False
                    break
                _DOC_BATCH["current"] = f"{row['id']} ({row['kind'] or 'general'})"
            try:
                insights, had_content, headline, ann_date = _analyze_doc_file(conn, row)
                cursor.execute("UPDATE company_files SET analyzed=1 WHERE id=?", (row["id"],))
                for ins in insights:
                    try:
                        cursor.execute("""
                            INSERT OR IGNORE INTO document_insights
                            (company_id, file_id, announcement_id, metric, insight_date,
                             headline, summary, amount, amount_text, sentiment, importance)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (row["company_id"], row["id"], row["announcement_id"],
                              ins["metric"], ann_date, headline,
                              ins["summary"], ins["amount"], ins["amount_text"],
                              ins["sentiment"], ins["importance"]))
                        generated += 1
                    except Exception:
                        continue
                conn.commit()
            except Exception:
                failed += 1
            processed += 1
            with _DOC_BATCH_LOCK:
                _DOC_BATCH["processed"] = processed
                _DOC_BATCH["generated"] = generated
                _DOC_BATCH["failed"] = failed
                _DOC_BATCH["message"] = f"{processed}/{len(rows)} processed"

        with _DOC_BATCH_LOCK:
            _DOC_BATCH["running"] = False
            _DOC_BATCH["finished_at"] = time.time()
            if _DOC_BATCH["message"] != "Stopped by user":
                _DOC_BATCH["message"] = f"Done: {generated} insights from {processed} files, {failed} failed"
    finally:
        conn.close()


@app.post("/api/documents/batch/start")
def doc_batch_start(company_id: int):
    """Start background AI analysis of a company's downloaded documents."""
    with _DOC_BATCH_LOCK:
        if _DOC_BATCH["running"]:
            return {"ok": False, "message": "A document batch job is already running", "status": dict(_DOC_BATCH)}
        _DOC_BATCH["running"] = True
        _DOC_BATCH["company_id"] = company_id
        _DOC_BATCH["started_at"] = time.time()
        _DOC_BATCH["finished_at"] = None
        _DOC_BATCH["stop_requested"] = False
        _DOC_BATCH["message"] = "Starting..."

    t = threading.Thread(target=_doc_batch_worker, args=(company_id,), daemon=True)
    t.start()
    return {"ok": True, "message": "Document analysis started", "status": dict(_DOC_BATCH)}


@app.get("/api/documents/batch/status")
def doc_batch_status():
    with _DOC_BATCH_LOCK:
        return dict(_DOC_BATCH)


@app.post("/api/documents/batch/stop")
def doc_batch_stop():
    with _DOC_BATCH_LOCK:
        if not _DOC_BATCH["running"]:
            return {"ok": False, "message": "No document batch job is running"}
        _DOC_BATCH["stop_requested"] = True
    return {"ok": True, "message": "Stop requested"}


# ---- Concall Analysis Job (background, on-demand per company) ----
# Queues transcript announcements, downloads missing PDFs, and AI-summarizes
# each transcript via the shared pipeline (OpenCode Zen by default).
_CONCALL_BATCH = {
    "running": False,
    "company_id": None,
    "scope": "all",
    "total": 0,
    "processed": 0,
    "downloaded": 0,
    "done": 0,
    "failed": 0,
    "current": "",
    "started_at": None,
    "finished_at": None,
    "stop_requested": False,
    "message": "",
}
_CONCALL_BATCH_LOCK = threading.Lock()


def _concall_stop_requested():
    with _CONCALL_BATCH_LOCK:
        return _CONCALL_BATCH["stop_requested"]


def _concall_batch_worker(company_id):
    conn = get_db()
    try:
        cutoff = recent_cutoff(2)
        if company_id:
            companies = [company_id]
        else:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT company_id FROM announcements WHERE announcement_date >= ? "
                "AND attachment_url IS NOT NULL AND attachment_url != '' "
                "AND (lower(headline) LIKE '%concall%' OR lower(headline) LIKE '%transcript%' "
                "OR lower(headline) LIKE '%con. call%' OR lower(headline) LIKE '%earnings call%' "
                "OR category IN ('Earnings Call Transcript','Analyst / Investor Meet',"
                "'Analysts/Institutional Investor Meet/Con. Call Updates',"
                "'Schedule of Analysts/Institutional Investor Meet/Con. Call')) ORDER BY company_id",
                (cutoff,))
            companies = [r["company_id"] for r in cursor.fetchall()]

        with _CONCALL_BATCH_LOCK:
            _CONCALL_BATCH["total"] = len(companies)
            _CONCALL_BATCH["processed"] = 0
            _CONCALL_BATCH["downloaded"] = 0
            _CONCALL_BATCH["done"] = 0
            _CONCALL_BATCH["failed"] = 0
            _CONCALL_BATCH["message"] = f"Queuing transcripts for {len(companies)} companies..."

        processed = done = failed = 0
        for i, cid in enumerate(companies):
            if _concall_stop_requested():
                break
            with _CONCALL_BATCH_LOCK:
                _CONCALL_BATCH["current"] = str(cid)
            try:
                ensure_queued(conn, cid, cutoff)

                def _cc_on_progress(_row_id, status, _msg):
                    with _CONCALL_BATCH_LOCK:
                        _CONCALL_BATCH["processed"] += 1
                        if status == "done":
                            _CONCALL_BATCH["done"] += 1

                d, a, f = download_pending(conn, cid, cutoff, stop_cb=_concall_stop_requested)
                with _CONCALL_BATCH_LOCK:
                    _CONCALL_BATCH["downloaded"] += d
                n = analyze_downloaded(
                    conn, cid, stop_cb=_concall_stop_requested, on_progress=_cc_on_progress)
                cursor = conn.cursor()
                done_rows = cursor.execute(
                    "SELECT COUNT(*) n FROM concalls WHERE company_id=? AND status='done'",
                    (cid,)).fetchone()["n"]
                done += done_rows
                processed += n
            except Exception as e:
                failed += 1
                with _CONCALL_BATCH_LOCK:
                    _CONCALL_BATCH["message"] = f"Company {cid} error: {str(e)[:120]}"
            with _CONCALL_BATCH_LOCK:
                _CONCALL_BATCH["processed"] = i + 1
                _CONCALL_BATCH["done"] = done
                _CONCALL_BATCH["failed"] = failed
                _CONCALL_BATCH["message"] = f"{i+1}/{len(companies)} companies; {done} transcripts summarized"

        with _CONCALL_BATCH_LOCK:
            _CONCALL_BATCH["running"] = False
            _CONCALL_BATCH["finished_at"] = time.time()
            if _CONCALL_BATCH["message"] != "Stopped by user":
                _CONCALL_BATCH["message"] = f"Done: {done} transcripts summarized, {failed} company errors"
    finally:
        conn.close()


@app.post("/api/concalls/batch/start")
def concall_batch_start(company_id: Optional[int] = None):
    """Start background concall transcript download + AI summarization."""
    with _CONCALL_BATCH_LOCK:
        if _CONCALL_BATCH["running"]:
            return {"ok": False, "message": "A concall batch job is already running", "status": dict(_CONCALL_BATCH)}
        _CONCALL_BATCH["running"] = True
        _CONCALL_BATCH["company_id"] = company_id
        _CONCALL_BATCH["scope"] = "company" if company_id else "all"
        _CONCALL_BATCH["started_at"] = time.time()
        _CONCALL_BATCH["finished_at"] = None
        _CONCALL_BATCH["stop_requested"] = False
        _CONCALL_BATCH["message"] = "Starting..."

    t = threading.Thread(target=_concall_batch_worker, args=(company_id,), daemon=True)
    t.start()
    return {"ok": True, "message": "Concall analysis started", "status": dict(_CONCALL_BATCH)}


@app.get("/api/concalls/batch/status")
def concall_batch_status():
    with _CONCALL_BATCH_LOCK:
        return dict(_CONCALL_BATCH)


@app.post("/api/concalls/batch/stop")
def concall_batch_stop():
    with _CONCALL_BATCH_LOCK:
        if not _CONCALL_BATCH["running"]:
            return {"ok": False, "message": "No concall batch job is running"}
        _CONCALL_BATCH["stop_requested"] = True
        _CONCALL_BATCH["message"] = "Stop requested"
    return {"ok": True, "message": "Stop requested"}


@app.get("/api/companies/{company_id}/concalls")
def company_concalls(company_id: int):
    """Stored concall summaries for a company (analyzed + queued)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, announcement_id, call_date, quarter, title, transcript_path,
                  summary, guidance, management_views, qna_summary,
                  key_topics, key_numbers, sentiment, importance, status, error, analyzed_at
           FROM concalls WHERE company_id = ? ORDER BY call_date DESC""",
        (company_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    for r in rows:
        r["key_topics"] = _safe_json_list(r["key_topics"])
        r["key_numbers"] = _safe_json_list(r["key_numbers"])
    conn.close()
    return {"company_id": company_id, "concalls": rows}


def _safe_json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


@app.get("/api/companies/{company_id}/ai-insights")
def company_ai_insights(company_id: int, metric: Optional[str] = None, limit: int = 200):
    """Metric-wise, datewise AI insights timeline for a company."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT metric FROM ai_insights WHERE company_id = ? ORDER BY metric", (company_id,))
    metrics = [r["metric"] for r in cursor.fetchall()]

    if metric:
        cursor.execute("""
            SELECT id, announcement_id, metric, announcement_date, headline, summary,
                   amount, amount_text, sentiment, importance
            FROM ai_insights WHERE company_id = ? AND metric = ?
            ORDER BY announcement_date DESC, id DESC LIMIT ?
        """, (company_id, metric, limit))
    else:
        cursor.execute("""
            SELECT id, announcement_id, metric, announcement_date, headline, summary,
                   amount, amount_text, sentiment, importance
            FROM ai_insights WHERE company_id = ?
            ORDER BY announcement_date DESC, id DESC LIMIT ?
        """, (company_id, limit))
    insights = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"company_id": company_id, "metrics": metrics, "metric": metric, "insights": insights}


@app.get("/api/companies/{company_id}/document-insights")
def company_document_insights(company_id: int, metric: Optional[str] = None, limit: int = 200):
    """Metric-wise AI insights extracted from downloaded document content."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT metric FROM document_insights WHERE company_id = ? ORDER BY metric", (company_id,))
    metrics = [r["metric"] for r in cursor.fetchall()]

    if metric:
        cursor.execute("""
            SELECT id, file_id, announcement_id, metric, insight_date, headline, summary,
                   amount, amount_text, sentiment, importance, raw_json
            FROM document_insights WHERE company_id = ? AND metric = ?
            ORDER BY insight_date DESC, id DESC LIMIT ?
        """, (company_id, metric, limit))
    else:
        cursor.execute("""
            SELECT id, file_id, announcement_id, metric, insight_date, headline, summary,
                   amount, amount_text, sentiment, importance, raw_json
            FROM document_insights WHERE company_id = ?
            ORDER BY insight_date DESC, id DESC LIMIT ?
        """, (company_id, limit))
    insights = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"company_id": company_id, "metrics": metrics, "metric": metric, "insights": insights}


@app.get("/api/companies/{company_id}/corporate-events")
def company_corporate_events(company_id: int, limit: int = 100):
    """Structured corporate events: corporate actions (mirrored into
    announcements with event_key), board meetings, and result calendar."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM companies WHERE id = ?", (company_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")

    # Corporate actions: mirrored rows carry event_key = 'ca:*'
    cursor.execute("""
        SELECT id, exchange, category, subcategory, headline, announcement_date, ai_summary
        FROM announcements
        WHERE company_id = ? AND event_key IS NOT NULL
        ORDER BY announcement_date DESC, id DESC LIMIT ?
    """, (company_id, limit))
    corporate_actions = []
    for r in cursor.fetchall():
        row = dict(r)
        parsed = None
        if row.get("ai_summary"):
            try:
                parsed = json.loads(row["ai_summary"])
            except Exception:
                parsed = None
        corporate_actions.append({
            "id": row["id"],
            "exchange": row["exchange"],
            "category": row["category"],
            "subcategory": row["subcategory"],
            "headline": row["headline"],
            "date": row["announcement_date"],
            "ai_summary": parsed,
        })

    cursor.execute("""
        SELECT id, exchange, meeting_date, purpose, description
        FROM board_meetings WHERE company_id = ?
        ORDER BY meeting_date DESC LIMIT ?
    """, (company_id, limit))
    board_meetings = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT id, exchange, result_date, event, period
        FROM result_calendar WHERE company_id = ?
        ORDER BY result_date DESC LIMIT ?
    """, (company_id, limit))
    result_calendar = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT exchange, status, actions_found, actions_mirrored, board_meetings_found,
               result_calendar_found, annual_reports_found, error
        FROM enrich_status WHERE company_id = ? ORDER BY exchange
    """, (company_id,))
    enrichment = [dict(r) for r in cursor.fetchall()]

    # Announcement-type breakdown for the company page (full-DB counts).
    cursor.execute("""
        SELECT
          CASE
            WHEN event_key IS NOT NULL AND event_key LIKE 'ca:%' THEN 'corporate_action'
            WHEN LOWER(COALESCE(category,'')) LIKE '%result%'
                 OR LOWER(COALESCE(category,'')) LIKE '%financial%' THEN 'financial_result'
            WHEN LOWER(COALESCE(category,'')) LIKE '%board%' THEN 'board_meeting'
            WHEN LOWER(COALESCE(category,'')) LIKE '%credit%' THEN 'credit_rating'
            WHEN LOWER(COALESCE(category,'')) LIKE '%general%' THEN 'general'
            ELSE 'other'
          END AS family,
          COUNT(*) AS cnt
        FROM announcements
        WHERE company_id = ?
        GROUP BY family
    """, (company_id,))
    type_counts = {r["family"]: r["cnt"] for r in cursor.fetchall()}

    conn.close()
    return {
        "company_id": company_id,
        "corporate_actions": corporate_actions,
        "board_meetings": board_meetings,
        "result_calendar": result_calendar,
        "enrichment": enrichment,
        "type_counts": type_counts,
    }


@app.get("/api/companies/{company_id}/insights")
def company_insights(company_id: int, source: str = "all", metric: Optional[str] = None, limit: int = 200):
    """Unified metric-wise AI insights timeline combining announcement-level
    (ai_insights) and document-level (document_insights) analysis."""
    source = (source or "all").lower()
    if source not in ("all", "announcement", "document"):
        source = "all"

    conn = get_db()
    cursor = conn.cursor()
    metrics = set()
    rows = []

    if source in ("all", "announcement"):
        cursor.execute("SELECT DISTINCT metric FROM ai_insights WHERE company_id = ?", (company_id,))
        metrics.update(r["metric"] for r in cursor.fetchall())
        q = """
            SELECT id, announcement_id, metric, announcement_date, headline, summary,
                   amount, amount_text, sentiment, importance
            FROM ai_insights WHERE company_id = ?
        """
        args = [company_id]
        if metric:
            q += " AND metric = ?"
            args.append(metric)
        q += " ORDER BY announcement_date DESC, id DESC LIMIT ?"
        args.append(limit)
        cursor.execute(q, args)
        for r in cursor.fetchall():
            rows.append({
                "id": r["id"], "source": "announcement", "metric": r["metric"],
                "date": r["announcement_date"], "headline": r["headline"],
                "summary": r["summary"], "amount": r["amount"],
                "amount_text": r["amount_text"], "sentiment": r["sentiment"],
                "importance": r["importance"],
            })

    if source in ("all", "document"):
        cursor.execute("SELECT DISTINCT metric FROM document_insights WHERE company_id = ?", (company_id,))
        metrics.update(r["metric"] for r in cursor.fetchall())
        q = """
            SELECT id, file_id, announcement_id, metric, insight_date, headline, summary,
                   amount, amount_text, sentiment, importance
            FROM document_insights WHERE company_id = ?
        """
        args = [company_id]
        if metric:
            q += " AND metric = ?"
            args.append(metric)
        q += " ORDER BY insight_date DESC, id DESC LIMIT ?"
        args.append(limit)
        cursor.execute(q, args)
        for r in cursor.fetchall():
            rows.append({
                "id": r["id"], "source": "document", "metric": r["metric"],
                "date": r["insight_date"], "headline": r["headline"],
                "summary": r["summary"], "amount": r["amount"],
                "amount_text": r["amount_text"], "sentiment": r["sentiment"],
                "importance": r["importance"],
            })

    conn.close()
    seen = set()
    deduped = []
    for r in rows:
        key = (r["date"], r["metric"], (r["headline"] or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    deduped.sort(key=lambda x: (x["date"] or ""), reverse=True)
    return {
        "company_id": company_id,
        "source": source,
        "metrics": sorted(metrics),
        "metric": metric,
        "insights": deduped[:limit],
    }


@app.get("/api/companies/{company_id}/documents/status")
def company_documents_status(company_id: int):
    """Status of per-company document files (downloaded + analyzed)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT status, kind, COUNT(*) as cnt
        FROM company_files WHERE company_id = ?
        GROUP BY status, kind ORDER BY status, kind
    """, (company_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.execute("""
        SELECT COUNT(*) as total, SUM(CASE WHEN analyzed=1 THEN 1 ELSE 0 END) as analyzed
        FROM company_files WHERE company_id = ? AND status='done'
    """, (company_id,))
    summary = cursor.fetchone()
    conn.close()
    return {
        "company_id": company_id,
        "status_counts": rows,
        "downloaded": summary["total"] or 0,
        "analyzed": summary["analyzed"] or 0,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)