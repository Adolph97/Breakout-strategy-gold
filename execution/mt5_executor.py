import os
import logging
import time
from datetime import datetime
import sys

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# Try to import mt5linux (for Docker/Wine MT5 connection)
try:
    from mt5linux import MetaTrader5
    MT5_LINUX_AVAILABLE = True
    logging.info("mt5linux module available - will connect to MT5 via RPC")
except ImportError:
    MT5_LINUX_AVAILABLE = False
    logging.warning("mt5linux module not available. Running in simulation mode.")

def initialize_mt5_connection():
    """
    Initialize connection to MT5 via mt5linux (Docker/Wine) or return True for simulation
    Returns True if MT5 connection successful or if in simulation mode
    """
    if not MT5_LINUX_AVAILABLE:
        logging.info("Running in simulation mode (mt5linux not available)")
        return True

    try:
        # Get MT5 connection parameters from environment or config
        mt5_host = os.getenv('MT5_HOST', 'localhost')
        mt5_port = int(os.getenv('MT5_PORT', 8001))

        # Initialize MT5 connection via mt5linux
        mt5_instance = MetaTrader5(host=mt5_host, port=mt5_port)

        if not mt5_instance.initialize():
            error = mt5_instance.last_error()
            logging.error(f"Failed to initialize MT5 connection: {error}")
            return False

        logging.info(f"MT5 initialized successfully via {mt5_host}:{mt5_port}")
        return True

    except Exception as e:
        logging.error(f"Error initializing MT5 connection: {e}")
        return False

def get_mt5_instance():
    """
    Get a new MT5 instance (to be used within context)
    """
    if not MT5_LINUX_AVAILABLE:
        return None

    mt5_host = os.getenv('MT5_HOST', 'localhost')
    mt5_port = int(os.getenv('MT5_PORT', 8001))
    return MetaTrader5(host=mt5_host, port=mt5_port)

def calculate_lot_size(account_balance=None, risk_pct=None, sl_distance_dollars=None):
    """
    Calculate lot size based on account risk and stop loss distance.

    For gold: 1 standard lot = $100 per $1 move, 1 mini lot (0.1) = $10 per $1 move

    Args:
        account_balance: Account balance in dollars (defaults to config.ACCOUNT_BALANCE)
        risk_pct: Fraction of account to risk (defaults to config.RISK_PER_TRADE)
        sl_distance_dollars: Distance to stop loss in dollars (Asian range width)
    """
    if account_balance is None:
        account_balance = config.ACCOUNT_BALANCE
    if risk_pct is None:
        risk_pct = config.RISK_PER_TRADE
    if sl_distance_dollars is None:
        sl_distance_dollars = 1.0  # Default fallback

    risk_dollars = account_balance * risk_pct
    # 1 lot = 100 oz, $1 move per lot = $100
    lot_size = risk_dollars / (sl_distance_dollars * 100)
    lot_size = round(lot_size, 2)
    lot_size = max(0.01, min(lot_size, 1.0))  # Between micro and 1 lot
    return lot_size

def calculate_sl_tp(price, direction, atr_value):
    """
    Calculate Stop Loss and Take Profit levels based on ATR
    Args:
        price: Entry price
        direction: "BUY" or "SELL"
        atr_value: ATR value for volatility-based SL/TP
    Returns:
        Tuple of (sl, tp)
    """
    # Use ATR multiples for SL and TP
    sl_multiplier = 1.5  # Stop Loss at 1.5 * ATR
    tp_multiplier = 3.0  # Take Profit at 3.0 * ATR (1:2 risk-reward)

    if direction == "BUY":
        sl = price - (atr_value * sl_multiplier)
        tp = price + (atr_value * tp_multiplier)
    else:  # SELL
        sl = price + (atr_value * sl_multiplier)
        tp = price - (atr_value * tp_multiplier)

    return round(sl, 2), round(tp, 2)

def place_order(direction, price, lot_size, sl, tp):
    """
    Place a market order via MT5 (or simulate if MT5 not available)
    Args:
        direction: "BUY" or "SELL"
        price: Entry price
        lot_size: Lot size for the trade
        sl: Stop Loss price
        tp: Take Profit price
    Returns:
        Mock order result or None if failed
    """
    if not MT5_LINUX_AVAILABLE:
        # Simulation mode
        logging.info(f"SIMULATION: Would place {direction} order:")
        logging.info(f"  Symbol: {config.SYMBOL}")
        logging.info(f"  Price: {price}")
        logging.info(f"  Lot size: {lot_size}")
        logging.info(f"  SL: {sl}")
        logging.info(f"  TP: {tp}")

        # Return a mock successful result
        class MockResult:
            def __init__(self):
                self.retcode = 10009  # TRADE_RETCODE_DONE equivalent
                self.order = int(time.time())  # Mock ticket number

        return MockResult()

    # Real MT5 execution via mt5linux
    # Define order type
    if direction == "BUY":
        order_type = getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'ORDER_TYPE_BUY', 0)
    else:
        order_type = getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'ORDER_TYPE_SELL', 1)

    # Prepare the trade request
    request = {
        "action": getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'TRADE_ACTION_DEAL', 1),
        "symbol": config.SYMBOL,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,  # Maximum price deviation in points
        "magic": 234000,  # EA Magic number
        "comment": "gold_trader_bot",
        "type_time": getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'ORDER_TIME_GTC', 0),  # Good till cancelled
        "type_filling": getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'ORDER_FILLING_IOC', 1),  # Immediate or cancel
    }

    # Get MT5 instance and send order
    try:
        mt5_instance = get_mt5_instance()
        if not mt5_instance.initialize():
            logging.error("Failed to initialize MT5 connection for order placement")
            return None

        # Send the order
        result = mt5_instance.order_send(request)
        mt5_instance.shutdown()
        return result

    except Exception as e:
        logging.error(f"Error placing MT5 order: {e}")
        try:
            mt5_instance.shutdown()
        except:
            pass
        return None

def execute_trade(direction, atr_value, range_width=None):
    """
    Execute a trade based on signal and ATR
    Args:
        direction: "BUY" or "SELL"
        atr_value: ATR value for SL/TP calculation
        range_width: Asian range width for dynamic position sizing (defaults to ATR)
    Returns:
        Dict with trade result information
    """
    if not initialize_mt5_connection():
        return {"success": False, "error": "Failed to initialize MT5"}

    try:
        # Get current tick price
        from data.mt5_feed import get_tick

        tick_data = get_tick()
        if not tick_data:
            return {"success": False, "error": "Failed to get tick data"}

        # Determine entry price
        if direction == "BUY":
            price = tick_data['ask']  # Buy at ask price
        else:
            price = tick_data['bid']  # Sell at bid price

        # Calculate lot size based on Asian range width (SL distance)
        sl_distance = range_width if range_width else atr_value * 1.5
        lot_size = calculate_lot_size(sl_distance_dollars=sl_distance)

        # Calculate SL and TP
        sl, tp = calculate_sl_tp(price, direction, atr_value)

        # Log trade attempt
        logging.info(f"Attempting to place {direction} order:")
        logging.info(f"  Symbol: {config.SYMBOL}")
        logging.info(f"  Price: {price}")
        logging.info(f"  Lot size: {lot_size}")
        logging.info(f"  SL: {sl}")
        logging.info(f"  TP: {tp}")

        # Place the order
        result = place_order(direction, price, lot_size, sl, tp)

        # Check result
        if result is None:
            error_msg = "Order placement failed (returned None)"
            logging.error(error_msg)
            return {
                "success": False,
                "error": error_msg
            }

        if hasattr(result, 'retcode') and result.retcode != 10009:  # TRADE_RETCODE_DONE
            error_msg = f"Order failed. Retcode: {getattr(result, 'retcode', 'Unknown')}"
            logging.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "retcode": getattr(result, 'retcode', None)
            }

        # Order successful
        ticket = getattr(result, 'order', int(time.time()))
        logging.info(f"Order placed successfully. Ticket: {ticket}")

        # Log trade to database if not in paper trading mode
        if not config.PAPER_TRADING:
            try:
                from storage.db import log_trade
                log_trade(
                    direction=direction,
                    price=price,
                    lot_size=lot_size,
                    sl=sl,
                    tp=tp,
                    score=0,  # Will be updated by main loop
                    reasons=["Execution"],
                    mt5_ticket=ticket
                )
            except Exception as db_error:
                logging.warning(f"Failed to log trade to database: {db_error}")
                # Continue anyway - trade execution succeeded even if logging failed

        return {
            "success": True,
            "ticket": ticket,
            "direction": direction,
            "price": price,
            "lot_size": lot_size,
            "sl": sl,
            "tp": tp,
            "retcode": getattr(result, 'retcode', 10009)
        }

    except Exception as e:
        logging.error(f"Error executing trade: {e}")
        return {"success": False, "error": str(e)}

def close_position(ticket):
    """
    Close an open position by ticket
    Args:
        ticket: MT5 position ticket number
    Returns:
        Bool indicating success
    """
    if not MT5_LINUX_AVAILABLE:
        logging.info(f"SIMULATION: Would close position {ticket}")
        return True

    if not initialize_mt5_connection():
        return False

    try:
        # Get position info
        mt5_instance = get_mt5_instance()
        if not mt5_instance.initialize():
            logging.error("Failed to initialize MT5 connection for position close")
            return False

        position = mt5_instance.positions_get(ticket=ticket)
        mt5_instance.shutdown()

        if not position:
            logging.error(f"No position found with ticket {ticket}")
            return False

        position = position[0]

        # Prepare close request
        request = {
            "action": getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'TRADE_ACTION_DEAL', 1),
            "symbol": config.SYMBOL,
            "volume": position.volume,
            "type": getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'ORDER_TYPE_SELL', 1) if position.type == getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'POSITION_TYPE_BUY', 0) else getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'ORDER_TYPE_BUY', 0),
            "position": ticket,
            "price": mt5_instance.symbol_info_tick(config.SYMBOL).bid if position.type == getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'POSITION_TYPE_BUY', 0) else mt5_instance.symbol_info_tick(config.SYMBOL).ask,
            "deviation": 20,
            "magic": 234000,
            "comment": "gold_trader_bot_close",
            "type_time": getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'ORDER_TIME_GTC', 0),
            "type_filling": getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'ORDER_FILLING_IOC', 1),
        }

        # Get fresh instance for the close operation
        mt5_instance_close = get_mt5_instance()
        if not mt5_instance_close.initialize():
            logging.error("Failed to initialize MT5 connection for position close (second attempt)")
            return False

        result = mt5_instance_close.order_send(request)
        mt5_instance_close.shutdown()

        if result.retcode != getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'TRADE_RETCODE_DONE', 10009):
            logging.error(f"Failed to close position {ticket}: {result.retcode}")
            return False

        logging.info(f"Position {ticket} closed successfully")
        return True

    except Exception as e:
        logging.error(f"Error closing position {ticket}: {e}")
        return False

if __name__ == "__main__":
    # Test the execution module
    logging.basicConfig(level=logging.INFO)

    print("Testing MT5 execution module...")
    if MT5_LINUX_AVAILABLE:
        print("mt5linux module available - attempting connection to MT5 container")
    else:
        print("mt5linux module NOT available - running in simulation mode")

    # Test initialization
    if initialize_mt5_connection():
        print("MT5 connection initialized successfully" if MT5_LINUX_AVAILABLE else "Simulation mode initialized")

        # Get current tick for testing
        from data.mt5_feed import get_tick
        tick = get_tick()
        if tick:
            print(f"Current tick: {tick}")

            # Test SL/TP calculation
            test_atr = 0.5  # Example ATR value
            sl, tp = calculate_sl_tp(tick['ask'], "BUY", test_atr)
            print(f"For BUY at {tick['ask']} with ATR {test_atr}:")
            print(f"  SL: {sl}, TP: {tp}")

            sl, tp = calculate_sl_tp(tick['bid'], "SELL", test_atr)
            print(f"For SELL at {tick['bid']} with ATR {test_atr}:")
            print(f"  SL: {sl}, TP: {tp}")

            # Test order placement
            print("\nTesting order placement...")
            result = execute_trade("BUY", test_atr)
            print(f"Buy order result: {result}")

            result = execute_trade("SELL", test_atr)
            print(f"Sell order result: {result}")

        print("\nNote: For live testing, ensure:")
        print("  1. MT5 container is running (docker-compose up -d mt5)")
        print("  2. You've logged into your broker via VNC (http://localhost:3000)")
        print("  3. The MT5 container is connected to your Exness account")
    else:
        print("Failed to initialize MT5 connection")