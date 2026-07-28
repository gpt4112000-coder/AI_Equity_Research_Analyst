#!/usr/bin/env python3
"""Extract structured insights from announcements using Ollama qwen2.5:3b."""
import sys
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:3b"


EXTRACTION_PROMPT = """Extract structured data from this stock market announcement. Return ONLY valid JSON, no other text.

Announcement:
Company: {company}
Category: {category}
Headline: {headline}
Description: {description}

Return JSON with these fields:
{{
  "insight_type": "one of: guidance, capex, order, financial, dividend, credit_rating, management, regulatory, general",
  "insight_subtype": "specific subtype like revenue_guidance, capex_guidance, order_win, quarterly_results, dividend_declared, rating_upgrade, etc.",
  "sentiment": "positive, negative, or neutral",
  "amount": null or numeric value in crores if mentioned,
  "period": null or financial period like Q1-FY27, FY26, H2-FY26 etc,
  "summary": "one line summary of the key insight",
  "confidence": 0.0 to 1.0
}}

Rules:
- If announcement is about financial results, set insight_type=financial
- If about orders/contracts, set insight_type=order and extract amount if mentioned
- If about capex/investment/capacity expansion, set insight_type=capex
- If about dividend, set insight_type=dividend and extract amount
- If about credit rating, set insight_type=credit_rating
- If about guidance/forecast/outlook, set insight_type=guidance
- If about board meeting/outcome, set insight_type=management
- If about investor presentation/analyst meet, set insight_type=guidance (contains guidance)
- If not important, set insight_type=general with low confidence
- Extract any rupee amounts mentioned (convert to crores)
- summary should be max 15 words
"""


def call_ollama(prompt, timeout=30):
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 200}
    }).encode("utf-8")

    try:
        req = urllib.request.Request(OLLAMA_URL, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "")
    except (urllib.error.URLError, TimeoutError) as e:
        return None


def parse_json_response(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def extract_insights_batch(announcements, batch_size=50):
    conn = get_db()
    cursor = conn.cursor()
    processed = 0
    success = 0
    failed = 0

    for i in range(0, len(announcements), batch_size):
        batch = announcements[i:i+batch_size]

        for ann in batch:
            ann_id = ann['id']
            company_id = ann['company_id']
            headline = ann['headline'] or ''
            description = ann['description'] or ''
            category = ann['category'] or ''
            company_name = ann.get('company_name', '')

            text_to_process = f"{headline}. {description}"[:500]

            prompt = EXTRACTION_PROMPT.format(
                company=company_name,
                category=category,
                headline=headline[:200],
                description=description[:300],
            )

            response = call_ollama(prompt)
            if not response:
                failed += 1
                processed += 1
                continue

            parsed = parse_json_response(response)
            if not parsed:
                failed += 1
                processed += 1
                continue

            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO announcement_insights
                    (announcement_id, company_id, insight_type, insight_subtype,
                     headline, summary, sentiment, amount, period, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ann_id, company_id,
                    parsed.get('insight_type', 'general'),
                    parsed.get('insight_subtype'),
                    headline[:500],
                    parsed.get('summary', ''),
                    parsed.get('sentiment', 'neutral'),
                    parsed.get('amount'),
                    parsed.get('period'),
                    parsed.get('confidence', 0.5),
                ))
                success += 1
            except Exception as e:
                failed += 1

            processed += 1

        conn.commit()
        print(f"  Progress: {processed}/{len(announcements)} processed, {success} success, {failed} failed", flush=True)

    conn.close()
    return success, failed


def main():
    conn = get_db()
    cursor = conn.cursor()

    # Get announcements that don't have insights yet
    cursor.execute("""
        SELECT a.id, a.company_id, a.category, a.headline, a.description, a.announcement_date,
               c.company_name, c.nse_symbol
        FROM announcements a
        JOIN companies c ON a.company_id = c.id
        WHERE a.id NOT IN (SELECT announcement_id FROM announcement_insights WHERE announcement_id IS NOT NULL)
        AND a.category IN (
            'Financial Results', 'Outcome of Board Meeting', 'Dividend',
            'Investor Presentation', 'Analysts/Institutional Investor Meet/Con. Call Updates',
            'Award of Order / Receipt of Order', 'Bagging/Receiving of orders/contracts',
            'Awarding of order(s)/contract(s)', 'Press Release', 'Press Release / Media Release',
            'Credit Rating', 'Credit Rating- New', 'Credit Rating- Revision',
            'Capacity addition', 'Commencement of commercial production/operations',
            'Preferential Issue', 'Updates', 'Acquisition', 'Spurt in Volume'
        )
        ORDER BY a.announcement_date DESC
    """)
    announcements = [dict(r) for r in cursor.fetchall()]
    conn.close()

    print(f"Found {len(announcements)} announcements to process with Ollama", flush=True)

    if not announcements:
        print("No announcements to process")
        return

    print(f"Starting extraction...", flush=True)

    success, failed = extract_insights_batch(announcements)

    print(f"\n=== Extraction Complete ===")
    print(f"Success: {success}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
