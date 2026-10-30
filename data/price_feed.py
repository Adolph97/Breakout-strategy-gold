import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz

# Using Kraken API as it is much more permissive for VPS/GCP regions than Binance.
# We track PAXG/USD (Gold-backed token) as a precise proxy for spot Gold.
KRAKEN_URL = "https://api.kraken.com/0/public"

# In-memory DXY cache to prevent rate-limiting
_dxy_cache = {
    "timestamp": 0,
    "data": {"dxy_close": 102.5, "dxy_bearish": False, "dxy_bullish": False}
}

def get_tick():
    """
    Fetch real-time price from Kraken (PAXG/USD)
    Returns dict with bid, ask, spread
    """
    try:
        # Get Ticker Info
        url = f"{KRAKEN_URL}/Ticker?pair=PAXGUSD"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if data.get('error'):
            logging.error(f"Kraken API Error: {data['error']}")
            return _generate_simulated_tick()

        # Kraken response structure for PAXGUSD
        # The key name can vary (PAXGUSD, XPAXGZUSD, etc), so we grab the first result
        pair_data = list(data['result'].values())[0]
        
        # a = ask [price, whole lot volume, lot volume]
        # b = bid [price, whole lot volume, lot volume]
        ask = float(pair_data['a'][0])
        bid = float(pair_data['b'][0])
        
        spread = round(ask - bid, 2)
        if spread < 0.10:
            spread = 0.35
            ask = bid + spread

        result = {
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread": spread,
            "time": datetime.now()
        }
        return result

    except Exception as e:
        logging.error(f"Error fetching tick data from Kraken: {e}")
        return _generate_simulated_tick()

def get_bars(count=100):
    """
    Fetch historical OHLC bars from Kraken (1 hour interval)
    Returns pandas DataFrame with OHLCV data
    """
    try:
        # Get OHLC data (interval 60 = 1 hour)
        url = f"{KRAKEN_URL}/OHLC?pair=PAXGUSD&interval=60"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if data.get('error'):
            logging.error(f"Kraken OHLC Error: {data['error']}")
            return _generate_simulated_bars(count)

        # Get the OHLC array from the result
        pair_key = list(data['result'].keys())[0]
        if pair_key == 'last': # Handle 'last' key if it's first
            pair_key = list(data['result'].keys())[1]
            
        ohlc_data = data['result'][pair_key]
        
        # Kraken OHLC format: [time, open, high, low, close, vwap, volume, count]
        bars = []
        for item in ohlc_data[-count:]:
            bars.append({
                'time': datetime.fromtimestamp(int(item[0]), tz=pytz.UTC),
                'open': float(item[1]),
                'high': float(item[2]),
                'low': float(item[3]),
                'close': float(item[4]),
                'volume': float(item[6])
            })
            
        df = pd.DataFrame(bars)
        return df

    except Exception as e:
        logging.error(f"Error fetching bar data from Kraken: {e}")
        return _generate_simulated_bars(count)

def get_kraken_ohlc_dataframe(interval, count=300):
    """
    Fetch OHLC DataFrame from Kraken for a given interval (60=1h, 240=4h, 1440=1d)
    """
    try:
        url = f"{KRAKEN_URL}/OHLC?pair=PAXGUSD&interval={interval}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get('error'):
            logging.error(f"Kraken OHLC Error: {data['error']}")
            return None
        pair_key = [k for k in data['result'].keys() if k != 'last'][0]
        ohlc_data = data['result'][pair_key][-count:]
        df = pd.DataFrame(ohlc_data, columns=['time', 'open', 'high', 'low', 'close', 'vwap', 'volume', 'count'])
        df['time'] = pd.to_datetime(df['time'].astype(int), unit='s', utc=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching Kraken OHLC (interval={interval}): {e}")
        return None

def fetch_dxy_signals():
    """
    Fetches daily US Dollar Index (DXY) momentum signals (cached for 1 hour).
    Returns dict: {'dxy_close': float, 'dxy_bearish': bool, 'dxy_bullish': bool}
    """
    global _dxy_cache
    now = time.time()
    if now - _dxy_cache["timestamp"] < 3600 and _dxy_cache["data"]:
        return _dxy_cache["data"]

    try:
        import yfinance as yf
        dxy = yf.download("DX-Y.NYB", period="3mo", interval="1d", progress=False)
        if isinstance(dxy.columns, pd.MultiIndex):
            dxy.columns = [c[0] for c in dxy.columns]
        dxy = dxy.reset_index()
        dxy['ema20'] = dxy['Close'].ewm(span=20, adjust=False).mean()
        dxy['ret5'] = dxy['Close'].pct_change(5)
        # Shift 1 day to strictly avoid lookahead bias
        prior = dxy.iloc[-2]
        is_bearish = bool((prior['Close'] < prior['ema20']) and (prior['ret5'] < 0))
        is_bullish = bool((prior['Close'] > prior['ema20']) and (prior['ret5'] > 0))
        res = {
            "dxy_close": float(prior['Close']),
            "dxy_bearish": is_bearish,
            "dxy_bullish": is_bullish
        }
        _dxy_cache["timestamp"] = now
        _dxy_cache["data"] = res
        return res
    except Exception as e:
        logging.warning(f"Failed to fetch DXY: {e}. Using cached value.")
        return _dxy_cache["data"]

def get_multi_timeframe_context():
    """
    Fetches and computes complete multi-timeframe and intermarket context:
    - 1H: Bars, Monday IB range (mon_high, mon_low), volume ratio, candle conviction
    - 4H: H4 EMA50, EMA200, 32-hour Donchian high (h4_h8) & low (h4_l8)
    - D1: Daily EMA50, EMA200, ATR(14)
    - DXY: Intermarket trend signals
    """
    # Kraken returns at most 720 bars; take all so the EMA200s are properly warmed up.
    h1 = get_kraken_ohlc_dataframe(interval=60, count=720)
    h4 = get_kraken_ohlc_dataframe(interval=240, count=720)
    d1 = get_kraken_ohlc_dataframe(interval=1440, count=720)

    if h1 is None or h4 is None or d1 is None:
        # Fail safe: never score a signal on simulated prices — the caller skips the cycle.
        return None

    # Daily features (shifted 1 day)
    d1['ema50'] = d1['close'].ewm(span=50, adjust=False).mean()
    d1['ema200'] = d1['close'].ewm(span=200, adjust=False).mean()
    d1['atr14'] = (d1['high'] - d1['low']).rolling(14).mean()
    d1_prior = d1.iloc[-2]

    # 4-Hour features (shifted 1 bar)
    h4['ema50'] = h4['close'].ewm(span=50, adjust=False).mean()
    h4['ema200'] = h4['close'].ewm(span=200, adjust=False).mean()
    # Donchian over the 8 H4 bars BEFORE the last completed one (matches trader/features.py)
    h4['h8'] = h4['high'].rolling(8).max().shift(1)
    h4['l8'] = h4['low'].rolling(8).min().shift(1)
    h4_prior = h4.iloc[-2]

    # 1H features & Monday IB
    h1['dow'] = h1['time'].dt.day_of_week
    now_utc = datetime.now(pytz.UTC)
    monday_bars = h1[(h1['dow'] == 0) & ((now_utc - h1['time']).dt.total_seconds() < 7 * 86400)]
    mon_high = float(monday_bars['high'].max()) if len(monday_bars) > 0 else 0.0
    mon_low = float(monday_bars['low'].min()) if len(monday_bars) > 0 else 0.0

    # Score the last CLOSED H1 bar (Kraken's final bar is still forming), as in the backtest
    latest_bar = h1.iloc[-2]
    vol_ma20 = h1['volume'].rolling(20).mean().shift(1).iloc[-2]
    vol_ratio = float(latest_bar['volume'] / max(vol_ma20, 0.001))
    c_rng = max(float(latest_bar['high'] - latest_bar['low']), 0.01)
    close_pct = float((latest_bar['close'] - latest_bar['low']) / c_rng)

    dxy = fetch_dxy_signals()

    return {
        "h1": h1,
        "latest_bar": latest_bar,
        "d1_atr14": float(d1_prior['atr14']),
        "d1_ema50": float(d1_prior['ema50']),
        "d1_ema200": float(d1_prior['ema200']),
        "h4_ema50": float(h4_prior['ema50']),
        "h4_ema200": float(h4_prior['ema200']),
        "h4_h8": float(h4_prior['h8']),
        "h4_l8": float(h4_prior['l8']),
        "mon_high": mon_high,
        "mon_low": mon_low,
        "vol_ratio": vol_ratio,
        "close_pct": close_pct,
        "dxy_bearish": dxy.get("dxy_bearish", False),
        "dxy_bullish": dxy.get("dxy_bullish", False),
        "dxy_close": dxy.get("dxy_close", 102.5)
    }


def _generate_simulated_tick():
    """Fallback simulation with fixed imports"""
    import random
    base_price = 4505.0 
    change = random.uniform(-1.0, 1.0)
    new_price = base_price + change
    spread = 0.40
    return {
        "bid": round(new_price - (spread / 2), 2),
        "ask": round(new_price + (spread / 2), 2),
        "spread": spread,
        "time": datetime.now()
    }

def _generate_simulated_bars(count):
    """Fallback simulation with fixed imports"""
    import numpy as np
    end_time = datetime.now(pytz.UTC)
    # Using timedelta from the top-level import
    start_time = end_time - timedelta(hours=count)
    timestamps = pd.date_range(start=start_time, end=end_time, periods=count, tz='UTC')
    base_price = 4505.0
    prices = [base_price + np.random.normal(0, 5) for _ in range(count)]
    df = pd.DataFrame({
        'time': timestamps,
        'open': prices,
        'high': [p + 2 for p in prices],
        'low': [p - 2 for p in prices],
        'close': prices,
        'volume': [500 for _ in range(count)]
    })
    return df

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing Real-Time Price Feed via Kraken (PAXGUSD)...")
    tick = get_tick()
    print(f"Current Tick: {tick}")
    
    print("\nFetching Historical Bars...")
    bars = get_bars(count=5)
    if not bars.empty:
        print(bars[['time', 'open', 'high', 'low', 'close']])
