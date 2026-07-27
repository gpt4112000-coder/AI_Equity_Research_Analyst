from data.collectors.yfinance_data import get_stock_info, get_price_history, get_financials
from data.storage.db import get_db
from datetime import datetime


def analyze_fundamentals(symbol):
    info = get_stock_info(symbol)
    prices = get_price_history(symbol, period="1y")
    financials = get_financials(symbol)

    analysis = {
        "symbol": symbol,
        "snapshot": info,
        "valuation": _assess_valuation(info),
        "financial_health": _assess_financial_health(info, financials),
        "price_analysis": _analyze_price(prices, info),
        "technical_signals": _technical_analysis(prices),
    }
    return analysis


def _assess_valuation(info):
    valuation = {}
    pe = info.get("pe_ratio")
    pb = info.get("pb_ratio")

    if pe:
        if pe < 15:
            valuation["pe_assessment"] = "Undervalued"
        elif pe < 25:
            valuation["pe_assessment"] = "Fairly valued"
        else:
            valuation["pe_assessment"] = "Overvalued"
        valuation["pe_ratio"] = pe

    if pb:
        if pb < 1:
            valuation["pb_assessment"] = "Undervalued"
        elif pb < 3:
            valuation["pb_assessment"] = "Fairly valued"
        else:
            valuation["pb_assessment"] = "Overvalued"
        valuation["pb_ratio"] = pb

    div_yield = info.get("dividend_yield")
    if div_yield:
        valuation["dividend_yield"] = f"{div_yield*100:.2f}%"
        valuation["dividend_assessment"] = "Good" if div_yield > 0.03 else "Low"

    return valuation


def _assess_financial_health(info, financials):
    health = {}
    de = info.get("debt_to_equity")
    if de is not None:
        health["debt_to_equity"] = de
        health["leverage"] = "Conservative" if de < 0.5 else "Moderate" if de < 1.5 else "Aggressive"

    roe = info.get("roe")
    if roe is not None:
        health["roe"] = f"{roe*100:.1f}%"
        health["profitability"] = "Strong" if roe > 0.15 else "Moderate" if roe > 0.08 else "Weak"

    if financials:
        latest = financials[0]
        if latest.get("operating_margin"):
            health["operating_margin"] = f"{latest['operating_margin']*100:.1f}%"
        if latest.get("revenue") and latest.get("net_profit"):
            health["net_margin"] = f"{latest['net_profit']/latest['revenue']*100:.1f}%"

    return health


def _analyze_price(prices, info):
    if not prices:
        return {}

    analysis = {}
    latest = prices[-1]["close_price"]
    analysis["current_price"] = latest

    high_52w = info.get("52w_high")
    low_52w = info.get("52w_low")
    if high_52w and low_52w:
        range_pct = (latest - low_52w) / (high_52w - low_52w) * 100
        analysis["52w_range_position"] = f"{range_pct:.1f}%"
        analysis["distance_from_52w_high"] = f"{(latest - high_52w)/high_52w*100:.1f}%"
        analysis["distance_from_52w_low"] = f"{(latest - low_52w)/low_52w*100:.1f}%"

    sma50 = info.get("sma_50")
    sma200 = info.get("sma_200")
    if sma50 and sma200:
        analysis["sma_50"] = sma50
        analysis["sma_200"] = sma200
        if latest > sma50 > sma200:
            analysis["trend"] = "Strong Uptrend"
        elif latest > sma50:
            analysis["trend"] = "Uptrend"
        elif latest < sma50 < sma200:
            analysis["trend"] = "Strong Downtrend"
        elif latest < sma50:
            analysis["trend"] = "Downtrend"
        else:
            analysis["trend"] = "Sideways"

    if len(prices) >= 20:
        recent_closes = [p["close_price"] for p in prices[-20:]]
        avg_volume = sum(p["volume"] for p in prices[-20:]) / 20
        analysis["avg_volume_20d"] = int(avg_volume)
        analysis["price_change_20d"] = f"{(recent_closes[-1] - recent_closes[0])/recent_closes[0]*100:.2f}%"

    return analysis


def _technical_analysis(prices):
    if len(prices) < 14:
        return {}

    signals = {}
    closes = [p["close_price"] for p in prices]

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    if len(gains) >= 14:
        avg_gain = sum(gains[-14:]) / 14
        avg_loss = sum(losses[-14:]) / 14
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        signals["rsi_14"] = round(rsi, 2)
        if rsi > 70:
            signals["rsi_signal"] = "Overbought"
        elif rsi < 30:
            signals["rsi_signal"] = "Oversold"
        else:
            signals["rsi_signal"] = "Neutral"

    if len(closes) >= 26:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_line = ema12 - ema26
        signals["macd"] = round(macd_line, 2)
        signals["macd_signal"] = "Bullish" if macd_line > 0 else "Bearish"

    return signals


def _ema(data, period):
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema
