"""
main.py — AlgoBot Entry Point
══════════════════════════════════════════════════════════════════════
Starts the trading bot and web dashboard.

Usage:
    python main.py              # Start bot + dashboard
    python main.py --check      # Validate config only
    python main.py --test-auth  # Test Zerodha login only
"""

import argparse
import sys
from modules.logger import get_logger
import config

log = get_logger("main")


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║          🤖  ALGO TRADING BOT  v2.0                         ║
║          Nifty • BankNifty • F&O Automation                 ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


def check_config():
    """Validate all configuration values."""
    errors, warnings = config.validate_config()

    print("\n📋 Configuration Validation:")
    for e in errors:
        print(f"  ❌ ERROR  : {e}")
    for w in warnings:
        print(f"  ⚠️  WARNING: {w}")

    if not errors and not warnings:
        print("  ✅ All configuration values are set correctly!")
    elif errors:
        print(f"\n  Please update your .env file and try again.")
        print(f"  .env location: {config.BASE_DIR / '.env'}")

    print(f"\n  Dashboard will run at: http://localhost:{config.DASHBOARD_PORT}")
    print(f"  Database location:     {config.DATABASE_DIR / 'algo_bot.db'}")
    print(f"  Log files:             {config.BASE_DIR / 'logs'}/\n")

    return len(errors) == 0


def test_auth():
    """Test Zerodha authentication flow."""
    log.info("Testing Zerodha authentication...")

    errors, _ = config.validate_config()
    if errors:
        for e in errors:
            log.error(e)
        log.error("Fix your .env file before testing auth.")
        return False

    try:
        from modules.auth import AuthManager
        auth = AuthManager()
        kite = auth.ensure_session()

        # Test by fetching profile
        profile = kite.profile()
        log.info(f"✅ Auth test passed!")
        log.info(f"   User: {profile['user_name']} ({profile['user_id']})")
        log.info(f"   Email: {profile['email']}")
        return True

    except Exception as e:
        log.error(f"❌ Auth test failed: {e}")
        return False


def test_balance():
    """Test balance fetch after successful auth."""
    log.info("Testing balance fetch...")
    try:
        from modules.auth import AuthManager
        from modules.capital_manager import CapitalManager

        auth = AuthManager()
        kite = auth.ensure_session()

        # Use default config for test
        cm = CapitalManager(kite, config.DEFAULT_CONFIG)
        summary = cm.get_capital_summary()

        print("\n💰 Live Balance Summary:")
        print(f"   Available Balance : ₹{summary['available_balance']:,.0f}")
        print(f"   Usable Capital    : ₹{summary['usable_capital']:,.0f}")
        print(f"   Per Trade Limit   : ₹{summary['per_trade_limit']:,.0f}")
        print(f"   Can Trade         : {'✅ Yes' if summary['can_trade'] else '🚫 No'}")
        if not summary['can_trade']:
            print(f"   Reason            : {summary['reason']}")
        return True

    except Exception as e:
        log.error(f"Balance fetch failed: {e}")
        return False


def start_bot():
    """Full bot startup — auth + dashboard + trading engine."""
    log.info("Starting AlgoBot in full mode...")

    errors, warnings = config.validate_config()
    if errors:
        for e in errors:
            log.error(f"Config error: {e}")
        log.error("Cannot start bot with missing configuration. Please check your .env file.")
        sys.exit(1)

    for w in warnings:
        log.warning(w)

    log.info("✅ Configuration validated.")
    log.info("📦 Modules will be loaded as they are built (Phase 1 in progress).")
    log.info(f"🌐 Dashboard will be available at: http://localhost:{config.DASHBOARD_PORT}")
    log.info("")
    log.info("Next steps to complete:")
    log.info("  1. Fill in .env with your Zerodha API credentials")
    log.info("  2. Run: python main.py --test-auth")
    log.info("  3. Run: python main.py --test-balance")
    log.info("  4. Continue building Module 3 (Data Feed)")


# ── CLI Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print_banner()

    parser = argparse.ArgumentParser(description="AlgoBot — Nifty/BankNifty F&O Trading Bot")
    parser.add_argument("--check",         action="store_true", help="Validate configuration only")
    parser.add_argument("--test-auth",     action="store_true", help="Test Zerodha login")
    parser.add_argument("--test-balance",  action="store_true", help="Test live balance fetch")
    args = parser.parse_args()

    if args.check:
        ok = check_config()
        sys.exit(0 if ok else 1)

    elif args.test_auth:
        ok = test_auth()
        sys.exit(0 if ok else 1)

    elif args.test_balance:
        ok = test_balance()
        sys.exit(0 if ok else 1)

    else:
        start_bot()
