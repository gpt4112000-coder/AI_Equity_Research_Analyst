import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta


def get_stock_info(symbol):
    ticker = yf.Ticker(f"{symbol}.NS")
    info = ticker.info
    return {
        "company_name": info.get("longName", ""),
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
        "promoter_holding": info.get("heldPercentInsiders"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "sma_50": info.get("fiftyDayAverage"),
        "sma_200": info.get("twoHundredDayAverage"),
        "beta": info.get("beta"),
        "current_price": info.get("currentPrice"),
        "previous_close": info.get("previousClose"),
        "day_high": info.get("dayHigh"),
        "day_low": info.get("dayLow"),
        "volume": info.get("volume"),
        "avg_volume": info.get("averageVolume"),
    }


def get_price_history(symbol, period="1y"):
    ticker = yf.Ticker(f"{symbol}.NS")
    hist = ticker.history(period=period)
    if hist.empty:
        return []

    prices = []
    for date, row in hist.iterrows():
        prices.append({
            "trade_date": date.strftime("%Y-%m-%d"),
            "open_price": round(row["Open"], 2),
            "high_price": round(row["High"], 2),
            "low_price": round(row["Low"], 2),
            "close_price": round(row["Close"], 2),
            "volume": int(row["Volume"]),
        })
    return prices


def get_financials(symbol):
    ticker = yf.Ticker(f"{symbol}.NS")
    financials = []

    for period in ["annual", "quarterly"]:
        try:
            inc = ticker.income_stmt if period == "annual" else ticker.quarterly_income_stmt
            bs = ticker.balance_sheet if period == "annual" else ticker.quarterly_balance_sheet
            cf = ticker.cashflow if period == "annual" else ticker.quarterly_cashflow

            if inc is not None and not inc.empty:
                for col in inc.columns[:4]:
                    entry = {
                        "report_date": col.strftime("%Y-%m-%d"),
                        "period": period,
                        "revenue": _safe_get(inc, "Total Revenue", col),
                        "net_profit": _safe_get(inc, "Net Income", col),
                        "ebitda": _safe_get(inc, "EBITDA", col),
                        "operating_margin": _safe_get(inc, "Operating Margin", col),
                        "net_margin": _safe_get(inc, "Profit Margin", col),
                    }
                    if bs is not None and not bs.empty:
                        entry["total_assets"] = _safe_get(bs, "Total Assets", col)
                        entry["total_equity"] = _safe_get(bs, "Stockholders Equity", col)
                        entry["total_debt"] = _safe_get(bs, "Total Debt", col)
                    if cf is not None and not cf.empty:
                        entry["cash_flow_operating"] = _safe_get(cf, "Operating Cash Flow", col)
                        entry["cash_flow_investing"] = _safe_get(cf, "Investing Cash Flow", col)
                        entry["cash_flow_financing"] = _safe_get(cf, "Financing Cash Flow", col)
                    financials.append(entry)
        except Exception:
            continue

    return financials


def _safe_get(df, row_label, col):
    try:
        val = df.loc[row_label, col]
        return float(val) if pd.notna(val) else None
    except (KeyError, TypeError):
        return None
