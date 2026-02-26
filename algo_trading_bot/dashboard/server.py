"""
dashboard/server.py — Module 9: Web Dashboard
═══════════════════════════════════════════════
Flask-based real-time dashboard for the algo trading bot.

Pages:
  GET  /                → Main dashboard (live P&L, open positions, signals)
  GET  /settings        → Settings page (trade config, risk params)

REST API:
  GET  /api/status           → Bot status + capital summary
  GET  /api/trades           → Today's trades (closed + open)
  GET  /api/signals          → Recent signals + risk decisions
  GET  /api/market_bias      → Current market intel snapshot
  GET  /api/positions        → Open positions with live P&L
  POST /api/settings         → Update bot configuration
  POST /api/control/start    → Start the bot
  POST /api/control/stop     → Stop the bot
  POST /api/control/squareoff → Force square-off all positions
  POST /api/control/mode     → Switch paper ↔ live mode

Usage:
    from dashboard.server import DashboardServer
    ds = DashboardServer(config, bot_context)
    ds.start()   # runs on port 8080
"""

import threading
from datetime import datetime
from typing import Optional

import pytz
from flask import Flask, jsonify, render_template, request

from modules.logger import get_logger

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class DashboardServer:
    """
    Lightweight Flask server that exposes the bot's state
    to the browser dashboard via REST JSON endpoints.
    """

    def __init__(self, config: dict, bot_context: dict = None):
        """
        Args:
            config      : Bot configuration dict
            bot_context : Dict of live module references:
                {
                  "capital_manager": CapitalManager,
                  "order_manager":   OrderManager,
                  "risk_manager":    RiskManager,
                  "strategy_engine": StrategyEngine,
                  "market_intel":    MarketIntel,
                  "telegram":        TelegramAlerts,
                }
        """
        self._config  = config
        self._ctx     = bot_context or {}
        self._running = False

        self._app = Flask(
            __name__,
            template_folder="templates",
            static_folder="static",
        )
        self._app.secret_key = config.get("dashboard_secret_key", "dev-secret")

        self._register_routes()
        log.info("DashboardServer initialised.")

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def start(self, host: str = "0.0.0.0", port: int = 8080):
        """Start Flask in a background daemon thread."""
        self._running = True
        t = threading.Thread(
            target=self._run_flask,
            args=(host, port),
            daemon=True,
            name="DashboardServer",
        )
        t.start()
        log.info(f"Dashboard running at http://localhost:{port}")

    def _run_flask(self, host, port):
        import logging as _logging
        _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
        self._app.run(host=host, port=port, debug=False, use_reloader=False)

    # ── Route Registration ────────────────────────────────────────────────────

    def _register_routes(self):
        app = self._app

        # ── Pages ─────────────────────────────────────────────────────────────
        @app.route("/")
        def index():
            return render_template("index.html", config=self._config)

        @app.route("/settings")
        def settings():
            return render_template("settings.html", config=self._config)

        # ── API: Status ───────────────────────────────────────────────────────
        @app.route("/api/status")
        def api_status():
            capital = {}
            if cm := self._ctx.get("capital_manager"):
                try:
                    capital = cm.get_capital_summary()
                except Exception as e:
                    capital = {"error": str(e)}

            om = self._ctx.get("order_manager")
            open_positions = []
            day_pnl        = 0.0
            trades_today   = 0

            if om:
                try:
                    open_positions = [t.to_dict() for t in om.get_open_positions()]
                    day_pnl        = om.get_day_pnl()
                    trades_today   = len(om.get_today_trades())
                except Exception:
                    pass

            rm = self._ctx.get("risk_manager")
            risk_stats = {}
            if rm:
                try:
                    risk_stats = rm.get_stats()
                except Exception:
                    pass

            return jsonify({
                "timestamp":      datetime.now(IST).isoformat(),
                "bot_running":    self._running,
                "trade_mode":     self._config.get("trade_mode", "paper"),
                "capital":        capital,
                "open_positions": open_positions,
                "day_pnl":        day_pnl,
                "trades_today":   trades_today,
                "risk_stats":     risk_stats,
            })

        # ── API: Trades ───────────────────────────────────────────────────────
        @app.route("/api/trades")
        def api_trades():
            om = self._ctx.get("order_manager")
            if not om:
                return jsonify({"trades": [], "error": "Order manager not available"})
            try:
                trades = [t.to_dict() for t in om.get_today_trades()]
                return jsonify({
                    "trades":   trades,
                    "count":    len(trades),
                    "day_pnl":  om.get_day_pnl(),
                })
            except Exception as e:
                return jsonify({"trades": [], "error": str(e)})

        # ── API: Positions ────────────────────────────────────────────────────
        @app.route("/api/positions")
        def api_positions():
            om = self._ctx.get("order_manager")
            if not om:
                return jsonify({"positions": []})
            try:
                positions = [t.to_dict() for t in om.get_open_positions()]
                return jsonify({"positions": positions, "count": len(positions)})
            except Exception as e:
                return jsonify({"positions": [], "error": str(e)})

        # ── API: Market Bias ──────────────────────────────────────────────────
        @app.route("/api/market_bias")
        def api_market_bias():
            intel = self._ctx.get("market_intel")
            if not intel:
                return jsonify({"error": "Market intel not available"})
            try:
                snap = intel.get_snapshot()
                if snap:
                    return jsonify({
                        "bias_score":        snap.bias_score,
                        "bias_label":        snap.bias_label,
                        "nifty":             snap.nifty_value,
                        "nifty_change_pct":  snap.nifty_change_pct,
                        "banknifty":         snap.banknifty_value,
                        "banknifty_change_pct": snap.banknifty_change_pct,
                        "india_vix":         snap.india_vix,
                        "usdinr":            snap.usdinr,
                        "dow_change_pct":    snap.dow_change_pct,
                        "fii_net_buy":       snap.fii_net_buy,
                        "dii_net_buy":       snap.dii_net_buy,
                        "high_impact_events": snap.high_impact_events,
                        "bias_reasoning":    snap.bias_reasoning,
                        "updated_at":        snap.timestamp.isoformat() if snap.timestamp else None,
                    })
                return jsonify({"error": "No snapshot available yet"})
            except Exception as e:
                return jsonify({"error": str(e)})

        # ── API: Signals ──────────────────────────────────────────────────────
        @app.route("/api/signals")
        def api_signals():
            rm = self._ctx.get("risk_manager")
            if not rm:
                return jsonify({"signals": []})
            try:
                stats  = rm.get_stats()
                blocks = stats.get("recent_blocks", [])
                return jsonify({
                    "stats":          stats,
                    "recent_blocks":  blocks,
                    "block_summary":  rm.get_block_summary(),
                })
            except Exception as e:
                return jsonify({"signals": [], "error": str(e)})

        # ── API: Settings GET ─────────────────────────────────────────────────
        @app.route("/api/settings", methods=["GET"])
        def api_settings_get():
            # Return current config (filter sensitive keys)
            safe = {k: v for k, v in self._config.items()
                    if k not in ("dashboard_secret_key",)}
            return jsonify(safe)

        # ── API: Settings POST ────────────────────────────────────────────────
        @app.route("/api/settings", methods=["POST"])
        def api_settings_post():
            data = request.get_json(silent=True) or {}

            # Allowed settings to update at runtime
            allowed = {
                "trade_mode", "funds_to_use_pct", "max_capital_per_trade_pct",
                "max_trades_per_day", "sl_percentage", "risk_reward_ratio",
                "trail_sl_trigger_pct", "min_signal_conditions",
                "daily_loss_limit", "daily_profit_target",
                "trading_start", "trading_end", "avoid_lunch",
                "use_market_bias_filter", "telegram_alerts",
                "min_confidence", "bias_block_threshold",
            }

            updated = {}
            for key, val in data.items():
                if key in allowed:
                    self._config[key] = val
                    updated[key] = val

            if not updated:
                return jsonify({"ok": False, "error": "No valid settings provided"}), 400

            log.info(f"Dashboard settings updated: {updated}")
            return jsonify({"ok": True, "updated": updated})

        # ── API: Control ──────────────────────────────────────────────────────
        @app.route("/api/control/squareoff", methods=["POST"])
        def api_squareoff():
            om = self._ctx.get("order_manager")
            if not om:
                return jsonify({"ok": False, "error": "Order manager not available"})
            try:
                om.square_off_all("Manual square-off from dashboard")
                return jsonify({"ok": True, "message": "All positions squared off"})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})

        @app.route("/api/control/mode", methods=["POST"])
        def api_switch_mode():
            data = request.get_json(silent=True) or {}
            mode = data.get("mode", "paper")
            if mode not in ("paper", "live"):
                return jsonify({"ok": False, "error": "mode must be 'paper' or 'live'"}), 400

            self._config["trade_mode"] = mode
            log.info(f"Trade mode switched to: {mode.upper()} via dashboard")
            return jsonify({"ok": True, "mode": mode})

        @app.route("/api/control/start", methods=["POST"])
        def api_start():
            self._running = True
            return jsonify({"ok": True, "message": "Bot marked as running"})

        @app.route("/api/control/stop", methods=["POST"])
        def api_stop():
            self._running = False
            return jsonify({"ok": True, "message": "Bot marked as stopped"})

        # ── Health check ──────────────────────────────────────────────────────
        @app.route("/api/health")
        def api_health():
            return jsonify({"status": "ok", "timestamp": datetime.now(IST).isoformat()})
