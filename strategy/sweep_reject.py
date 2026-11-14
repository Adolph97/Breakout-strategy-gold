"""
strategy/sweep_reject.py
------------------------
PAPER-ONLY forward test: "prior-day sweep & reject, with the daily trend".

Rule (evaluated on each closed H1 bar, Mon-Fri, bar hour 07-16 UTC):
    LONG : bar's low trades BELOW yesterday's low, the bar CLOSES back above it,
           in the top 40% of its own range, and the close is above the D1 EMA50.
    SHORT: mirror image (sweep of yesterday's high, close below D1 EMA50).
Execution (as backtested): enter at the next bar's open, stop 1.0 x D1 ATR,
target 1.5 x D1 ATR, flat Friday 20:00 UTC, at most 1 trade per ISO week.

Evidence (spot H1, 2010 to Nov-2026, costs included): 690 trades, ~41/yr, 49% win,
+0.07R per trade, t = 1.7, max drawdown 15.7R; positive in each of the four multi-year
eras but -0.28R on the 13 trades after Jul-2026. That is NOT a proven edge — it was the
best of ~10 weekly-cadence setups tested, which is roughly what noise produces.
Hence paper only: signals are logged and resolved automatically so the result can be
judged after 50+ forward trades. Do not change the parameters during the test.

"Yesterday" is the previous UTC calendar date present in the data (on Mondays that is
the short Sunday-evening session) — kept exactly as backtested.
"""

import pandas as pd

HOUR_START, HOUR_END = 7, 16     # signal bar's UTC hour, inclusive
MIN_CLOSE_PCT = 0.60
STOP_ATR = 1.0
TARGET_ATR = 1.5
FRIDAY_EXIT_HOUR = 20
MAX_PER_WEEK = 1
SETUP_NAME = "Sweep & Reject (paper)"
COST = 0.40                      # $ per oz round trip (spread + commission), as in the backtest


def week_key(ts) -> str:
    iso = pd.Timestamp(ts).isocalendar()
    return f"{iso[0]}_{iso[1]}"


def detect(row):
    """Returns 'BUY', 'SELL' or None for one closed H1 feature row."""
    need = ("prev_day_high", "prev_day_low", "d1_ema50", "d1_atr14")
    if any(pd.isna(row.get(k)) for k in need) or float(row["d1_atr14"]) <= 0:
        return None
    if int(row["dow"]) > 4 or not (HOUR_START <= int(row["hour"]) <= HOUR_END):
        return None

    close, cp = float(row["close"]), float(row["close_pct"])
    if (float(row["low"]) < float(row["prev_day_low"]) and close > float(row["prev_day_low"])
            and cp >= MIN_CLOSE_PCT and close > float(row["d1_ema50"])):
        return "BUY"
    if (float(row["high"]) > float(row["prev_day_high"]) and close < float(row["prev_day_high"])
            and cp <= 1.0 - MIN_CLOSE_PCT and close < float(row["d1_ema50"])):
        return "SELL"
    return None


def build_trade(row, direction, entry_price):
    """Paper trade for a signal on `row`, entered at `entry_price` (next bar's open)."""
    atr = float(row["d1_atr14"])
    side = 1 if direction == "BUY" else -1
    signal_time = pd.Timestamp(row["time"])
    return {
        "setup": SETUP_NAME,
        "direction": direction,
        "signal_time": signal_time.isoformat(),
        "entry_time": (signal_time + pd.Timedelta(hours=1)).isoformat(),
        "week_key": week_key(signal_time + pd.Timedelta(hours=1)),
        "entry_price": round(float(entry_price), 2),
        "sl_price": round(float(entry_price) - side * STOP_ATR * atr, 2),
        "tp_price": round(float(entry_price) + side * TARGET_ATR * atr, 2),
        "d1_atr": round(atr, 2),
        "swept_level": round(float(row["prev_day_low"] if side == 1 else row["prev_day_high"]), 2),
    }


def resolve(trade, h1, now=None):
    """
    Walks closed H1 bars from the entry bar onward and returns (status, exit_time, exit_price, r)
    or None if the trade is still open. Same order of checks as the backtest: Friday flatten,
    then stop (pessimistic when stop and target share a bar), then target.
    """
    side = 1 if trade["direction"] == "BUY" else -1
    entry, sl, tp = float(trade["entry_price"]), float(trade["sl_price"]), float(trade["tp_price"])
    risk = abs(entry - sl)
    if risk <= 0:
        return ("void", trade["entry_time"], entry, 0.0)

    entry_time = pd.Timestamp(trade["entry_time"])
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    bars = h1[(h1["time"] >= entry_time) & (h1["time"] + pd.Timedelta(hours=1) <= now)]

    for i, (_, b) in enumerate(bars.iterrows()):
        t = b["time"]
        if i > 0 and t.dayofweek == 4 and t.hour >= FRIDAY_EXIT_HOUR:
            px = float(b["close"])
            return ("time_exit", t.isoformat(), px, (side * (px - entry) - COST) / risk)
        if (b["low"] <= sl) if side == 1 else (b["high"] >= sl):
            return ("stopped", t.isoformat(), sl, (-risk - COST) / risk)
        if i > 0 and ((b["high"] >= tp) if side == 1 else (b["low"] <= tp)):
            return ("target", t.isoformat(), tp, (side * (tp - entry) - COST) / risk)
    return None
