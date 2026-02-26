"""
main.py — AlgoBot Entry Point
══════════════════════════════════════════════════════════════════════
Starts the trading bot and web dashboard.

Usage:
    python main.py              # Start bot + dashboard (paper mode works without Zerodha)
    python main.py --check      # Validate config only
    python main.py --test-auth  # Test Zerodha login only
    python main.py --test-balance  # Test live balance fetch
"""

import argparse
import signal
import sys
import time

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
        profile = kite.profile()
        log.info(f"✅ Auth test passed!")
        log.info(f"   User : {profile['user_name']} ({profile['user_id']})")
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
        auth    = AuthManager()
        kite    = auth.ensure_session()
        cm      = CapitalManager(kite, config.DEFAULT_CONFIG)
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
    """
    Full bot startup.
    - PAPER mode (default): starts dashboard immediately, no Zerodha needed.
    - LIVE mode: authenticates with Zerodha first, then starts everything.
    """
    log.info("Starting AlgoBot...")

    # ── Config check ─────────────────────────────────────────────────────────
    errors, warnings = config.validate_config()
    for w in warnings:
        log.warning(w)

    bot_config = dict(config.DEFAULT_CONFIG)
    trade_mode = bot_config.get("trade_mode", "paper")

    # Only block startup in LIVE mode with missing credentials
    if errors and trade_mode == "live":
        for e in errors:
            log.error(f"Config error: {e}")
        log.error("Cannot start in LIVE mode with missing credentials.")
        log.error("Tip: set trade_mode = 'paper' in config.py to run without Zerodha.")
        sys.exit(1)

    log.info(f"✅ Config OK — starting in {'LIVE' if trade_mode == 'live' else 'PAPER'} mode")

    # ── Bot context: references to all live modules ───────────────────────────
    bot_context = {}

    # ── Zerodha session (paper = None) ────────────────────────────────────────
    kite = None
    if trade_mode == "live":
        try:
            from modules.auth import AuthManager
            kite = AuthManager().ensure_session()
            log.info("✅ Zerodha session established.")
        except Exception as e:
            log.error(f"Zerodha auth failed: {e}")
            log.warning("Falling back to PAPER mode.")
            bot_config["trade_mode"] = "paper"

    # ── Capital Manager ───────────────────────────────────────────────────────
    try:
        from modules.capital_manager import CapitalManager
        bot_context["capital_manager"] = CapitalManager(kite, bot_config)
        log.info("✅ Capital Manager ready.")
    except Exception as e:
        log.warning(f"Capital Manager unavailable: {e}")

    # ── Order Manager ─────────────────────────────────────────────────────────
    try:
        from modules.order_manager import OrderManager
        bot_context["order_manager"] = OrderManager(
            kite, bot_context.get("capital_manager"), bot_config
        )
        log.info("✅ Order Manager ready.")
    except Exception as e:
        log.warning(f"Order Manager unavailable: {e}")

    # ── Market Intel ──────────────────────────────────────────────────────────
    try:
        from modules.market_intel import MarketIntel
        bot_context["market_intel"] = MarketIntel(bot_config)
        log.info("✅ Market Intel ready.")
    except Exception as e:
        log.warning(f"Market Intel unavailable: {e}")

    # ── Risk Manager ──────────────────────────────────────────────────────────
    try:
        from modules.risk_manager import RiskManager
        rm = RiskManager(
            bot_config,
            capital_manager = bot_context.get("capital_manager"),
            market_intel    = bot_context.get("market_intel"),
        )
        if "order_manager" in bot_context:
            rm.attach_order_manager(bot_context["order_manager"])
        bot_context["risk_manager"] = rm
        log.info("✅ Risk Manager ready.")
    except Exception as e:
        log.warning(f"Risk Manager unavailable: {e}")

    # ── Strategy Engine ───────────────────────────────────────────────────────
    try:
        from modules.strategy_engine import StrategyEngine
        se = StrategyEngine(bot_config, market_intel=bot_context.get("market_intel"))
        if "risk_manager" in bot_context:
            bot_context["risk_manager"].attach_strategy_engine(se)
        bot_context["strategy_engine"] = se
        log.info("✅ Strategy Engine ready.")
    except Exception as e:
        log.warning(f"Strategy Engine unavailable: {e}")

    # ── Telegram Alerts ───────────────────────────────────────────────────────
    try:
        from modules.telegram_alerts import TelegramAlerts
        ta = TelegramAlerts(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        if ta.is_enabled:
            if "order_manager" in bot_context:
                ta.attach_order_manager(bot_context["order_manager"])
            if "risk_manager" in bot_context:
                ta.attach_risk_manager(bot_context["risk_manager"])
        bot_context["telegram"] = ta
        log.info(f"✅ Telegram ready (enabled={ta.is_enabled}).")
    except Exception as e:
        log.warning(f"Telegram unavailable: {e}")

    # ── Dashboard Server ──────────────────────────────────────────────────────
    try:
        from dashboard.server import DashboardServer
        ds = DashboardServer(bot_config, bot_context)
        ds.start(port=config.DASHBOARD_PORT)
        bot_context["dashboard"] = ds
        log.info(f"✅ Dashboard started.")
    except Exception as e:
        log.error(f"❌ Dashboard failed to start: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # ── All systems go ────────────────────────────────────────────────────────
    mode_label = "📝 PAPER" if bot_config.get("trade_mode") == "paper" else "💰 LIVE"
    log.info("─" * 55)
    log.info(f"🚀 AlgoBot running in {mode_label} mode")
    log.info(f"🌐 Dashboard → http://localhost:{config.DASHBOARD_PORT}")
    log.info(f"⚙️  Settings  → http://localhost:{config.DASHBOARD_PORT}/settings")
    log.info("─" * 55)
    log.info("Press Ctrl+C to stop.")

    # ── Keep the main thread alive ────────────────────────────────────────────
    # Flask runs in a daemon thread — without this loop the process exits
    # immediately and takes the daemon thread with it.
    def on_shutdown(sig, frame):
        log.info("Shutdown signal received — stopping AlgoBot...")
        if "order_manager" in bot_context:
            try:
                bot_context["order_manager"].square_off_all("Bot shutdown")
                log.info("All positions squared off.")
            except Exception:
                pass
        if "telegram" in bot_context:
            try:
                bot_context["telegram"].stop()
            except Exception:
                pass
        log.info("AlgoBot stopped. Goodbye.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  on_shutdown)
    signal.signal(signal.SIGTERM, on_shutdown)

    while True:
        time.sleep(1)


# ── CLI Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print_banner()

    parser = argparse.ArgumentParser(description="AlgoBot — Nifty/BankNifty F&O Trading Bot")
    parser.add_argument("--check",        action="store_true", help="Validate configuration only")
    parser.add_argument("--test-auth",    action="store_true", help="Test Zerodha login")
    parser.add_argument("--test-balance", action="store_true", help="Test live balance fetch")
    args = parser.parse_args()

    if args.check:
        sys.exit(0 if check_config() else 1)
    elif args.test_auth:
        sys.exit(0 if test_auth() else 1)
    elif args.test_balance:
        sys.exit(0 if test_balance() else 1)
    else:
        start_bot()