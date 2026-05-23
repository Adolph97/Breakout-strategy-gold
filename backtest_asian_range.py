"""
Backtest script for Asian Range Breakout strategy on XAUUSD
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from strategy.asian_range import get_asian_range_signal, get_asian_range
from strategy.indicators import trend_filter
import dukascopy_python
from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD
import requests
import time
import json
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

COT_CACHE = {}
COT_CACHE_TIMESTAMP = None
COT_CACHE_DURATION = 24 * 60 * 60

INSTRUMENT_MAP = {
    "XAUUSD": INSTRUMENT_FX_METALS_XAU_USD,
}

def fetch_ohlc_data(instrument="XAUUSD", years=2):
    """
    Fetch H1 data from Dukascopy — free, no API key, no rate limit
    """
    logger.info(f"Fetching {years} years of {instrument} H1 data from Dukascopy...")

    end   = datetime.now()
    start = datetime(end.year - years, end.month, end.day)

    dukas_instrument = INSTRUMENT_MAP.get(instrument)
    if not dukas_instrument:
        logger.error(f"Unknown instrument: {instrument}")
        return None

    df = dukascopy_python.fetch(
        instrument=dukas_instrument,
        interval=dukascopy_python.INTERVAL_HOUR_1,
        offer_side=dukascopy_python.OFFER_SIDE_BID,
        start=start,
        end=end,
    )

    if df is None or df.empty:
        logger.error("No data returned from Dukascopy")
        return None

    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]

    if 'timestamp' in df.columns:
        df = df.rename(columns={'timestamp': 'time'})

    logger.info(f"Retrieved {len(df)} H1 bars")
    logger.info(f"Date range: {df['time'].min()} to {df['time'].max()}")

    return df

def get_latest_cot_data():
    """
    Fetch latest COT data for Gold from CFTC website with caching
    Returns dict with COT data or None if failed
    """
    global COT_CACHE, COT_CACHE_TIMESTAMP

    # Check if cache is still valid
    current_time = time.time()
    if COT_CACHE_TIMESTAMP and (current_time - COT_CACHE_TIMESTAMP) < COT_CACHE_DURATION:
        return COT_CACHE

    try:
        # Try multiple CFTC endpoints
        COT_ENDPOINTS = [
            "https://publicreporting.cftc.gov/resource/jun7-fc8e.json",
            "https://publicreporting.cftc.gov/resource/6dca-a2qw.json",
            "https://publicreporting.cftc.gov/resource/bu7f-ynin.json",
        ]

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json',
        }

        for endpoint in COT_ENDPOINTS:
            try:
                params = {
                    "cftc_contract_market_code": "088691",
                    "$order": "report_date_as_yyyy_mm_dd DESC",
                    "$limit": "1"
                }

                response = requests.get(endpoint, params=params, headers=headers, timeout=10)

                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        latest = data[0]

                        # Extract key metrics
                        non_comm_long = int(latest.get('noncommercial_long_all', 0)) if latest.get('noncommercial_long_all') else 0
                        non_comm_short = int(latest.get('noncommercial_short_all', 0)) if latest.get('noncommercial_short_all') else 0
                        comm_long = int(latest.get('commercial_long_all', 0)) if latest.get('commercial_long_all') else 0
                        comm_short = int(latest.get('commercial_short_all', 0)) if latest.get('commercial_short_all') else 0

                        non_comm_net = non_comm_long - non_comm_short
                        total_non_comm = non_comm_long + non_comm_short

                        if total_non_comm > 0:
                            non_comm_long_pct = (non_comm_long / total_non_comm) * 100
                        else:
                            non_comm_long_pct = 50

                        # Determine bias
                        if non_comm_long_pct > 70:
                            bias = "BEARISH"  # Overbought = potential downturn
                        elif non_comm_long_pct < 30:
                            bias = "BULLISH"   # Oversold = potential upturn
                        else:
                            bias = "NEUTRAL"

                        cot_data = {
                            "report_date": latest.get('report_date_as_yyyy_mm_dd'),
                            "non_comm_long": non_comm_long,
                            "non_comm_short": non_comm_short,
                            "comm_long": comm_long,
                            "comm_short": comm_short,
                            "non_comm_net": non_comm_net,
                            "non_comm_long_pct": round(non_comm_long_pct, 2),
                            "bias": bias,
                            "timestamp": datetime.now().isoformat()
                        }

                        # Update cache
                        COT_CACHE = cot_data
                        COT_CACHE_TIMESTAMP = current_time
                        logger.info(f"Fetched COT data: {cot_data['bias']} ({cot_data['non_comm_long_pct']}%)")
                        return cot_data

            except Exception as e:
                logger.debug(f"Failed to fetch COT from {endpoint}: {e}")
                continue

        logger.warning("All COT endpoints failed")
        return COT_CACHE if COT_CACHE else None  # Return cached data if available

    except Exception as e:
        logger.error(f"Error fetching COT data: {e}")
        return COT_CACHE if COT_CACHE else None

def is_valid_session(timestamp):
    """
    Check if timestamp is within valid trading sessions (London or NY)
    London: 7:00-10:00 UTC
    NY: 13:00-17:00 UTC
    """
    # Force to timezone-naive for consistent hour extraction
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert('UTC').tz_localize(None)
    
    hour = timestamp.hour
    london_session = 7 <= hour < 10
    ny_session = 13 <= hour < 17
    return london_session or ny_session

def calculate_atr(df, period=14):
    """
    Calculate Average True Range (ATR) for given dataframe
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

def atr_filter(df, idx, period=14, min_atr=0.3):
    """
    Check if ATR is above minimum threshold at given index
    """
    if idx < period:
        return False

    # Calculate ATR for the period ending at idx
    atr_value = calculate_atr(df.iloc[:idx+1], period)
    return atr_value is not None and atr_value >= min_atr

def adx_filter(df, idx, period=14, min_adx=20):
    """
    Only trade when market is trending.
    ADX < 20 = choppy/ranging = skip
    ADX > 20 = trending = trade
    """
    if idx < period + 1:
        return True

    window = df.iloc[idx-period:idx]
    high   = window['high']
    low    = window['low']
    close  = window['close']

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)

    up   = high.diff()
    down = -low.diff()

    plus_dm  = up.where((up > down)   & (up > 0),   0)
    minus_dm = down.where((down > up) & (down > 0), 0)

    atr      = tr.ewm(span=period).mean()
    plus_di  = 100 * plus_dm.ewm(span=period).mean()  / atr
    minus_di = 100 * minus_dm.ewm(span=period).mean() / atr

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(span=period).mean().iloc[-1]

    return adx >= min_adx

def get_cached_cot_data():
    """
    Get COT data from cache, fetching if needed
    """
    global COT_CACHE, COT_CACHE_TIMESTAMP

    current_time = time.time()
    if not COT_CACHE_TIMESTAMP or (current_time - COT_CACHE_TIMESTAMP) >= COT_CACHE_DURATION:
        COT_CACHE = get_latest_cot_data()
        COT_CACHE_TIMESTAMP = current_time

    return COT_CACHE

def cot_filter(direction):
    """
    Check if trade direction aligns with COT bias
    """
    cot_data = get_cached_cot_data()
    if not cot_data:
        return True  # If no COT data, don't filter (allow trade)

    cot_bias = cot_data['bias']

    # Only trade in direction of institutional positioning
    if direction == "BUY" and cot_bias == "BULLISH":
        return True
    elif direction == "SELL" and cot_bias == "BEARISH":
        return True
    else:
        return False

def calculate_returns(trades):
    """
    Calculate returns from a list of trades
    Args:
        trades: List of dicts with 'entry_price', 'exit_price', 'direction'
    Returns:
        List of return percentages
    """
    returns = []
    for trade in trades:
        if trade['direction'] == 'BUY':
            ret = (trade['exit_price'] - trade['entry_price']) / trade['entry_price']
        else:  # SELL
            ret = (trade['entry_price'] - trade['exit_price']) / trade['entry_price']
        returns.append(ret)
    return returns

def calculate_max_drawdown(returns):
    """
    Calculate maximum drawdown from a series of returns
    Args:
        returns: List of return percentages
    Returns:
        Max drawdown as a positive percentage
    """
    if not returns:
        return 0.0

    # Calculate cumulative returns
    cum_returns = np.cumprod([1 + r for r in returns])
    # Calculate running maximum
    running_max = np.maximum.accumulate(cum_returns)
    # Calculate drawdown
    drawdown = (running_max - cum_returns) / running_max
    # Return max drawdown
    return np.max(drawdown) if len(drawdown) > 0 else 0.0

def backtest_asian_range(df, initial_capital=10000, risk_per_trade=0.02):
    """
    Backtest the Asian Range Breakout strategy with filters
    """
    logger.info("Starting Asian Range Breakout backtest with filters...")

    required_cols = ['time', 'open', 'high', 'low', 'close']
    if not all(col in df.columns for col in required_cols):
        logger.error(f"Missing required columns. Have: {list(df.columns)}")
        return None

    df = df.sort_values('time').reset_index(drop=True)

    trades = []
    capital = initial_capital
    peak_capital = initial_capital

    traded_ranges = set()
    last_trade_date = None

    start_idx = 0
    while start_idx < len(df) - 24:
        time_diff = df.iloc[start_idx + 23]['time'] - df.iloc[start_idx]['time']
        if time_diff >= pd.Timedelta(hours=23):
            break
        start_idx += 1

    if start_idx >= len(df) - 24:
        logger.error("Insufficient data for backtest")
        return None

    logger.info(f"Starting backtest from index {start_idx} ({df.iloc[start_idx]['time']})")

    for i in range(start_idx + 24, len(df)):
        data_for_range = df.iloc[start_idx:i]

        signal_str, asian_range_signal = get_asian_range_signal(data_for_range)

        if signal_str is None:
            continue

        current_time = df.iloc[i]['time']
        if not is_valid_session(current_time):
            continue

        if not atr_filter(df, i, period=14, min_atr=0.3):
            continue

        atr_value = calculate_atr(df.iloc[:i+1], 14)
        if atr_value is not None and atr_value > 50:
            continue

        cot_data = get_cached_cot_data()
        cot_score = 1 if (cot_data and cot_data.get('bias') == ("BULLISH" if signal_str == "BUY" else "BEARISH")) else 0

        use_trend_filter = False
        if use_trend_filter and not trend_filter(df.iloc[:i], signal_str):
            logger.debug(f"Trade blocked by 200 EMA filter: {signal_str}")
            continue

        use_adx_filter = False
        if use_adx_filter and not adx_filter(df, i, period=14, min_adx=20):
            logger.debug(f"Trade blocked by ADX filter: {signal_str}")
            continue

        if i + 1 >= len(df):
            break

        entry_bar = df.iloc[i + 1]
        entry_price = entry_bar['open']
        entry_time = entry_bar['time']

        asian_range_data = df.iloc[start_idx:i+1]
        asian_range = get_asian_range(asian_range_data)

        if asian_range is None:
            continue

        range_key = f"{round(asian_range['high'], 1)}_{round(asian_range['low'], 1)}"
        trade_date = entry_bar['time'].date()

        if range_key in traded_ranges:
            continue
        if last_trade_date == trade_date:
            continue

        range_width = asian_range['width']
        asian_high = asian_range['high']
        asian_low = asian_range['low']

        if signal_str == "BUY":
            sl_price = asian_low - (range_width * 0.1)
            tp_price = entry_price + (range_width * 2.0)
        else:
            sl_price = asian_high + (range_width * 0.1)
            tp_price = entry_price - (range_width * 2.0)

        sl_distance = abs(entry_price - sl_price)
        if sl_distance <= 0:
            sl_distance = range_width

        position_size = (capital * risk_per_trade) / sl_distance

        logger.info(f"Signal: {signal_str} | Range: ${asian_low:.2f}-${asian_high:.2f} (${range_width:.2f}) | Entry: ${entry_price:.2f} | SL: ${sl_price:.2f} | TP: ${tp_price:.2f}")

        traded_ranges.add(range_key)
        last_trade_date = trade_date

        trade_executed = False
        exit_price = None
        exit_time = None
        exit_idx = None
        entry_date = entry_time.date()

        for j in range(i + 1, len(df)):
            current_bar = df.iloc[j]
            high_price = current_bar['high']
            low_price = current_bar['low']
            bar_date = current_bar['time'].date()
            days_held = (bar_date - entry_date).days

            if days_held > 3:
                exit_price = current_bar['close']
                exit_time = current_bar['time']
                exit_idx = j
                trade_executed = True
                break

            if signal_str == "BUY":
                if low_price <= sl_price:
                    exit_price = sl_price
                    exit_time = current_bar['time']
                    exit_idx = j
                    trade_executed = True
                    break
                elif high_price >= tp_price:
                    exit_price = tp_price
                    exit_time = current_bar['time']
                    exit_idx = j
                    trade_executed = True
                    break
            else:
                if high_price >= sl_price:
                    exit_price = sl_price
                    exit_time = current_bar['time']
                    exit_idx = j
                    trade_executed = True
                    break
                elif low_price <= tp_price:
                    exit_price = tp_price
                    exit_time = current_bar['time']
                    exit_idx = j
                    trade_executed = True
                    break

        if not trade_executed and j + 1 >= len(df):
            exit_price = df.iloc[-1]['close']
            exit_time = df.iloc[-1]['time']
            exit_idx = len(df) - 1
            trade_executed = True

        if trade_executed:
            if signal_str == 'BUY':
                pnl = (exit_price - entry_price) * position_size
            else:
                pnl = (entry_price - exit_price) * position_size

            capital += pnl
            if capital > peak_capital:
                peak_capital = capital

            trades.append({
                'entry_time': entry_time,
                'exit_time': exit_time,
                'direction': signal_str,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'pnl': pnl,
                'capital_after': capital
            })

            start_idx = max(start_idx, exit_idx - 24) if exit_idx else start_idx
        else:
            break

    logger.info(f"Backtest completed with filters. {len(trades)} trades executed.")

    if not trades:
        logger.warning("No trades generated - check strategy parameters and filters")
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0,
            'avg_pnl': 0,
            'total_pnl': 0,
            'final_capital': initial_capital,
            'return_pct': 0,
            'max_drawdown': 0,
            'sharpe_ratio': 0,
            'trades': []
        }

    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] <= 0]

    total_pnl = sum(t['pnl'] for t in trades)
    avg_pnl = np.mean([t['pnl'] for t in trades]) if trades else 0
    win_rate = len(winning_trades) / len(trades) if trades else 0
    return_pct = (capital - initial_capital) / initial_capital * 100

    returns = calculate_returns(trades)
    sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252) if len(returns) > 1 and np.std(returns) > 0 else 0
    max_dd = calculate_max_drawdown(returns)

    results = {
        'total_trades': len(trades),
        'winning_trades': len(winning_trades),
        'losing_trades': len(losing_trades),
        'win_rate': win_rate,
        'avg_pnl': avg_pnl,
        'total_pnl': total_pnl,
        'final_capital': capital,
        'return_pct': return_pct,
        'max_drawdown': max_dd,
        'sharpe_ratio': sharpe_ratio,
        'trades': trades
    }

    return results

def print_backtest_results(results, years=2):
    """
    Print formatted backtest results
    """
    print("\n" + "="*60)
    print("ASIAN RANGE BREAKOUT STRATEGY BACKTEST RESULTS (WITH FILTERS)")
    print("="*60)
    print(f"Period: {years} years of XAUUSD H1 data")
    print(f"Initial Capital: $10,000")
    print(f"-" * 60)
    print(f"Total Trades: {results['total_trades']}")
    print(f"Winning Trades: {results['winning_trades']}")
    print(f"Losing Trades: {results['losing_trades']}")
    print(f"Win Rate: {results['win_rate']*100:.1f}%")
    print(f"-" * 60)
    print(f"Total P&L: ${results['total_pnl']:.2f}")
    print(f"Average P&L per Trade: ${results['avg_pnl']:.2f}")
    print(f"Final Capital: ${results['final_capital']:.2f}")
    print(f"Return: {results['return_pct']:.2f}%")
    print(f"-" * 60)
    print(f"Max Drawdown: {results['max_drawdown']*100:.2f}%")
    print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
    print(f"-" * 60)

    # Performance assessment
    print("PERFORMANCE ASSESSMENT:")
    print("-" * 60)
    if results['win_rate'] >= 0.55:
        print("✅ Win Rate (>55%): PASS")
    else:
        print("❌ Win Rate (>55%): FAIL ({:.1f}%)".format(results['win_rate']*100))

    if results['sharpe_ratio'] >= 1.0:
        print("✅ Sharpe Ratio (>1.0): PASS")
    else:
        print("❌ Sharpe Ratio (>1.0): FAIL ({:.2f})".format(results['sharpe_ratio']))

    if results['max_drawdown'] <= 0.20:
        print("✅ Max Drawdown (<20%): PASS")
    else:
        print("❌ Max Drawdown (<20%): FAIL ({:.1f}%)".format(results['max_drawdown']*100))

    trades_per_month = results['total_trades'] / (years * 12)  # years * 12 months
    if trades_per_month >= 8:
        print("✅ Trades/Month (>8): PASS ({:.1f})".format(trades_per_month))
    else:
        print("❌ Trades/Month (>8): FAIL ({:.1f}%)".format(trades_per_month))

    print("="*60)

    # Sizing recommendation
    print("\nPOSITION SIZING ANALYSIS:")
    print("-" * 60)
    if results['total_trades'] > 0:
        winning = [t for t in results['trades'] if t['pnl'] > 0]
        losing  = [t for t in results['trades'] if t['pnl'] <= 0]
        avg_win  = np.mean([t['pnl'] for t in winning]) if winning else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losing])) if losing else 1
        profit_factor = sum(t['pnl'] for t in winning) / abs(sum(t['pnl'] for t in losing)) if losing else 999
        kelly_pct = ((results['win_rate'] * (avg_win/avg_loss)) - (1 - results['win_rate'])) / (avg_win/avg_loss) if avg_loss > 0 else 0
        kelly_pct = max(0, min(kelly_pct, 0.25))
        print(f"Avg win:     ${avg_win:.2f}")
        print(f"Avg loss:    ${avg_loss:.2f}")
        print(f"Profit factor: {profit_factor:.2f}")
        print(f"Kelly optimal: {kelly_pct*100:.1f}% of capital")
        print(f"Conservative (25% Kelly): {kelly_pct*0.25*100:.1f}% of capital")
        print(f"Sharpe-based max: {min(results['sharpe_ratio']*0.02, 0.05)*100:.2f}% per trade")
    else:
        print("No trades to analyze")
    print("="*60)


def walk_forward_test(df, train_months=6, test_months=3):
    """
    Splits data into rolling train/test windows.
    Strategy parameters fixed — only tests on unseen data.
    Uses 3-month test windows for more reliable statistics.
    """
    df['time'] = pd.to_datetime(df['time'], utc=True)

    results = []
    start   = df['time'].min()
    end     = df['time'].max()

    window_start = start
    window_num   = 1

    while True:
        train_end = window_start + pd.DateOffset(months=train_months)
        test_end  = train_end   + pd.DateOffset(months=test_months)

        if test_end > end:
            break

        test_df = df[(df['time'] >= train_end) & (df['time'] < test_end)]

        if len(test_df) < 100:
            break

        result = backtest_asian_range(test_df, initial_capital=10000)

        if result and result['total_trades'] > 0:
            profit_factor = 0
            winning_trades = [t for t in result['trades'] if t['pnl'] > 0]
            losing_trades = [t for t in result['trades'] if t['pnl'] <= 0]
            if losing_trades and sum(t['pnl'] for t in losing_trades) != 0:
                profit_factor = abs(sum(t['pnl'] for t in winning_trades) / sum(t['pnl'] for t in losing_trades))
            elif winning_trades:
                profit_factor = 999

            passed = (result['win_rate'] >= 0.45 and
                     result['sharpe_ratio'] >= 0.5 and
                     result['max_drawdown'] <= 0.25)
            results.append({
                'window':     window_num,
                'test_start': train_end.strftime('%Y-%m'),
                'test_end':   test_end.strftime('%Y-%m'),
                'trades':     result['total_trades'],
                'win_rate':   round(result['win_rate'] * 100, 1),
                'sharpe':     round(result['sharpe_ratio'], 2),
                'drawdown':   round(result['max_drawdown'] * 100, 1),
                'return_pct': round(result['return_pct'], 1),
                'profit_factor': round(profit_factor, 2),
                'passed':     passed
            })

            print(f"Window {window_num} [{train_end.strftime('%Y-%m')} → "
                  f"{test_end.strftime('%Y-%m')}]: "
                  f"WR={result['win_rate']*100:.1f}% "
                  f"Sharpe={result['sharpe_ratio']:.2f} "
                  f"DD={result['max_drawdown']*100:.1f}% "
                  f"PF={profit_factor:.2f} "
                  f"{'✅' if passed else '❌'}")

        window_start += pd.DateOffset(months=2)
        window_num   += 1

    return results


def print_walk_forward_summary(results):
    if not results:
        print("No walk-forward results")
        return

    passed  = sum(1 for r in results if r['passed'])
    total   = len(results)
    avg_wr  = sum(r['win_rate']   for r in results) / total
    avg_sh  = sum(r['sharpe']     for r in results) / total
    avg_dd  = sum(r['drawdown']   for r in results) / total
    avg_pf  = sum(r['profit_factor'] for r in results) / total

    print("\n" + "="*60)
    print("WALK-FORWARD VALIDATION SUMMARY")
    print("="*60)
    print(f"Windows tested:     {total}")
    print(f"Windows passed:     {passed}/{total} "
          f"({'✅ ROBUST' if passed/total >= 0.7 else '❌ UNSTABLE'})")
    print(f"Avg win rate:       {avg_wr:.1f}%")
    print(f"Avg Sharpe:         {avg_sh:.2f}")
    print(f"Avg profit factor:  {avg_pf:.2f}")
    print(f"Avg max drawdown:   {avg_dd:.1f}%")
    print("="*60)

    if passed / total >= 0.7:
        print("✅ STRATEGY IS ROBUST — proceed to paper trading")
    else:
        print("❌ STRATEGY UNSTABLE — needs more work before live")
    print("="*60)

    print("\nRECOMMENDED POSITION SIZING:")
    print("-" * 60)
    sharpe_sorted = sorted([r['sharpe'] for r in results])
    wr_sorted = sorted([r['win_rate'] for r in results])
    med_sharpe = sharpe_sorted[total // 2]
    med_wr     = wr_sorted[total // 2]
    print(f"Median win rate across windows: {med_wr:.1f}%")
    print(f"Median Sharpe across windows:   {med_sharpe:.2f}")
    suggested_risk = min(max(med_sharpe * 0.015, 0.01), 0.03)
    print(f"Suggested risk per trade:       {suggested_risk*100:.2f}%")
    print(f"Max suggested risk per trade:   3.00%")
    print("="*60)


def run_full_analysis(instrument="XAUUSD", years=2, risk_per_trade=0.02):
    """Run full backtest + walk-forward + sizing projection for one instrument."""
    df = fetch_ohlc_data(instrument, years=years)
    if df is None:
        print(f"❌ Failed to fetch {instrument} data")
        return None

    print(f"\n{'='*60}")
    print(f"  {instrument} — FULL BACKTEST (risk {risk_per_trade*100:.1f}%/trade)")
    print(f"{'='*60}")
    results = backtest_asian_range(df, initial_capital=10000, risk_per_trade=risk_per_trade)
    if results:
        print_backtest_results(results, years=years)
        total_return = results['return_pct']
        if results['total_trades'] > 0:
            winning = [t for t in results['trades'] if t['pnl'] > 0]
            losing  = [t for t in results['trades'] if t['pnl'] <= 0]
            avg_win  = np.mean([t['pnl'] for t in winning]) if winning else 0
            avg_loss = abs(np.mean([t['pnl'] for t in losing])) if losing else 0.01
            kelly = ((results['win_rate'] * (avg_win/avg_loss)) - (1 - results['win_rate'])) / (avg_win/avg_loss) if avg_win > 0 and avg_loss > 0 else 0
            kelly = max(0, min(kelly, 0.25))
            conservative_kelly = kelly * 0.25
            print(f"\n  RETURN PROJECTIONS FOR {instrument}:")
            print(f"  Current (2% risk):     {total_return:.1f}% over {years} years")
            print(f"  At conservative Kelly ({conservative_kelly*100:.1f}% risk):  {total_return * (conservative_kelly / risk_per_trade):.1f}%")
            if kelly > 0:
                print(f"  At Kelly optimal ({kelly*100:.1f}% risk):       {total_return * (kelly / risk_per_trade):.1f}%")
        else:
            print(f"\n  {instrument}: No trades generated — strategy parameters incompatible")

    print(f"\n{'='*60}")
    print(f"  {instrument} — WALK-FORWARD VALIDATION")
    print(f"{'='*60}")
    wf_results = walk_forward_test(df, train_months=6, test_months=2)
    print_walk_forward_summary(wf_results)
    return results


if __name__ == "__main__":
    run_full_analysis("XAUUSD", years=2, risk_per_trade=0.02)