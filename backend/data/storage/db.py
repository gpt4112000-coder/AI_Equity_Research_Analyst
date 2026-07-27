import sqlite3
from pathlib import Path
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bse_code TEXT UNIQUE,
        nse_symbol TEXT UNIQUE,
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
        promoter_holding REAL,
        current_price REAL,
        sma_50 REAL,
        sma_200 REAL,
        beta REAL,
        week_52_high REAL,
        week_52_low REAL,
        logo_url TEXT,
        isin TEXT,
        listed_since TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (company_id) REFERENCES companies(id),
        UNIQUE(exchange, company_id, announcement_date, announcement_time, category)
    );

    CREATE TABLE IF NOT EXISTS concalls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        call_date DATE NOT NULL,
        call_type TEXT,
        transcript_text TEXT,
        key_highlights TEXT,
        guidance TEXT,
        management_views TEXT,
        qna_summary TEXT,
        sentiment_score REAL,
        source_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (company_id) REFERENCES companies(id)
    );

    CREATE TABLE IF NOT EXISTS financials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        report_date DATE NOT NULL,
        period TEXT,
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (company_id) REFERENCES companies(id),
        UNIQUE(company_id, report_date, period)
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
        delivery_pct REAL,
        sma_20 REAL,
        sma_50 REAL,
        sma_200 REAL,
        rsi_14 REAL,
        macd REAL,
        macd_signal REAL,
        bollinger_upper REAL,
        bollinger_lower REAL,
        FOREIGN KEY (company_id) REFERENCES companies(id),
        UNIQUE(company_id, trade_date)
    );

    CREATE TABLE IF NOT EXISTS sector_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sector TEXT NOT NULL,
        analysis_date DATE NOT NULL,
        top_gainers TEXT,
        top_losers TEXT,
        sector_sentiment TEXT,
        key_themes TEXT,
        sector_outlook TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(sector, analysis_date)
    );

    CREATE TABLE IF NOT EXISTS company_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        profile_text TEXT,
        investment_thesis TEXT,
        key_strengths TEXT,
        key_risks TEXT,
        catalysts TEXT,
        valuation_summary TEXT,
        analyst_rating TEXT,
        target_price REAL,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (company_id) REFERENCES companies(id),
        UNIQUE(company_id)
    );

    CREATE INDEX IF NOT EXISTS idx_announcements_company ON announcements(company_id);
    CREATE INDEX IF NOT EXISTS idx_announcements_date ON announcements(announcement_date);
    CREATE INDEX IF NOT EXISTS idx_announcements_category ON announcements(category);
    CREATE INDEX IF NOT EXISTS idx_price_history_company ON price_history(company_id);
    CREATE INDEX IF NOT EXISTS idx_price_history_date ON price_history(trade_date);
    CREATE INDEX IF NOT EXISTS idx_concalls_company ON concalls(company_id);
    CREATE INDEX IF NOT EXISTS idx_financials_company ON financials(company_id);
    """)

    conn.commit()
    conn.close()
    print("Database initialized successfully.")


if __name__ == "__main__":
    init_db()
