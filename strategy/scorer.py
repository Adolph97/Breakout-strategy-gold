import logging
from datetime import datetime, time
import sys
import os

def is_valid_session():
    """
    Check if current time is within valid trading sessions (London or NY)
    Returns:
        Boolean: True if in valid session
    """
    # Get current UTC time
    utc_now = datetime.utcnow()

    # London session: 8:00 AM - 4:00 PM UTC (approximately)
    # New York session: 1:00 PM - 9:00 PM UTC (approximately)

    current_time = utc_now.time()
    london_start = time(8, 0)   # 8:00 AM UTC
    london_end = time(16, 0)    # 4:00 PM UTC
    ny_start = time(13, 0)      # 1:00 PM UTC
    ny_end = time(21, 0)        # 9:00 PM UTC

    # Check if in London or NY session
    in_london = london_start <= current_time <= london_end
    in_ny = ny_start <= current_time <= ny_end

    return in_london or in_ny

def calculate_proposed_direction(bars):
    """
    Calculate proposed direction based on price action
    Simple method: compare current close to previous close
    Returns:
        String: "BUY", "SELL", or None
    """
    if len(bars) < 2:
        return None

    curr_close = bars['close'].iloc[-1]
    prev_close = bars['close'].iloc[-2]

    if curr_close > prev_close:
        return "BUY"
    elif curr_close < prev_close:
        return "SELL"
    else:
        return None

def score(tick_data, bars_df, cot_data, news_data, webhook_signal=None):
    """
    Calculate confluence score for trading signal
    Args:
        tick_data: Dict with bid, ask, spread from data feed
        bars_df: DataFrame with OHLC data
        cot_data: Dict with COT data from database
        news_data: Dict with news events from database
        webhook_signal: String from TradingView webhook ("BUY"/"SELL"/None)
    Returns:
        Dict with score, fire signal, direction, and reasons
    """
    points = 0
    reasons = []

    # Get proposed direction from price action
    proposed_direction = calculate_proposed_direction(bars_df)
    if not proposed_direction:
        return {"score": 0, "fire": False, "direction": None, "reasons": ["No clear direction"]}

    # 1. COT bias matches direction
    if cot_data and cot_data.get('bias') == proposed_direction:
        points += 1
        reasons.append("COT aligned")
    elif cot_data:
        reasons.append(f"COT bias: {cot_data.get('bias')}")

    # 2. Spread is tight (not pre-news widening)
    if tick_data and tick_data.get('spread', 999) < 0.80:  # 80 cents spread max
        points += 1
        reasons.append("Spread OK")
    elif tick_data:
        reasons.append(f"Spread: {tick_data.get('spread'):.2f}")

    # 3. No high-impact news within 30 min
    if not news_data.get('high_impact_soon', True):
        points += 1
        reasons.append("News clear")
    else:
        reasons.append("News pending")

    # 4. Derivative strategy fired
    try:
        from .derivative import get_derivative_signal
        derivative_signal, asian_range = get_derivative_signal(bars_df)
        if derivative_signal == proposed_direction:
            points += 1
            reasons.append("Strategy signal")
        elif derivative_signal:
            reasons.append(f"Strategy: {derivative_signal}")
    except Exception as e:
        logging.warning(f"Error in derivative signal calculation: {e}")
        reasons.append("Strategy: Error")
        derivative_signal = None
        asian_range = None

    # 5. Session window (London or NY)
    if is_valid_session():
        points += 1
        reasons.append("Valid session")
    else:
        reasons.append("Outside session")

    # 6. TradingView webhook agrees (if received)
    if webhook_signal and webhook_signal == proposed_direction:
        points += 1
        reasons.append("TV signal")
    elif webhook_signal:
        reasons.append(f"TV: {webhook_signal}")

    # Determine if score meets minimum threshold
    # Import config here to avoid circular imports
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        import config
        min_score = config.MIN_SCORE
    except:
        min_score = 4  # Default fallback

    fire_signal = points >= min_score

    result = {
        "score": points,
        "fire": fire_signal,
        "direction": proposed_direction if fire_signal else None,
        "reasons": reasons,
        "asian_range": asian_range
    }

    logging.info(f"Score calculation: {result}")
    return result

if __name__ == "__main__":
    # Test the scorer with sample data
    logging.basicConfig(level=logging.INFO)

    # Sample data
    tick_sample = {"bid": 1815.50, "ask": 1815.70, "spread": 0.20}

    # Sample OHLC data
    import pandas as pd
    bars_sample = pd.DataFrame({
        'open': [1800, 1805, 1810, 1808, 1812, 1815, 1813, 1818, 1820, 1817],
        'high': [1807, 1812, 1815, 1812, 1818, 1820, 1819, 1822, 1825, 1822],
        'low': [1798, 1800, 1805, 1805, 1810, 1812, 1810, 1815, 1815, 1813],
        'close': [1805, 1810, 1808, 1812, 1815, 1813, 1818, 1820, 1817, 1819]
    })

    cot_sample = {
        "bias": "BUY",
        "non_comm_long_pct": 35.0,
        "timestamp": datetime.now().isoformat()
    }

    news_sample = {
        "high_impact_soon": False,
        "upcoming_events": [],
        "timestamp": datetime.now().isoformat()
    }

    print("Testing scorer...")
    result = score(tick_sample, bars_sample, cot_sample, news_sample, webhook_signal="BUY")
    print(f"Score result: {result}")