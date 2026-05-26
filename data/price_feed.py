import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime
import pytz

# Using PAXG/USDT as a reliable proxy for Gold Spot (XAU/USD)
# PAXG is a gold-backed token that tracks spot gold prices very closely.
SYMBOL = "PAXGUSDT"
BINANCE_URL = "https://api.binance.com/api/v3"

def get_tick():
    """
    Fetch real-time price from Binance (PAXG/USDT)
    Returns dict with bid, ask, spread
    """
    try:
        # Get latest ticker price
        url = f"{BINANCE_URL}/ticker/bookTicker?symbol={SYMBOL}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Binance bookTicker gives us real-time bid/ask
        bid = float(data['bidPrice'])
        ask = float(data['askPrice'])
        
        # If the exchange spread is too tight (0.01), we add a small realistic spread for the bot
        spread = ask - bid
        if spread < 0.10:
            spread = 0.35
            ask = bid + spread

        result = {
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread": round(spread, 2),
            "time": datetime.now()
        }
        return result

    except Exception as e:
        logging.error(f"Error fetching tick data from Binance: {e}")
        return _generate_simulated_tick()

def get_bars(count=100):
    """
    Fetch historical OHLC bars from Binance
    Returns pandas DataFrame with OHLCV data
    """
    try:
        # Get Klines (candlestick data)
        # interval: 1h, limit: count
        url = f"{BINANCE_URL}/klines?symbol={SYMBOL}&interval=1h&limit={count}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Binance kline format:
        # [
        #   [
        #     1499040000000,      // Open time
        #     "0.01634790",       // Open
        #     "0.80000000",       // High
        #     "0.01575800",       // Low
        #     "0.01577100",       // Close
        #     "148976.11427815",  // Volume
        #     1499644799999,      // Close time
        #     ...
        #   ]
        # ]
        
        bars = []
        for item in data:
            bars.append({
                'time': datetime.fromtimestamp(item[0] / 1000, tz=pytz.UTC),
                'open': float(item[1]),
                'high': float(item[2]),
                'low': float(item[3]),
                'close': float(item[4]),
                'volume': float(item[5])
            })
            
        df = pd.DataFrame(bars)
        return df

    except Exception as e:
        logging.error(f"Error fetching bar data from Binance: {e}")
        return _generate_simulated_bars(count)

def _generate_simulated_tick():
    """Fallback simulation (last resort)"""
    import random
    # Use a more realistic 2026 base price if simulation is triggered
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
    """Fallback simulation (last resort)"""
    import numpy as np
    end_time = datetime.now(pytz.UTC)
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
    print(f"Testing Real-Time Price Feed via Binance ({SYMBOL})...")
    tick = get_tick()
    print(f"Current Tick: {tick}")
    
    print("\nFetching Historical Bars...")
    bars = get_bars(count=5)
    if not bars.empty:
        print(bars[['time', 'open', 'high', 'low', 'close']])
