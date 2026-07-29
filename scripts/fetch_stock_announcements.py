#!/usr/bin/env python3
"""
Fetch announcements for any stock from BSE/NSE APIs.
Tries NSE first, falls back to BSE.
Supports timeline: 1y, 3y, 5y, 10y
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path("/home/ubuntu/FinEng")))

from data.storage.db import get_db, init_db

# Timeline options in years
TIMELINE_OPTIONS = {
    "1y": 1,
    "3y": 3,
    "5y": 5,
    "10y": 10,
}


def classify_sector(industry):
    """Map industry to sector category."""
    if not industry:
        return "other"
    ind = industry.lower()
    if any(x in ind for x in ["bank", "finance", "nbfc", "housing", "insurance"]):
        return "banking_finance"
    if any(x in ind for x in ["software", "it ", "computer", "technology", "telecom"]):
        return "it_telecom"
    if any(x in ind for x in ["pharma", "drug", "medical", "health", "hospital"]):
        return "pharma_healthcare"
    if any(x in ind for x in ["chemical", "fertilizer", "pesticide"]):
        return "chemicals"
    if any(x in ind for x in ["auto", "tyre", "rubber", "parts"]):
        return "auto_ancillary"
    if any(x in ind for x in ["textile", "cotton", "jute", "silk", "wool"]):
        return "textiles"
    if any(x in ind for x in ["power", "energy", "oil", "gas", "coal", "uranium"]):
        return "energy"
    if any(x in ind for x in ["metal", "steel", "iron", "aluminium", "copper", "zinc"]):
        return "metals"
    if any(x in ind for x in ["cement", "brick", "glass", "ceramic"]):
        return "cement_building"
    if any(x in ind for x in ["real estate", "realty", "construction", "infra"]):
        return "realty_infra"
    if any(x in ind for x in ["food", "beverage", "tobacco", "consumer"]):
        return "consumer"
    if any(x in ind for x in ["paper", "packaging", "printing"]):
        return "paper_packaging"
    if any(x in ind for x in ["jewel", "diamond", "gold", "silver"]):
        return "jewellery"
    if any(x in ind for x in ["media", "entertainment", "publishing"]):
        return "media"
    if any(x in ind for x in ["agri", "farm", "fishery", "dairy"]):
        return "agriculture"
    return "other"


def check_existing(symbol):
    """Check if stock already exists in fetched_stock table."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, company_id, exchange, announcement_count FROM fetched_stock WHERE symbol = ?",
        (symbol.upper(),)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "exists": True,
            "company_id": row["company_id"],
            "exchange": row["exchange"],
            "count": row["announcement_count"],
        }
    return {"exists": False}


def try_nse_fetch(symbol, from_date, to_date):
    """Try fetching from NSE API."""
    try:
        from NseIndiaApi.src.nse import NSE

        with NSE("/tmp/nse_fetch") as nse:
            # Get meta info first
            try:
                meta = nse.equityMetaInfo(symbol)
                company_name = meta.get("info", {}).get("companyName", symbol)
                industry = meta.get("info", {}).get("industry", "")
                isin = meta.get("isin", "")
            except Exception:
                company_name = symbol
                industry = ""
                isin = ""

            # Fetch announcements
            announcements = []
            chunk_size = 90  # NSE limit per request
            current_start = from_date

            while current_start <= to_date:
                current_end = current_start + timedelta(days=chunk_size - 1)
                if current_end > to_date:
                    current_end = to_date

                try:
                    data = nse.announcements(
                        index="equities",
                        symbol=symbol,
                        from_date=current_start,
                        to_date=current_end,
                    )
                    if isinstance(data, list):
                        announcements.extend(data)
                    elif isinstance(data, dict) and "data" in data:
                        announcements.extend(data["data"])
                except Exception:
                    pass

                current_start = current_end + timedelta(days=1)

            return {
                "success": True,
                "exchange": "NSE",
                "nse_symbol": symbol,
                "bse_code": None,
                "company_name": company_name,
                "industry": industry,
                "isin": isin,
                "announcements": announcements,
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def try_bse_fetch(symbol, from_date, to_date):
    """Try fetching from BSE API."""
    try:
        from BseIndiaApi.src.bse import BSE

        with BSE("/tmp/bse_fetch") as bse:
            # Try to get scrip code from symbol
            try:
                scripcode = bse.getScripCode(symbol)
            except Exception:
                # Maybe symbol is already a scrip code
                scripcode = symbol

            # Get meta info
            try:
                company_name = bse.getScripName(scripcode)
            except Exception:
                company_name = symbol

            # Fetch announcements
            announcements = []
            page_no = 1
            total_rows = None

            while True:
                try:
                    data = bse.announcements(
                        scripcode=scripcode,
                        from_date=from_date,
                        to_date=to_date,
                        page_no=page_no,
                    )

                    if "Table" in data:
                        announcements.extend(data["Table"])

                    if "Table1" in data and data["Table1"]:
                        total_rows = data["Table1"][0].get("ROWCNT", 0)

                    if not data.get("Table") or (total_rows and len(announcements) >= total_rows):
                        break

                    page_no += 1
                except Exception:
                    break

            return {
                "success": True,
                "exchange": "BSE",
                "nse_symbol": None,
                "bse_code": scripcode,
                "company_name": company_name,
                "industry": "",
                "isin": "",
                "announcements": announcements,
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def store_company(symbol, exchange, bse_code, nse_symbol, company_name, industry):
    """Store or update company in DB."""
    conn = get_db()
    cursor = conn.cursor()
    sector = classify_sector(industry)

    # Check if company already exists
    if nse_symbol:
        cursor.execute("SELECT id FROM companies WHERE nse_symbol = ?", (nse_symbol,))
    elif bse_code:
        cursor.execute("SELECT id FROM companies WHERE bse_code = ?", (bse_code,))
    else:
        cursor.execute("SELECT id FROM companies WHERE company_name = ?", (company_name,))

    row = cursor.fetchone()
    if row:
        company_id = row["id"]
    else:
        cursor.execute("""
            INSERT INTO companies (bse_code, nse_symbol, company_name, sector, industry, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (bse_code, nse_symbol, company_name, sector, industry))
        company_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return company_id


def store_announcements(company_id, exchange, announcements, from_date, to_date):
    """Store announcements in DB."""
    conn = get_db()
    cursor = conn.cursor()
    stored = 0

    for ann in announcements:
        try:
            if exchange == "NSE":
                headline = ann.get("desc") or ann.get("sm_name") or ""
                description = ann.get("attchmntText") or ann.get("desc") or ""
                category = ann.get("categoryName") or "General"
                ann_date = ann.get("sort_date") or ann.get("an_dt") or ""
                attachment = ann.get("attchmntFile") or ""
            else:  # BSE
                headline = ann.get("HEADLINE") or ""
                description = ann.get("NEWSSUB") or ann.get("HEADLINE") or ""
                category = ann.get("CATEGORYNAME") or "General"
                ann_date = ann.get("DT_TM") or ""
                attachment = ann.get("NSURL") or ""

            # Parse date
            if ann_date:
                try:
                    if "T" in ann_date:
                        date_part = ann_date[:10]
                    elif "-" in ann_date:
                        date_part = ann_date[:10]
                    else:
                        date_part = ann_date[:10]
                except Exception:
                    date_part = ""
            else:
                date_part = ""

            # Skip if no date
            if not date_part:
                continue

            # Check for duplicate
            cursor.execute("""
                SELECT id FROM announcements
                WHERE company_id = ? AND headline = ? AND announcement_date = ?
                LIMIT 1
            """, (company_id, headline[:500], date_part))
            if cursor.fetchone():
                continue

            cursor.execute("""
                INSERT INTO announcements
                (company_id, exchange, category, headline, description,
                 attachment_url, announcement_date, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                company_id, exchange, category, headline[:500], description,
                attachment, date_part, json.dumps(ann, default=str)[:5000],
            ))
            stored += 1
        except Exception:
            continue

    conn.commit()
    conn.close()
    return stored


def run_insight_extraction(company_id):
    """Run rule-based insight extraction for new announcements."""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
        from extract_insights_fast import extract_insight
        from data.storage.db import get_db

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT a.id, a.company_id, a.category, a.headline, a.description
            FROM announcements a
            WHERE a.company_id = ? AND a.id NOT IN (
                SELECT announcement_id FROM announcement_insights
                WHERE announcement_id IS NOT NULL
            )
        """, (company_id,))
        announcements = [dict(r) for r in cursor.fetchall()]

        if not announcements:
            conn.close()
            return 0

        success = 0
        for ann in announcements:
            insight = extract_insight(ann['headline'], ann['description'], ann['category'])
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO announcement_insights
                    (announcement_id, company_id, insight_type, insight_subtype,
                     headline, summary, sentiment, amount, period, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ann['id'], ann['company_id'],
                    insight['insight_type'], insight['insight_subtype'],
                    (ann['headline'] or '')[:500],
                    insight['summary'], insight['sentiment'],
                    insight['amount'], insight['period'], insight['confidence'],
                ))
                success += 1
            except Exception:
                continue

        conn.commit()
        conn.close()
        return success
    except Exception as e:
        print(f"  Insight extraction error: {e}")
        return 0


def build_company_summary(company_id):
    """Build company summary from insights."""
    try:
        from build_company_summaries import build_summaries
        from data.storage.db import get_db

        conn = get_db()
        cursor = conn.cursor()

        # Count announcements
        cursor.execute("SELECT COUNT(*) FROM announcements WHERE company_id = ?", (company_id,))
        total_ann = cursor.fetchone()[0]

        # Get insights
        cursor.execute("""
            SELECT insight_type, sentiment, summary, amount, headline
            FROM announcement_insights WHERE company_id = ?
            ORDER BY extracted_at DESC
        """, (company_id,))
        insights = [dict(r) for r in cursor.fetchall()]

        from collections import Counter
        by_type = {}
        for ins in insights:
            t = ins['insight_type']
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(ins)

        # Build summary fields
        has_guidance = 1 if 'guidance' in by_type else 0
        guidance_text = by_type['guidance'][0].get('summary', '')[:200] if 'guidance' in by_type else None

        has_capex = 1 if 'capex' in by_type else 0
        capex_text = None
        if 'capex' in by_type:
            amounts = [i['amount'] for i in by_type['capex'] if i.get('amount')]
            capex_text = f"Total capex: {sum(amounts):.0f} Cr" if amounts else by_type['capex'][0].get('summary', '')[:200]

        has_orders = 1 if 'order' in by_type else 0
        order_text = None
        if 'order' in by_type:
            amounts = [i['amount'] for i in by_type['order'] if i.get('amount')]
            order_text = f"Total orders: {sum(amounts):.0f} Cr ({len(amounts)} announcements)" if amounts else f"{len(by_type['order'])} order announcements"

        has_financials = 1 if 'financial' in by_type else 0
        financial_text = f"{len(by_type['financial'])} financial announcements" if 'financial' in by_type else None

        has_dividend = 1 if 'dividend' in by_type else 0
        dividend_text = None
        if 'dividend' in by_type:
            amounts = [i['amount'] for i in by_type['dividend'] if i.get('amount')]
            dividend_text = f"Dividend: {amounts[0]:.2f} per share" if amounts else "Dividend announced"

        sentiments = Counter(i['sentiment'] for i in insights if i.get('sentiment'))
        themes = list(by_type.keys())[:5]

        cursor.execute("SELECT MAX(announcement_date) FROM announcements WHERE company_id = ?", (company_id,))
        latest_date = cursor.fetchone()[0]

        cursor.execute("""
            INSERT OR REPLACE INTO company_summary
            (company_id, total_announcements,
             has_guidance, guidance_text, has_capex_news, capex_text,
             has_order_news, order_text, has_financial_results, financial_results_text,
             has_dividend_news, dividend_text,
             sentiment_positive, sentiment_negative, sentiment_neutral,
             key_themes, latest_announcement_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            company_id, total_ann,
            has_guidance, guidance_text, has_capex, capex_text,
            has_orders, order_text, has_financials, financial_text,
            has_dividend, dividend_text,
            sentiments.get('positive', 0), sentiments.get('negative', 0), sentiments.get('neutral', 0),
            json.dumps(themes), latest_date,
        ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  Summary build error: {e}")
        return False


def fetch_stock(symbol, years=5):
    """
    Main function: Fetch announcements for a stock.
    Returns dict with company_id and status.
    """
    symbol = symbol.upper().strip()
    years = int(years)

    print(f"\n{'='*60}")
    print(f"FETCHING: {symbol} ({years} years)")
    print(f"{'='*60}")

    # Check if already fetched
    existing = check_existing(symbol)
    if existing["exists"]:
        print(f"  Already in DB: company_id={existing['company_id']}, {existing['count']} announcements")
        return {
            "status": "exists",
            "company_id": existing["company_id"],
            "exchange": existing["exchange"],
            "count": existing["count"],
            "message": f"Stock already fetched from {existing['exchange']}",
        }

    # Calculate date range
    to_date = datetime.now()
    from_date = to_date - relativedelta(years=years)

    print(f"  Date range: {from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}")

    # Try NSE first
    print(f"  Trying NSE...")
    nse_result = try_nse_fetch(symbol, from_date, to_date)

    if nse_result["success"] and nse_result["announcements"]:
        print(f"  NSE: Found {len(nse_result['announcements'])} announcements")
        result = nse_result
    else:
        print(f"  NSE failed or no data, trying BSE...")
        bse_result = try_bse_fetch(symbol, from_date, to_date)

        if bse_result["success"] and bse_result["announcements"]:
            print(f"  BSE: Found {len(bse_result['announcements'])} announcements")
            result = bse_result
        else:
            return {
                "status": "error",
                "message": f"Could not find {symbol} on NSE or BSE",
            }

    # Store company
    company_id = store_company(
        symbol,
        result["exchange"],
        result["bse_code"],
        result["nse_symbol"],
        result["company_name"],
        result["industry"],
    )
    print(f"  Company ID: {company_id}")

    # Store announcements
    stored = store_announcements(
        company_id,
        result["exchange"],
        result["announcements"],
        from_date,
        to_date,
    )
    print(f"  Announcements stored: {stored}")

    # Record in fetched_stock
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO fetched_stock
        (symbol, exchange, bse_code, nse_symbol, company_name, company_id,
         date_from, date_to, announcement_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol, result["exchange"], result["bse_code"], result["nse_symbol"],
        result["company_name"], company_id,
        from_date.strftime('%Y-%m-%d'), to_date.strftime('%Y-%m-%d'), stored,
    ))
    conn.commit()
    conn.close()

    # Run insight extraction
    print(f"  Running insight extraction...")
    insights = run_insight_extraction(company_id)
    print(f"  Insights extracted: {insights}")

    # Build company summary
    print(f"  Building company summary...")
    build_company_summary(company_id)
    print(f"  Summary built")

    return {
        "status": "success",
        "company_id": company_id,
        "exchange": result["exchange"],
        "count": stored,
        "company_name": result["company_name"],
        "message": f"Fetched {stored} announcements from {result['exchange']}",
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fetch_stock_announcements.py <SYMBOL> [YEARS]")
        print("Example: python fetch_stock_announcements.py KPITTECH 5")
        sys.exit(1)

    symbol = sys.argv[1]
    years = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    result = fetch_stock(symbol, years)
    print(f"\nResult: {json.dumps(result, indent=2)}")
