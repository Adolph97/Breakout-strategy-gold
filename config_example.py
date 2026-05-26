# Configuration for the gold_trader bot
# Edit these values to match your Exness MT5 account and preferences

# MT5 Account credentials
MT5_LOGIN = 12345678          # Replace with your MT5 login number
MT5_PASSWORD = "your_password" # Replace with your MT5 password
MT5_SERVER = "Exness-MT5Real" # Replace with your MT5 server (e.g., Exness-MT5Real, Exness-MT5Trial)

# Trading symbol and timeframe
SYMBOL = "XAUUSD"
TIMEFRAME = "H1"  # Can be mt5.TIMEFRAME_M1, mt5.TIMEFRAME_M5, etc. We'll use H1 for hourly bars

# Trading parameters
MIN_SCORE = 4     # Minimum score (out of 6) required to fire a trade
RISK_PER_TRADE = 0.037    # 3.7% of account per trade (Kelly-optimal for this strategy)
ACCOUNT_BALANCE = 10000  # Starting demo account balance
NEWS_BLOCK_MINUTES = 30  # Block trades around high-impact news (minutes before and after)

# Optional: Set to True to enable paper trading (simulate trades without real money)
PAPER_TRADING = True
# Telegram Notifications
TELEGRAM_TOKEN = "your_bot_token"   # from @BotFather
TELEGRAM_CHAT_ID = "your_chat_id"
