"""
config.py — Central Application Configuration
All hardcoded app-level constants live here.
Trading settings are stored in the database (managed via dashboard).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# ── Base Paths ────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent
DATA_DIR     = BASE_DIR / "data"
DATABASE_DIR = BASE_DIR / "database"
LOGS_DIR     = BASE_DIR / "logs"
CANDLES_DIR  = DATA_DIR / "candles"
SESSION_FILE = DATA_DIR / "session_token.json"

# Create directories if they don't exist
for d in [DATA_DIR, DATABASE_DIR, LOGS_DIR, CANDLES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Zerodha API ───────────────────────────────────────────────────────────────
KITE_API_KEY    = os.getenv("KITE_API_KEY",    "YOUR_API_KEY_HERE")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "YOUR_API_SECRET_HERE")
ZERODHA_USER_ID = os.getenv("ZERODHA_USER_ID", "YOUR_CLIENT_ID")
ZERODHA_PASSWORD    = os.getenv("ZERODHA_PASSWORD",    "")
ZERODHA_TOTP_SECRET = os.getenv("ZERODHA_TOTP_SECRET", "")

# Kite Connect login redirect — our local auth server listens here
AUTH_REDIRECT_URL = "http://127.0.0.1:5000/callback"
AUTH_SERVER_PORT  = 5000

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_PORT       = int(os.getenv("DASHBOARD_PORT", 8080))
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "dev-secret-change-me")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = f"sqlite:///{DATABASE_DIR}/algo_bot.db"

# ── App ───────────────────────────────────────────────────────────────────────
APP_ENV   = os.getenv("APP_ENV",   "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
IS_DEV    = APP_ENV == "development"

# ── Market Hours (IST) ────────────────────────────────────────────────────────
MARKET_OPEN  = "09:15"
MARKET_CLOSE = "15:30"
BOT_LOGIN_TIME = "08:55"   # Bot triggers login prompt at this time

# ── Instruments ───────────────────────────────────────────────────────────────
# NSE instrument tokens (used for WebSocket subscriptions)
# These are fixed Zerodha instrument tokens for indices
INSTRUMENT_TOKENS = {
    "NIFTY 50":   256265,
    "NIFTY BANK": 260105,
}

# ── Trading Defaults (overridden by dashboard/database settings) ──────────────
DEFAULT_CONFIG = {
    "active_instruments":      ["NIFTY", "BANKNIFTY"],
    "active_strategy":         "momentum_strike",
    "trade_mode":              "paper",           # 'paper' or 'live'
    "funds_to_use_pct":        80,                # % of available balance to use
    "max_capital_per_trade_pct": 25,              # % of daily funds per trade
    "max_trades_per_day":      2,
    "sl_percentage":           33,               # 33% of entry premium
    "risk_reward_ratio":       2.0,
    "trail_sl_trigger_pct":    45,               # Move SL to BE at 45% profit
    "min_signal_conditions":   3,                # Out of 5
    "trading_start":           "09:30",
    "trading_end":             "15:00",
    "avoid_lunch":             True,
    "lunch_start":             "11:30",
    "lunch_end":               "13:00",
    "daily_loss_limit":        5000,             # Rs. — stop trading if breached
    "daily_profit_target":     0,                # Rs. — 0 = disabled
    "use_market_bias_filter":  True,
    "telegram_alerts":         True,
    "market_intel_interval":   10,               # minutes
}

# ── Validation ────────────────────────────────────────────────────────────────
def validate_config():
    """Check if critical config values are set. Called at startup."""
    warnings = []
    errors   = []

    if KITE_API_KEY == "YOUR_API_KEY_HERE":
        errors.append("KITE_API_KEY not set in .env file")
    if KITE_API_SECRET == "YOUR_API_SECRET_HERE":
        errors.append("KITE_API_SECRET not set in .env file")
    if not TELEGRAM_BOT_TOKEN:
        warnings.append("TELEGRAM_BOT_TOKEN not set — Telegram alerts will be disabled")
    if not TELEGRAM_CHAT_ID:
        warnings.append("TELEGRAM_CHAT_ID not set — Telegram alerts will be disabled")

    return errors, warnings


if __name__ == "__main__":
    errors, warnings = validate_config()
    print("\n📋 Config Validation:")
    for e in errors:
        print(f"  ❌ ERROR:   {e}")
    for w in warnings:
        print(f"  ⚠️  WARNING: {w}")
    if not errors and not warnings:
        print("  ✅ All config values look good!")
    print(f"\n  BASE_DIR    : {BASE_DIR}")
    print(f"  DATABASE_URL: {DATABASE_URL}")
    print(f"  APP_ENV     : {APP_ENV}")
    print(f"  DASHBOARD   : http://localhost:{DASHBOARD_PORT}")
