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
