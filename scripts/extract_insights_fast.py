#!/usr/bin/env python3
"""Fast rule-based insight extraction. Ollama only for ambiguous cases."""
import sys
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db


# Pattern-based rules (order matters - first match wins)
RULES = [
    # Financial Results
    {
        "type": "financial",
        "subtype": "quarterly_results",
        "patterns": [
            r"Q[1-4]\s*(FY|fy)?\s*\d{2,4}.*(?:result|profit|revenue|loss|EBITDA|earnings)",
            r"(?:result|profit|revenue|loss|EBITDA|earnings).*Q[1-4]\s*(FY|fy)?\s*\d{2,4}",
            r"(?:unaudited|audited).*(?:result|financial)",
            r"(?:financial|result).*(?:quarterly|annual|half.?year|H[12])",
            r"(?:net.?profit|PAT|PBT|revenue|EBITDA).*(?:rise|fall|jump|drop|surge|decline|up|down|grow|increas|decreas).*\d+",
        ],
        "sentiment_patterns": {
            "positive": [r"(?:rise|jump|surge|up|grow|increas|profit|gain|higher|better|strong)", r"\+\d+"],
            "negative": [r"(?:fall|drop|decline|down|decreas|loss|lower|weak|weakness|miss)", r"-\d+"],
        },
    },
    # Dividend
    {
        "type": "dividend",
        "subtype": "dividend_declared",
        "patterns": [
            r"dividend",
            r"(?:interim|final|special).*pay",
            r"record.?date.*dividend",
        ],
        "sentiment_patterns": {
            "positive": [r"dividend"],
        },
        "amount_pattern": r"(?:Rs|INR|₹)\s*([\d.]+)\s*(?:per|/\s*share|each)",
    },
    # Orders
    {
        "type": "order",
        "subtype": "order_win",
        "patterns": [
            r"(?:bag|win|receiv|get|secure|confirm|land|book).*order",
            r"order.*(?:bag|win|receiv|get|secure|confirm|land|book)",
            r"(?:contract|project).*(?:bag|win|receiv|get|secure|award)",
            r"award.*(?:order|contract|project)",
            r"(?:LOI|work.?order|purchase.?order)",
        ],
        "sentiment_patterns": {
            "positive": [r"(?:bag|win|receiv|get|secure|confirm|land|book|award)"],
        },
        "amount_pattern": r"(?:Rs|INR|₹)\s*([\d,.]+)\s*(?:crore|Cr|lakh|L|million|billion)",
    },
    # Capex / Capacity
    {
        "type": "capex",
        "subtype": "capacity_expansion",
        "patterns": [
            r"(?:capex|capital.?expenditure|investment|expand|expansion)",
            r"capacity.?(?:addition|expand|increase|augment)",
            r"(?:new.?plant|greenfield|brownfield|new.?facility|new.?unit)",
            r"commencement.*(?:commercial|production|operation)",
        ],
        "sentiment_patterns": {
            "positive": [r"(?:expand|addition|new|grow)"],
        },
        "amount_pattern": r"(?:Rs|INR|₹)\s*([\d,.]+)\s*(?:crore|Cr|lakh|L|million|billion)",
    },
    # Credit Rating
    {
        "type": "credit_rating",
        "subtype": "rating_change",
        "patterns": [
            r"credit.?rating",
            r"(?:CRISIL|ICRA|CARE|Ind|BRICKWORK|SMERA|acuite|care).*rating",
            r"rating.*(?:upgrade|downgrade|reaffirm|stable|positive|negative|watch)",
        ],
        "sentiment_patterns": {
            "positive": [r"(?:upgrade|positive|reaffirm|stable|AAA|AA|A)"],
            "negative": [r"(?:downgrade|negative|watch|BBB|BB|B)"],
        },
    },
    # Investor Presentation / Guidance
    {
        "type": "guidance",
        "subtype": "management_guidance",
        "patterns": [
            r"investor.?presentation",
            r"(?:analyst|investor).*(?:meet|call|conference)",
            r"earnings.?call.*transcript",
            r"(?:guidance|outlook|forecast|target|expect|anticipat)",
            r"management.*(?:commentary|observation|view)",
        ],
        "sentiment_patterns": {
            "positive": [r"(?:optimistic|positive|strong|bullish|confident|growth)"],
            "negative": [r"(?:cautious|conservative|challeng|difficult|headwind|risk)"],
        },
    },
    # Board Meeting
    {
        "type": "management",
        "subtype": "board_decision",
        "patterns": [
            r"board.*(?:meeting|approve|consider|discuss|decide)",
            r"(?:outcome|minutes).*board",
            r"board.*(?:appoint|nominat|elevate|reshuffle)",
        ],
    },
    # Acquisition / M&A
    {
        "type": "acquisition",
        "subtype": "ma_activity",
        "patterns": [
            r"(?:acqui|merger|amalgam|demerger|stake|stake.?sale|buyout)",
            r"(?:purchase|acquire).*?(?:equity|share|stake|stok|business|asset)",
        ],
        "sentiment_patterns": {
            "positive": [r"(?:acqui|buyout|stake.?purchase)"],
        },
        "amount_pattern": r"(?:Rs|INR|₹)\s*([\d,.]+)\s*(?:crore|Cr|lakh|L|million|billion)",
    },
]

AMOUNT_PATTERNS = [
    (r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:crore|Cr)", lambda x: float(x.replace(",", ""))),
    (r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:lakh|L)", lambda x: float(x.replace(",", "")) / 100),
    (r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:million)", lambda x: float(x.replace(",", ""))),
    (r"([\d,]+(?:\.\d+)?)\s*(?:crore|Cr)", lambda x: float(x.replace(",", ""))),
]

PERIOD_PATTERN = r"(Q[1-4]\s*(?:FY|fy)?\s*\d{2,4}|FY\s*\d{2,4}|H[12]\s*(?:FY|fy)?\s*\d{2,4}|(?:\d{4}[-/]\d{4}|\d{4}))"


def extract_amount(text):
    for pattern, converter in AMOUNT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return converter(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def extract_period(text):
    match = re.search(PERIOD_PATTERN, text, re.IGNORECASE)
    return match.group(1) if match else None


def detect_sentiment(text, rule):
    text_lower = text.lower()
    sentiment_patterns = rule.get("sentiment_patterns", {})

    pos_count = sum(1 for p in sentiment_patterns.get("positive", []) if re.search(p, text_lower))
    neg_count = sum(1 for p in sentiment_patterns.get("negative", []) if re.search(p, text_lower))

    if pos_count > neg_count:
        return "positive"
    elif neg_count > pos_count:
        return "negative"
    return "neutral"


def extract_insight(headline, description, category):
    text = f"{headline or ''} {description or ''} {category or ''}"
    text_lower = text.lower()

    for rule in RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, text_lower):
                amount = extract_amount(text)
                period = extract_period(text)
                sentiment = detect_sentiment(text, rule)

                # Generate summary
                summary = headline[:150] if headline else description[:150]
                summary = summary.replace("\n", " ").strip()

                return {
                    "insight_type": rule["type"],
                    "insight_subtype": rule.get("subtype"),
                    "sentiment": sentiment,
                    "amount": amount,
                    "period": period,
                    "summary": summary[:200],
                    "confidence": 0.85,
                }

    return {
        "insight_type": "general",
        "insight_subtype": None,
        "sentiment": "neutral",
        "amount": None,
        "period": None,
        "summary": (headline or description or "")[:200],
        "confidence": 0.3,
    }


def main():
    conn = get_db()
    cursor = conn.cursor()

    # Get all announcements without insights
    cursor.execute("""
        SELECT a.id, a.company_id, a.category, a.headline, a.description, a.announcement_date
        FROM announcements a
        WHERE a.id NOT IN (SELECT announcement_id FROM announcement_insights WHERE announcement_id IS NOT NULL)
        ORDER BY a.announcement_date DESC
    """)
    announcements = [dict(r) for r in cursor.fetchall()]
    conn.close()

    print(f"Found {len(announcements)} announcements to process", flush=True)

    if not announcements:
        print("No announcements to process")
        return

    conn = get_db()
    cursor = conn.cursor()
    success = 0
    type_counts = {}

    for i, ann in enumerate(announcements):
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
            t = insight['insight_type']
            type_counts[t] = type_counts.get(t, 0) + 1
        except Exception:
            continue

        if (i + 1) % 1000 == 0:
            conn.commit()
            print(f"  Progress: {i+1}/{len(announcements)} — {success} extracted — types: {type_counts}", flush=True)

    conn.commit()
    conn.close()

    print(f"\n=== Extraction Complete ===", flush=True)
    print(f"Total processed: {len(announcements)}", flush=True)
    print(f"Successfully extracted: {success}", flush=True)
    print(f"By type:", flush=True)
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}", flush=True)


if __name__ == "__main__":
    main()
