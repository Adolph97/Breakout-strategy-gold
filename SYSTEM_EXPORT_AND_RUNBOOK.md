# Gold (XAU/USD) 500-Pip Institutional Sentinel — System Export & Runbook

This master export contains the complete architecture, mathematical foundations, measured performance metrics, directory structure, full source code reference, and step-by-step instructions to replicate, run, and improve this system in any repository or hosting environment.

---

## 1. Executive Summary & Measured Results

- **Instrument**: Gold vs. US Dollar (XAU/USD)
- **Target Move**: **At least 500 pips ($50.00 price expansion per ounce)**
- **Cadence Governor**: Maximum **1 high-conviction trade per calendar week** (Zero overtrading)
- **Execution Model**: **2-Stage Asymmetric Execution**
  - **Stage 1**: Take 50% volume profit at **0.55 x prior-day D1 ATR** and move Stop Loss to **Breakeven + spread/commission**.
  - **Stage 2**: Remaining 50% runner targets **1.8 x D1 ATR**. Stops must sit 0.3-1.6 x D1 ATR from entry or the setup is skipped.
- **Weekend Risk Rule**: **Strict Friday 20:00 UTC flattening**. 100% cash over weekends. Eliminates 100% of adverse weekend gap risk (which historically gapped up to -$111.94).
- **Status (audit of 2026-11-14): NO DEMONSTRATED EDGE.** The headline numbers below come from the window the rules were tuned on.
- **Backtest Metrics (ATR-scaled rules, spot XAU/USD H1 from Dukascopy)**:
  - **Tuned window, Jan-2023 to Jul-2026**: 27 trades (25 long / 2 short), 74.1% win, +0.44R per trade. Random trend-side entries with the same stops and exits make +0.20R in this window; 27 random trades match +0.44R about 9% of the time.
  - **After the tuned window, 20-Jul to 13-Nov-2026**: 5 trades, all short, -0.27R per trade.
  - **Full file to 13-Nov-2026 (what `backtest_runner.py` now prints)**: 32 trades, 65.6% win, +0.33R per trade, PF 1.16.
  - **Same rules on 2010 to Nov-2026**: 98 trades, +0.03R per trade (t = 0.4). Negative in 2010-12 (-0.34R), 2013-18 (-0.02R) and 2019-22 (-0.08R).
  - Removing any single gate leaves the 17-year expectancy between -0.02R and +0.06R: no gate carries an edge.
  - The old "84.2% / PF 4.58 / Sharpe 10.1" figures came from fixed-dollar rules and a buggy Sharpe/R:R calculation. Ignore them.
- **Cadence**: about one 8-gate signal every 6 to 7 weeks. Weeks of silence are normal for these rules.
- **Live bot (`main.py`)**: scores each closed H1 bar of spot XAU/USD (Dukascopy, the backtest's own source) exactly once. A replay of Feb to Nov 2026 through the live code reproduces the backtest's 9 signals exactly.
- **Paper forward test (`strategy/sweep_reject.py`)**: "prior-day sweep & reject with the D1 trend", about 41 signals a year, +0.07R per trade over 2010-2026 (t = 1.7, not proven, -0.28R on the 13 trades since Jul-2026). Alerts are labelled PAPER and results are tracked automatically in the `paper_trades` table. Judge it after 50+ forward signals; do not change its parameters during the test.

---

## 2. The 8-Gate Confluence Engine

A signal is **only generated** when all 8 gates pass simultaneously:

1. **Gate 1: High-Liquidity Session Window**
   - London Open (**07:00–10:00 UTC**) or New York Open (**13:00–15:00 UTC**). No off-hours trading.
2. **Gate 2: Volatility Floor**
   - Daily ATR(14) $\ge$ 0.9% of price (about \$20 at \$2,300). Skips dead/dormant summer chop.
3. **Gate 3: Macro Daily Regime**
   - **Longs**: Daily Close $> \text{EMA50} > \text{EMA200}$.
   - **Shorts**: Daily Close $< \text{EMA50} < \text{EMA200}$.
4. **Gate 4: Intermarket DXY Trend Alpha**
   - Gold does not trade in isolation.
   - **Longs**: US Dollar Index (DXY) is in a confirmed downtrend ($\text{DXY} < 20\text{-day EMA}$ AND 5-day return $< 0$).
   - **Shorts**: DXY in an uptrend ($\text{DXY} > 20\text{-day EMA}$ AND 5-day return $> 0$).
5. **Gate 5: Structural Accumulation Breakout**
   - **Tue/Wed**: Breakout of Monday Initial Balance range (`close > mon_high` for longs, `close < mon_low` for shorts).
   - **Wed/Thu**: Mid-week 32-hour / 4-Hour Donchian Channel breakout (`close > h4_h8` or `close < h4_l8`).
6. **Gate 6: Candle Closing Conviction**
   - Breakout bar must close in the top 30% of its candle range for Longs (`(close - low) / (high - low) >= 0.70`).
   - Reverse for Shorts (`<= 0.30`). Eliminates topping wicks, gravestone dojis, and rejection bars.
7. **Gate 7: Institutional Volume Expansion**
   - Breakout candle volume $\ge 1.2\times$ the 20-period moving average. Confirms institutional participation.
8. **Gate 8: Weekly Governor**
   - SQLite check ensures no prior trade has executed during the current calendar week.

---

## 3. Directory Layout

```
trader/
├── .env                              # Telegram credentials & environment variables
├── requirements.txt                  # Python dependencies
├── fetch_gold.py                     # Appends new XAU/USD H1 bars from Dukascopy + refreshes DXY (run before every scan)
├── features.py                       # Computes lookahead-free D1/H4/Intermarket features
├── validate.py                       # Data integrity & lookahead audit gate
├── db/
│   └── trading.db                    # SQLite database (trades & strategy_scores tables)
├── data/
│   ├── xauusd_h1.csv                 # Raw 3.5y XAU/USD H1 bars (20,946 rows)
│   ├── dxy_daily.csv                 # Raw Daily DXY bars (920 rows)
│   └── features_gold_h1.csv          # Feature store with strictly shifted indicators
├── reports/
│   ├── validation_report.txt         # Output of data validation
│   └── backtest_report.txt           # Output of strategy backtests
└── src/
    ├── backtest_engine.py            # Execution engine (2-stage exits, spreads, commissions)
    ├── strategy_gold_certainty.py    # Vectorized 8-Gate Alpha implementation
    ├── backtest_runner.py            # Runner writing results to SQLite & comparison tables
    ├── telegram_notifier.py          # HTTP client sending HTML alerts to @breakout_xauusd_bot
    ├── gold_sentinel.py              # Production CLI scanner with position sizing & --telegram
    └── telegram_bot.py               # Interactive phone daemon (/scan, /status, /risk, /levels)
```

---

## 4. Complete Code Listings

### A. Environment Configuration (`trader/.env`)
```bash
FETCH_SOURCE_1H=dukascopy 
FETCH_SOURCE_INTRADAY=dukascopy
TELEGRAM_BOT_TOKEN=<your-bot-token — never commit the real value>
TELEGRAM_CHAT_ID=<your-chat-id>
DISPLAY_TZ=Africa/Lagos
ACCOUNT_BALANCE=50000.0
RISK_PER_TRADE=0.01
```

### B. Dependencies (`trader/requirements.txt`)
```text
pandas>=2.0.0
numpy>=1.24.0
pyarrow<16
yfinance>=0.2.0
requests>=2.28.0
python-dotenv>=1.0.0
dukascopy-python>=0.1.0
python-telegram-bot>=21.0
```

---

## 5. Execution Pipeline — How to Run From Scratch

### Step 1: Install Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r trader/requirements.txt
```

### Step 2: Fetch Data & Build Feature Matrix
```bash
# 1. Fetch Gold hourly bars and DXY daily bars
python trader/fetch_gold.py

# 2. Compute 8-Gate indicators with zero lookahead bias
python trader/features.py
```

### Step 3: Audit Data Integrity
```bash
python trader/validate.py --symbol all
```
*Must output `VALIDATION PASSED` before proceeding.*

### Step 4: Run Verified Backtest
```bash
python trader/src/backtest_runner.py --strategy gold_certainty
```
*Output on data to 13-Nov-2026 (it changes as `fetch_gold.py` appends new bars):*
```text
Strategy             Mode            Trades    Win%   Sharpe    MaxDD    ExpR     Pips     PF
───────────────────────────────────────────────────────────────────────────────────────────────
gold_certainty_alpha conservative        32   65.6%    1.050   3277.4    0.33    815.2   1.16
───────────────────────────────────────────────────────────────────────────────────────────────
Report saved → trader/reports/backtest_report.txt
Backtesting complete. Strategy scores written to db/trading.db
```

### Step 5: Test Telegram Dispatch Handshake
```bash
python trader/src/telegram_notifier.py
```
*Must output `Handshake success: True` and `Ping delivered: True`.*

### Step 6: Run Real-Time Sentinel Scanner
```bash
# Refresh data first — the scanner refuses to run on data more than 72 hours old
python trader/fetch_gold.py && python trader/features.py

# Scan latest market bar
python trader/src/gold_sentinel.py --account 50000 --risk 1.0

# Scan and automatically broadcast alert to Telegram if a setup triggers
python trader/src/gold_sentinel.py --account 50000 --risk 1.0 --telegram

# Force-broadcast current market status and watch levels to Telegram
python trader/src/gold_sentinel.py --telegram --force
```

### Step 7: Launch Interactive Phone Bot (Optional Daemon)
```bash
python trader/src/telegram_bot.py
```
*Now open Telegram and text `@breakout_xauusd_bot`:*
- `/scan` — Evaluates all 8 gates and sends the trade card or breakout levels.
- `/status` — Displays current macro regime, D1 ATR, DXY trend, and weekly quota.
- `/risk 25000 1.0` — Recalculates lot sizing for a $25,000 account at 1% risk.
- `/rules` — Explains the 8-Gate strategy rules.

---

## 6. Porting to a New Repository or VPS / Cloud Host

To port this entire system into another repository (e.g. `gold_v2`, `gold_trader`, or a fresh Ubuntu/Debian server):

1. **Copy the directory**:
   ```bash
   cp -r trader/ /path/to/target_repo/trader/
   ```
2. **Copy the environment file**:
   ```bash
   cp trader/.env /path/to/target_repo/trader/.env
   ```
3. **Automate with Cron**:
   To scan automatically on every hourly bar close during London and New York sessions (07:00 to 17:00 UTC, Mon–Fri), add this line to your `crontab -e`:
   ```cron
   5 8,9,10,14,15,16 * * 1-5 cd /path/to/target_repo && .venv/bin/python trader/fetch_gold.py && .venv/bin/python trader/features.py && .venv/bin/python trader/src/gold_sentinel.py --telegram >> trader/logs/cron.log 2>&1
   ```
4. **Run Bot as a Systemd Service (Linux)**:
   Create `/etc/systemd/system/gold-sentinel.service`:
   ```ini
   [Unit]
   Description=Gold Sentinel Telegram Bot
   After=network.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/path/to/target_repo
   ExecStart=/path/to/target_repo/.venv/bin/python trader/src/telegram_bot.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```
   Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable gold-sentinel
   sudo systemctl start gold-sentinel
   ```
