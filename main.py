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
from data.price_feed import get_tick, get_bars
from data.cot_feed import get_latest_cot_from_db
from data.calendar_feed import get_latest_events_from_db
from storage.db import init_database, log_strategy_signal, get_db_connection
from strategy.indicators import calculate_adx, calculate_atr, calculate_ema
from strategy.scorer import score, is_valid_session
from signals.webhook_receiver import get_latest_signal
from signals.telegram_notifier import send_signal_alert, send_status_update, send_telegram_message

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
    Check if forex market is open (simplified - XAUUSD trades 24/5)
    Returns True if Monday-Friday, not Saturday-Sunday
    """
    now = datetime.now(pytz.UTC)
    weekday = now.weekday()  # Monday=0, Sunday=6
    return weekday < 5  # Monday-Friday

def main():
    """Main signal generation loop"""
    # --- CONFIGURATION ---
    SCAN_INTERVAL = 300   # Scan every 5 minutes
    HEARTBEAT_CYCLES = 48 # Heartbeat every 4 hours (48 * 5 mins) during active sessions
    
    # SIGNAL LOCK: Prevents duplicate alerts for the same direction within 4 hours
    COOLDOWN_PERIOD = timedelta(hours=4)
    last_signal_time = {"BUY": None, "SELL": None}
    
    # Session state tracking
    is_sleeping = False
    # ---------------------

    logger.info("Starting Gold Trader Signal Bot (Signal-Only Mode)")
    
    # Send Startup Notification
    send_status_update("STARTUP")
    
    logger.info(f"Configuration: Symbol={config.SYMBOL}, Timeframe={config.TIMEFRAME}")
    logger.info(f"Minimum score required for Telegram alert: {config.MIN_SCORE}/6")
    logger.info(f"Scan Interval: {SCAN_INTERVAL} seconds")

    # Initialize database
    init_database()

    try:
        # Get a persistent database connection for the main loop
        db_conn = get_db_connection()
        
        cycle_count = 0
        # Main loop
        while True:
            # 1. Market Open Check (Weekends)
            if not is_market_open():
                if not is_sleeping:
                    send_telegram_message("💤 <b>Weekend Mode:</b> Market is closed. Bot is sleeping until Sunday night.")
                    is_sleeping = True
                logger.info("Market is closed. Waiting...")
                time.sleep(3600) # Check once an hour on weekends
                continue

            # 2. STRICT SESSION LOCK (Only trade London/NY)
            if not is_valid_session():
                if not is_sleeping:
                    send_telegram_message("🌙 <b>Session Lock:</b> Trading hours ended. Bot is entering power-save mode.")
                    is_sleeping = True
                logger.info("Outside valid trading sessions (London/NY). Scanning suspended.")
                time.sleep(SCAN_INTERVAL)
                continue
            
            # If we were sleeping and now session is valid
            if is_sleeping:
                send_telegram_message("🌅 <b>Waking Up:</b> Market session active. Starting Gold scan...")
                is_sleeping = False
                cycle_count = 0 # Reset cycle count for the new session

            # 3. Heartbeat (Only during active sessions, every 4 hours)
            if cycle_count > 0 and cycle_count % HEARTBEAT_CYCLES == 0:
                send_telegram_message("❤️ <b>Heartbeat:</b> Bot is actively scanning Gold. All systems green.")

            logger.info("--- New scanning cycle ---")

            # Get market data (Live Feed)
            tick_data = get_tick()
            bars_data = get_bars(count=100)

            if not tick_data or bars_data is None:
                logger.warning("Failed to get market data. Retrying in 10s...")
                time.sleep(10)
                continue

            # Get supplemental data
            try:
                cot_data = get_latest_cot_from_db(db_conn)
                news_data = get_latest_events_from_db(db_conn)
            except sqlite3.Error as e:
                logging.warning(f"Database read error: {e}. Attempting reconnect...")
                # Try to reconnect
                try:
                    db_conn.close()
                except:
                    pass
                db_conn = get_db_connection()
                cot_data = get_latest_cot_from_db(db_conn)
                news_data = get_latest_events_from_db(db_conn)
            webhook_signal = get_latest_signal()

            # Calculate indicators
            adx = calculate_adx(bars_data)
            atr = calculate_atr(bars_data)
            ema_fast = calculate_ema(bars_data, period=9)
            ema_slow = calculate_ema(bars_data, period=21)

            # Calculate score
            result = score(
                tick_data=tick_data,
                bars_df=bars_data,
                cot_data=cot_data,
                news_data=news_data,
                webhook_signal=webhook_signal.get('direction') if webhook_signal else None
            )

            # 4. SIGNAL FIRE & COOLDOWN LOGIC
            if result['fire']:
                direction = result['direction']
                now = datetime.now(pytz.UTC)
                
                # Check if this direction is currently "locked" (Cooldown)
                last_time = last_signal_time.get(direction)
                if last_time and (now - last_time) < COOLDOWN_PERIOD:
                    logger.info(f"SIGNAL SUPPRESSED: {direction} fired, but is currently in a 4-hour cooldown lock.")
                else:
                    logger.info(f"SIGNAL FIRED: {direction} with score {result['score']}")
                    
                    # Update the lock time
                    last_signal_time[direction] = now
                    
                    # Format a mock trade_result for the Telegram alert
                    mock_trade_result = {
                        "success": True,
                        "price": tick_data['ask'] if direction == "BUY" else tick_data['bid'],
                        "sl": 0, "tp": 0, "lot_size": 0, "ticket": "SIGNAL_ONLY"
                    }
                    
                    # Dynamic SL/TP calculation
                    if atr:
                        sl_multiplier, tp_multiplier = 1.5, 3.0
                        if direction == "BUY":
                            mock_trade_result['sl'] = round(mock_trade_result['price'] - (atr * sl_multiplier), 2)
                            mock_trade_result['tp'] = round(mock_trade_result['price'] + (atr * tp_multiplier), 2)
                        else:
                            mock_trade_result['sl'] = round(mock_trade_result['price'] + (atr * sl_multiplier), 2)
                            mock_trade_result['tp'] = round(mock_trade_result['price'] - (atr * tp_multiplier), 2)

                    send_signal_alert(result, mock_trade_result)
            else:
                logger.info(f"No signal - Score: {result['score']}/6")

            # Log strategy signal for analysis (with error handling)
            try:
                log_strategy_signal(
                    symbol=config.SYMBOL, timeframe=config.TIMEFRAME,
                    adx=adx if adx else 0, atr=atr if atr else 0,
                    ema_fast=ema_fast if ema_fast else 0, ema_slow=ema_slow if ema_slow else 0,
                    derivative_signal="N/A", cot_bias=cot_data.get('bias') if cot_data else "NONE",
                    spread=tick_data.get('spread', 0),
                    news_clear=not news_data.get('high_impact_soon', True) if news_data else True,
                    session_valid=is_valid_session(), tv_signal=webhook_signal.get('direction') if webhook_signal else None,
                    total_score=result['score'], fire_signal=result['fire']
                )
            except sqlite3.Error as e:
                logging.warning(f"Database write error during log_strategy_signal: {e}")
                # Continue running - don't let DB write failure crash the loop

            # Increment and wait
            cycle_count += 1
            logger.info(f"--- End of cycle {cycle_count} (Waiting {SCAN_INTERVAL/60} mins) ---")
            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal. Stopping bot...")
        send_status_update("SHUTDOWN", "Manual interruption (Ctrl+C)")
    except Exception as e:
        error_details = str(e)
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        send_status_update("ERROR", error_details)
    finally:
        if 'db_conn' in locals():
            db_conn.close()

if __name__ == "__main__":
    main()
