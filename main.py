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
    log_sentinel_trade
)
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
    send_heartbeat_status
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


def main():
    """Main signal generation loop running the 8-Gate Institutional Sentinel"""
    # --- CONFIGURATION ---
    SCAN_INTERVAL = 300   # Scan every 5 minutes
    HEARTBEAT_CYCLES = 48 # Heartbeat every 4 hours (48 * 5 mins) during active sessions
    
    # Session state tracking
    is_sleeping = False
    flattened_this_weekend = False
    # ---------------------

    logger.info("Starting Gold Institutional Sentinel Bot (8-Gate Confluence Engine)")
    
    # Send Startup Notification
    send_status_update("STARTUP")
    
    logger.info(f"Configuration: Symbol={getattr(config, 'SYMBOL', 'XAUUSD')}")
    logger.info("Targets scale with D1 ATR: TP1 0.55x ATR -> BE lock | Runner 1.8x ATR")
    logger.info(f"Scan Interval: {SCAN_INTERVAL} seconds")

    # Initialize database
    init_database()

    try:
        # Get a persistent database connection for the main loop
        db_conn = get_db_connection()
        cycle_count = 0

        while True:
            # 1. Market Open & Weekend Risk Flattening Check
            market_open, is_friday_flatten = is_market_open()

            if not market_open:
                if is_friday_flatten and not flattened_this_weekend:
                    logger.info("Friday 20:00 UTC reached. Flattening all open risk.")
                    send_status_update("WEEKEND_FLATTEN")
                    flattened_this_weekend = True
                    is_sleeping = True

                elif not is_sleeping:
                    send_telegram_message("💤 <b>Weekend Mode:</b> Market is closed. Bot is sleeping until Sunday night.")
                    is_sleeping = True

                logger.info("Market is closed. Sleeping 1 hour...")
                time.sleep(3600)
                continue

            # Reset weekend flag when week reopens
            flattened_this_weekend = False

            # 2. STRICT SESSION LOCK (London 07-10 UTC & NY 13-15 UTC)
            # Signals come from the last CLOSED H1 bar, so scan while that bar's hour is a session hour
            if not is_valid_session(datetime.now(pytz.UTC) - timedelta(hours=1)):
                if not is_sleeping:
                    session_name = get_session_name()
                    send_telegram_message(
                        f"🌙 <b>Session Lock:</b> Current window is {session_name}. "
                        f"Scanning suspended until London (07:00 UTC) or NY (13:00 UTC)."
                    )
                    is_sleeping = True
                logger.info(f"Outside high-liquidity session windows ({get_session_name()}). Waiting...")
                time.sleep(SCAN_INTERVAL)
                continue
            
            # Waking up for active session
            if is_sleeping:
                send_telegram_message(f"🌅 <b>Session Active:</b> {get_session_name()} window open. Evaluating 8-Gate Confluence...")
                is_sleeping = False
                cycle_count = 0

            logger.info("--- New 8-Gate scanning cycle ---")

            # 3. Fetch Live Market Data & Multi-Timeframe Context
            tick_data = get_tick()
            try:
                context = get_multi_timeframe_context()
            except Exception as e:
                logger.error(f"Failed to fetch multi-timeframe context: {e}")
                time.sleep(10)
                continue

            if not tick_data or context is None:
                logger.warning("Failed to obtain market feed. Retrying in 10s...")
                time.sleep(10)
                continue

            # 4. Evaluate 8-Gate Confluence Engine
            result = score(tick_data=tick_data, context=context)

            # 5. Heartbeat (Every 4 hours during active sessions)
            if cycle_count > 0 and cycle_count % HEARTBEAT_CYCLES == 0:
                send_heartbeat_status(result)

            # 6. SIGNAL FIRE & ASYMMETRIC EXECUTION
            if result['fire']:
                direction = result['direction']
                logger.info(f"*** HIGH-CONVICTION SIGNAL FIRED: {direction} ***")
                logger.info(f"Setup: {result['setup']} | Score: {result['score']}/8")
                logger.info(f"Entry: ${result['entry_price']:.2f} | SL: ${result['sl_price']:.2f}")
                logger.info(f"Stage 1 TP: ${result['tp_stage1']:.2f}")
                logger.info(f"Stage 2 TP: ${result['tp_stage2']:.2f}")
                logger.info(f"Lots: Order A={result['lot_order_a']} | Order B={result['lot_order_b']}")

                # Broadcast complete Trade Card to Telegram
                send_trade_card(result)

                # Record trade to SQLite (Enforces weekly governor quota)
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
                logger.info(f"Status: STAND BY — Score: {result['score']}/8 gates passed.")
                for k, v in result['gates'].items():
                    logger.info(f"  {k}: {v}")

            # 7. Telemetry Logging to SQLite
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
                    session_valid=is_valid_session(),
                    tv_signal=None,
                    total_score=result['score'],
                    fire_signal=result['fire']
                )
            except sqlite3.Error as e:
                logger.warning(f"Database write error during telemetry logging: {e}")

            # Increment and wait
            cycle_count += 1
            logger.info(f"--- End of cycle {cycle_count} (Waiting {SCAN_INTERVAL/60:.1f} mins) ---")
            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal. Stopping bot...")
        send_status_update("SHUTDOWN", "Manual interruption (Ctrl+C)")
    except Exception as e:
        error_details = str(e)
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        send_status_update("ERROR", error_details)
    finally:
        if 'db_conn' in locals() and db_conn:
            try:
                db_conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
