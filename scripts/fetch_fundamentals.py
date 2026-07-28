import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db
from data.collectors.yfinance_data import get_stock_info, get_financials


def fetch_fundamentals():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT id, nse_symbol FROM companies WHERE nse_symbol IS NOT NULL')
    companies = cursor.fetchall()

    print(f"Fetching fundamentals for {len(companies)} companies...")

    success = 0
    failed = 0
    for company_id, symbol in companies:
        try:
            info = get_stock_info(symbol)
            financials = get_financials(symbol)

            cursor.execute('''
                UPDATE companies SET
                    pe_ratio = ?, pb_ratio = ?, dividend_yield = ?, eps = ?,
                    book_value = ?, debt_to_equity = ?, roe = ?, roce = ?,
                    current_price = ?, sma_50 = ?, sma_200 = ?, beta = ?,
                    week_52_high = ?, week_52_low = ?, market_cap = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                info.get('pe_ratio'), info.get('pb_ratio'),
                info.get('dividend_yield'), info.get('eps'),
                info.get('book_value'), info.get('debt_to_equity'),
                info.get('roe'), info.get('roce'),
                info.get('current_price'), info.get('sma_50'),
                info.get('sma_200'), info.get('beta'),
                info.get('52w_high'), info.get('52w_low'),
                info.get('market_cap'), company_id
            ))

            for f in financials:
                cursor.execute('''
                    INSERT OR REPLACE INTO financials
                    (company_id, report_date, period, revenue, net_profit, ebitda,
                     operating_margin, net_margin, total_assets, total_equity,
                     total_debt, cash_flow_operating, cash_flow_investing,
                     cash_flow_financing)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    company_id, f.get('report_date'), f.get('period'),
                    f.get('revenue'), f.get('net_profit'), f.get('ebitda'),
                    f.get('operating_margin'), f.get('net_margin'),
                    f.get('total_assets'), f.get('total_equity'),
                    f.get('total_debt'), f.get('cash_flow_operating'),
                    f.get('cash_flow_investing'), f.get('cash_flow_financing')
                ))

            conn.commit()
            success += 1
            print(f"  {symbol}: OK ({len(financials)} financials)")

        except Exception as e:
            failed += 1
            print(f"  {symbol}: FAILED - {str(e)[:50]}")

    conn.close()
    print(f"\n=== Done ===")
    print(f"Success: {success}, Failed: {failed}")


if __name__ == "__main__":
    fetch_fundamentals()
