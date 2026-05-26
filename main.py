import time
import logging
import sys
import os
from datetime import datetime
import pytz

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import all modules
import config
from data.price_feed import get_tick, get_bars
from data.cot_feed import get_latest_cot_from_db
from data.calendar_feed import get_latest_events_from_db
from storage.db import init_database, log_strategy_signal, get_db_connection
from strategy.indicators import calculate_adx, calculate_atr, calculate_ema
from strategy.scorer import score
from signals.webhook_receiver import get_latest_signal
from signals.telegram_notifier import send_signal_alert, send_status_update

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
    logger.info("Starting Gold Trader Signal Bot (Signal-Only Mode)")
    
    # Send Startup Notification
    send_status_update("STARTUP")
    
    logger.info(f"Configuration: Symbol={config.SYMBOL}, Timeframe={config.TIMEFRAME}")
    logger.info(f"Minimum score required for Telegram alert: {config.MIN_SCORE}/6")

    # Initialize database
    init_database()

    try:
        # Get a persistent database connection for the main loop
        db_conn = get_db_connection()
        
        cycle_count = 0
        # Main loop
        while True:
            # Heartbeat every 60 cycles (roughly 1 hour)
            if cycle_count % 60 == 0 and cycle_count > 0:
                from signals.telegram_notifier import send_telegram_message
                send_telegram_message("❤️ <b>Heartbeat:</b> Bot is still scanning Gold...")

            # Check if market is open
            if not is_market_open():
                logger.info("Market is closed. Waiting...")
                time.sleep(300)
                continue

            logger.info("--- New scanning cycle ---")

            # Get market data (Simulation fallback used if MT5 not available)
            tick_data = get_tick()
            bars_data = get_bars(count=100)

            if not tick_data or bars_data is None:
                logger.warning("Failed to get market data. Retrying...")
                time.sleep(10)
                continue

            # Get supplemental data
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

            logger.info(f"Score result: {result['score']}/6 - Fire: {result['fire']}")

            # Log strategy signal for analysis
            log_strategy_signal(
                symbol=config.SYMBOL,
                timeframe=config.TIMEFRAME,
                adx=adx if adx else 0,
                atr=atr if atr else 0,
                ema_fast=ema_fast if ema_fast else 0,
                ema_slow=ema_slow if ema_slow else 0,
                derivative_signal="N/A",
                cot_bias=cot_data.get('bias') if cot_data else "NONE",
                spread=tick_data.get('spread', 0),
                news_clear=not news_data.get('high_impact_soon', True) if news_data else True,
                session_valid=True,
                tv_signal=webhook_signal.get('direction') if webhook_signal else None,
                total_score=result['score'],
                fire_signal=result['fire']
            )

            # Send Telegram alert if score meets threshold
            if result['fire']:
                logger.info(f"SIGNAL FIRED: {result['direction']} with score {result['score']}")
                
                # Format a mock trade_result for the Telegram alert (since we aren't executing)
                mock_trade_result = {
                    "success": True,
                    "price": tick_data['ask'] if result['direction'] == "BUY" else tick_data['bid'],
                    "sl": 0, 
                    "tp": 0,
                    "lot_size": 0,
                    "ticket": "SIGNAL_ONLY"
                }
                
                # If we want real SL/TP in the message, we can calculate them here
                if atr:
                    # Modular SL/TP calculation
                    sl_multiplier = 1.5
                    tp_multiplier = 3.0
                    if result['direction'] == "BUY":
                        mock_trade_result['sl'] = round(mock_trade_result['price'] - (atr * sl_multiplier), 2)
                        mock_trade_result['tp'] = round(mock_trade_result['price'] + (atr * tp_multiplier), 2)
                    else:
                        mock_trade_result['sl'] = round(mock_trade_result['price'] + (atr * sl_multiplier), 2)
                        mock_trade_result['tp'] = round(mock_trade_result['price'] - (atr * tp_multiplier), 2)

                send_signal_alert(result, mock_trade_result)
            else:
                logger.info("No signal meeting threshold.")

            # Wait before next cycle
            logger.info("--- End of cycle ---")
            cycle_count += 1
            time.sleep(60)

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
    os.makedirs('logs', exist_ok=True)
    main()
