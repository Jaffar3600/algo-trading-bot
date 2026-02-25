# 🤖 AlgoBot — Nifty/BankNifty F&O Intraday Trading Bot

Fully automated intraday options trading bot for Zerodha.
Trades Nifty & BankNifty options using the Momentum Strike strategy.

---

## ✅ Prerequisites

- Python 3.11+ installed
- Zerodha trading account
- Kite Connect app created at https://developers.kite.trade

---

## 🚀 Setup (One-Time)

### Step 1: Install dependencies
```bash
cd algo_trading_bot
pip install -r requirements.txt
```

### Step 2: Configure your credentials
Open the `.env` file and fill in:
```
KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here
ZERODHA_USER_ID=your_client_id
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

### Step 3: Validate configuration
```bash
python main.py --check
```

### Step 4: Test Zerodha login
```bash
python main.py --test-auth
```
This will open your browser → log in with Zerodha → bot captures the token automatically.

### Step 5: Test balance fetch
```bash
python main.py --test-balance
```
Confirms the bot can read your live account balance.

---

## 🏃 Daily Usage

```bash
python main.py
```

The bot will:
- Trigger login at 8:55 AM
- Start monitoring at 9:30 AM
- Trade automatically until 3:00 PM
- Square off all positions by 3:00 PM
- Send daily summary via Telegram

Dashboard available at: **http://localhost:8080**

---

## 📁 Project Structure

```
algo_trading_bot/
├── main.py                  # Entry point
├── config.py                # App configuration
├── .env                     # Your credentials (never share!)
├── modules/                 # Core bot modules
├── strategies/              # Trading strategies (plug-and-play)
├── dashboard/               # Web UI
├── database/                # SQLite models
├── data/                    # Session tokens, candle cache
├── logs/                    # Daily log files
└── tests/                   # Unit tests & paper trading
```

---

## ⚠️ Important

- Always test in **Paper Trade mode** for 2 weeks before going live
- Never commit your `.env` file to GitHub
- Monitor the bot via Telegram especially in the first few weeks

---

## 📦 Build Status

| Module | Status |
|--------|--------|
| M1: Auth & Session Manager | ✅ Complete |
| M2: Capital Manager | ✅ Complete |
| M3: Data Feed & Candle Builder | 🔲 Next |
| M4: Market Intelligence | 🔲 Pending |
| M5: Strategy Engine | 🔲 Pending |
| M6: Order Manager | 🔲 Pending |
| M7: Risk Manager | 🔲 Pending |
| M8: Telegram Notifications | 🔲 Pending |
| M9: Web Dashboard | 🔲 Pending |
