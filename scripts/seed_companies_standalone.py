#!/usr/bin/env python3
"""Seed companies from config.py SECTORS into DB. Standalone - no external data needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db
from config import SECTORS


def get_stock_info_safe(symbol):
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info
        return {
            "company_name": info.get("longName", symbol),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "pb_ratio": info.get("priceToBook"),
            "dividend_yield": info.get("dividendYield"),
            "eps": info.get("trailingEps"),
            "book_value": info.get("bookValue"),
            "debt_to_equity": info.get("debtToEquity"),
            "roe": info.get("returnOnEquity"),
            "roce": info.get("returnOnCapital"),
            "current_price": info.get("currentPrice"),
            "sma_50": info.get("fiftyDayAverage"),
            "sma_200": info.get("twoHundredDayAverage"),
            "beta": info.get("beta"),
            "week_52_high": info.get("fiftyTwoWeekHigh"),
            "week_52_low": info.get("fiftyTwoWeekLow"),
        }
    except Exception as e:
        print(f"  yfinance error for {symbol}: {e}")
        return None


def seed_companies():
    conn = get_db()
    cursor = conn.cursor()
    added = 0
    failed = 0

    for sector_name, symbols in SECTORS.items():
        print(f"\n--- {sector_name} ({len(symbols)} symbols) ---")
        for symbol in symbols:
            info = get_stock_info_safe(symbol)
            if not info:
                failed += 1
                continue
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO companies 
                    (nse_symbol, company_name, sector, industry, market_cap, pe_ratio,
                     pb_ratio, dividend_yield, eps, book_value, debt_to_equity, roe, roce,
                     current_price, sma_50, sma_200, beta, week_52_high, week_52_low)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol, info.get("company_name", symbol), sector_name,
                    info.get("industry"), info.get("market_cap"),
                    info.get("pe_ratio"), info.get("pb_ratio"),
                    info.get("dividend_yield"), info.get("eps"),
                    info.get("book_value"), info.get("debt_to_equity"),
                    info.get("roe"), info.get("roce"),
                    info.get("current_price"), info.get("sma_50"),
                    info.get("sma_200"), info.get("beta"),
                    info.get("week_52_high"), info.get("week_52_low"),
                ))
                added += 1
                print(f"  {symbol}: OK")
            except Exception as e:
                failed += 1
                print(f"  {symbol}: FAILED - {str(e)[:50]}")

    conn.commit()
    conn.close()
    print(f"\n=== Seeding Complete ===")
    print(f"Added: {added}, Failed: {failed}")


if __name__ == "__main__":
    seed_companies()
