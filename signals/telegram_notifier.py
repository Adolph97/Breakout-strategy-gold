import requests
import logging
import os
import sys

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import config
except ImportError:
    # Fallback if config is not in path
    class config:
        TELEGRAM_TOKEN = None
        TELEGRAM_CHAT_ID = None

logger = logging.getLogger(__name__)

def send_telegram_message(message):
    """
    Send a message to the configured Telegram chat.
    """
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID or config.TELEGRAM_TOKEN == "your_bot_token":
        logger.warning("Telegram not configured. Skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False

def send_signal_alert(result, trade_result=None):
    """
    Send a formatted signal alert to Telegram.
    Args:
        result: The result dict from scorer.score()
        trade_result: Optional result dict from mt5_executor.execute_trade()
    """
    direction = result.get('direction')
    score = result.get('score')
    reasons = result.get('reasons', [])
    
    emoji = "🟢" if direction == "BUY" else "🔴"
    
    message = f"<b>{emoji} GOLD SIGNAL: {direction}</b>\n\n"
    message += f"<b>Score:</b> {score}/6\n"
    message += f"<b>Confluences:</b> {', '.join(reasons)}\n\n"
    
    if trade_result and trade_result.get('success'):
        message += "<b>--- Execution Details ---</b>\n"
        message += f"<b>Price:</b> ${trade_result.get('price'):.2f}\n"
        message += f"<b>SL:</b> ${trade_result.get('sl'):.2f}\n"
        message += f"<b>TP:</b> ${trade_result.get('tp'):.2f}\n"
        message += f"<b>Lot Size:</b> {trade_result.get('lot_size'):.2f}\n"
        message += f"<b>Ticket:</b> #{trade_result.get('ticket')}\n"
    elif trade_result:
        message += f"⚠️ <b>Execution Failed:</b> {trade_result.get('error')}\n"
    
    if result.get('asian_range'):
        ar = result['asian_range']
        message += f"\n<b>Asian Range:</b> ${ar.get('width'):.2f} wide"

    return send_telegram_message(message)
