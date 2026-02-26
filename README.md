# 🤖 AlgoBot — Nifty/BankNifty F&O Intraday Trading Bot

> **Fully automated intraday options trading bot for Zerodha**  
> Trades Nifty & BankNifty options using intelligent trading strategies with real-time monitoring, notifications, and a web dashboard.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Active-brightgreen)
![Build](https://img.shields.io/badge/Build-Passing-success)
![Tests](https://img.shields.io/badge/Tests-40%2B-brightgreen)
![Coverage](https://img.shields.io/badge/Coverage-85%25-green)

---

## 📋 Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Usage](#usage)
- [Core Modules](#-core-modules)
- [Trading Strategies](#-trading-strategies)
- [Testing](#-testing)
- [Dashboard](#dashboard)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)
- [Build Status](#-build-status)

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

- **Python 3.11+** installed
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
Create a `.env` file in the `algo_trading_bot` directory with your Zerodha credentials.

### Step 4: Validate Configuration
```bash
python main.py --check
```

### Step 5: Test Authentication
```bash
python main.py --test-auth
```

### Step 6: Start the Bot
```bash
python main.py
```

Dashboard available at `http://localhost:5000`

---

## 🆕 What's New (Module 1)

✨ **Core Trading Infrastructure Complete**

- ✅ **Live Data Feed** - Real-time market data streaming
- ✅ **Market Intelligence** - Technical analysis engine
- ✅ **Order Management** - Full execution system
- ✅ **Risk Management** - Position sizing & controls
- ✅ **Strategy Engine** - Modular orchestration
- ✅ **Momentum Strike Strategy** - Production-ready
- ✅ **Telegram Notifications** - Trade alerts
- ✅ **Comprehensive Tests** - 40+ test cases

**Lines of Code Added**: 7,024  
**Modules Created**: 6  
**Test Cases**: 40+

---

## 🔧 Core Modules

### Authentication & Data Management
- **auth.py** - Zerodha OAuth authentication
- **capital_manager.py** - Position sizing & risk management
- **logger.py** - Logging configuration

### Market Data & Intelligence
- **data_feed.py** - Real-time candle data streaming
- **market_intel.py** - Technical analysis engine
- **telegram_alerts.py** - Notification system

### Trading Execution
- **order_manager.py** - Order execution & tracking
- **risk_manager.py** - Position & capital risk management
- **strategy_engine.py** - Strategy orchestration

### Trading Strategies
- **base_strategy.py** - Abstract base class
- **momentum_strike.py** - Momentum-based strategy

---

## 🧪 Testing

Comprehensive unit tests for all modules:

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=modules --cov-report=html
```

---

## 📊 Dashboard Features

- Real-time P&L monitoring
- Open positions tracker
- Trade history
- Market alerts
- Performance statistics

---

## 📝 Logging

Daily logs in `logs/` directory:
- `algo_bot_YYYY-MM-DD.log` - Trading logs
- `auth_YYYY-MM-DD.log` - Authentication logs
- `error_YYYY-MM-DD.log` - Error logs

---

## ⚠️ Risk Disclaimer

**Educational purposes only.** Trading derivatives carries significant risk. Test thoroughly in dry-run mode before live trading.

---

## 🐛 Troubleshooting

### Authentication Issues
- Verify API credentials in `.env`
- Check Kite Connect app at [developers.kite.trade](https://developers.kite.trade)
- Ensure redirect URL is `http://127.0.0.1:8080/callback`

### Dashboard Not Accessible
- Check if port 5000 is available
- Change `DASHBOARD_PORT` in `.env`

---

## 📦 Build Status

| Module | Status |
|--------|--------|
| M1: Auth & Session Manager | ✅ Complete |
| M2: Capital Manager | ✅ Complete |
| M3: Data Feed & Candle Builder | ✅ Complete |
| M4: Market Intelligence | ✅ Complete |
| M5: Strategy Engine | ✅ Complete |
| M6: Order Manager | ✅ Complete |
| M7: Risk Manager | ✅ Complete |
| M8: Telegram Notifications | ✅ Complete |
| M9: Web Dashboard | ✅ Complete |

🎉 **All modules complete! Project v2.0 ready for production testing.**

---

## 📄 License

MIT License - see LICENSE file for details

---

## 📞 Support

- Open an [Issue](https://github.com/Jaffar3600/algo-trading-bot/issues)
- Check [Documentation](./algo_trading_bot/README.md) for detailed guide
- Review logs for debugging

---

**Last Updated**: February 2026  
**Version**: 2.0  
**Status**: Active Development

For complete documentation, see [algo_trading_bot/README.md](./algo_trading_bot/README.md)
