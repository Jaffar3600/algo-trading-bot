"""
tests/test_risk_manager.py — Test Module 7: Risk Manager
══════════════════════════════════════════════════════════
Tests all 8 pre-trade risk checks and portfolio-level logic.

T1  — Approved signal passes all 8 checks
T2  — R1: Daily loss limit blocks trading
T3  — R2: Max trades per day blocks further entries
T4  — R3: Low confidence signal blocked
T5  — R4: PUT signal blocked by strongly bullish bias
T6  — R4: CALL signal blocked by strongly bearish bias
T7  — R4: Neutral bias allows both directions
T8  — R5: High-impact event blocks trading
T9  — R5: No event → check passes
T10 — R6: Too few conditions met → blocked
T11 — R7: Duplicate position in same symbol blocked
T12 — R7: Max concurrent positions reached → blocked
T13 — R8: Signal before trading hours blocked
T14 — R8: Signal during lunch zone blocked
T15 — R8: Signal after trading end blocked
T16 — Approved signal auto-forwarded to Order Manager
T17 — on_approved callback fires for approved signals
T18 — Block log records rejected signals
T19 — get_stats() returns correct approval rate
T20 — Daily reset clears all stats

Run:
    python tests/test_risk_manager.py
"""

import sys, os, types
import datetime as _dt
from zoneinfo import ZoneInfo

# ── pandas before pytz mock ───────────────────────────────────────────────────
import pandas as _pd  # noqa — must be imported before pytz mock

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

from unittest.mock import MagicMock, patch
from modules.risk_manager import RiskManager, RiskDecision
from strategies.base_strategy import Signal
from modules.logger import get_logger
import config as app_config

log = get_logger("test_risk_manager")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_config(**overrides):
    cfg = dict(app_config.DEFAULT_CONFIG)
    cfg.update({
        "trade_mode":             "paper",
        "min_confidence":         50,
        "min_signal_conditions":  3,
        "max_trades_per_day":     2,
        "daily_loss_limit":       5000,
        "bias_block_threshold":   4,
        "max_concurrent_positions": 2,
        "use_market_bias_filter": True,
        "trading_start":          "09:30",
        "trading_end":            "15:00",
        "avoid_lunch":            True,
        "lunch_start":            "11:30",
        "lunch_end":              "13:00",
    })
    cfg.update(overrides)
    return cfg


def make_signal(
    action="CALL",
    symbol="NIFTY",
    strike="22200CE",
    confidence=75,
    conditions_met=4,
    bias_score=2,
):
    return Signal(
        action=action,
        symbol=symbol,
        suggested_strike=strike,
        confidence=confidence,
        conditions_met=conditions_met,
        conditions_total=5,
        bias_score=bias_score,
        reason=f"{conditions_met}/5 conditions",
    )


def make_rm(
    day_pnl=0.0,
    trades_taken=0,
    open_positions=None,
    bias_score=2,
    has_events=False,
    **config_overrides,
):
    """Build a RiskManager with mocked dependencies."""
    cfg = make_config(**config_overrides)

    # Mock CapitalManager
    cm = MagicMock()
    cm.get_capital_summary.return_value = {
        "can_trade":   True,
        "day_pnl":     day_pnl,
        "trades_taken": trades_taken,
        "reason":      "OK",
    }

    # Mock MarketIntel
    intel = MagicMock()
    snap  = MagicMock()
    snap.high_impact_events = (
        ["High VIX: 22.5 (above 20)", "Crude oil surge: +4.2%"] if has_events else []
    )
    intel.get_snapshot.return_value   = snap
    intel.get_bias_score.return_value = bias_score

    # Mock OrderManager
    om = MagicMock()
    om.get_day_pnl.return_value       = day_pnl
    om.get_today_trades.return_value  = [MagicMock()] * trades_taken
    om.get_open_positions.return_value= open_positions or []
    om.receive_signal.return_value    = MagicMock(trade_id="T001")

    rm = RiskManager(cfg, capital_manager=cm, market_intel=intel)
    rm.attach_order_manager(om)

    return rm, om, intel


def with_time(rm, hour: int, minute: int):
    """Context manager patch that sets the clock for risk manager checks."""
    import modules.risk_manager as rm_module
    orig = rm_module.datetime

    IST = ZoneInfo("Asia/Kolkata")

    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, hour, minute)
            return d.replace(tzinfo=IST) if tz else d
    
    rm_module.datetime = FakeDT
    return orig, rm_module


# ─────────────────────────────────────────────────────────────────────────────
#  Tests
# ─────────────────────────────────────────────────────────────────────────────

p = f = 0
def ok(name):
    global p; p += 1
    print(f"  ✅ {name}")

def fail(name, e):
    global f; f += 1
    print(f"  ❌ {name}: {e}")


def test_all_checks_pass():
    """T1: Clean signal with good conditions passes all 8 checks."""
    log.info("T1: All checks pass — clean signal")
    rm, om, _ = make_rm(day_pnl=500, trades_taken=0, bias_score=3)
    sig = make_signal(confidence=70, conditions_met=4, bias_score=3)

    import modules.risk_manager as rmmod
    orig = rmmod.datetime
    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, 10, 30)
            return d.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if tz else d
    rmmod.datetime = FakeDT

    decision = rm.evaluate_signal(sig)
    rmmod.datetime = orig

    assert decision.approved, f"Should be approved. Blocked by: {decision.blocked_by} — {decision.block_reason}"
    assert len(decision.checks_run) == 8
    assert all(c["passed"] for c in decision.checks_run)
    om.receive_signal.assert_called_once_with(sig)
    ok("T1: All 8 checks passed — signal approved and forwarded")


def test_R1_daily_loss_limit():
    """T2: R1 blocks when day P&L ≤ -daily_loss_limit."""
    log.info("T2: R1 — daily loss limit")
    rm, om, _ = make_rm(day_pnl=-6000, daily_loss_limit=5000)
    sig = make_signal()

    decision = rm.evaluate_signal(sig)

    assert not decision.approved
    assert decision.blocked_by == "R1_daily_loss_limit"
    assert "Day P&L" in decision.block_reason
    om.receive_signal.assert_not_called()
    ok(f"T2: R1 blocked — day P&L ₹-6000 ≤ -₹5000 limit")


def test_R2_max_trades_per_day():
    """T3: R2 blocks when trades_taken >= max_trades_per_day."""
    log.info("T3: R2 — max trades per day")
    rm, om, _ = make_rm(trades_taken=2, max_trades_per_day=2)
    sig = make_signal()

    decision = rm.evaluate_signal(sig)

    assert not decision.approved
    assert decision.blocked_by == "R2_max_trades_per_day"
    assert "2/2" in decision.block_reason
    om.receive_signal.assert_not_called()
    ok("T3: R2 blocked — 2/2 trades taken today")


def test_R3_low_confidence():
    """T4: R3 blocks when signal confidence < min_confidence."""
    log.info("T4: R3 — low confidence")
    rm, om, _ = make_rm(min_confidence=60)
    sig = make_signal(confidence=45)   # below 60% threshold

    decision = rm.evaluate_signal(sig)

    assert not decision.approved
    assert decision.blocked_by == "R3_min_confidence"
    assert "45%" in decision.block_reason
    ok("T4: R3 blocked — confidence 45% < min 60%")


def test_R4_put_blocked_by_bullish_bias():
    """T5: R4 blocks PUT signal when bias is strongly bullish (≥ +4)."""
    log.info("T5: R4 — PUT blocked by strong bullish bias")
    rm, _, _ = make_rm(bias_score=5, bias_block_threshold=4)
    sig = make_signal(action="PUT", bias_score=5)

    decision = rm.evaluate_signal(sig)

    assert not decision.approved
    assert decision.blocked_by == "R4_bias_alignment"
    assert "PUT" in decision.block_reason
    assert "bullish" in decision.block_reason.lower()
    ok("T5: R4 blocked — PUT signal opposed by bias +5")


def test_R4_call_blocked_by_bearish_bias():
    """T6: R4 blocks CALL signal when bias is strongly bearish (≤ -4)."""
    log.info("T6: R4 — CALL blocked by strong bearish bias")
    rm, _, _ = make_rm(bias_score=-5, bias_block_threshold=4)
    sig = make_signal(action="CALL", bias_score=-5)

    decision = rm.evaluate_signal(sig)

    assert not decision.approved
    assert decision.blocked_by == "R4_bias_alignment"
    assert "CALL" in decision.block_reason
    assert "bearish" in decision.block_reason.lower()
    ok("T6: R4 blocked — CALL signal opposed by bias -5")


def test_R4_neutral_bias_allows_both():
    """T7: R4 passes in neutral bias (-1 to +1) for both CALL and PUT."""
    log.info("T7: R4 — neutral bias allows both directions")

    import modules.risk_manager as rmmod
    orig = rmmod.datetime
    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, 10, 30)
            return d.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if tz else d
    rmmod.datetime = FakeDT

    for action in ("CALL", "PUT"):
        rm, _, _ = make_rm(bias_score=0)
        sig = make_signal(action=action, bias_score=0, confidence=70, conditions_met=4)
        passed, name, detail = rm._check_R4_bias_alignment(sig)
        assert passed, f"{action} should pass neutral bias. Got: {detail}"

    rmmod.datetime = orig
    ok("T7: R4 passes — neutral bias allows CALL and PUT")


def test_R5_high_impact_event_blocks():
    """T8: R5 blocks when MarketIntel reports a high-impact event."""
    log.info("T8: R5 — high-impact event blocks trading")
    rm, om, _ = make_rm(has_events=True)
    sig = make_signal()

    decision = rm.evaluate_signal(sig)

    assert not decision.approved
    assert decision.blocked_by == "R5_high_impact_events"
    assert "High-impact event" in decision.block_reason
    om.receive_signal.assert_not_called()
    ok(f"T8: R5 blocked — event: {decision.block_reason[:60]}")


def test_R5_no_event_passes():
    """T9: R5 passes when no high-impact events."""
    log.info("T9: R5 — no events → check passes")
    rm, _, _ = make_rm(has_events=False)
    sig = make_signal()
    passed, name, detail = rm._check_R5_high_impact_events(sig)
    assert passed, f"Should pass with no events. Got: {detail}"
    ok("T9: R5 passes — no high-impact events")


def test_R6_too_few_conditions():
    """T10: R6 blocks when conditions_met < min_signal_conditions."""
    log.info("T10: R6 — too few conditions")
    rm, om, _ = make_rm(min_signal_conditions=4)
    sig = make_signal(conditions_met=3)   # 3 < 4 minimum

    decision = rm.evaluate_signal(sig)

    assert not decision.approved
    assert decision.blocked_by == "R6_min_conditions"
    assert "3/5" in decision.block_reason
    ok("T10: R6 blocked — 3/5 conditions met (need 4)")


def test_R7_duplicate_position():
    """T11: R7 blocks if same symbol already has an open position."""
    log.info("T11: R7 — duplicate position blocked")

    # Create a fake open trade for NIFTY
    existing_trade = MagicMock()
    existing_trade.symbol = "NIFTY"
    existing_trade.action = "CALL"
    existing_trade.trade_id = "T001"

    rm, om, _ = make_rm(open_positions=[existing_trade])
    sig = make_signal(symbol="NIFTY")   # same symbol

    decision = rm.evaluate_signal(sig)

    assert not decision.approved
    assert decision.blocked_by == "R7_duplicate_position"
    assert "NIFTY" in decision.block_reason
    ok("T11: R7 blocked — NIFTY position already open")


def test_R7_max_concurrent_positions():
    """T12: R7 blocks when max concurrent open positions reached."""
    log.info("T12: R7 — max concurrent positions")

    # 2 open positions in different symbols (max=2)
    t1, t2 = MagicMock(), MagicMock()
    t1.symbol, t2.symbol = "NIFTY", "BANKNIFTY"
    t1.action, t2.action = "CALL", "PUT"
    t1.trade_id, t2.trade_id = "T001", "T002"

    rm, om, _ = make_rm(open_positions=[t1, t2], max_concurrent_positions=2)
    sig = make_signal(symbol="FINNIFTY")   # different symbol, but slots full

    decision = rm.evaluate_signal(sig)

    # R7 might catch either duplicate or max positions — both are R7
    # For FINNIFTY with no existing position but 2 open, max concurrent triggers
    assert not decision.approved
    assert decision.blocked_by == "R7_duplicate_position"
    assert "Max concurrent" in decision.block_reason
    ok("T12: R7 blocked — max 2 concurrent positions already open")


def test_R8_before_trading_hours():
    """T13: R8 blocks signals before trading_start (09:30)."""
    log.info("T13: R8 — before trading hours")
    rm, _, _ = make_rm()

    import modules.risk_manager as rmmod
    orig = rmmod.datetime
    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, 9, 15)   # 09:15 — before 09:30 start
            return d.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if tz else d
    rmmod.datetime = FakeDT

    passed, name, detail = rm._check_R8_trading_time(make_signal())
    rmmod.datetime = orig

    assert not passed
    assert "Before trading start" in detail
    ok(f"T13: R8 blocked — 09:15 before 09:30 start")


def test_R8_lunch_zone():
    """T14: R8 blocks signals during lunch zone (11:30–13:00)."""
    log.info("T14: R8 — lunch zone")
    rm, _, _ = make_rm()

    import modules.risk_manager as rmmod
    orig = rmmod.datetime
    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, 12, 0)   # 12:00 — in lunch zone
            return d.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if tz else d
    rmmod.datetime = FakeDT

    passed, name, detail = rm._check_R8_trading_time(make_signal())
    rmmod.datetime = orig

    assert not passed
    assert "Lunch zone" in detail
    ok("T14: R8 blocked — 12:00 in lunch zone (11:30–13:00)")


def test_R8_after_trading_end():
    """T15: R8 blocks signals after trading_end (15:00)."""
    log.info("T15: R8 — after trading end")
    rm, _, _ = make_rm()

    import modules.risk_manager as rmmod
    orig = rmmod.datetime
    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, 15, 5)   # 15:05 — after 15:00 end
            return d.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if tz else d
    rmmod.datetime = FakeDT

    passed, name, detail = rm._check_R8_trading_time(make_signal())
    rmmod.datetime = orig

    assert not passed
    assert "After trading end" in detail
    ok("T15: R8 blocked — 15:05 after 15:00 trading end")


def test_approved_signal_forwarded_to_order_manager():
    """T16: Approved signal is auto-forwarded to Order Manager."""
    log.info("T16: Approved signal forwarded to Order Manager")
    rm, om, _ = make_rm(bias_score=3)
    sig = make_signal(confidence=70, conditions_met=4, bias_score=3)

    import modules.risk_manager as rmmod
    orig = rmmod.datetime
    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, 10, 30)
            return d.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if tz else d
    rmmod.datetime = FakeDT

    decision = rm.evaluate_signal(sig)
    rmmod.datetime = orig

    assert decision.approved
    om.receive_signal.assert_called_once_with(sig)
    ok("T16: Approved signal forwarded to Order Manager ✅")


def test_on_approved_callback():
    """T17: on_approved callback fires when signal is approved."""
    log.info("T17: on_approved callback")
    rm, _, _ = make_rm(bias_score=2)
    sig = make_signal(confidence=70, conditions_met=4, bias_score=2)

    approved_signals = []
    rm.set_on_approved(lambda s, d: approved_signals.append((s, d)))

    import modules.risk_manager as rmmod
    orig = rmmod.datetime
    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, 10, 30)
            return d.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if tz else d
    rmmod.datetime = FakeDT

    rm.evaluate_signal(sig)
    rmmod.datetime = orig

    assert len(approved_signals) == 1
    assert approved_signals[0][1].approved is True
    ok("T17: on_approved callback fired for approved signal")


def test_block_log_records_rejections():
    """T18: Rejected signals are recorded in the block log."""
    log.info("T18: Block log records rejections")
    rm, _, _ = make_rm(day_pnl=-9000, daily_loss_limit=5000)

    # Send 3 signals — all should be blocked by R1
    for _ in range(3):
        rm.evaluate_signal(make_signal())

    assert len(rm._block_log) == 3
    for decision in rm._block_log:
        assert decision.blocked_by == "R1_daily_loss_limit"

    block_summary = rm.get_block_summary()
    assert "R1_daily_loss_limit" in block_summary
    assert block_summary["R1_daily_loss_limit"] == 3
    ok("T18: Block log correctly records 3 R1 rejections")


def test_get_stats():
    """T19: get_stats() returns correct approval rate and counts."""
    log.info("T19: get_stats() accuracy")
    rm, _, _ = make_rm(bias_score=2)

    import modules.risk_manager as rmmod
    orig = rmmod.datetime
    class FakeDT:
        @staticmethod
        def now(tz=None):
            d = _dt.datetime(2026, 2, 25, 10, 30)
            return d.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if tz else d
    rmmod.datetime = FakeDT

    # 2 approved signals (good conditions)
    good = make_signal(confidence=70, conditions_met=4, bias_score=2)
    rm.evaluate_signal(good)
    rm.evaluate_signal(good)

    rmmod.datetime = orig

    # 1 blocked signal (loss limit)
    rm2, _, _ = make_rm(day_pnl=-9000)
    rm2.evaluate_signal(make_signal())

    stats_good  = rm.get_stats()
    stats_block = rm2.get_stats()

    assert stats_good["signals_received"]  == 2
    assert stats_good["signals_approved"]  == 2
    assert stats_good["signals_blocked"]   == 0
    assert stats_good["approval_rate"]     == 100.0

    assert stats_block["signals_received"] == 1
    assert stats_block["signals_blocked"]  == 1
    assert stats_block["approval_rate"]    == 0.0

    ok(f"T19: Stats — good: {stats_good['approval_rate']}% approved | block: {stats_block['approval_rate']}% approved")


def test_daily_reset():
    """T20: reset_daily() clears all stats."""
    log.info("T20: Daily reset")
    rm, _, _ = make_rm(day_pnl=-9000, daily_loss_limit=5000)

    rm.evaluate_signal(make_signal())
    rm.evaluate_signal(make_signal())

    assert rm._signals_received == 2
    assert rm._signals_blocked  == 2

    rm.reset_daily()

    assert rm._signals_received == 0
    assert rm._signals_approved == 0
    assert rm._signals_blocked  == 0
    assert len(rm._block_log)   == 0

    stats = rm.get_stats()
    assert stats["signals_received"] == 0
    ok("T20: Daily reset cleared all stats ✅")


# ─────────────────────────────────────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all():
    print("\n" + "=" * 60)
    print("  MODULE 7 — RISK MANAGER TESTS")
    print("=" * 60 + "\n")

    tests = [
        test_all_checks_pass,
        test_R1_daily_loss_limit,
        test_R2_max_trades_per_day,
        test_R3_low_confidence,
        test_R4_put_blocked_by_bullish_bias,
        test_R4_call_blocked_by_bearish_bias,
        test_R4_neutral_bias_allows_both,
        test_R5_high_impact_event_blocks,
        test_R5_no_event_passes,
        test_R6_too_few_conditions,
        test_R7_duplicate_position,
        test_R7_max_concurrent_positions,
        test_R8_before_trading_hours,
        test_R8_lunch_zone,
        test_R8_after_trading_end,
        test_approved_signal_forwarded_to_order_manager,
        test_on_approved_callback,
        test_block_log_records_rejections,
        test_get_stats,
        test_daily_reset,
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
        print("✅ All Module 7 tests passed! Risk Manager is ready.\n")
    else:
        print("❌ Some tests failed. Check logs above.\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
