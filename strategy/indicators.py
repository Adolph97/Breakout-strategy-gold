import pandas as pd
import numpy as np
import logging

def calculate_adx(df, period=14):
    """
    Calculate Average Directional Index (ADX)
    Args:
        df: DataFrame with OHLC data
        period: Period for ADX calculation
    Returns:
        ADX value (float)
    """
    if len(df) < period + 1:
        return None

    high = df['high']
    low = df['low']
    close = df['close']

    # Calculate True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()

    # Calculate Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    # Smoothed directional movement
    plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).mean() / atr)

    # Calculate DX and ADX
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(window=period).mean()

    return adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else None

def calculate_atr(df, period=14):
    """
    Calculate Average True Range (ATR)
    Args:
        df: DataFrame with OHLC data
        period: Period for ATR calculation
    Returns:
        ATR value (float)
    """
    if len(df) < period:
        return None

    high = df['high']
    low = df['low']
    close = df['close']

    # Calculate True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()

    return atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else None

def calculate_ema(df, period=20):
    """
    Calculate Exponential Moving Average (EMA)
    Args:
        df: DataFrame with OHLC data
        period: Period for EMA calculation
    Returns:
        EMA value (float)
    """
    if len(df) < period:
        return None

    close = df['close']
    ema = close.ewm(span=period, adjust=False).mean()
    return ema.iloc[-1] if not pd.isna(ema.iloc[-1]) else None

def ema_200(df):
    """200-period EMA on close prices"""
    return df['close'].ewm(span=200, adjust=False).mean()

def trend_filter(df, direction):
    """
    Only trade in direction of 200 EMA trend.
    BUY  → price must be ABOVE 200 EMA
    SELL → price must be BELOW 200 EMA
    Returns True if trade is allowed, False if blocked.
    """
    if len(df) < 200:
        return True

    ema = ema_200(df)
    current_price = df['close'].iloc[-1]
    current_ema   = ema.iloc[-1]

    if direction == "BUY"  and current_price > current_ema:
        return True
    if direction == "SELL" and current_price < current_ema:
        return True

    return False

def calculate_spread_from_tick(tick_data):
    """
    Calculate spread from tick data
    Args:
        tick_data: Dict with bid and ask prices
    Returns:
        Spread value (float)
    """
    if not tick_data or 'bid' not in tick_data or 'ask' not in tick_data:
        return None
    return tick_data['ask'] - tick_data['bid']

if __name__ == "__main__":
    # Test indicators with sample data
    logging.basicConfig(level=logging.INFO)

    # Create sample OHLC data
    sample_data = {
        'open': [1800, 1805, 1810, 1808, 1812, 1815, 1813, 1818, 1820, 1817],
        'high': [1807, 1812, 1815, 1812, 1818, 1820, 1819, 1822, 1825, 1822],
        'low': [1798, 1800, 1805, 1805, 1810, 1812, 1810, 1815, 1815, 1813],
        'close': [1805, 1810, 1808, 1812, 1815, 1813, 1818, 1820, 1817, 1819]
    }
    df = pd.DataFrame(sample_data)

    print("Testing indicators...")
    print("ADX:", calculate_adx(df))
    print("ATR:", calculate_atr(df))
    print("EMA:", calculate_ema(df))

    # Test spread calculation
    tick = {'bid': 1815.50, 'ask': 1815.70}
    print("Spread:", calculate_spread_from_tick(tick))