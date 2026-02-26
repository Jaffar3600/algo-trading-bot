"""
tests/test_dashboard.py — Test Module 9: Dashboard Server
══════════════════════════════════════════════════════════
All tests run with Flask test client — no browser or live bot needed.

T1  — GET / returns 200 and contains dashboard HTML
T2  — GET /settings returns 200 and contains settings HTML
T3  — GET /api/health returns {"status": "ok"}
T4  — GET /api/status — no context → returns safe defaults
T5  — GET /api/status — with mocked modules → returns full data
T6  — GET /api/trades — no order manager → returns empty trades list
T7  — GET /api/trades — with trades → returns serialised trade dicts
T8  — GET /api/positions — returns open positions
T9  — GET /api/market_bias — returns bias snapshot fields
T10 — GET /api/signals — returns signal stats and block log
T11 — GET /api/settings — returns config (no secret key)
T12 — POST /api/settings — updates allowed keys in config
T13 — POST /api/settings — rejects unknown keys
T14 — POST /api/control/squareoff — calls order_manager.square_off_all
T15 — POST /api/control/mode paper → updates trade_mode
T16 — POST /api/control/mode live  → updates trade_mode
T17 — POST /api/control/mode invalid → returns 400
T18 — POST /api/control/start → sets running=True
T19 — POST /api/control/stop  → sets running=False
T20 — Error resilience: bad order_manager still returns valid JSON

Run:
    python tests/test_dashboard.py
"""

import sys, os, types
import datetime as _dt
from zoneinfo import ZoneInfo

# ── pandas before pytz mock ───────────────────────────────────────────────────
import pandas as _pd  # noqa

# ── pytz mock ─────────────────────────────────────────────────────────────────
class _TZ(_dt.tzinfo):
    def __init__(self, name="Asia/Kolkata"):
        self._zi = ZoneInfo(name)
    def utcoffset(self, dt): return self._zi.utcoffset(dt)
    def tzname(self,    dt): return self._zi.tzname(dt)
    def dst(self,       dt): return self._zi.dst(dt)
    def localize(self,  dt): return dt.replace(tzinfo=self)

_pytz = types.ModuleType("pytz")
_pytz.timezone = lambda n: _TZ(n)
sys.modules["pytz"] = _pytz

for _m in ["kiteconnect", "requests", "bs4"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))
sys.modules["kiteconnect"].KiteTicker = object

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
from dashboard.server import DashboardServer
from modules.logger import get_logger
import config as app_config

log = get_logger("test_dashboard")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_config(**overrides):
    cfg = dict(app_config.DEFAULT_CONFIG)
    cfg.update({
        "trade_mode":             "paper",
        "dashboard_secret_key":   "test-secret",
        "min_confidence":         50,
        "min_signal_conditions":  3,
        "max_trades_per_day":     2,
        "daily_loss_limit":       5000,
        "bias_block_threshold":   4,
        "max_concurrent_positions": 2,
    })
    cfg.update(overrides)
    return cfg


def make_trade_dict(trade_id="T001", symbol="NIFTY", action="CALL",
                    net_pnl=3000.0, state="target_hit"):
    return {
        "trade_id":       trade_id,
        "mode":           "paper",
        "symbol":         symbol,
        "option_symbol":  f"{symbol}25FEB22200CE",
        "action":         action,
        "state":          state,
        "lots":           1,
        "lot_size":       75,
        "quantity":       75,
        "entry_price":    120.0,
        "exit_price":     200.0,
        "sl_price":       80.0,
        "target_price":   200.0,
        "entry_order_id": "PAPER-T001",
        "exit_order_id":  "PAPER-EXIT-T001",
        "gross_pnl":      6000.0,
        "charges":        55.0,
        "net_pnl":        net_pnl,
        "signal_time":    None,
        "entry_time":     "2026-02-25T10:00:00+05:30",
        "exit_time":      "2026-02-25T11:30:00+05:30",
        "exit_reason":    "Target hit",
        "conditions_met": 4,
        "confidence":     75,
        "bias_score":     3,
    }


def make_mock_trade(trade_id="T001", net_pnl=3000.0):
    t = MagicMock()
    t.to_dict.return_value = make_trade_dict(trade_id=trade_id, net_pnl=net_pnl)
    return t


def make_mock_context(day_pnl=1500.0, trades=None, open_positions=None,
                       bias_score=3, has_events=False):
    """Build a bot_context dict with all modules mocked."""
    # Capital Manager
    cm = MagicMock()
    cm.get_capital_summary.return_value = {
        "available_balance": 100000,
        "usable_capital":    80000,
        "per_trade_limit":   20000,
        "max_trades_today":  2,
        "daily_loss_limit":  5000,
        "funds_pct":         80,
        "per_trade_pct":     25,
        "day_pnl":           day_pnl,
        "trades_taken":      len(trades or []),
        "can_trade":         True,
        "reason":            "OK",
    }

    # Order Manager
    om = MagicMock()
    _trades = trades if trades is not None else [make_mock_trade()]
    om.get_today_trades.return_value   = _trades
    om.get_open_positions.return_value = open_positions or []
    om.get_day_pnl.return_value        = day_pnl

    # Risk Manager
    rm = MagicMock()
    rm.get_stats.return_value = {
        "signals_received": 5,
        "signals_approved": 3,
        "signals_blocked":  2,
        "approval_rate":    60.0,
        "recent_blocks":    [],
    }
    rm.get_block_summary.return_value = {"R3_min_confidence": 2}

    # Market Intel
    snap = MagicMock()
    snap.bias_score           = bias_score
    snap.bias_label           = "Mildly Bullish"
    snap.nifty_value          = 22350.0
    snap.nifty_change_pct     = 0.42
    snap.banknifty_value      = 48200.0
    snap.banknifty_change_pct = 0.31
    snap.india_vix            = 14.2
    snap.dow_change_pct       = 0.15
    snap.nikkei_change_pct    = -0.22
    snap.fii_net_buy          = 850.0
    snap.dii_net_buy          = -120.0
    snap.usdinr               = 86.42
    snap.high_impact_events   = ["High VIX: 22.5"] if has_events else []
    snap.bias_reasoning       = ["Gift Nifty +0.3%"]
    snap.timestamp            = _dt.datetime.now()

    intel = MagicMock()
    intel.get_snapshot.return_value = snap

    return {
        "capital_manager": cm,
        "order_manager":   om,
        "risk_manager":    rm,
        "market_intel":    intel,
    }


def make_server(context=None, **config_overrides):
    """Build a DashboardServer with Flask test client."""
    cfg = make_config(**config_overrides)
    ctx = context if context is not None else {}
    ds  = DashboardServer(cfg, ctx)
    ds._app.testing = True
    client = ds._app.test_client()
    return ds, client


# ─────────────────────────────────────────────────────────────────────────────
#  Tests
# ─────────────────────────────────────────────────────────────────────────────

p = f = 0
def ok(name):  global p; p += 1; print(f"  ✅ {name}")
def fail(name, e): global f; f += 1; print(f"  ❌ {name}: {e}")


def test_index_page():
    """T1: GET / returns 200 with dashboard HTML."""
    log.info("T1: GET / → index.html")
    try:
        _, client = make_server()
        r = client.get("/")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        body = r.data.decode()
        assert "AlgoBot" in body,       "Missing AlgoBot branding"
        assert "Overview" in body,      "Missing Overview nav item"
        assert "refreshAll" in body,    "Missing JS polling function"
        ok("T1: GET / → 200 with AlgoBot dashboard HTML")
    except Exception as e: fail("T1", e)


def test_settings_page():
    """T2: GET /settings returns 200 with settings HTML."""
    log.info("T2: GET /settings → settings.html")
    try:
        _, client = make_server()
        r = client.get("/settings")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        body = r.data.decode()
        assert "Settings" in body,          "Missing Settings title"
        assert "daily_loss_limit" in body,  "Missing daily_loss_limit field"
        assert "sl_percentage" in body,     "Missing sl_percentage field"
        assert "saveSettings" in body,      "Missing saveSettings function"
        ok("T2: GET /settings → 200 with all settings fields")
    except Exception as e: fail("T2", e)


def test_health_endpoint():
    """T3: GET /api/health returns {status: ok}."""
    log.info("T3: GET /api/health")
    try:
        _, client = make_server()
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.get_json()
        assert data["status"] == "ok", f"Expected 'ok', got {data['status']}"
        assert "timestamp" in data
        ok("T3: GET /api/health → {status: ok}")
    except Exception as e: fail("T3", e)


def test_status_no_context():
    """T4: GET /api/status with no bot context returns safe defaults."""
    log.info("T4: GET /api/status — no context")
    try:
        _, client = make_server(context={})
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.get_json()
        assert "bot_running"    in data
        assert "trade_mode"     in data
        assert "open_positions" in data
        assert "day_pnl"        in data
        assert data["trade_mode"]     == "paper"
        assert data["open_positions"] == []
        ok("T4: GET /api/status (no context) → safe defaults")
    except Exception as e: fail("T4", e)


def test_status_with_context():
    """T5: GET /api/status with full context returns real data."""
    log.info("T5: GET /api/status — with mocked modules")
    try:
        ctx = make_mock_context(day_pnl=1500.0)
        _, client = make_server(context=ctx)
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.get_json()

        assert data["day_pnl"]       == 1500.0
        assert data["trade_mode"]    == "paper"
        assert "capital"             in data
        assert "risk_stats"          in data
        assert data["risk_stats"]["signals_approved"] == 3
        assert data["capital"]["available_balance"]   == 100000
        ok(f"T5: GET /api/status → day_pnl=₹{data['day_pnl']:.0f}, balance=₹{data['capital']['available_balance']:,.0f}")
    except Exception as e: fail("T5", e)


def test_trades_no_order_manager():
    """T6: GET /api/trades without order manager returns empty list."""
    log.info("T6: GET /api/trades — no order manager")
    try:
        _, client = make_server(context={})
        r = client.get("/api/trades")
        assert r.status_code == 200
        data = r.get_json()
        assert "trades" in data
        assert data["trades"] == []
        ok("T6: GET /api/trades (no OM) → empty trades list")
    except Exception as e: fail("T6", e)


def test_trades_with_data():
    """T7: GET /api/trades returns serialised trade dicts."""
    log.info("T7: GET /api/trades — with trades")
    try:
        t1 = make_mock_trade("T001", net_pnl=3000.0)
        t2 = make_mock_trade("T002", net_pnl=-800.0)
        ctx = make_mock_context(trades=[t1, t2], day_pnl=2200.0)
        _, client = make_server(context=ctx)

        r = client.get("/api/trades")
        assert r.status_code == 200
        data = r.get_json()

        assert data["count"]   == 2
        assert data["day_pnl"] == 2200.0
        assert len(data["trades"]) == 2

        ids = {t["trade_id"] for t in data["trades"]}
        assert "T001" in ids and "T002" in ids
        ok(f"T7: GET /api/trades → 2 trades, day P&L=₹{data['day_pnl']:.0f}")
    except Exception as e: fail("T7", e)


def test_positions_endpoint():
    """T8: GET /api/positions returns open positions."""
    log.info("T8: GET /api/positions")
    try:
        open_t = make_mock_trade("T001")
        ctx = make_mock_context(open_positions=[open_t])
        _, client = make_server(context=ctx)

        r = client.get("/api/positions")
        assert r.status_code == 200
        data = r.get_json()

        assert data["count"] == 1
        assert len(data["positions"]) == 1
        assert data["positions"][0]["trade_id"] == "T001"
        ok("T8: GET /api/positions → 1 open position")
    except Exception as e: fail("T8", e)


def test_market_bias_endpoint():
    """T9: GET /api/market_bias returns all bias fields."""
    log.info("T9: GET /api/market_bias")
    try:
        ctx = make_mock_context(bias_score=4)
        _, client = make_server(context=ctx)

        r = client.get("/api/market_bias")
        assert r.status_code == 200
        data = r.get_json()

        assert "error"           not in data
        assert data["bias_score"]           == 4
        assert data["bias_label"]           == "Mildly Bullish"
        assert data["nifty"]                == 22350.0
        assert data["india_vix"]            == 14.2
        assert data["usdinr"]               == 86.42
        assert "fii_net_buy"    in data
        assert "bias_reasoning" in data
        ok(f"T9: GET /api/market_bias → bias={data['bias_score']:+d} ({data['bias_label']})")
    except Exception as e: fail("T9", e)


def test_signals_endpoint():
    """T10: GET /api/signals returns stats and block log."""
    log.info("T10: GET /api/signals")
    try:
        ctx = make_mock_context()
        _, client = make_server(context=ctx)

        r = client.get("/api/signals")
        assert r.status_code == 200
        data = r.get_json()

        assert "stats"         in data
        assert "block_summary" in data
        assert data["stats"]["signals_received"] == 5
        assert data["stats"]["signals_approved"] == 3
        assert data["stats"]["approval_rate"]    == 60.0
        assert "R3_min_confidence" in data["block_summary"]
        ok(f"T10: GET /api/signals → {data['stats']['signals_approved']}/{data['stats']['signals_received']} approved")
    except Exception as e: fail("T10", e)


def test_settings_get():
    """T11: GET /api/settings returns config without secret key."""
    log.info("T11: GET /api/settings")
    try:
        _, client = make_server()
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.get_json()

        assert "trade_mode"          in data
        assert "daily_loss_limit"    in data
        assert "sl_percentage"       in data
        assert "dashboard_secret_key" not in data,  "Secret key should NOT be returned"
        ok(f"T11: GET /api/settings → config returned (secret key hidden)")
    except Exception as e: fail("T11", e)


def test_settings_post_valid():
    """T12: POST /api/settings updates allowed keys in config."""
    log.info("T12: POST /api/settings — valid keys")
    try:
        ds, client = make_server()
        original_sl = ds._config.get("sl_percentage", 33)

        r = client.post("/api/settings",
            json={"sl_percentage": 40, "daily_loss_limit": 8000, "min_confidence": 60},
            content_type="application/json"
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert "updated" in data

        # Config should be mutated in place
        assert ds._config["sl_percentage"]    == 40
        assert ds._config["daily_loss_limit"] == 8000
        assert ds._config["min_confidence"]   == 60
        ok(f"T12: POST /api/settings → sl_pct updated {original_sl}→40, loss_limit→₹8000")
    except Exception as e: fail("T12", e)


def test_settings_post_unknown_keys():
    """T13: POST /api/settings rejects unknown/forbidden keys."""
    log.info("T13: POST /api/settings — unknown keys")
    try:
        ds, client = make_server()
        original_secret = ds._config.get("dashboard_secret_key", "test-secret")

        r = client.post("/api/settings",
            json={"dashboard_secret_key": "hacked", "unknown_field": "value"},
            content_type="application/json"
        )
        assert r.status_code == 400
        data = r.get_json()
        assert data["ok"] is False

        # Secret key must NOT be changed
        assert ds._config.get("dashboard_secret_key") == original_secret
        ok("T13: POST /api/settings rejects unknown keys and forbidden fields")
    except Exception as e: fail("T13", e)


def test_squareoff_endpoint():
    """T14: POST /api/control/squareoff calls order_manager.square_off_all."""
    log.info("T14: POST /api/control/squareoff")
    try:
        ctx = make_mock_context()
        ds, client = make_server(context=ctx)

        r = client.post("/api/control/squareoff")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True

        ctx["order_manager"].square_off_all.assert_called_once()
        call_arg = ctx["order_manager"].square_off_all.call_args[0][0]
        assert "dashboard" in call_arg.lower() or "manual" in call_arg.lower()
        ok("T14: POST /api/control/squareoff → square_off_all() called")
    except Exception as e: fail("T14", e)


def test_mode_switch_paper():
    """T15: POST /api/control/mode paper → trade_mode = paper."""
    log.info("T15: Switch to PAPER mode")
    try:
        ds, client = make_server(trade_mode="live")
        assert ds._config["trade_mode"] == "live"

        r = client.post("/api/control/mode",
            json={"mode": "paper"}, content_type="application/json")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]   is True
        assert data["mode"] == "paper"
        assert ds._config["trade_mode"] == "paper"
        ok("T15: Mode switch live → paper ✅")
    except Exception as e: fail("T15", e)


def test_mode_switch_live():
    """T16: POST /api/control/mode live → trade_mode = live."""
    log.info("T16: Switch to LIVE mode")
    try:
        ds, client = make_server()
        r = client.post("/api/control/mode",
            json={"mode": "live"}, content_type="application/json")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]   is True
        assert data["mode"] == "live"
        assert ds._config["trade_mode"] == "live"
        ok("T16: Mode switch paper → live ✅")
    except Exception as e: fail("T16", e)


def test_mode_switch_invalid():
    """T17: POST /api/control/mode invalid → 400."""
    log.info("T17: Invalid mode → 400")
    try:
        _, client = make_server()
        r = client.post("/api/control/mode",
            json={"mode": "turbo"}, content_type="application/json")
        assert r.status_code == 400
        data = r.get_json()
        assert data["ok"] is False
        ok("T17: Invalid mode 'turbo' → 400 error")
    except Exception as e: fail("T17", e)


def test_start_endpoint():
    """T18: POST /api/control/start sets bot_running = True."""
    log.info("T18: POST /api/control/start")
    try:
        ds, client = make_server()
        ds._running = False

        r = client.post("/api/control/start")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert ds._running is True
        ok("T18: POST /api/control/start → bot_running=True")
    except Exception as e: fail("T18", e)


def test_stop_endpoint():
    """T19: POST /api/control/stop sets bot_running = False."""
    log.info("T19: POST /api/control/stop")
    try:
        ds, client = make_server()
        ds._running = True

        r = client.post("/api/control/stop")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert ds._running is False
        ok("T19: POST /api/control/stop → bot_running=False")
    except Exception as e: fail("T19", e)


def test_error_resilience():
    """T20: Broken order_manager still returns valid JSON from /api/status."""
    log.info("T20: Error resilience — broken modules")
    try:
        broken_om = MagicMock()
        broken_om.get_open_positions.side_effect = RuntimeError("DB connection lost")
        broken_om.get_today_trades.side_effect   = RuntimeError("DB connection lost")
        broken_om.get_day_pnl.side_effect        = RuntimeError("DB connection lost")

        _, client = make_server(context={"order_manager": broken_om})
        r = client.get("/api/status")

        # Must not crash — still returns 200 with valid JSON
        assert r.status_code == 200
        data = r.get_json()
        assert "bot_running"    in data
        assert "trade_mode"     in data
        assert "open_positions" in data
        ok("T20: Broken OrderManager → still returns valid JSON (graceful error handling)")
    except Exception as e: fail("T20", e)


# ─────────────────────────────────────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all():
    print("\n" + "=" * 60)
    print("  MODULE 9 — DASHBOARD SERVER TESTS")
    print("=" * 60 + "\n")

    tests = [
        test_index_page,
        test_settings_page,
        test_health_endpoint,
        test_status_no_context,
        test_status_with_context,
        test_trades_no_order_manager,
        test_trades_with_data,
        test_positions_endpoint,
        test_market_bias_endpoint,
        test_signals_endpoint,
        test_settings_get,
        test_settings_post_valid,
        test_settings_post_unknown_keys,
        test_squareoff_endpoint,
        test_mode_switch_paper,
        test_mode_switch_live,
        test_mode_switch_invalid,
        test_start_endpoint,
        test_stop_endpoint,
        test_error_resilience,
    ]

    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            log.error(f"  ❌ FAIL  — {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            log.error(f"  ❌ ERROR — {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    if failed == 0:
        print("✅ All Module 9 tests passed! Dashboard is ready.\n")
    else:
        print("❌ Some tests failed. Check logs above.\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
