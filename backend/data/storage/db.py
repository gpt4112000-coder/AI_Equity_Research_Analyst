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
    book_value REAL,
    debt_to_equity REAL,
    roe REAL,
    roce REAL,
    current_price REAL,
    sma_20 REAL,
    sma_50 REAL,
    sma_200 REAL,
    beta REAL,
    week_52_high REAL,
    week_52_low REAL,
    isin TEXT,
    listing_date TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_nse ON companies(nse_symbol);
CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_bse ON companies(bse_code);
CREATE INDEX IF NOT EXISTS idx_companies_sector ON companies(sector);
CREATE INDEX IF NOT EXISTS idx_companies_mcap ON companies(market_cap);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    exchange TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    headline TEXT,
    description TEXT,
    attachment_url TEXT,
    announcement_date DATE NOT NULL,
    announcement_time TIME,
    is_critical INTEGER DEFAULT 0,
    raw_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_ann_company ON announcements(company_id);
CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(announcement_date);
CREATE INDEX IF NOT EXISTS idx_ann_category ON announcements(category);
CREATE INDEX IF NOT EXISTS idx_ann_exchange ON announcements(exchange);
CREATE INDEX IF NOT EXISTS idx_ann_company_date ON announcements(company_id, announcement_date);

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
CREATE INDEX IF NOT EXISTS idx_insight_date ON announcement_insights(extracted_at);

CREATE TABLE IF NOT EXISTS company_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER UNIQUE,
    total_announcements INTEGER DEFAULT 0,
    important_announcements INTEGER DEFAULT 0,
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
    has_credit_rating INTEGER DEFAULT 0,
    credit_rating_text TEXT,
    sentiment_positive INTEGER DEFAULT 0,
    sentiment_negative INTEGER DEFAULT 0,
    sentiment_neutral INTEGER DEFAULT 0,
    key_themes TEXT,
    latest_announcement_date DATE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS financials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    report_date DATE NOT NULL,
    period TEXT NOT NULL,
    revenue REAL,
    net_profit REAL,
    ebitda REAL,
    operating_margin REAL,
    net_margin REAL,
    total_assets REAL,
    total_equity REAL,
    total_debt REAL,
    cash_flow_operating REAL,
    cash_flow_investing REAL,
    cash_flow_financing REAL,
    free_cash_flow REAL,
    yoy_revenue_growth REAL,
    yoy_profit_growth REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    UNIQUE(company_id, report_date, period)
);

CREATE INDEX IF NOT EXISTS idx_fin_company ON financials(company_id);
CREATE INDEX IF NOT EXISTS idx_fin_date ON financials(report_date);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    trade_date DATE NOT NULL,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    volume INTEGER,
    delivery_pct REAL,
    turnover REAL,
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
    ema_12 REAL, ema_26 REAL, rsi_14 REAL,
    macd REAL, macd_signal REAL, macd_histogram REAL,
    bollinger_upper REAL, bollinger_middle REAL, bollinger_lower REAL,
    atr_14 REAL, obv REAL, vwap REAL,
    stochastic_k REAL, stochastic_d REAL, adx REAL, cci REAL, williams_r REAL, mfi REAL,
    trend TEXT, signal TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    UNIQUE(company_id, indicator_date)
);

CREATE INDEX IF NOT EXISTS idx_tech_company ON technical_indicators(company_id);
CREATE INDEX IF NOT EXISTS idx_tech_date ON technical_indicators(indicator_date);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    notes TEXT,
    added_date DATE DEFAULT CURRENT_DATE,
    target_price REAL,
    stop_loss REAL,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    UNIQUE(company_id)
);
"""


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    print("Database initialized successfully.")


def add_column_if_missing(table, column, col_type):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()


NEW_TABLES_SQL = """
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
    important_announcements INTEGER DEFAULT 0,
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
    has_credit_rating INTEGER DEFAULT 0,
    credit_rating_text TEXT,
    sentiment_positive INTEGER DEFAULT 0,
    sentiment_negative INTEGER DEFAULT 0,
    sentiment_neutral INTEGER DEFAULT 0,
    key_themes TEXT,
    latest_announcement_date DATE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);
"""


def ensure_schema():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.executescript(NEW_TABLES_SQL)
        conn.commit()
    except Exception:
        pass
    add_column_if_missing("companies", "isin", "TEXT")
    add_column_if_missing("companies", "listing_date", "TEXT")
    add_column_if_missing("announcements", "raw_data", "TEXT")
    conn.close()


if __name__ == "__main__":
    init_db()
