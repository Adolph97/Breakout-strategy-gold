"""
data/price_feed.py
------------------
Live market data for the Gold Sentinel.

The feed is spot XAU/USD hourly bars from Dukascopy — the SAME source the strategy
was backtested on — and every feature is derived from those H1 bars exactly the way
trader/features.py derives them. Live and backtest therefore evaluate identical numbers.

(The previous feed was Kraken PAXG/USD. PAXG trades 24/7 with unrelated volume, so the
volume gate, the Monday range and the daily EMAs/ATR all differed from the backtest.)

There is no simulated fallback: if the feed fails or is stale, callers get None and
must skip the cycle. A signal is never scored on made-up prices.
"""

import time
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ~3 years of H1 bars: enough for the D1 EMA200 to be fully converged
# (the weight of the seed value after 750+ daily bars is < 0.1%).
LOOKBACK_DAYS = 1100
# The last closed H1 bar must be at most this old, or the feed is treated as stale.
MAX_BAR_AGE_HOURS = 3.0

_dxy_cache = {"timestamp": 0.0, "frame": None}


# ── Raw data ──────────────────────────────────────────────────────────────────

def _fetch_dukascopy(start, end, side="bid"):
    import dukascopy_python as dk
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD

    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)
    offer = dk.OFFER_SIDE_BID if side == "bid" else dk.OFFER_SIDE_ASK
    df = dk.fetch(INSTRUMENT_FX_METALS_XAU_USD, dk.INTERVAL_HOUR_1, offer, start, end)
    if df is None or len(df) == 0:
        return None
    df = df.reset_index().rename(columns={"timestamp": "time"})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df[["time", "open", "high", "low", "close", "volume"]].astype(
        {c: float for c in ["open", "high", "low", "close", "volume"]}
    )
    # Drop market-closed filler bars, as the backtest data does
    df = df[(df["volume"] > 0) & (df["high"] > df["low"])]
    return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def get_spot_h1(lookback_days=LOOKBACK_DAYS, now=None):
    """Spot XAU/USD H1 bars (bid side, UTC, bar time = bar open). None on failure."""
    now = now or datetime.now(timezone.utc)
    try:
        return _fetch_dukascopy(now - timedelta(days=lookback_days), now + timedelta(hours=1))
    except Exception as e:
        logger.error(f"Dukascopy H1 fetch failed: {e}")
        return None


def get_tick():
    """
    Current spot bid/ask, taken from the forming H1 bar on each side of the book.
    Returns None on failure (never a simulated price).
    """
    now = datetime.now(timezone.utc)
    try:
        bid_df = _fetch_dukascopy(now - timedelta(days=4), now + timedelta(hours=1), "bid")
        ask_df = _fetch_dukascopy(now - timedelta(days=4), now + timedelta(hours=1), "ask")
        if bid_df is None or ask_df is None:
            return None
        bid = float(bid_df["close"].iloc[-1])
        ask = float(ask_df["close"].iloc[-1])
        if ask <= bid:
            ask = bid + 0.50
        return {
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread": round(ask - bid, 2),
            "time": bid_df["time"].iloc[-1].to_pydatetime(),
        }
    except Exception as e:
        logger.error(f"Error fetching spot tick: {e}")
        return None


def get_bars(count=100):
    """Last `count` spot H1 bars (kept for backwards compatibility). Empty frame on failure."""
    h1 = get_spot_h1(lookback_days=max(10, int(count / 16) + 7))
    return h1.tail(count).reset_index(drop=True) if h1 is not None else pd.DataFrame()


def fetch_dxy_daily():
    """Daily DXY closes (cached for 1 hour). Returns DataFrame[date, Close] or None."""
    now = time.time()
    if _dxy_cache["frame"] is not None and now - _dxy_cache["timestamp"] < 3600:
        return _dxy_cache["frame"]
    try:
        import yfinance as yf
        dxy = yf.download("DX-Y.NYB", period="1y", interval="1d", progress=False)
        if isinstance(dxy.columns, pd.MultiIndex):
            dxy.columns = [c[0] for c in dxy.columns]
        dxy = dxy.reset_index()
        dxy["date"] = pd.to_datetime(dxy["Date"]).dt.date
        dxy = dxy[["date", "Close"]].dropna().reset_index(drop=True)
        if len(dxy) < 30:
            raise ValueError(f"only {len(dxy)} DXY rows")
        _dxy_cache["timestamp"] = now
        _dxy_cache["frame"] = dxy
        return dxy
    except Exception as e:
        logger.warning(f"Failed to fetch DXY: {e}. Using last cached frame (if any).")
        return _dxy_cache["frame"]


# ── Features (mirror of trader/features.py) ───────────────────────────────────

def dxy_signals(dxy):
    """
    Per-date DXY flags. As in trader/features.py: the row for date D carries the
    values of the previous DXY trading day, so a bar never sees its own day's close.
    """
    d = dxy.copy()
    d["ema20"] = d["Close"].ewm(span=20, adjust=False).mean()
    d["ret5"] = d["Close"].pct_change(5)
    d["dxy_bearish"] = (d["Close"] < d["ema20"]) & (d["ret5"] < 0)
    d["dxy_bullish"] = (d["Close"] > d["ema20"]) & (d["ret5"] > 0)
    out = d[["Close", "dxy_bearish", "dxy_bullish"]].shift(1)
    out["date"] = d["date"].values
    # Early in the UTC day the DXY feed may not have a row for "today" yet. The previous
    # trading day's values are then simply the last row, so add them for the next 4 dates
    # (covers a weekend) to keep live identical to the backtest.
    last = d.iloc[-1]
    extra = pd.DataFrame({
        "Close": last["Close"], "dxy_bearish": last["dxy_bearish"], "dxy_bullish": last["dxy_bullish"],
        "date": [last["date"] + timedelta(days=k) for k in range(1, 5)],
    })
    out = pd.concat([out, extra], ignore_index=True)
    return out.rename(columns={"Close": "dxy_close"})


def build_features(h1, dxy=None):
    """
    Computes, for every H1 bar, the features the strategies use. All higher-timeframe
    values are shifted so a bar only sees completed days / completed H4 bars.
    """
    df = h1.sort_values("time").reset_index(drop=True).copy()
    df["date"] = df["time"].dt.date
    df["dow"] = df["time"].dt.dayofweek
    df["hour"] = df["time"].dt.hour
    iso = df["time"].dt.isocalendar()
    df["week_key"] = iso.year.astype(str) + "_" + iso.week.astype(str)

    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

    # Daily context: yesterday's completed day
    d1 = df.set_index("time").resample("1D").agg(agg).dropna()
    d1["d1_ema50"] = d1["close"].ewm(span=50, adjust=False).mean()
    d1["d1_ema200"] = d1["close"].ewm(span=200, adjust=False).mean()
    d1["d1_atr14"] = (d1["high"] - d1["low"]).rolling(14).mean()
    d1["prev_day_high"] = d1["high"]
    d1["prev_day_low"] = d1["low"]
    d1s = d1[["d1_ema50", "d1_ema200", "d1_atr14", "prev_day_high", "prev_day_low"]].shift(1).reset_index()
    d1s["date"] = d1s["time"].dt.date
    df = df.merge(d1s.drop(columns="time"), on="date", how="left")

    # H4 context: last completed H4 bar
    h4 = df.set_index("time").resample("4h").agg(agg).dropna()
    h4["h4_ema50"] = h4["close"].ewm(span=50, adjust=False).mean()
    h4["h4_ema200"] = h4["close"].ewm(span=200, adjust=False).mean()
    h4["h4_h8"] = h4["high"].rolling(8).max().shift(1)
    h4["h4_l8"] = h4["low"].rolling(8).min().shift(1)
    h4s = h4[["h4_ema50", "h4_ema200", "h4_h8", "h4_l8"]].shift(1).reset_index()
    df = pd.merge_asof(df.sort_values("time"), h4s.sort_values("time"), on="time", direction="backward")

    # Monday range of the bar's ISO week
    mon = df[df["dow"] == 0].groupby("week_key").agg(mon_high=("high", "max"), mon_low=("low", "min")).reset_index()
    df = df.merge(mon, on="week_key", how="left")

    # H1 microstructure
    df["vol_ma20"] = df["volume"].rolling(20).mean().shift(1)
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]
    rng = np.maximum(df["high"] - df["low"], 0.01)
    df["close_pct"] = (df["close"] - df["low"]) / rng

    if dxy is not None and len(dxy):
        df = df.merge(dxy_signals(dxy), on="date", how="left")
    else:
        df["dxy_close"] = np.nan
        df["dxy_bearish"] = False
        df["dxy_bullish"] = False
    return df.sort_values("time").reset_index(drop=True)


def last_closed_index(feat, now=None):
    """Index of the last fully closed H1 bar (a bar opened at T closes at T+1h)."""
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    closed = feat.index[feat["time"] + pd.Timedelta(hours=1) <= now]
    return int(closed[-1]) if len(closed) else None


def context_from_features(feat, idx):
    """Builds the scorer's context dict for the bar at `idx`."""
    row = feat.iloc[idx]

    def f(key):
        v = row.get(key)
        return float(v) if pd.notna(v) else 0.0

    return {
        "feed": "dukascopy_xauusd",
        "features": feat,
        "bar_index": idx,
        "latest_bar": row,
        "d1_atr14": f("d1_atr14"),
        "d1_ema50": f("d1_ema50"),
        "d1_ema200": f("d1_ema200"),
        "h4_ema50": f("h4_ema50"),
        "h4_ema200": f("h4_ema200"),
        "h4_h8": f("h4_h8"),
        "h4_l8": f("h4_l8"),
        "mon_high": f("mon_high"),
        "mon_low": f("mon_low"),
        "prev_day_high": f("prev_day_high"),
        "prev_day_low": f("prev_day_low"),
        "vol_ratio": f("vol_ratio"),
        "close_pct": float(row["close_pct"]),
        "dxy_bearish": bool(row.get("dxy_bearish") is True or row.get("dxy_bearish") == True),
        "dxy_bullish": bool(row.get("dxy_bullish") is True or row.get("dxy_bullish") == True),
        "dxy_close": f("dxy_close"),
    }


def get_multi_timeframe_context(now=None):
    """
    Fetches spot H1 bars + DXY and returns the context for the last CLOSED H1 bar.
    Returns None if the feed failed or the last closed bar is stale.
    """
    now = now or datetime.now(timezone.utc)
    h1 = get_spot_h1(now=now)
    if h1 is None or len(h1) < 24 * 250:
        logger.warning("Spot H1 feed unavailable or too short — skipping cycle.")
        return None

    feat = build_features(h1, fetch_dxy_daily())
    idx = last_closed_index(feat, now)
    if idx is None:
        return None

    age_h = (pd.Timestamp(now) - (feat["time"].iloc[idx] + pd.Timedelta(hours=1))).total_seconds() / 3600.0
    if age_h > MAX_BAR_AGE_HOURS:
        logger.warning(f"Spot feed is stale: last closed bar ended {age_h:.1f}h ago — skipping cycle.")
        return None

    return context_from_features(feat, idx)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing spot XAU/USD feed (Dukascopy)...")
    print(f"Tick: {get_tick()}")
    bars = get_spot_h1(lookback_days=10)
    if bars is not None:
        print(bars.tail(5)[["time", "open", "high", "low", "close", "volume"]])
    feat = build_features(get_spot_h1(), fetch_dxy_daily())
    ctx = context_from_features(feat, len(feat) - 1)
    print({k: v for k, v in ctx.items() if k not in ("features", "latest_bar")})
