"""
strategy/scorer.py
------------------
Unified 8-Gate Confluence Engine for Gold (XAU/USD).
Enforces the complete Institutional Sentinel Strategy:
  - Backtest (2023-07/2026, ATR-scaled rules): 27 trades, 74% win, +0.44R/trade, PF 1.75.
  - 2-Stage Asymmetric Exits (distances scale with the prior day's D1 ATR):
      Stage 1 (0.55 x ATR): Bank 50% volume & move Stop Loss to Breakeven.
      Stage 2 (1.8 x ATR): 50% Runner target.
  - Weekly Cadence Governor (Max 1 trade per calendar week).
"""

import os
import sys
import logging
from datetime import datetime, timezone, time
import pytz
import pandas as pd

logger = logging.getLogger(__name__)

# Add root directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import config
except ImportError:
    class config:
        ACCOUNT_BALANCE = 50000.0
        RISK_PER_TRADE = 0.01  # 1% standard institutional risk
        SYMBOL = "XAUUSD"

try:
    from storage.db import check_weekly_governor
except ImportError:
    def check_weekly_governor(week_start=None):
        return True

PIP_SIZE = 0.10  # 1 pip in Gold = $0.10 ($1.00 move = 10 pips)

# Must match DEFAULT_PARAMS in trader/src/strategy_gold_certainty.py (the backtested rules).
# Distances are multiples of the prior day's D1 ATR(14) so they scale with gold's price.
MIN_D1_ATR_PCT = 0.9    # D1 ATR >= 0.9% of price
MIN_VOL_RATIO  = 1.2
MIN_CLOSE_PCT  = 0.70
STAGE1_ATR     = 0.55   # TP1 = 0.55 x D1 ATR
RUNNER_ATR     = 1.8    # Runner = 1.8 x D1 ATR
H4_BUFFER_ATR  = 0.2    # stop buffer beyond H4 EMA50
SL_MIN_ATR     = 0.3    # skip the setup if the stop is tighter than this...
SL_MAX_ATR     = 1.6    # ...or wider than this


def is_valid_session(utc_dt=None) -> bool:
    """
    Check if current UTC time falls within high-liquidity Gold institutional breakout windows:
      - London Open: 07:00 - 10:00 UTC
      - New York Open: 13:00 - 15:00 UTC
    """
    if utc_dt is None:
        utc_dt = datetime.now(pytz.UTC)
    
    hour = utc_dt.hour
    is_london = hour in [7, 8, 9]
    is_ny     = hour in [13, 14, 15]
    return is_london or is_ny


def get_session_name(utc_dt=None) -> str:
    if utc_dt is None:
        utc_dt = datetime.now(pytz.UTC)
    hour = utc_dt.hour
    if hour in [7, 8, 9]:
        return "London Open"
    elif hour in [13, 14, 15]:
        return "New York Open"
    return "Off-Session"


def score(tick_data=None, bars_df=None, cot_data=None, news_data=None, webhook_signal=None, context=None):
    """
    Evaluates the complete 8-Gate Confluence Engine.
    Accepts direct multi-timeframe context or fetches from data.price_feed.
    Returns:
        dict: Complete audit with score, action, direction, 2-stage exits, sizing, gates breakdown.
    """
    now_utc = datetime.now(pytz.UTC)

    # If context is not passed, fetch multi-timeframe context
    if context is None:
        try:
            from data.price_feed import get_multi_timeframe_context
            context = get_multi_timeframe_context()
        except Exception as e:
            logger.warning(f"Could not load multi-timeframe context: {e}")
            context = {}

    current_price = tick_data.get("ask", 4000.0) if tick_data else context.get("latest_bar", {}).get("close", 4000.0)
    if isinstance(current_price, pd.Series):
        current_price = float(current_price)

    d1_atr   = float(context.get("d1_atr14", 30.0))
    d1_e50   = float(context.get("d1_ema50", 0.0))
    d1_e200  = float(context.get("d1_ema200", 0.0))
    h4_e50   = float(context.get("h4_ema50", 0.0))
    h4_h8    = float(context.get("h4_h8", 0.0))
    h4_l8    = float(context.get("h4_l8", 0.0))
    mon_high = float(context.get("mon_high", 0.0))
    mon_low  = float(context.get("mon_low", 0.0))
    vol_ratio = float(context.get("vol_ratio", 1.0))
    close_pct = float(context.get("close_pct", 0.5))
    dxy_bear = bool(context.get("dxy_bearish", False))
    dxy_bull = bool(context.get("dxy_bullish", False))

    # Gates are evaluated on the last CLOSED H1 bar (as in the backtest); the tick is only the fill price.
    bar = context.get("latest_bar")
    if bar is not None and "time" in bar:
        bar_time = pd.Timestamp(bar["time"])
        close = float(bar["close"])
    else:
        bar_time = pd.Timestamp(now_utc)
        close = current_price
    hour = bar_time.hour
    dow = bar_time.weekday()  # Monday = 0, Sunday = 6
    entry_ref = float(tick_data["ask"] if tick_data else current_price)

    # ── GATE 1: Session Window (the signal bar's hour) ──
    session_ok = is_valid_session(bar_time)
    session_name = get_session_name(bar_time)

    # ── GATE 2: Volatility Floor ──
    volatility_ok = d1_atr >= close * MIN_D1_ATR_PCT / 100.0

    # ── GATE 3: Macro Daily Trend ──
    macro_bull = (close > d1_e50) and (d1_e50 > d1_e200) if (d1_e50 > 0 and d1_e200 > 0) else False
    macro_bear = (close < d1_e50) and (d1_e50 < d1_e200) if (d1_e50 > 0 and d1_e200 > 0) else False

    # ── GATE 4: Intermarket DXY Confirmation ──
    dxy_ok_long  = dxy_bear
    dxy_ok_short = dxy_bull

    # ── GATE 5: Structural Breakout ──
    # Tue/Wed: Monday Initial Balance breakout (dow 1 = Tue, 2 = Wed)
    s1_long_mon  = (dow in [1, 2]) and (close > mon_high) and (mon_high > 0)
    s1_short_mon = (dow in [1, 2]) and (close < mon_low) and (mon_low > 0)
    # Wed/Thu: 32-hour / H4 Donchian breakout (dow 2 = Wed, 3 = Thu)
    s2_long_h4   = (dow in [2, 3]) and (close > h4_h8) and (h4_h8 > 0)
    s2_short_h4  = (dow in [2, 3]) and (close < h4_l8) and (h4_l8 > 0)

    structure_long = s1_long_mon or s2_long_h4
    structure_short = s1_short_mon or s2_short_h4

    # ── GATE 6: Candle Closing Conviction ──
    conviction_long  = close_pct >= MIN_CLOSE_PCT        # Closes in top 30% of bar range
    conviction_short = close_pct <= 1.0 - MIN_CLOSE_PCT  # Closes in bottom 30% of bar range

    # ── GATE 7: Institutional Volume Expansion ──
    volume_ok = vol_ratio >= MIN_VOL_RATIO

    # ── GATE 8: Weekly Governor (Max 1 trade / week) ──
    governor_ok = check_weekly_governor()

    # Evaluate Confluences
    long_gates_passed = [
        session_ok, volatility_ok, macro_bull, dxy_ok_long,
        structure_long, conviction_long, volume_ok, governor_ok
    ]
    short_gates_passed = [
        session_ok, volatility_ok, macro_bear, dxy_ok_short,
        structure_short, conviction_short, volume_ok, governor_ok
    ]

    # Stop must sit inside the backtested band; outside it the backtest SKIPS the setup (it never clamps)
    def stop_ok(sl):
        dist = abs(entry_ref - sl)
        return h4_e50 > 0 and SL_MIN_ATR * d1_atr <= dist <= SL_MAX_ATR * d1_atr

    sl_long_raw  = h4_e50 - H4_BUFFER_ATR * d1_atr
    sl_short_raw = h4_e50 + H4_BUFFER_ATR * d1_atr
    stop_band_ok = stop_ok(sl_long_raw) if macro_bull else (stop_ok(sl_short_raw) if macro_bear else False)

    actionable_buy  = all(long_gates_passed) and stop_ok(sl_long_raw)
    actionable_sell = all(short_gates_passed) and stop_ok(sl_short_raw)

    action = "STAND_BY"
    direction = None
    setup_name = "None"
    entry_price = entry_ref
    sl_price = 0.0
    tp_stage1 = 0.0
    tp_stage2 = 0.0

    if actionable_buy:
        action = "ACTIONABLE_BUY"
        direction = "BUY"
        setup_name = "Monday Range Expansion" if s1_long_mon else "H4 Structural Breakout"
        entry_price = float(tick_data["ask"]) if tick_data else entry_ref
        sl_price = round(sl_long_raw, 2)
        tp_stage1 = round(entry_price + STAGE1_ATR * d1_atr, 2)
        tp_stage2 = round(entry_price + RUNNER_ATR * d1_atr, 2)

    elif actionable_sell:
        action = "ACTIONABLE_SELL"
        direction = "SELL"
        setup_name = "Monday Range Breakdown" if s1_short_mon else "H4 Structural Breakdown"
        entry_price = float(tick_data["bid"]) if tick_data else entry_ref
        sl_price = round(sl_short_raw, 2)
        tp_stage1 = round(entry_price - STAGE1_ATR * d1_atr, 2)
        tp_stage2 = round(entry_price - RUNNER_ATR * d1_atr, 2)

    # Position sizing
    account_size = getattr(config, 'ACCOUNT_BALANCE', 50000.0)
    risk_pct = getattr(config, 'RISK_PER_TRADE', 0.01)
    if risk_pct > 1.0:
        risk_pct = risk_pct / 100.0  # Support e.g. 1.0 or 0.01

    sl_dist_dollars = abs(entry_price - sl_price) if sl_price > 0 else 0.0
    sl_dist_pips = sl_dist_dollars / PIP_SIZE
    risk_dollars = account_size * risk_pct

    # 1.0 lot = 100 oz -> $1 move = $100
    total_lots = round(risk_dollars / (sl_dist_dollars * 100.0), 2) if sl_dist_dollars > 0 else 0.0
    total_lots = max(0.01, total_lots) if sl_dist_dollars > 0 else 0.0
    lot_order_a = round(total_lots / 2.0, 2)
    lot_order_b = round(total_lots - lot_order_a, 2)

    points = sum(long_gates_passed) if macro_bull else (sum(short_gates_passed) if macro_bear else 0)

    gates_status = {
        "1_session_window": f"{session_name} {'[PASS]' if session_ok else '[WAIT]'}",
        "2_volatility_floor": f"ATR ${d1_atr:.2f} {f'[PASS >= {MIN_D1_ATR_PCT}% of price]' if volatility_ok else '[LOW VOL]'}",
        "3_macro_trend": f"{'Macro Bull' if macro_bull else ('Macro Bear' if macro_bear else 'Neutral')}",
        "4_intermarket_dxy": f"{'DXY Bearish (Bull Confirmed)' if dxy_bear else ('DXY Bullish (Bear Confirmed)' if dxy_bull else 'Neutral')}",
        "5_structural_breakout": f"{'Triggered' if (structure_long or structure_short) else 'Consolidating'}",
        "6_candle_conviction": f"Close {close_pct*100:.1f}% {'[PASS >= 70%]' if (conviction_long or conviction_short) else '[WEAK]'}",
        "7_volume_expansion": f"{vol_ratio:.2f}x {'[PASS >= 1.2x]' if volume_ok else '[LOW]'}",
        "8_weekly_governor": f"{'Eligible (0/1 trades)' if governor_ok else '[QUOTA FILLED]'}",
        "9_stop_band": f"{'[PASS]' if stop_band_ok else f'[SKIP: stop outside {SL_MIN_ATR}-{SL_MAX_ATR}x ATR]'}"
    }

    reasons = [f"{k.split('_', 1)[1]}: {v}" for k, v in gates_status.items()]

    return {
        "score": points,
        "fire": actionable_buy or actionable_sell,
        "action": action,
        "direction": direction,
        "setup": setup_name,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "sl_pips": sl_dist_pips,
        "tp_stage1": tp_stage1,
        "tp_stage2": tp_stage2,
        "account_size": account_size,
        "risk_pct": risk_pct * 100.0,
        "risk_dollars": risk_dollars,
        "total_lots": total_lots,
        "lot_order_a": lot_order_a,
        "lot_order_b": lot_order_b,
        "d1_atr": d1_atr,
        "gates": gates_status,
        "watch_levels": {
            "mon_high": mon_high,
            "mon_low": mon_low,
            "h4_high": h4_h8,
            "h4_low": h4_l8,
        },
        "reasons": reasons
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing 8-Gate Institutional Sentinel Scorer...")
    res = score()
    print(f"Action: {res['action']} | Direction: {res['direction']} | Score: {res['score']}/8")
    for k, v in res['gates'].items():
        print(f"  {k}: {v}")
    print("Watch Levels:", res['watch_levels'])
