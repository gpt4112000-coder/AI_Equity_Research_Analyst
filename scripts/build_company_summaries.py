#!/usr/bin/env python3
"""Build per-company summary from announcement insights."""
import sys
import json
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db


def build_summaries():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT id, nse_symbol, company_name FROM companies")
    companies = cursor.fetchall()
    print(f"Building summaries for {len(companies)} companies...")

    for company_id, symbol, name in companies:
        # Count announcements
        cursor.execute("SELECT COUNT(*) FROM announcements WHERE company_id = ?", (company_id,))
        total_ann = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM announcements
            WHERE company_id = ? AND category IN (
                'Financial Results', 'Outcome of Board Meeting', 'Dividend',
                'Investor Presentation', 'Analysts/Institutional Investor Meet/Con. Call Updates',
                'Award of Order / Receipt of Order', 'Press Release'
            )
        """, (company_id,))
        important_ann = cursor.fetchone()[0]

        # Get insights
        cursor.execute("""
            SELECT insight_type, insight_subtype, sentiment, summary, amount, period, headline
            FROM announcement_insights WHERE company_id = ?
            ORDER BY extracted_at DESC
        """, (company_id,))
        insights = [dict(r) for r in cursor.fetchall()]

        # Aggregate by type
        by_type = {}
        for ins in insights:
            t = ins['insight_type']
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(ins)

        # Guidance
        has_guidance = 0
        guidance_text = None
        if 'guidance' in by_type:
            has_guidance = 1
            latest = by_type['guidance'][0]
            guidance_text = latest.get('summary') or latest.get('headline', '')[:200]

        # Capex
        has_capex = 0
        capex_text = None
        if 'capex' in by_type:
            has_capex = 1
            amounts = [i['amount'] for i in by_type['capex'] if i.get('amount')]
            if amounts:
                capex_text = f"Total capex mentions: {sum(amounts):.0f} Cr"
            else:
                capex_text = by_type['capex'][0].get('summary', '')[:200]

        # Orders
        has_orders = 0
        order_text = None
        if 'order' in by_type:
            has_orders = 1
            amounts = [i['amount'] for i in by_type['order'] if i.get('amount')]
            if amounts:
                order_text = f"Total orders: {sum(amounts):.0f} Cr ({len(amounts)} announcements)"
            else:
                order_text = f"{len(by_type['order'])} order announcements"

        # Financial results
        has_financials = 0
        financial_text = None
        if 'financial' in by_type:
            has_financials = 1
            financial_text = f"{len(by_type['financial'])} financial announcements"

        # Dividend
        has_dividend = 0
        dividend_text = None
        if 'dividend' in by_type:
            has_dividend = 1
            amounts = [i['amount'] for i in by_type['dividend'] if i.get('amount')]
            if amounts:
                dividend_text = f"Dividend: {amounts[0]:.2f} per share"
            else:
                dividend_text = "Dividend announced"

        # Credit rating
        has_credit = 0
        credit_text = None
        if 'credit_rating' in by_type:
            has_credit = 1
            credit_text = by_type['credit_rating'][0].get('summary', '')[:200]

        # Sentiment
        sentiments = Counter(i['sentiment'] for i in insights if i.get('sentiment'))

        # Key themes
        themes = list(by_type.keys())[:5]

        # Latest announcement
        cursor.execute("""
            SELECT MAX(announcement_date) FROM announcements WHERE company_id = ?
        """, (company_id,))
        latest_date = cursor.fetchone()[0]

        cursor.execute("""
            INSERT OR REPLACE INTO company_summary
            (company_id, total_announcements, important_announcements,
             has_guidance, guidance_text, has_capex_news, capex_text,
             has_order_news, order_text, has_financial_results, financial_results_text,
             has_dividend_news, dividend_text, has_credit_rating, credit_rating_text,
             sentiment_positive, sentiment_negative, sentiment_neutral,
             key_themes, latest_announcement_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            company_id, total_ann, important_ann,
            has_guidance, guidance_text, has_capex, capex_text,
            has_orders, order_text, has_financials, financial_text,
            has_dividend, dividend_text, has_credit, credit_text,
            sentiments.get('positive', 0), sentiments.get('negative', 0), sentiments.get('neutral', 0),
            json.dumps(themes), latest_date,
        ))

    conn.commit()
    conn.close()
    print("Done building summaries.")


if __name__ == "__main__":
    build_summaries()
