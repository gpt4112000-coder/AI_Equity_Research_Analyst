import json
from datetime import datetime
from collections import Counter, defaultdict
from data.storage.db import get_db


def build_company_profile(company_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
    company = dict(cursor.fetchone())

    cursor.execute("""
        SELECT category, COUNT(*) as cnt, 
               MIN(announcement_date) as first_seen, 
               MAX(announcement_date) as last_seen
        FROM announcements 
        WHERE company_id = ? 
        GROUP BY category 
        ORDER BY cnt DESC
    """, (company_id,))
    categories = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT headline, description, announcement_date, category
        FROM announcements 
        WHERE company_id = ? AND category IN ('Financial Results', 'Board Meeting', 'Earnings Call Transcript', 'Investor Presentation')
        ORDER BY announcement_date DESC
        LIMIT 20
    """, (company_id,))
    key_announcements = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT guidance, management_views, qna_summary, call_date
        FROM concalls 
        WHERE company_id = ? 
        ORDER BY call_date DESC
        LIMIT 5
    """, (company_id,))
    concalls = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT * FROM financials 
        WHERE company_id = ? 
        ORDER BY report_date DESC 
        LIMIT 4
    """, (company_id,))
    financials = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT close_price, trade_date 
        FROM price_history 
        WHERE company_id = ? 
        ORDER BY trade_date DESC 
        LIMIT 200
    """, (company_id,))
    prices = [dict(r) for r in cursor.fetchall()]

    conn.close()

    profile = {
        "company": company,
        "announcement_categories": categories,
        "key_announcements": key_announcements,
        "recent_concalls": concalls,
        "financials": financials,
        "price_data": prices,
        "summary": _generate_summary(company, categories, key_announcements, concalls, financials),
    }
    return profile


def _generate_summary(company, categories, key_announcements, concalls, financials):
    summary = {
        "total_announcements": sum(c["cnt"] for c in categories),
        "top_categories": [c["category"] for c in categories[:5]],
        "announcement_frequency": _calc_frequency(categories),
        "key_themes": _extract_themes(key_announcements),
        "management_outlook": _extract_outlook(concalls),
        "financial_health": _assess_financials(financials),
    }
    return summary


def _calc_frequency(categories):
    if not categories:
        return "No data"
    total = sum(c["cnt"] for c in categories)
    first = min(c["first_seen"] for c in categories)
    last = max(c["last_seen"] for c in categories)
    if first == last:
        return f"{total} announcements on {first}"
    return f"{total} announcements from {first} to {last}"


def _extract_themes(announcements):
    themes = []
    for ann in announcements:
        cat = ann.get("category", "")
        if cat == "Financial Results":
            themes.append("Earnings")
        elif cat == "Board Meeting":
            themes.append("Corporate Action")
        elif cat == "Dividend":
            themes.append("Dividend")
        elif "Order" in cat:
            themes.append("Business Growth")
    return list(set(themes))


def _extract_outlook(concalls):
    if not concalls:
        return "No concall data available"
    latest = concalls[0]
    return latest.get("management_views", "No outlook data")


def _assess_financials(financials):
    if not financials:
        return "No financial data available"
    latest = financials[0]
    assessment = {}
    if latest.get("revenue"):
        assessment["revenue"] = f"₹{latest['revenue']/1e7:.0f} Cr"
    if latest.get("net_profit"):
        assessment["net_profit"] = f"₹{latest['net_profit']/1e7:.0f} Cr"
    if latest.get("operating_margin"):
        assessment["operating_margin"] = f"{latest['operating_margin']*100:.1f}%"
    if latest.get("total_debt") and latest.get("total_equity"):
        assessment["debt_equity"] = f"{latest['total_debt']/latest['total_equity']:.2f}"
    return assessment
