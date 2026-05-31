import sqlite3
import logging
from datetime import datetime
import os

# Database file path
DB_PATH = os.path.join(os.path.dirname(__file__), 'gold_trader.db')

def get_db_connection():
    """
    Create and return a database connection
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Enable column access by name
    return conn

def execute_db_operation(query, params=(), fetch_one=False, fetch_all=False, commit=False, max_retries=3):
    """
    Execute a database operation with automatic reconnection on failure
    Returns query result or None on failure
    """
    last_error = None
    for attempt in range(max_retries):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)

            if fetch_one:
                result = cursor.fetchone()
                if result:
                    columns = [description[0] for description in cursor.description]
                    result = dict(zip(columns, result))
            elif fetch_all:
                rows = cursor.fetchall()
                if rows:
                    columns = [description[0] for description in cursor.description]
                    result = [dict(zip(columns, row)) for row in rows]
                else:
                    result = []
            else:
                result = None

            if commit:
                conn.commit()

            conn.close()
            return result

        except sqlite3.Error as e:
            last_error = e
            logging.warning(f"DB operation failed (attempt {attempt + 1}/{max_retries}): {e}")
            if conn:
                try:
                    conn.close()
                except:
                    pass

    logging.error(f"DB operation failed after {max_retries} attempts: {last_error}")
    return None

def init_database():
    """
    Initialize database tables if they don't exist
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # COT data table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cot_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT,
            non_comm_long INTEGER,
            non_comm_short INTEGER,
            comm_long INTEGER,
            comm_short INTEGER,
            non_comm_net INTEGER,
            comm_net INTEGER,
            non_comm_long_pct REAL,
            bias TEXT,
            timestamp TEXT
        )
    ''')

    # Calendar events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT,
            event_time TEXT,
            impact TEXT,
            currency TEXT,
            week_start TEXT,
            timestamp TEXT
        )
    ''')

    # Trades log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            direction TEXT,
            price REAL,
            lot_size REAL,
            sl REAL,
            tp REAL,
            score INTEGER,
            reasons TEXT,
            mt5_ticket INTEGER,
            status TEXT DEFAULT 'open'
        )
    ''')

    # Strategy signals log
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS strategy_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            timeframe TEXT,
            adx REAL,
            atr REAL,
            ema_fast REAL,
            ema_slow REAL,
            derivative_signal TEXT,
            cot_bias TEXT,
            spread REAL,
            news_clear BOOLEAN,
            session_valid BOOLEAN,
            tv_signal TEXT,
            total_score INTEGER,
            fire_signal BOOLEAN
        )
    ''')

    conn.commit()
    conn.close()
    logging.info("Database initialized successfully")

def log_trade(direction, price, lot_size, sl, tp, score, reasons, mt5_ticket=None):
    """
    Log a trade to the database
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trades_log
        (timestamp, direction, price, lot_size, sl, tp, score, reasons, mt5_ticket)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().isoformat(),
        direction,
        price,
        lot_size,
        sl,
        tp,
        score,
        ', '.join(reasons) if isinstance(reasons, list) else reasons,
        mt5_ticket
    ))
    conn.commit()
    conn.close()

def log_strategy_signal(symbol, timeframe, adx, atr, ema_fast, ema_slow,
                       derivative_signal, cot_bias, spread, news_clear,
                       session_valid, tv_signal, total_score, fire_signal):
    """
    Log strategy signal data for analysis
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO strategy_signals
        (timestamp, symbol, timeframe, adx, atr, ema_fast, ema_slow,
         derivative_signal, cot_bias, spread, news_clear, session_valid,
         tv_signal, total_score, fire_signal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().isoformat(),
        symbol,
        timeframe,
        adx,
        atr,
        ema_fast,
        ema_slow,
        derivative_signal,
        cot_bias,
        spread,
        news_clear,
        session_valid,
        tv_signal,
        total_score,
        fire_signal
    ))
    conn.commit()
    conn.close()

def get_latest_cot():
    """
    Retrieve latest COT data from database
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM cot_data
        ORDER BY timestamp DESC
        LIMIT 1
    ''')
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None

def get_upcoming_news():
    """
    Retrieve upcoming news events from database
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM calendar_events
        WHERE week_start = ?
        ORDER BY timestamp DESC
    ''', [datetime.now().strftime('%Y-%W')])

    rows = cursor.fetchall()
    conn.close()

    if rows:
        events = [dict(row) for row in rows]
        return {
            'high_impact_soon': len(events) > 0,
            'upcoming_events': events,
            'timestamp': datetime.now().isoformat()
        }
    return {
        'high_impact_soon': False,
        'upcoming_events': [],
        'timestamp': datetime.now().isoformat()
    }

if __name__ == "__main__":
    # Test database initialization
    logging.basicConfig(level=logging.INFO)
    init_database()
    print("Database initialized at:", DB_PATH)

    # Test inserting and retrieving data
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables in database:")
    for table in tables:
        print(f"  - {table[0]}")
    conn.close()