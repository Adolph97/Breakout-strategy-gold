import os
import time
import logging
import sys
from datetime import datetime

# Add project root to path for importing config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# Try to import mt5linux (for Docker/Wine MT5 connection) or fall back to simulation
try:
    from mt5linux import MetaTrader5
    MT5_LINUX_AVAILABLE = True
    logging.info("mt5linux module available - will connect to MT5 via RPC")
except ImportError:
    MT5_LINUX_AVAILABLE = False
    logging.warning("mt5linux module not available. Will use simulation/data feed fallbacks.")

# Global variable to store last known good price for simulation fallback
_last_known_price = None
_use_simulation_fallback = False

def initialize_mt5_connection():
    """
    Initialize connection to MT5 via mt5linux (Docker/Wine) or return False for simulation
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
        # Store the instance for later use (in a real implementation, you might want to manage this better)
        # For now, we'll create a new instance each time we need it
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

def get_tick():
    """
    Get current tick data for XAUUSD using MT5 via mt5linux
    Returns dict with bid, ask, spread
    """
    global _last_known_price, _use_simulation_fallback

    # If we're in simulation fallback mode, generate simulated data
    if _use_simulation_fallback:
        return _generate_simulated_tick()

    try:
        # Try to get real data from MT5
        tick_data = _get_mt5_tick()
        if tick_data is not None:
            _last_known_price = tick_data
            _use_simulation_fallback = False
            return tick_data
        else:
            logging.warning("MT5 returned no data, checking if we should fallback to simulation")

    except Exception as e:
        logging.error(f"Error fetching tick data from MT5: {e}")

    # If we get here, MT5 failed - check if we should use simulation
    if _last_known_price is not None:
        logging.info("Falling back to simulated data based on last known price")
        _use_simulation_fallback = True
        return _generate_simulated_tick()
    else:
        # No last known price, generate completely simulated data
        logging.warning("No price data available, using purely simulated data")
        _use_simulation_fallback = True
        return _generate_simulated_tick()

def _get_mt5_tick():
    """Attempt to get tick data from MT5 via mt5linux"""
    if not MT5_LINUX_AVAILABLE:
        return None

    try:
        mt5_instance = get_mt5_instance()
        if not mt5_instance.initialize():
            logging.error("Failed to initialize MT5 connection for tick data")
            return None

        # Get symbol info first to verify connection
        symbol_info = mt5_instance.symbol_info(config.SYMBOL)
        if symbol_info is None:
            logging.error(f"Failed to get symbol info for {config.SYMBOL}")
            mt5_instance.shutdown()
            return None

        # Get tick data
        tick = mt5_instance.symbol_info_tick(config.SYMBOL)
        mt5_instance.shutdown()

        if tick is None:
            logging.error(f"Failed to get tick data for {config.SYMBOL}")
            return None

        result = {
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": round(tick.ask - tick.bid, 2),
            "time": datetime.fromtimestamp(tick.time)
        }

        return result

    except Exception as e:
        logging.error(f"Error in _get_mt5_tick: {e}")
        try:
            mt5_instance.shutdown()
        except:
            pass
        return None

def _generate_simulated_tick():
    """Generate simulated tick data for testing"""
    global _last_known_price

    # Base price around current gold levels (adjust as needed)
    base_price = 2300.0 if _last_known_price is None else _last_known_price['ask']

    # Add small random walk movement
    import random
    change = random.uniform(-0.5, 0.5)
    new_price = base_price + change

    # Ensure price stays reasonable
    new_price = max(2000, min(2500, new_price))

    spread = 0.30  # Typical gold spread

    result = {
        "bid": new_price - (spread / 2),
        "ask": new_price + (spread / 2),
        "spread": spread,
        "time": datetime.now()
    }

    _last_known_price = result
    return result

def get_bars(timeframe=None, count=100):
    """
    Get historical OHLC bars for XAUUSD using MT5 via mt5linux
    Args:
        timeframe: MT5 timeframe constant (default: TIMEFRAME_H1)
        count: Number of bars to retrieve
    Returns:
        pandas DataFrame with OHLCV data
    """
    global _use_simulation_fallback

    # If we're in simulation fallback mode, generate simulated data
    if _use_simulation_fallback:
        return _generate_simulated_bars(count)

    try:
        # Try to get real data from MT5
        bars_data = _get_mt5_bars(timeframe, count)
        if bars_data is not None and len(bars_data) > 0:
            _use_simulation_fallback = False
            return bars_data
        else:
            logging.warning("MT5 returned no bar data, checking if we should fallback to simulation")

    except Exception as e:
        logging.error(f"Error fetching bars data from MT5: {e}")

    # If we get here, MT5 failed - check if we should use simulation
    logging.info("Falling back to simulated bar data")
    _use_simulation_fallback = True
    return _generate_simulated_bars(count)

def _get_mt5_bars(timeframe, count):
    """Attempt to get bars data from MT5 via mt5linux"""
    if not MT5_LINUX_AVAILABLE:
        return None

    if timeframe is None:
        timeframe = getattr(__import__('mt5linux', fromlist=['MetaTrader5']), 'TIMEFRAME_H1', 1)  # Default to H1 if we can't import properly

    try:
        mt5_instance = get_mt5_instance()
        if not mt5_instance.initialize():
            logging.error("Failed to initialize MT5 connection for bars data")
            return None

        # Get bars data
        rates = mt5_instance.copy_rates_from_pos(config.SYMBOL, timeframe, 0, count)
        mt5_instance.shutdown()

        if rates is None:
            logging.error(f"Failed to get bars data for {config.SYMBOL}")
            return None

        # Convert to DataFrame
        import pandas as pd
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        return df

    except Exception as e:
        logging.error(f"Error in _get_mt5_bars: {e}")
        try:
            mt5_instance.shutdown()
        except:
            pass
        return None

def _generate_simulated_bars(count):
    """Generate simulated OHLC bar data for testing"""
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta
    import pytz

    # Generate timestamps for the last 'count' hours with UTC timezone
    end_time = datetime.now(pytz.UTC)
    start_time = end_time - timedelta(hours=count)
    timestamps = pd.date_range(start=start_time, end=end_time, periods=count, tz='UTC')

    # Generate price series with random walk
    base_price = 2300.0
    prices = []
    current_price = base_price

    for i in range(count):
        # Random walk with slight upward bias
        change = np.random.normal(0.1, 0.5)  # mean 0.1, std 0.5
        current_price += change
        current_price = max(2000, min(2500, current_price))  # Keep reasonable
        prices.append(current_price)

    # Generate OHLC from close prices
    opens = [prices[0]] + prices[:-1]  # Open is previous close
    highs = [p + abs(np.random.normal(0, 0.3)) for p in prices]  # High >= close
    lows = [p - abs(np.random.normal(0, 0.3)) for p in prices]   # Low <= close

    # Ensure OHLC relationships are valid
    for i in range(count):
        highs[i] = max(highs[i], opens[i], prices[i])
        lows[i] = min(lows[i], opens[i], prices[i])

    # Generate volume (random but realistic)
    volumes = [np.random.uniform(100, 1000) for _ in range(count)]

    df = pd.DataFrame({
        'time': timestamps,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': prices,
        'volume': volumes
    })

    logging.info(f"Generated {count} simulated bars with UTC timestamps")
    return df

def get_symbol_info():
    """
    Get symbol information (for MT5 compatibility)
    """
    return {
        "name": config.SYMBOL,
        "point": 0.01,  # Gold typically has 2 decimal places
        "digits": 2,
    }

def reset_simulation():
    """Reset simulation state (useful for testing)"""
    global _last_known_price, _use_simulation_fallback
    _last_known_price = None
    _use_simulation_fallback = False

if __name__ == "__main__":
    # Test the connection
    logging.basicConfig(level=logging.INFO)

    print("Testing MT5 connection...")
    print("Fetching tick data...")
    tick = get_tick()
    if tick:
        print(f"Tick: {tick}")

    print("\nFetching historical bars...")
    bars = get_bars(count=5)
    if bars is not None:
        print(bars.head())
        print(f"\nTotal bars received: {len(bars)}")

    print("\nTesting simulation fallback...")
    # Temporarily force simulation by making mt5linux fail
    import mt5linux
    original_MetaTrader5 = mt5linux.MetaTrader5

    class FailingMetaTrader5:
        def __init__(self, *args, **kwargs):
            pass
        def initialize(self):
            return False
        def shutdown(self):
            pass
        def symbol_info_tick(self, *args, **kwargs):
            return None
        def copy_rates_from_pos(self, *args, **kwargs):
            return None
        def symbol_info(self, *args, **kwargs):
            return None

    mt5linux.MetaTrader5 = FailingMetaTrader5

    try:
        tick_sim = get_tick()
        print(f"Simulated tick: {tick_sim}")

        bars_sim = get_bars(count=3)
        print(f"Simulated bars:\n{bars_sim.head()}")
    finally:
        # Restore original class
        mt5linux.MetaTrader5 = original_MetaTrader5