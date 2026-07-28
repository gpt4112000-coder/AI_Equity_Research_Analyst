#!/usr/bin/env python3
"""
Batch process announcements through Ollama to generate AI summaries.
Stores results in announcements.ai_summary column.
"""
import sys
import sqlite3
import httpx
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
DB_PATH = Path(__file__).parent.parent / "backend" / "data" / "equity_research.db"
LOG_PATH = Path("/tmp/batch_ai_progress.log")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def generate_summary(headline, description, category, exchange):
    prompt = f"""Summarize this Indian stock market announcement in 2-3 sentences. Be factual with numbers, dates, amounts.

Category: {category}
Exchange: {exchange}
Title: {headline}
Details: {(description or '')[:500]}

Summary:"""
    try:
        response = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": "qwen2.5:3b", "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.2, "num_predict": 150}},
            timeout=60.0
        )
        return response.json().get("response", "").strip()
    except Exception as e:
        return f"Error: {str(e)[:100]}"


def main():
    # Clear old log
    LOG_PATH.write_text("")

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM announcements WHERE ai_summary IS NULL OR ai_summary = ''")
    total = cursor.fetchone()[0]
    log(f"Total unprocessed: {total}")

    batch_num = 0
    while True:
        cursor.execute("""
            SELECT id, headline, description, category, exchange
            FROM announcements
            WHERE ai_summary IS NULL OR ai_summary = ''
            ORDER BY announcement_date DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if not row:
            log("All done!")
            break

        batch_num += 1
        ann_id, headline, desc, cat, exch = row
        summary = generate_summary(headline, desc, cat, exch)

        if not summary.startswith("Error:"):
            cursor.execute("UPDATE announcements SET ai_summary = ? WHERE id = ?", (summary, ann_id))
            conn.commit()

        remaining = total - batch_num
        log(f"[{batch_num}/{total}] {cat}: {(headline or '')[:50]}... -> {summary[:80]}... (remaining: {remaining})")

        # Small delay to not overload Ollama
        time.sleep(0.5)

    conn.close()
    log(f"COMPLETE: {batch_num} summaries generated")


if __name__ == "__main__":
    main()
