import pandas as pd
import numpy as np
import logging
from . import asian_range

def get_derivative_signal(df):
    """
    Get derivative strategy signal - now using Asian Range Breakout as primary strategy
    Args:
        df: DataFrame with OHLC data
    Returns:
        Tuple of (String: "BUY", "SELL", or None, dict or None)
    """
    # Primary strategy: Asian Range Breakout
    ar_signal, ar_data = asian_range.get_asian_range_signal(df)
    if ar_signal:
        logging.info(f"Asian range signal: {ar_signal}, width: {ar_data['width']}")
        return ar_signal, ar_data

    # Fallback: Keep the old liquidity sweep/pullback logic as backup
    fallback_signal = _get_legacy_derivative_signal(df)
    return fallback_signal, None

def _get_legacy_derivative_signal(df):
    """
    Original derivative strategy logic (liquidity sweep & pullback)
    Kept as fallback/confluence enhancer
    """
    # Try liquidity sweep first (more aggressive)
    signal = detect_liquidity_sweep(df)
    if signal:
        logging.info(f"Liquidity sweep signal: {signal}")
        return signal

    # Fall back to pullback detection
    signal = detect_pullback(df)
    if signal:
        logging.info(f"Pullback signal: {signal}")
        return signal

    return None

def detect_liquidity_sweep(df, lookback=20):
    """
    Detect liquidity sweep (stop hunt) patterns
    Args:
        df: DataFrame with OHLC data
        lookback: Number of periods to look back for swing points
    Returns:
        String: "BUY", "SELL", or None
    """
    if len(df) < lookback + 5:
        return None

    # Get recent high and low for swing point detection
    recent_high = df['high'].rolling(window=lookback).max().iloc[-2]  # Exclude current bar
    recent_low = df['low'].rolling(window=lookback).min().iloc[-2]   # Exclude current bar

    # Current bar data
    curr_high = df['high'].iloc[-1]
    curr_low = df['low'].iloc[-1]
    curr_close = df['close'].iloc[-1]
    curr_open = df['open'].iloc[-1]

    # Bullish liquidity sweep: price spikes below recent low then closes back above it
    if curr_low < recent_low and curr_close > recent_low:
        # Additional confirmation: strong bullish candle
        body_size = abs(curr_close - curr_open)
        total_range = curr_high - curr_low
        if body_size > (total_range * 0.6):  # At least 60% body
            return "BUY"

    # Bearish liquidity sweep: price spikes above recent high then closes back below it
    if curr_high > recent_high and curr_close < recent_high:
        # Additional confirmation: strong bearish candle
        body_size = abs(curr_close - curr_open)
        total_range = curr_high - curr_low
        if body_size > (total_range * 0.6):  # At least 60% body
            return "SELL"

    return None

def detect_pullback(df, ema_fast_period=9, ema_slow_period=21):
    """
    Detect pullback to EMA in trending market
    Args:
        df: DataFrame with OHLC data
        ema_fast_period: Fast EMA period
        ema_slow_period: Slow EMA period
    Returns:
        String: "BUY", "SELL", or None
    """
    if len(df) < ema_slow_period + 10:
        return None

    close = df['close']

    # Calculate EMAs
    ema_fast = close.ewm(span=ema_fast_period, adjust=False).mean()
    ema_slow = close.ewm(span=ema_slow_period, adjust=False).mean()

    # Current values
    curr_close = close.iloc[-1]
    curr_ema_fast = ema_fast.iloc[-1]
    curr_ema_slow = ema_slow.iloc[-1]
    prev_close = close.iloc[-2]
    prev_ema_fast = ema_fast.iloc[-2]
    prev_ema_slow = ema_slow.iloc[-2]

    # Uptrend: fast EMA above slow EMA
    if curr_ema_fast > curr_ema_slow:
        # Bullish pullback: price dips to or slightly below fast EMA then resumes up
        if curr_close >= curr_ema_fast * 0.999 and prev_close < prev_ema_fast:
            # Additional check: bullish candle
            if curr_close > df['open'].iloc[-1]:
                return "BUY"

    # Downtrend: fast EMA below slow EMA
    elif curr_ema_fast < curr_ema_slow:
        # Bearish pullback: price rallies to or slightly above fast EMA then resumes down
        if curr_close <= curr_ema_fast * 1.001 and prev_close > prev_ema_fast:
            # Additional check: bearish candle
            if curr_close < df['open'].iloc[-1]:
                return "SELL"

    return None

if __name__ == "__main__":
    # Test the derivative strategy with Asian range as primary
    logging.basicConfig(level=logging.INFO)

    # Create test data that shows an Asian range breakout
    from datetime import datetime, timedelta
    import pandas as pd

    base_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    timestamps = [base_time - pd.Timedelta(hours=i) for i in range(30, 0, -1)]

    # Create price data: Asian range 1800-1810, then breakout above
    data = []
    for i, ts in enumerate(timestamps):
        hour = ts.hour
        if 0 <= hour < 6:  # Asian session
            # Range 1800-1810
            open_price = 1800 + (i % 2) * 2
            high_price = open_price + 1
            low_price = open_price - 0.5
            close_price = open_price + (i % 3) * 0.5
        else:  # Non-Asian - make last bar breakout
            if i == len(timestamps) - 1:  # Last bar
                open_price = 1805
                high_price = 1815  # Clear breakout above
                low_price = 1804
                close_price = 1814  # Close above breakout
            else:
                open_price = 1805 + (i % 3) * 0.5
                high_price = open_price + 1
                low_price = open_price - 0.5
                close_price = open_price + (i % 2) * 0.5

        data.append({
            'time': ts,
            'open': open_price,
            'high': high_price,
            'low': low_price,
            'close': close_price,
            'volume': 1000
        })

    df = pd.DataFrame(data)

    print("Testing derivative strategy with Asian range primary...")
    print(f"Data shape: {df.shape}")
    print(f"Last bar: {df.iloc[-1].to_dict()}")

    signal = get_derivative_signal(df)
    print(f"Signal: {signal}")

    # Test with no breakout
    df_no_break = df.copy()
    df_no_break.iloc[-1, df_no_break.columns.get_loc('close')] = 1805.0  # Within range

    print("\nTesting with no breakout...")
    signal_no_break = get_derivative_signal(df_no_break)
    print(f"Signal: {signal_no_break}")