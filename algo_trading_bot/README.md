# 🤖 AlgoBot — Nifty/BankNifty F&O Intraday Trading Bot

> **Fully automated intraday options trading bot for Zerodha**  
> Trades Nifty & BankNifty options using intelligent trading strategies with real-time monitoring, notifications, and a web dashboard.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Active-brightgreen)

---

## 📋 Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Usage](#usage)
- [Modules](#modules)
- [Dashboard](#dashboard)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## ✨ Features

- **🤖 Fully Automated Trading**: Executes trades based on technical indicators without manual intervention
- **📊 Real-Time Monitoring**: Live candle data processing and strategy execution
- **💬 Telegram Notifications**: Instant alerts for trades, errors, and account updates
- **📈 Web Dashboard**: Interactive dashboard to monitor bot performance and market data
- **💰 Capital Management**: Intelligent position sizing and risk management
- **🔐 Secure Authentication**: OAuth-based Zerodha authentication via Kite Connect
- **📉 Technical Analysis**: Built-in indicators (EMA, RSI, VWAP, Momentum)
- **📝 Comprehensive Logging**: Detailed logs for debugging and performance tracking
- **⏰ Scheduled Jobs**: Pre-market, intraday, and end-of-day automation
- **💾 Data Persistence**: SQLite database for historical data and performance metrics

---

## ✅ Prerequisites

- **Python 3.11+** installed ([Download](https://www.python.org/downloads/))
- **Zerodha Trading Account** (Free account available at [zerodha.com](https://zerodha.com))
- **Kite Connect API App** created at [developers.kite.trade](https://developers.kite.trade)
- **Telegram Bot** (Optional, for notifications)
- **Internet Connection** for live market data

---

## 🚀 Quick Start

### Step 1: Clone & Setup Environment
```bash
git clone https://github.com/Jaffar3600/algo-trading-bot.git
cd algo-trading-bot/algo_trading_bot
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Credentials
Create a `.env` file in the `algo_trading_bot` directory:
```env
# Zerodha API Credentials
KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here
ZERODHA_USER_ID=your_client_id

# Telegram Notifications (Optional)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Dashboard Configuration
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=5000

# Trading Parameters
TRADING_SYMBOL=NIFTY
CANDLE_INTERVAL=5  # minutes
MAX_POSITIONS=2
```

### Step 4: Validate Configuration
```bash
python main.py --check
```
Expected output:
```
📋 Configuration Validation:
  ✅ All configuration values are set correctly!
  Dashboard will run at: http://localhost:5000
```

### Step 5: Test Zerodha Authentication
```bash
python main.py --test-auth
```
This will:
1. Open your browser
2. Redirect to Zerodha login
3. Automatically capture the authentication token

### Step 6: Test Account Connection
```bash
python main.py --test-balance
```
Confirms the bot can read your live account balance.

### Step 7: Start the Bot
```bash
python main.py
```
The bot will start trading and the dashboard will be available at `http://localhost:5000`

---

## 🔧 Configuration

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `KITE_API_KEY` | Your Kite Connect API Key | `abc123xyz` |
| `KITE_API_SECRET` | Your Kite Connect API Secret | `xyz123abc` |
| `ZERODHA_USER_ID` | Your Zerodha Client ID | `AB1234` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token (Optional) | `123456:ABC-DEF` |
| `TELEGRAM_CHAT_ID` | Your Telegram Chat ID (Optional) | `987654321` |
| `DASHBOARD_PORT` | Port for web dashboard | `5000` |
| `TRADING_SYMBOL` | Symbol to trade | `NIFTY`, `BANKNIFTY` |
| `CANDLE_INTERVAL` | Candle timeframe in minutes | `5`, `15`, `60` |

### Zerodha Setup

1. **Create Kite Connect App**:
   - Visit [developers.kite.trade](https://developers.kite.trade)
   - Log in with your Zerodha credentials
   - Create a new app with these redirect settings:
     - **Redirect URL**: `http://127.0.0.1:8080/callback`

2. **Get Your Credentials**:
   - Copy your **API Key** and **API Secret**
   - Add to `.env` file

3. **First Time Login**:
   - Run `python main.py --test-auth`
   - Browser will open for Zerodha login
   - Grant permissions when prompted

---

## 📁 Project Structure

```
algo_trading_bot/
├── main.py                 # Entry point - Start the bot
├── config.py              # Configuration management
├── requirements.txt       # Python dependencies
├── .env                   # Credentials (do NOT commit)
├── .gitignore             # Git ignore rules
├── README.md              # This file
│
├── modules/               # Core trading modules
│   ├── auth.py           # Zerodha authentication
│   ├── capital_manager.py # Position sizing & risk management
│   ├── logger.py         # Logging configuration
│   └── __init__.py
│
├── strategies/            # Trading strategies
│   └── __init__.py
│
├── database/              # Data persistence
│   └── __init__.py
│
├── dashboard/             # Web dashboard
│   ├── __init__.py
│   ├── static/
│   │   ├── css/
│   │   └── js/
│   └── templates/
│
├── data/                  # Market data
│   └── candles/          # Historical candle data
│
├── tests/                 # Unit tests
│   └── __init__.py
│
└── logs/                  # Trading logs (auto-generated)
```

---

## 🎯 Usage

### Starting the Bot

**Full Mode** (Trading + Dashboard):
```bash
python main.py
```

**Dry Run** (No actual trades):
```bash
python main.py --dry-run
```

**Configuration Check Only**:
```bash
python main.py --check
```

**Authentication Test**:
```bash
python main.py --test-auth
```

**Balance Check**:
```bash
python main.py --test-balance
```

### Dashboard Access

Once the bot is running, open your browser and navigate to:
```
http://localhost:5000
```

The dashboard displays:
- Real-time P&L
- Open positions
- Trading history
- Market alerts
- System status

---

## 🔧 Modules

### `auth.py`
Handles Zerodha authentication via OAuth
- Token refresh
- Session management
- Error handling

### `capital_manager.py`
Manages trading capital and position sizing
- Available balance calculation
- Position limit checks
- Risk per trade calculation
- Margin requirements

### `logger.py`
Centralized logging configuration
- File and console logging
- Color-coded output
- Log rotation
- Performance metrics

---

## 📊 Dashboard Features

- **Live Charts**: Real-time candle visualization
- **Position Monitor**: Current open positions and P&L
- **Trade History**: Complete trading record with entry/exit prices
- **Alerts**: System notifications and warnings
- **Market Data**: Key indices and options chain data
- **Performance Stats**: Win rate, profit factor, max drawdown

---

## 📝 Logging

Logs are automatically generated in the `logs/` directory:

```
logs/
├── algo_bot_YYYY-MM-DD.log    # Daily trading logs
├── auth_YYYY-MM-DD.log        # Authentication logs
├── dashboard_YYYY-MM-DD.log   # Dashboard logs
└── error_YYYY-MM-DD.log       # Error logs
```

View logs:
```bash
# Linux/Mac
tail -f logs/algo_bot_*.log

# Windows PowerShell
Get-Content logs/algo_bot_*.log -Wait
```

---

## ⚠️ Risk Disclaimer

**This bot is provided for educational purposes only.**

- Trading derivatives carries significant risk
- You can lose more than your initial investment
- Always trade with capital you can afford to lose
- Test thoroughly in dry-run mode before live trading
- Monitor the bot regularly during trading hours
- Use position sizing limits (set in configuration)

**Start small. Monitor closely. Scale gradually.**

---

## 🐛 Troubleshooting

### Authentication Fails
**Problem**: "Invalid API credentials"
- Verify API Key and Secret in `.env`
- Check that app is created at [developers.kite.trade](https://developers.kite.trade)
- Ensure redirect URL is `http://127.0.0.1:8080/callback`

### Dashboard Not Accessible
**Problem**: "Cannot connect to http://localhost:5000"
- Check if port 5000 is already in use: `netstat -ano | findstr :5000` (Windows)
- Change `DASHBOARD_PORT` in `.env`
- Ensure Flask is installed: `pip install flask`

### No Data in Dashboard
**Problem**: Dashboard shows "No data available"
- Verify bot is running: Check console output
- Check internet connection
- Verify Zerodha login is successful
- Check logs in `logs/` directory

### Zerodha Login Loop
**Problem**: Browser keeps redirecting to login
- Clear browser cookies for zerodha.com
- Try incognito/private browsing mode
- Check system time is correct (UTC ±5:30 for IST)

---

## 📦 Dependencies

All dependencies are listed in `requirements.txt`:

| Package | Purpose |
|---------|---------|
| `kiteconnect` | Zerodha API integration |
| `pandas` | Data manipulation |
| `ta` | Technical indicators |
| `flask` | Web dashboard |
| `APScheduler` | Job scheduling |
| `sqlalchemy` | Database ORM |
| `python-telegram-bot` | Notifications |
| `requests` | HTTP requests |
| `beautifulsoup4` | Web scraping |
| `colorlog` | Colored logging |

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/YourFeature`
3. Commit changes: `git commit -m 'Add YourFeature'`
4. Push to branch: `git push origin feature/YourFeature`
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 📞 Support

For issues, questions, or suggestions:
- Open an [Issue](https://github.com/Jaffar3600/algo-trading-bot/issues)
- Check existing documentation
- Review logs for error messages

---

## 🙏 Acknowledgments

- [Zerodha](https://zerodha.com) for the API
- [Python](https://python.org) community
- Contributors and testers

---

**Last Updated**: February 2026  
**Version**: 2.0  
**Status**: Active Development

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
