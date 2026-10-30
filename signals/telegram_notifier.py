import requests
import logging
import os
import sys
from datetime import datetime

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import config
except ImportError:
    class config:
        TELEGRAM_TOKEN = None
        TELEGRAM_CHAT_ID = None

logger = logging.getLogger(__name__)


def send_telegram_message(message):
    """
    Send a message to the configured Telegram chat.
    """
    token = getattr(config, 'TELEGRAM_TOKEN', None)
    chat_id = getattr(config, 'TELEGRAM_CHAT_ID', None)

    if not token or not chat_id or token == "your_bot_token":
        logger.warning("Telegram not configured. Skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
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


def send_status_update(status_type, details=None):
    """
    Send a status update (Startup, Shutdown, Error, Flatten) to Telegram.
    """
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if status_type == "STARTUP":
        emoji = "🚀"
        message = (
            f"{emoji} <b>Gold Institutional Sentinel Bot Started</b>\n"
            f"<b>Strategy:</b> 8-Gate Confluence (84.2% Win Rate / 4.58 PF)\n"
            f"<b>Target:</b> Runner at 1.8x D1 ATR (TP1 at 0.55x ATR)\n"
            f"<b>Governor:</b> Max 1 High-Conviction Trade/Week\n"
            f"<b>Time:</b> {now_str} UTC"
        )
    elif status_type == "SHUTDOWN":
        emoji = "🛑"
        message = f"{emoji} <b>Gold Sentinel Bot Offline</b>\n<b>Reason:</b> {details if details else 'Manual stop or system signal'}"
    elif status_type == "ERROR":
        emoji = "⚠️"
        message = f"{emoji} <b>CRITICAL ERROR</b>\n<b>Details:</b> <code>{details}</code>"
    elif status_type == "WEEKEND_FLATTEN":
        emoji = "🛡️"
        message = (
            f"{emoji} <b>FRIDAY 20:00 UTC RISK FLATTENING</b>\n\n"
            f"All open positions flattened. 100% Cash over weekend.\n"
            f"Eliminates adverse weekend gap risk.\n"
            f"Bot sleeping until Sunday night session."
        )
    else:
        return False

    return send_telegram_message(message)


def send_trade_card(res: dict) -> bool:
    """
    Broadcasts the complete 8-Gate Institutional Sentinel Trade Card to Telegram.
    Enforces 2-stage execution (TP1 at 0.55x D1 ATR -> BE, runner at 1.8x D1 ATR).
    """
    action = res.get("action", "ACTIONABLE")
    direction = res.get("direction", "BUY")
    setup = res.get("setup", "Structural Breakout")
    entry = res.get("entry_price", 0.0)
    sl = res.get("sl_price", 0.0)
    sl_pips = res.get("sl_pips", 0.0)
    tp1 = res.get("tp_stage1", 0.0)
    tp2 = res.get("tp_stage2", 0.0)
    rr = abs(tp2 - entry) / abs(entry - sl) if sl and entry != sl else 0.0

    account = res.get("account_size", 50000.0)
    risk_pct = res.get("risk_pct", 1.0)
    risk_dollars = res.get("risk_dollars", account * 0.01)
    total_lots = res.get("total_lots", 0.0)
    lot_a = res.get("lot_order_a", 0.0)
    lot_b = res.get("lot_order_b", 0.0)

    emoji = "🟢" if direction in ["BUY", "LONG"] else "🔴"

    lines = [
        f"🚨 <b>INSTITUTIONAL GOLD SENTINEL SIGNAL</b> 🚨",
        f"<b>Action:</b> {emoji} <b>{action} ({direction})</b>",
        f"<b>Setup:</b> {setup}",
        f"<b>Entry Price:</b> ${entry:,.2f}",
        f"<b>Stop Loss:</b> ${sl:,.2f} (${abs(entry - sl):.2f} / {sl_pips:.1f} pips)",
        f"<b>Risk / Reward:</b> 1 : {rr:.2f} (Runner Target)",
        "",
        f"🎯 <b>2-STAGE ASYMMETRIC EXECUTION</b>",
        f"• <b>Stage 1 (+{abs(tp1 - entry):.2f}):</b> ${tp1:,.2f}",
        f"  ↳ <i>Bank 50% volume & lock SL to Breakeven</i>",
        f"• <b>Stage 2 (+{abs(tp2 - entry):.2f}):</b> ${tp2:,.2f}",
        f"  ↳ <i>50% Runner target</i>",
        "",
        f"⚖️ <b>POSITION SIZING (${account:,.0f} @ {risk_pct:.1f}% Risk = ${risk_dollars:,.2f})</b>",
        f"• <b>Total Volume:</b> {total_lots:.2f} Lots",
        f"• <b>Order A (50%):</b> {lot_a:.2f} Lots (TP: ${tp1:,.2f})",
        f"• <b>Order B (50%):</b> {lot_b:.2f} Lots (TP: ${tp2:,.2f})",
        "",
        f"🛡️ <b>8-GATE CONFLUENCE AUDIT:</b>"
    ]

    for k, v in res.get("gates", {}).items():
        gate_num = k.split("_")[0]
        gate_name = k.split("_", 1)[1].replace("_", " ").title()
        lines.append(f"  • Gate {gate_num} ({gate_name}): <code>{v}</code>")

    lines.append("")
    lines.append("⚠️ <b>Weekend Rule:</b> Flat all open positions Friday 20:00 UTC (Zero Gap Risk)")
    lines.append(f"🕒 <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</i>")

    return send_telegram_message("\n".join(lines))


def send_signal_alert(result, trade_result=None):
    """
    Backwards-compatible wrapper. If 2-stage Sentinel fields exist, sends Trade Card.
    """
    if "tp_stage1" in result and result.get("fire"):
        return send_trade_card(result)

    direction = result.get('direction', 'BUY')
    score = result.get('score', 0)
    reasons = result.get('reasons', [])
    emoji = "🟢" if direction in ["BUY", "LONG"] else "🔴"

    message = f"<b>{emoji} GOLD SIGNAL: {direction}</b>\n\n"
    message += f"<b>Score:</b> {score}/8\n"
    message += f"<b>Confluences:</b> {', '.join(reasons)}\n\n"

    if trade_result and trade_result.get('success'):
        message += "<b>--- Execution Details ---</b>\n"
        message += f"<b>Price:</b> ${trade_result.get('price'):.2f}\n"
        message += f"<b>SL:</b> ${trade_result.get('sl'):.2f}\n"
        message += f"<b>TP:</b> ${trade_result.get('tp'):.2f}\n"
        message += f"<b>Lot Size:</b> {trade_result.get('lot_size'):.2f}\n"
        message += f"<b>Ticket:</b> #{trade_result.get('ticket')}\n"

    return send_telegram_message(message)


def send_heartbeat_status(res: dict) -> bool:
    """
    Sends standby / watchlist status update to Telegram.
    """
    wl = res.get("watch_levels", {})
    price = res.get("entry_price", res.get("current_price", 0.0))
    d1_atr = res.get("d1_atr", 0.0)
    gates = res.get("gates", {})
    macro = gates.get("3_macro_trend", "Neutral")
    dxy = gates.get("4_intermarket_dxy", "Neutral")
    quota = gates.get("8_weekly_governor", "Eligible")

    msg = (
        f"📊 <b>GOLD SENTINEL STATUS UPDATE</b>\n\n"
        f"<b>Market Price:</b> ${price:,.2f}\n"
        f"<b>Macro Trend:</b> {macro}\n"
        f"<b>DXY Momentum:</b> {dxy}\n"
        f"<b>Daily ATR(14):</b> ${d1_atr:.2f}\n"
        f"<b>Weekly Quota:</b> {quota}\n\n"
        f"<b>Breakout Watchlist Levels:</b>\n"
        f"• <b>Monday Range:</b> ${wl.get('mon_high', 0.0):,.2f} / ${wl.get('mon_low', 0.0):,.2f}\n"
        f"• <b>H4 32h Channel:</b> ${wl.get('h4_high', 0.0):,.2f} / ${wl.get('h4_low', 0.0):,.2f}\n\n"
        f"🕒 <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</i>"
    )
    return send_telegram_message(msg)
