import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from data.storage.db import get_db
from data.collectors.yfinance_data import get_price_history


def calculate_indicators(prices):
    if len(prices) < 20:
        return None

    closes = [p['close_price'] for p in prices]
    highs = [p['high_price'] for p in prices]
    lows = [p['low_price'] for p in prices]
    volumes = [p['volume'] for p in prices]

    def sma(data, period):
        if len(data) < period:
            return None
        return sum(data[-period:]) / period

    def ema(data, period):
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for price in data[period:]:
            ema_val = (price - ema_val) * multiplier + ema_val
        return ema_val

    def rsi(data, period=14):
        if len(data) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(data)):
            diff = data[i] - data[i-1]
            gains.append(max(0, diff))
            losses.append(max(0, -diff))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def bollinger(data, period=20):
        if len(data) < period:
            return None, None, None
        sma_val = sum(data[-period:]) / period
        variance = sum((x - sma_val) ** 2 for x in data[-period:]) / period
        std_dev = variance ** 0.5
        return sma_val + 2 * std_dev, sma_val, sma_val - 2 * std_dev

    def atr(highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = (ema12 - ema26) if ema12 and ema26 else None

    upper, middle, lower = bollinger(closes)

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)

    rsi_val = rsi(closes)

    if sma50 and sma200:
        if closes[-1] > sma50 > sma200:
            trend = "Strong Uptrend"
        elif closes[-1] > sma50:
            trend = "Uptrend"
        elif closes[-1] < sma50 < sma200:
            trend = "Strong Downtrend"
        elif closes[-1] < sma50:
            trend = "Downtrend"
        else:
            trend = "Sideways"
    else:
        trend = "Insufficient Data"

    if rsi_val:
        if rsi_val > 70:
            signal = "Overbought"
        elif rsi_val < 30:
            signal = "Oversold"
        else:
            signal = "Neutral"
    else:
        signal = "Insufficient Data"

    return {
        'sma_20': sma20,
        'sma_50': sma50,
        'sma_200': sma200,
        'ema_12': ema12,
        'ema_26': ema26,
        'rsi_14': rsi_val,
        'macd': macd_line,
        'macd_signal': None,
        'bollinger_upper': upper,
        'bollinger_middle': middle,
        'bollinger_lower': lower,
        'atr_14': atr(highs, lows, closes),
        'trend': trend,
        'signal': signal
    }


def fetch_technicals():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT id, nse_symbol FROM companies WHERE nse_symbol IS NOT NULL')
    companies = cursor.fetchall()

    print(f"Calculating technicals for {len(companies)} companies...")

    success = 0
    for company_id, symbol in companies:
        try:
            prices = get_price_history(symbol, period="1y")
            if not prices or len(prices) < 20:
                continue

            for p in prices:
                cursor.execute('''
                    INSERT OR IGNORE INTO price_history
                    (company_id, trade_date, open_price, high_price, low_price, close_price, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (company_id, p['trade_date'], p['open_price'], p['high_price'],
                      p['low_price'], p['close_price'], p['volume']))

            indicators = calculate_indicators(prices)
            if indicators:
                cursor.execute('''
                    INSERT OR REPLACE INTO technical_indicators
                    (company_id, indicator_date, sma_20, sma_50, sma_200,
                     ema_12, ema_26, rsi_14, macd, bollinger_upper,
                     bollinger_middle, bollinger_lower, atr_14, trend, signal)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    company_id, prices[-1]['trade_date'],
                    indicators['sma_20'], indicators['sma_50'], indicators['sma_200'],
                    indicators['ema_12'], indicators['ema_26'], indicators['rsi_14'],
                    indicators['macd'], indicators['bollinger_upper'],
                    indicators['bollinger_middle'], indicators['bollinger_lower'],
                    indicators['atr_14'], indicators['trend'], indicators['signal']
                ))

            conn.commit()
            success += 1
            print(f"  {symbol}: OK ({len(prices)} days)")

        except Exception as e:
            print(f"  {symbol}: FAILED - {str(e)[:50]}")

    conn.close()
    print(f"\n=== Done ===")
    print(f"Processed: {success}/{len(companies)}")


if __name__ == "__main__":
    fetch_technicals()
