import pandas as pd
import logging

logger = logging.getLogger(__name__)

def get_asian_range(df):
    """
    Asian consolidation window for XAUUSD.
    True quiet period = 21:00-03:00 UTC (Tokyo/early London).

    Only considers the MOST RECENT contiguous Asian session,
    not all Asian bars across the entire history.
    """
    df = df.copy()
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df['hour'] = df['time'].dt.hour

    asian_hours = [21, 22, 23, 0, 1, 2, 3]
    asian_mask  = df['hour'].isin(asian_hours)

    asian_bars_all = df[asian_mask]

    if asian_bars_all.empty:
        logger.debug("Asian range: no bars found in asian hours")
        return None

    # Group consecutive Asian bars into sessions.
    # Non-Asian hours create gaps > 4h between Asian groups.
    asian_bars_all = asian_bars_all.copy()
    asian_bars_all['gap'] = asian_bars_all['time'].diff().dt.total_seconds() / 3600
    asian_bars_all['session_id'] = (asian_bars_all['gap'] > 4).cumsum()

    # Take only the last (most recent) Asian session
    last_session = asian_bars_all[
        asian_bars_all['session_id'] == asian_bars_all['session_id'].max()
    ]

    if len(last_session) < 3:
        logger.debug(f"Asian range: too few bars ({len(last_session)})")
        return None

    asian_high  = last_session['high'].max()
    asian_low   = last_session['low'].min()
    range_width = asian_high - asian_low
    mid_price   = (asian_high + asian_low) / 2

    # Range quality band: adaptive based on price level
    # Gold ranges from ~1800 to ~2800 - adjust min/max accordingly
    min_range = max(1.5, mid_price * 0.002)   # $1.50 floor or 0.2% - lower threshold for more signals
    max_range = mid_price * 0.025   # 2.5% upper - wider range allowed for volatility spikes

    if range_width < min_range:
        logger.debug(
            f"Asian range too narrow: ${range_width:.2f} "
            f"< min ${min_range:.2f} at price ${mid_price:.2f}"
        )
        return None

    if range_width > max_range:
        logger.debug(
            f"Asian range too wide: ${range_width:.2f} "
            f"> max ${max_range:.2f} at price ${mid_price:.2f}"
        )
        return None

    logger.debug(
        f"Asian range accepted: ${asian_low:.2f}-${asian_high:.2f} "
        f"width=${range_width:.2f}"
    )

    return {
        "high":  asian_high,
        "low":   asian_low,
        "width": round(range_width, 2),
        "mid":   round(mid_price, 2)
    }


def get_asian_range_signal(df):
    """
    Returns tuple of (BUY/SELL/None, range_dict or None).
    Only fires during London (07-10 UTC) or NY (13-17 UTC).
    The range dict contains 'width', 'high', 'low' for position sizing.
    """
    df = df.copy()
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df['hour'] = df['time'].dt.hour

    asian_range = get_asian_range(df)
    if asian_range is None:
        return None, None

    current      = df.iloc[-1]
    current_hour = current['hour']

    london = 7  <= current_hour < 10
    ny     = 13 <= current_hour < 17

    if not (london or ny):
        return None, asian_range

    close = current['close']

    if close > asian_range['high']:
        return "BUY", asian_range
    elif close < asian_range['low']:
        return "SELL", asian_range

    return None, asian_range
