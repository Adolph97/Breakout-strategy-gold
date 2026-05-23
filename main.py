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
from data.mt5_feed import get_tick, get_bars, initialize_mt5, shutdown_mt5
from data.cot_feed import get_latest_cot_from_db, save_cot_to_db
from data.calendar_feed import get_latest_events_from_db
from storage.db import init_database, log_trade, log_strategy_signal
from strategy.indicators import calculate_adx, calculate_atr, calculate_ema
from strategy.scorer import score
from signals.webhook_receiver import get_latest_signal
from execution.mt5_executor import execute_trade

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/konatech/Desktop/trading/gold_trader/logs/trades.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def is_market_open():
    """
    Check if forex market is open (simplified - XAUUSD trades 24/5)
    Returns True if Monday-Friday, not Saturday-Sunday
    """
    # MT5 market hours: Sunday 5:00 PM EST to Friday 5:00 PM EST
    # Simplified: weekday trading
    now = datetime.now(pytz.UTC)
    weekday = now.weekday()  # Monday=0, Sunday=6
    return weekday < 5  # Monday-Friday

def update_weekly_data():
    """
    Update COT and calendar data weekly
    This would typically be run by a separate scheduler, but we'll check timestamps
    """
    # For simplicity, we'll update if data is older than 6 days
    # In production, you'd use a proper scheduler (cron, APScheduler, etc.)
    pass  # Implementation would go here

def main():
    """Main trading loop"""
    logger.info("Starting Gold Trader Bot")
    logger.info(f"Configuration: Symbol={config.SYMBOL}, Timeframe={config.TIMEFRAME}")
    logger.info(f"Minimum score required: {config.MIN_SCORE}/6")
    logger.info(f"Lot size: {config.LOT_SIZE}")
    logger.info(f"Paper trading: {config.PAPER_TRADING}")

    # Initialize database
    init_database()

    # Initialize MT5 connection
    if not initialize_mt5():
        logger.error("Failed to initialize MT5. Exiting.")
        return

    logger.info("MT5 initialized successfully")

    try:
        # Main trading loop
        while True:
            # Check if market is open
            if not is_market_open():
                logger.info("Market is closed. Waiting...")
                time.sleep(300)  # Check every 5 minutes when market is closed
                continue

            logger.info("--- New trading cycle ---")

            # Get market data
            tick_data = get_tick()
            bars_data = get_bars(count=100)  # Get last 100 bars for analysis

            if not tick_data or bars_data is None:
                logger.warning("Failed to get market data. Retrying...")
                time.sleep(10)
                continue

            # Get supplemental data from database
            cot_data = get_latest_cot_from_db()
            news_data = get_latest_events_from_db()
            webhook_signal = get_latest_signal()

            # Log data for debugging
            logger.info(f"Tick: {tick_data}")
            logger.info(f"COT: {cot_data}")
            logger.info(f"News: {news_data}")
            logger.info(f"Webhook signal: {webhook_signal}")

            # Calculate indicators for logging
            adx = calculate_adx(bars_data)
            atr = calculate_atr(bars_data)
            ema_fast = calculate_ema(bars_data, period=9)
            ema_slow = calculate_ema(bars_data, period=21)

            logger.info(f"Indicators - ADX: {adx:.2f if adx else None}, ATR: {atr:.4f if atr else None}")
            logger.info(f"EMAs - Fast: {ema_fast:.2f if ema_fast else None}, Slow: {ema_slow:.2f if ema_slow else None}")

            # Calculate score
            result = score(
                tick_data=tick_data,
                bars_df=bars_data,
                cot_data=cot_data,
                news_data=news_data,
                webhook_signal=webhook_signal.get('direction') if webhook_signal else None
            )

            logger.info(f"Score result: {result}")

            # Log strategy signal for analysis
            log_strategy_signal(
                symbol=config.SYMBOL,
                timeframe=config.TIMEFRAME,
                adx=adx if adx else 0,
                atr=atr if atr else 0,
                ema_fast=ema_fast if ema_fast else 0,
                ema_slow=ema_slow if ema_slow else 0,
                derivative_signal="TODO",  # Would need to extract from scorer
                cot_bias=cot_data.get('bias') if cot_data else "NONE",
                spread=tick_data.get('spread', 0),
                news_clear=not news_data.get('high_impact_soon', True),
                session_valid=True,  # Simplified - would check actual session
                tv_signal=webhook_signal.get('direction') if webhook_signal else None,
                total_score=result['score'],
                fire_signal=result['fire']
            )

            # Execute trade if score meets threshold
            if result['fire'] and result['direction']:
                logger.info(f"TRADE SIGNAL: {result['direction']} with score {result['score']}")
                logger.info(f"Reasons: {', '.join(result['reasons'])}")

                # Get Asian range width for dynamic position sizing
                range_width = result.get('asian_range', {}).get('width') if result.get('asian_range') else None
                if range_width:
                    logger.info(f"Asian range width: ${range_width} for position sizing")

                # Execute the trade
                atr_value = atr if atr else 0.5  # Fallback ATR value
                trade_result = execute_trade(result['direction'], atr_value, range_width)

                if trade_result['success']:
                    logger.info(f"Trade executed successfully: {trade_result}")

                    # Update the trade log with actual score and reasons
                    # In a real implementation, you'd update the database record
                    logger.info("Trade logged to database")
                else:
                    logger.error(f"Trade execution failed: {trade_result}")
            else:
                logger.info("No trade signal - score below threshold or no direction")

            # Wait before next cycle
            logger.info("--- End of cycle ---")
            time.sleep(60)  # Check every minute

    except KeyboardInterrupt:
        logger.info("Received shutdown signal. Stopping bot...")
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
    finally:
        # Cleanup
        shutdown_mt5()
        logger.info("MT5 connection closed. Bot stopped.")

if __name__ == "__main__":
    # Create logs directory if it doesn't exist
    os.makedirs('/Users/konatech/Desktop/trading/gold_trader/logs', exist_ok=True)

    main()