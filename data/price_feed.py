import os
import time
import logging
from datetime import datetime

# Global variable to store last known price for simulation fallback
_last_known_price = None

def get_tick():
    """
    Generate simulated tick data for Gold (XAUUSD)
    Returns dict with bid, ask, spread
    """
    global _last_known_price

    # Base price around current gold levels
    base_price = 2350.0 if _last_known_price is None else _last_known_price['ask']

    # Add small random walk movement
    import random
    change = random.uniform(-0.5, 0.5)
    new_price = base_price + change

    # Ensure price stays reasonable
    new_price = max(1800, min(3000, new_price))

    spread = 0.30  # Typical gold spread

    result = {
        "bid": round(new_price - (spread / 2), 2),
        "ask": round(new_price + (spread / 2), 2),
        "spread": spread,
        "time": datetime.now()
    }

    _last_known_price = result
    return result

def get_bars(count=100):
    """
    Generate simulated OHLC bar data for Gold
    Returns pandas DataFrame with OHLCV data
    """
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta
    import pytz

    # Generate timestamps
    end_time = datetime.now(pytz.UTC)
    start_time = end_time - timedelta(hours=count)
    timestamps = pd.date_range(start=start_time, end=end_time, periods=count, tz='UTC')

    # Generate price series with random walk
    base_price = 2350.0
    prices = []
    current_price = base_price

    for i in range(count):
        change = np.random.normal(0.1, 0.8)
        current_price += change
        prices.append(current_price)

    # Generate OHLC
    opens = [prices[0]] + prices[:-1]
    highs = [p + abs(np.random.normal(0, 0.5)) for p in prices]
    lows = [p - abs(np.random.normal(0, 0.5)) for p in prices]

    for i in range(count):
        highs[i] = max(highs[i], opens[i], prices[i])
        lows[i] = min(lows[i], opens[i], prices[i])

    volumes = [np.random.uniform(100, 1000) for _ in range(count)]

    df = pd.DataFrame({
        'time': timestamps,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': prices,
        'volume': volumes
    })

    return df

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing Price Feed...")
    print(f"Tick: {get_tick()}")
    print(f"Bars (last 5):\n{get_bars(5).tail()}")
