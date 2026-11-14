import time
import logging
import sys
import os
import sqlite3
from datetime import datetime, timedelta
import pytz

# Ensure logs directory exists before configuring logging
os.makedirs('logs', exist_ok=True)

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import all modules
import config
from data.price_feed import get_tick, get_bars, get_multi_timeframe_context
from data.cot_feed import get_latest_cot_from_db
from data.calendar_feed import get_latest_events_from_db
from storage.db import (
    init_database,
    log_strategy_signal,
    get_db_connection,
    check_weekly_governor,
    log_sentinel_trade,
    log_paper_trade,
    paper_trades_in_week,
    get_open_paper_trades,
    close_paper_trade,
    paper_stats
)
from strategy import sweep_reject
from strategy.scorer import score, is_valid_session, get_session_name
try:
    from signals.webhook_receiver import get_latest_signal
except ImportError:
    def get_latest_signal():
        return None
from signals.telegram_notifier import (
    send_signal_alert,
    send_trade_card,
    send_status_update,
    send_telegram_message,
    send_heartbeat_status,
    send_paper_signal,
    send_paper_result
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/trades.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def is_market_open():
    """
    Check if forex market is open and not in Friday flattening.
    Friday >= 20:00 UTC triggers 100% cash mode through Sunday night.
    Returns:
        tuple (is_open: bool, is_friday_flatten: bool)
    """
    now = datetime.now(pytz.UTC)
    weekday = now.weekday()  # Monday=0, Sunday=6
    hour = now.hour

    if weekday == 4 and hour >= 20:  # Friday 20:00+ UTC
        return False, True
    if weekday in [5, 6]:  # Saturday & Sunday
        return False, False
    return True, False


def in_scan_window(utc_dt=None) -> bool:
    """
    True while the last CLOSED H1 bar's hour is one a strategy can signal on:
    07-16 UTC for the paper Sweep & Reject test (a superset of the 8-gate
    London 07-09 / New York 13-15 hours). The 8-gate scorer applies its own session gate.
    """
    utc_dt = utc_dt or datetime.now(pytz.UTC)
    bar_hour = (utc_dt - timedelta(hours=1)).hour
    return sweep_reject.HOUR_START <= bar_hour <= sweep_reject.HOUR_END


def resolve_paper_trades(feat):
    """Closes any paper trade whose stop, target or Friday exit has been reached."""
    for trade in get_open_paper_trades():
        try:
            outcome = sweep_reject.resolve(trade, feat)
            if outcome is None:
                continue
            status, exit_time, exit_price, r = outcome
            close_paper_trade(trade["id"], status, exit_time, exit_price, round(r, 3))
            logger.info(f"PAPER trade #{trade['id']} closed: {status} at {exit_price:.2f} ({r:+.2f}R)")
            send_paper_result(trade, status, exit_price, r, paper_stats())
        except Exception as e:
            logger.error(f"Error resolving paper trade #{trade.get('id')}: {e}")


def process_paper_setup(context, tick_data):
    """Evaluates the paper-only Sweep & Reject setup on the newly closed bar."""
    bar = context["latest_bar"]
    direction = sweep_reject.detect(bar)
    if direction is None:
        return None

    entry_time = bar["time"] + timedelta(hours=1)
    if paper_trades_in_week(sweep_reject.week_key(entry_time)) >= sweep_reject.MAX_PER_WEEK:
        logger.info(f"PAPER {direction} setup on {bar['time']} skipped: weekly cap reached.")
        return None

    # Backtest entry = next bar's open (+/- half the spread). Use that bar's open if the feed
    # already has it, otherwise the live quote.
    feat, idx = context["features"], context["bar_index"]
    half_spread = tick_data.get("spread", 0.5) / 2.0
    if idx + 1 < len(feat):
        mid_ref = float(feat["open"].iloc[idx + 1]) + half_spread   # bars are bid-side
        entry = mid_ref + half_spread if direction == "BUY" else mid_ref - half_spread
    else:
        entry = tick_data["ask"] if direction == "BUY" else tick_data["bid"]

    trade = sweep_reject.build_trade(bar, direction, entry)
    if log_paper_trade(trade):
        logger.info(f"PAPER SIGNAL {direction}: entry {trade['entry_price']} "
                    f"SL {trade['sl_price']} TP {trade['tp_price']}")
        send_paper_signal(trade, paper_stats())
        return trade
    return None


def process_closed_bar(context, tick_data, heartbeat=False):
    """Scores one newly closed H1 bar with the 8-gate engine and the paper setup."""
    result = score(tick_data=tick_data, context=context)

    if heartbeat:
        send_heartbeat_status(result)

    if result['fire']:
        direction = result['direction']
        logger.info(f"*** 8-GATE SIGNAL FIRED: {direction} ***")
        logger.info(f"Setup: {result['setup']} | Score: {result['score']}/8")
        logger.info(f"Entry: ${result['entry_price']:.2f} | SL: ${result['sl_price']:.2f}")
        logger.info(f"Stage 1 TP: ${result['tp_stage1']:.2f}")
        logger.info(f"Stage 2 TP: ${result['tp_stage2']:.2f}")
        logger.info(f"Lots: Order A={result['lot_order_a']} | Order B={result['lot_order_b']}")

        send_trade_card(result)

        # Record trade to SQLite (enforces weekly governor quota)
        try:
            gates_summary = ", ".join([f"{k}:{v}" for k, v in result['gates'].items()])
            log_sentinel_trade(
                direction=direction,
                price=result['entry_price'],
                lot_size=result['total_lots'],
                sl=result['sl_price'],
                tp_stage1=result['tp_stage1'],
                tp_stage2=result['tp_stage2'],
                setup=result['setup'],
                gates_summary=gates_summary
            )
            logger.info("Trade successfully logged to SQLite trades_log.")
        except Exception as e:
            logger.error(f"Error logging trade to database: {e}")
    else:
        logger.info(f"8-gate: STAND BY — Score: {result['score']}/8 gates passed.")
        for k, v in result['gates'].items():
            logger.info(f"  {k}: {v}")

    try:
        process_paper_setup(context, tick_data)
    except Exception as e:
        logger.error(f"Error processing paper setup: {e}")

    # Telemetry
    try:
        log_strategy_signal(
            symbol=getattr(config, 'SYMBOL', 'XAUUSD'),
            timeframe="H1",
            adx=0.0,
            atr=result.get('d1_atr', 0.0),
            ema_fast=context.get('h4_ema50', 0.0),
            ema_slow=context.get('d1_ema200', 0.0),
            derivative_signal=result.get('setup', 'N/A'),
            cot_bias="N/A",
            spread=tick_data.get('spread', 0.0),
            news_clear=True,
            session_valid=is_valid_session(context["latest_bar"]["time"]),
            tv_signal=None,
            total_score=result['score'],
            fire_signal=result['fire']
        )
    except sqlite3.Error as e:
        logger.warning(f"Database write error during telemetry logging: {e}")
    return result


def main():
    """Main loop: 8-Gate Sentinel + paper-only Sweep & Reject forward test, on spot XAU/USD."""
    SCAN_INTERVAL = 300    # Check for a newly closed H1 bar every 5 minutes
    HEARTBEAT_BARS = 4     # Heartbeat every 4th scored bar (~4 hours of active scanning)
    OFF_SESSION_RESOLVE_EVERY = 12  # While idle, still settle open paper trades hourly

    is_sleeping = False
    flattened_this_weekend = False
    last_bar_time = None
    idle_cycles = 0
    bars_scored = 0

    logger.info("Starting Gold Sentinel Bot (8-Gate engine + paper Sweep & Reject test)")
    send_status_update("STARTUP")
    logger.info(f"Configuration: Symbol={getattr(config, 'SYMBOL', 'XAUUSD')} | Feed: spot XAU/USD H1 (Dukascopy)")
    logger.info(f"Scan Interval: {SCAN_INTERVAL} seconds")

    init_database()

    try:
        cycle_count = 0

        while True:
            # 1. Market open & weekend flattening
            market_open, is_friday_flatten = is_market_open()

            if not market_open:
                if is_friday_flatten and not flattened_this_weekend:
                    logger.info("Friday 20:00 UTC reached. Flattening all open risk.")
                    send_status_update("WEEKEND_FLATTEN")
                    flattened_this_weekend = True
                    is_sleeping = True
                    # Settle paper trades at the Friday 20:00 bar once it has closed
                    time.sleep(3660)
                    try:
                        ctx = get_multi_timeframe_context()
                        if ctx:
                            resolve_paper_trades(ctx["features"])
                    except Exception as e:
                        logger.error(f"Weekend paper settlement failed: {e}")
                    continue

                elif not is_sleeping:
                    send_telegram_message("💤 <b>Weekend Mode:</b> Market is closed. Bot is sleeping until Sunday night.")
                    is_sleeping = True

                logger.info("Market is closed. Sleeping 1 hour...")
                time.sleep(3600)
                continue

            flattened_this_weekend = False

            # 2. Scan window (last closed bar's hour 07-16 UTC)
            if not in_scan_window():
                if not is_sleeping:
                    send_telegram_message(
                        "🌙 <b>Scan window closed.</b> Signals are evaluated on H1 bars closing "
                        "08:00–17:00 UTC (Mon–Fri)."
                    )
                    is_sleeping = True
                idle_cycles += 1
                if idle_cycles % OFF_SESSION_RESOLVE_EVERY == 0 and get_open_paper_trades():
                    try:
                        ctx = get_multi_timeframe_context()
                        if ctx:
                            resolve_paper_trades(ctx["features"])
                    except Exception as e:
                        logger.error(f"Off-session paper settlement failed: {e}")
                time.sleep(SCAN_INTERVAL)
                continue

            if is_sleeping:
                send_telegram_message("🌅 <b>Scan window open.</b> Evaluating each closed H1 bar (spot XAU/USD).")
                is_sleeping = False
                cycle_count = 0
                idle_cycles = 0

            # 3. Market data
            try:
                context = get_multi_timeframe_context()
            except Exception as e:
                logger.error(f"Failed to fetch multi-timeframe context: {e}")
                time.sleep(30)
                continue

            if context is None:
                logger.warning("Spot feed unavailable or stale. Retrying in 60s...")
                time.sleep(60)
                continue

            resolve_paper_trades(context["features"])

            # 4. Score each closed bar exactly once (as the backtest does)
            bar_time = context["latest_bar"]["time"]
            if last_bar_time is not None and bar_time <= last_bar_time:
                cycle_count += 1
                time.sleep(SCAN_INTERVAL)
                continue

            tick_data = get_tick()
            if not tick_data:
                logger.warning("Failed to obtain spot quote. Retrying in 30s...")
                time.sleep(30)
                continue

            logger.info(f"--- New closed H1 bar {bar_time} | close {float(context['latest_bar']['close']):.2f} ---")
            bars_scored += 1
            process_closed_bar(context, tick_data, heartbeat=(bars_scored % HEARTBEAT_BARS == 0))
            last_bar_time = bar_time

            cycle_count += 1
            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal. Stopping bot...")
        send_status_update("SHUTDOWN", "Manual interruption (Ctrl+C)")
    except Exception as e:
        error_details = str(e)
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        send_status_update("ERROR", error_details)


if __name__ == "__main__":
    main()
