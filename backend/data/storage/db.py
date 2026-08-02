import sqlite3
from pathlib import Path
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-64000")
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bse_code TEXT,
    nse_symbol TEXT,
    company_name TEXT NOT NULL,
    sector TEXT,
    industry TEXT,
    market_cap REAL,
    pe_ratio REAL,
    pb_ratio REAL,
    dividend_yield REAL,
    eps REAL,
    debt_to_equity REAL,
    roe REAL,
    current_price REAL,
    sma_50 REAL,
    sma_200 REAL,
    beta REAL,
    week_52_high REAL,
    week_52_low REAL,
    isin TEXT,
    listing_date TEXT,
    group_name TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_nse ON companies(nse_symbol);
CREATE INDEX IF NOT EXISTS idx_companies_bse ON companies(bse_code);
CREATE INDEX IF NOT EXISTS idx_companies_isin ON companies(isin);
CREATE INDEX IF NOT EXISTS idx_companies_sector ON companies(sector);
CREATE INDEX IF NOT EXISTS idx_companies_mcap ON companies(market_cap);
CREATE INDEX IF NOT EXISTS idx_companies_active ON companies(is_active);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    exchange TEXT NOT NULL,
    category TEXT NOT NULL,
    headline TEXT,
    description TEXT,
    attachment_url TEXT,
    announcement_date DATE NOT NULL,
    announcement_time TIME,
    is_critical INTEGER DEFAULT 0,
    ai_summary TEXT,
    raw_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_ann_company ON announcements(company_id);
CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(announcement_date);
CREATE INDEX IF NOT EXISTS idx_ann_category ON announcements(category);
CREATE INDEX IF NOT EXISTS idx_ann_exchange ON announcements(exchange);
CREATE INDEX IF NOT EXISTS idx_ann_company_date ON announcements(company_id, announcement_date);

CREATE TABLE IF NOT EXISTS ai_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    announcement_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    announcement_date DATE NOT NULL,
    headline TEXT,
    summary TEXT,
    amount REAL,
    amount_text TEXT,
    sentiment TEXT,
    importance TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (announcement_id) REFERENCES announcements(id)
);

CREATE INDEX IF NOT EXISTS idx_ai_company_metric_date ON ai_insights(company_id, metric, announcement_date);
CREATE INDEX IF NOT EXISTS idx_ai_company_date ON ai_insights(company_id, announcement_date);
CREATE INDEX IF NOT EXISTS idx_ai_announcement ON ai_insights(announcement_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_unique ON ai_insights(company_id, announcement_id, metric);

CREATE TABLE IF NOT EXISTS announcement_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    announcement_id INTEGER,
    company_id INTEGER,
    insight_type TEXT NOT NULL,
    insight_subtype TEXT,
    headline TEXT,
    summary TEXT,
    sentiment TEXT,
    amount REAL,
    period TEXT,
    confidence REAL DEFAULT 0.5,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (announcement_id) REFERENCES announcements(id),
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_insight_company ON announcement_insights(company_id);
CREATE INDEX IF NOT EXISTS idx_insight_type ON announcement_insights(insight_type);
CREATE INDEX IF NOT EXISTS idx_insight_sentiment ON announcement_insights(sentiment);

CREATE TABLE IF NOT EXISTS company_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER UNIQUE,
    total_announcements INTEGER DEFAULT 0,
    has_guidance INTEGER DEFAULT 0,
    guidance_text TEXT,
    has_capex_news INTEGER DEFAULT 0,
    capex_text TEXT,
    has_order_news INTEGER DEFAULT 0,
    order_text TEXT,
    has_financial_results INTEGER DEFAULT 0,
    financial_results_text TEXT,
    has_dividend_news INTEGER DEFAULT 0,
    dividend_text TEXT,
    sentiment_positive INTEGER DEFAULT 0,
    sentiment_negative INTEGER DEFAULT 0,
    sentiment_neutral INTEGER DEFAULT 0,
    key_themes TEXT,
    latest_announcement_date DATE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS company_ai_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER UNIQUE,
    summary TEXT NOT NULL,
    announcements_used INTEGER DEFAULT 0,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    trade_date DATE NOT NULL,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    volume INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    UNIQUE(company_id, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_price_company ON price_history(company_id);
CREATE INDEX IF NOT EXISTS idx_price_date ON price_history(trade_date);

CREATE TABLE IF NOT EXISTS technical_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    indicator_date DATE NOT NULL,
    sma_20 REAL, sma_50 REAL, sma_200 REAL,
    rsi_14 REAL, macd REAL,
    bollinger_upper REAL, bollinger_middle REAL, bollinger_lower REAL,
    trend TEXT, signal TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    UNIQUE(company_id, indicator_date)
);

CREATE TABLE IF NOT EXISTS fetched_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    bse_code TEXT,
    nse_symbol TEXT,
    company_name TEXT,
    company_id INTEGER,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    date_from DATE,
    date_to DATE,
    announcement_count INTEGER DEFAULT 0,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_fetched_symbol ON fetched_stock(symbol);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fetched_symbol_exchange ON fetched_stock(symbol, exchange);
"""


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.executescript(SCHEMA_SQL)
    # --- Migrations for existing databases ---
    # 1) Add is_critical column if missing (older schema)
    cols = [r[1] for r in cursor.execute("PRAGMA table_info(announcements)").fetchall()]
    if "is_critical" not in cols:
        cursor.execute("ALTER TABLE announcements ADD COLUMN is_critical INTEGER DEFAULT 0")
        print("  Migration: added is_critical column to announcements")
    # 1b) Add ai_summary column if missing (older schema)
    if "ai_summary" not in cols:
        cursor.execute("ALTER TABLE announcements ADD COLUMN ai_summary TEXT")
        print("  Migration: added ai_summary column to announcements")
    # 2) Dedup existing rows (keep lowest id per company+date+headline)
    cursor.execute("""
        DELETE FROM announcements WHERE id NOT IN (
            SELECT MIN(id) FROM announcements GROUP BY company_id, announcement_date, headline
        )
    """)
    if cursor.rowcount > 0:
        print(f"  Migration: removed {cursor.rowcount} duplicate announcement rows")
    # 3) Unique index for future INSERT OR IGNORE dedup (only if not present)
    existing = [r["name"] for r in cursor.execute("PRAGMA index_list(announcements)").fetchall()]
    if "idx_ann_unique" not in existing:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ann_unique ON announcements(company_id, announcement_date, headline)")
    # 4) Partial index for fast pending-AI lookups (needs ai_summary column, hence after migrations)
    if "idx_ann_pending_ai" not in existing:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ann_pending_ai ON announcements(company_id) WHERE ai_summary IS NULL OR ai_summary = ''")
    conn.commit()
    conn.close()
    print("Database initialized successfully.")


if __name__ == "__main__":
    init_db()
