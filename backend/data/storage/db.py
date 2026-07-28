import sqlite3
from pathlib import Path
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("PRAGMA foreign_keys = OFF")
    cursor.executescript("""
    DROP TABLE IF EXISTS watchlist;
    DROP TABLE IF EXISTS announcements;
    DROP TABLE IF EXISTS financials;
    DROP TABLE IF EXISTS price_history;
    DROP TABLE IF EXISTS technical_indicators;
    DROP TABLE IF EXISTS company_profiles;
    DROP TABLE IF EXISTS concalls;
    DROP TABLE IF EXISTS sector_analysis;
    DROP TABLE IF EXISTS companies;

    CREATE TABLE companies (
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

    CREATE TABLE announcements (
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
        sentiment_score REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (company_id) REFERENCES companies(id)
    );

    CREATE INDEX IF NOT EXISTS idx_ann_company ON announcements(company_id);
    CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(announcement_date);
    CREATE INDEX IF NOT EXISTS idx_ann_category ON announcements(category);
    CREATE INDEX IF NOT EXISTS idx_ann_exchange ON announcements(exchange);
    CREATE INDEX IF NOT EXISTS idx_ann_company_date ON announcements(company_id, announcement_date);

    CREATE TABLE financials (
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

    CREATE TABLE price_history (
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
    CREATE INDEX IF NOT EXISTS idx_price_company_date ON price_history(company_id, trade_date);

    CREATE TABLE technical_indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        indicator_date DATE NOT NULL,
        sma_20 REAL,
        sma_50 REAL,
        sma_200 REAL,
        ema_12 REAL,
        ema_26 REAL,
        rsi_14 REAL,
        macd REAL,
        macd_signal REAL,
        macd_histogram REAL,
        bollinger_upper REAL,
        bollinger_middle REAL,
        bollinger_lower REAL,
        atr_14 REAL,
        obv REAL,
        vwap REAL,
        stochastic_k REAL,
        stochastic_d REAL,
        adx REAL,
        cci REAL,
        williams_r REAL,
        mfi REAL,
        trend TEXT,
        signal TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (company_id) REFERENCES companies(id),
        UNIQUE(company_id, indicator_date)
    );

    CREATE INDEX IF NOT EXISTS idx_tech_company ON technical_indicators(company_id);
    CREATE INDEX IF NOT EXISTS idx_tech_date ON technical_indicators(indicator_date);

    CREATE TABLE company_profiles (
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

    CREATE TABLE watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        notes TEXT,
        added_date DATE DEFAULT CURRENT_DATE,
        target_price REAL,
        stop_loss REAL,
        FOREIGN KEY (company_id) REFERENCES companies(id),
        UNIQUE(company_id)
    );
    """)

    conn.commit()
    conn.close()
    print("Database initialized successfully.")


if __name__ == "__main__":
    init_db()
