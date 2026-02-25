"""
tests/test_order_manager.py — Test Module 6: Order Manager
════════════════════════════════════════════════════════════
Tests the full trade lifecycle in PAPER mode (no live orders needed).

T1  — Trade dataclass and to_dict serialisation
T2  — Paper entry: signal → trade entered correctly
T3  — SL price: 33% below entry price
T4  — Target price: 2:1 risk-reward ratio
T5  — SL hit → trade exits with SL_HIT state and negative P&L
T6  — Target hit → trade exits with TARGET_HIT and positive P&L
T7  — Trailing SL moves to breakeven at 45% profit trigger
T8  — EOD square_off_all() force-exits all open positions
T9  — Duplicate position in same symbol rejected
T10 — Capital check blocks trade when daily limit hit
T11 — P&L: gross = (exit-entry)×qty, net = gross - charges
T12 — Option symbol built correctly (SYMBOL + EXPIRY + STRIKE)
T13 — Trade callbacks (on_enter / on_exit) fire correctly
T14 — Daily reset clears all state and restarts counter

Run:
    python tests/test_order_manager.py
"""

import sys, os, types
import datetime as _dt

# ── CRITICAL: import pandas BEFORE mocking pytz ──────────────────────────────
# pandas imports pytz internally during initialisation. If we mock pytz first,
# pandas breaks. Import pandas first, then replace pytz with our mock.
import pandas as _pd  # noqa — ensures pandas is initialised before pytz mock

# ── pytz mock using zoneinfo (built-in Python 3.9+) ──────────────────────────
from zoneinfo import ZoneInfo

class _TZ(_dt.tzinfo):
    """Drop-in pytz timezone replacement using Python's built-in zoneinfo."""
    def __init__(self, name: str = "Asia/Kolkata"):
        self._zi = ZoneInfo(name)
    def utcoffset(self, dt): return self._zi.utcoffset(dt)
    def tzname(self, dt):    return self._zi.tzname(dt)
    def dst(self, dt):       return self._zi.dst(dt)
    def localize(self, dt):  return dt.replace(tzinfo=self)

_pytz_mock = types.ModuleType("pytz")
_pytz_mock.timezone = lambda name: _TZ(name)
sys.modules["pytz"] = _pytz_mock

# ── Stub heavy optional dependencies ─────────────────────────────────────────
for _m in ["kiteconnect", "requests", "bs4"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))
sys.modules["kiteconnect"].KiteTicker = object

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch
from modules.order_manager import OrderManager, Trade, TradeState
from strategies.base_strategy import Signal
from modules.logger import get_logger
import config as app_config

log = get_logger("test_order_manager")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_config(**kw):
    cfg = dict(app_config.DEFAULT_CONFIG)
    cfg.update({
        "trade_mode":           "paper",
        "sl_percentage":        33,
        "risk_reward_ratio":    2.0,
        "trail_sl_trigger_pct": 45,
        "max_trades_per_day":   2,
        "daily_loss_limit":     5000,
    })
    cfg.update(kw)
    return cfg


def make_cm(can_trade=True, reason="OK", lot_qty=1):
    cm = MagicMock()
    cm.get_capital_summary.return_value = {
        "can_trade":       can_trade,
        "reason":          reason,
        "per_trade_limit": 15000,
        "available_balance": 100000,
        "usable_capital":  80000,
        "trades_taken":    0,
        "day_pnl":         0,
    }
    cm.get_lot_quantity.return_value = lot_qty
    cm.record_trade_entry = MagicMock()
    cm.record_trade_exit  = MagicMock()
    return cm


def make_om(can_trade=True, lot_qty=1, **kw):
    cfg = make_config(**kw)
    cm  = make_cm(can_trade=can_trade, lot_qty=lot_qty)
    om  = OrderManager(MagicMock(), cm, cfg)
    return om, cm


def sig(action="CALL", symbol="NIFTY", strike="22200CE", cond=4):
    return Signal(
        action=action, symbol=symbol, suggested_strike=strike,
        confidence=75, conditions_met=cond, bias_score=3,
        reason=f"{cond}/5 conditions met",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_trade_dataclass():
    """T1: Trade dataclass initialises correctly and to_dict works."""
    log.info("T1: Trade dataclass")
    t = Trade(
        trade_id="T001", mode="paper", symbol="NIFTY",
        option_symbol="NIFTY25FEB22200CE", action="CALL",
        state=TradeState.PENDING, lots=1, lot_size=75, quantity=75,
        entry_price=120., sl_price=80., target_price=200.,
    )
    d = t.to_dict()
    assert d["state"]    == "pending"
    assert d["quantity"] == 75
    assert d["option_symbol"] == "NIFTY25FEB22200CE"
    log.info("  ✅ PASS")


def test_paper_entry():
    """T2: Signal → paper trade entered with correct fields."""
    log.info("T2: Paper entry from signal")
    om, cm = make_om()
    with patch.object(om, "_simulate_ltp", return_value=120.0):
        trade = om.receive_signal(sig())
    assert trade is not None
    assert trade.state       == TradeState.ENTERED
    assert trade.entry_price == 120.0
    assert "CE"              in trade.option_symbol
    assert trade.entry_order_id.startswith("PAPER-")
    assert len(om.get_open_positions()) == 1
    cm.record_trade_entry.assert_called_once()
    log.info(f"  ✅ PASS — {trade.option_symbol} @ ₹{trade.entry_price}")


def test_sl_price():
    """T3: SL = entry × (1 - 33%) = entry × 0.67."""
    log.info("T3: SL price calculation")
    om, _ = make_om()
    with patch.object(om, "_simulate_ltp", return_value=150.0):
        trade = om.receive_signal(sig())
    expected = round(150.0 * 0.67, 1)
    assert trade.sl_price == expected, f"Got {trade.sl_price}, expected {expected}"
    log.info(f"  ✅ PASS — SL=₹{trade.sl_price} (33% below entry ₹150)")


def test_target_price():
    """T4: Target = entry + (risk × 2.0) → 2:1 R:R."""
    log.info("T4: Target price calculation")
    om, _ = make_om()
    with patch.object(om, "_simulate_ltp", return_value=150.0):
        trade = om.receive_signal(sig())
    risk     = 150.0 - trade.sl_price
    expected = round(150.0 + risk * 2.0, 1)
    assert trade.target_price == expected, f"Got {trade.target_price}, expected {expected}"
    log.info(f"  ✅ PASS — Target=₹{trade.target_price} (R:R 1:2)")


def test_sl_hit():
    """T5: LTP ≤ SL → trade exits with SL_HIT state."""
    log.info("T5: SL hit → trade exits")
    om, cm = make_om()
    with patch.object(om, "_simulate_ltp", return_value=150.0):
        trade = om.receive_signal(sig())
    with patch.object(om, "_get_ltp", return_value=trade.sl_price - 1):
        om.monitor_positions()
    assert trade.state   == TradeState.SL_HIT
    assert trade.net_pnl  < 0
    assert len(om.get_open_positions()) == 0
    cm.record_trade_exit.assert_called_once()
    log.info(f"  ✅ PASS — SL hit | Net P&L: ₹{trade.net_pnl:+.0f}")


def test_target_hit():
    """T6: LTP ≥ Target → trade exits with TARGET_HIT state."""
    log.info("T6: Target hit → trade exits")
    om, cm = make_om()
    with patch.object(om, "_simulate_ltp", return_value=150.0):
        trade = om.receive_signal(sig())
    with patch.object(om, "_get_ltp", return_value=trade.target_price + 5):
        om.monitor_positions()
    assert trade.state   == TradeState.TARGET_HIT
    assert trade.net_pnl  > 0
    assert len(om.get_open_positions()) == 0
    log.info(f"  ✅ PASS — Target hit | Net P&L: ₹{trade.net_pnl:+.0f}")


def test_trailing_sl_to_breakeven():
    """T7: At 45%+ profit, SL moves up to entry price (breakeven)."""
    log.info("T7: Trailing SL to breakeven")
    om, _ = make_om()
    with patch.object(om, "_simulate_ltp", return_value=100.0):
        trade = om.receive_signal(sig())
    entry = trade.entry_price  # 100.0
    old_sl = trade.sl_price
    # LTP at 46% profit (just above 45% trigger)
    trigger_ltp = round(entry * 1.46, 1)
    with patch.object(om, "_get_ltp", return_value=trigger_ltp):
        om.monitor_positions()
    assert trade.sl_price == entry, f"Expected SL={entry}, got {trade.sl_price}"
    assert trade.state    == TradeState.ENTERED  # still open
    log.info(f"  ✅ PASS — SL moved ₹{old_sl} → ₹{trade.sl_price} at LTP=₹{trigger_ltp}")


def test_square_off_all():
    """T8: EOD square_off_all() force-closes all open positions."""
    log.info("T8: EOD square-off")
    om, _ = make_om()
    with patch.object(om, "_simulate_ltp", return_value=120.0):
        t1 = om.receive_signal(sig("CALL", "NIFTY",     "22200CE"))
        t2 = om.receive_signal(sig("PUT",  "BANKNIFTY", "48000PE"))
    assert len(om.get_open_positions()) == 2
    with patch.object(om, "_get_ltp", return_value=125.0):
        om.square_off_all("EOD square-off")
    assert len(om.get_open_positions()) == 0
    assert t1.state != TradeState.ENTERED
    assert t2.state != TradeState.ENTERED
    log.info(f"  ✅ PASS — 2 positions closed | T1=₹{t1.net_pnl:+.0f} T2=₹{t2.net_pnl:+.0f}")


def test_duplicate_rejected():
    """T9: Second signal in same underlying rejected if position already open."""
    log.info("T9: Duplicate position rejected")
    om, _ = make_om()
    s = sig()
    with patch.object(om, "_simulate_ltp", return_value=120.0):
        t1 = om.receive_signal(s)
        t2 = om.receive_signal(s)
    assert t1 is not None
    assert t2 is None
    assert len(om.get_open_positions()) == 1
    log.info("  ✅ PASS — Duplicate NIFTY signal correctly rejected")


def test_capital_check_blocks():
    """T10: When can_trade=False, trade is rejected."""
    log.info("T10: Capital check blocks trade")
    cm = make_cm(can_trade=False, reason="Daily loss limit hit")
    om = OrderManager(MagicMock(), cm, make_config())
    with patch.object(om, "_simulate_ltp", return_value=120.0):
        trade = om.receive_signal(sig())
    assert trade is None
    log.info("  ✅ PASS — Trade blocked by capital check")


def test_pnl_calculation():
    """T11: gross_pnl = (exit-entry)×qty; net_pnl = gross - charges."""
    log.info("T11: P&L calculation")
    om, _ = make_om(lot_qty=1)
    with patch.object(om, "_simulate_ltp", return_value=100.0):
        trade = om.receive_signal(sig())
    qty = trade.quantity                # 1 lot × 75 = 75
    om._exit_trade(trade, 150.0, "Target hit")
    expected_gross = (150.0 - 100.0) * qty   # ₹3750
    assert trade.gross_pnl == expected_gross,  f"Gross: {trade.gross_pnl} ≠ {expected_gross}"
    assert trade.charges    > 0,               "Charges must be positive"
    assert trade.net_pnl    == round(trade.gross_pnl - trade.charges, 2)
    log.info(f"  ✅ PASS — Gross=₹{trade.gross_pnl} Charges=₹{trade.charges:.2f} Net=₹{trade.net_pnl}")


def test_option_symbol_built():
    """T12: Option symbol = SYMBOL + EXPIRY_STR + STRIKE, correct lot size."""
    log.info("T12: Option symbol construction")
    om, _ = make_om()
    sym, lot = om._resolve_option_instrument("NIFTY", "22200CE")
    assert "NIFTY"   in sym and "22200CE" in sym and lot == 75
    sym2, lot2 = om._resolve_option_instrument("BANKNIFTY", "48000PE")
    assert "BANKNIFTY" in sym2 and "48000PE" in sym2 and lot2 == 30
    log.info(f"  ✅ PASS — {sym}(lot={lot}) | {sym2}(lot={lot2})")


def test_callbacks_fire():
    """T13: on_trade_enter and on_trade_exit callbacks are called."""
    log.info("T13: Trade callbacks")
    om, _ = make_om()
    entered, exited = [], []
    om.set_on_trade_enter(lambda t: entered.append(t))
    om.set_on_trade_exit(lambda t:  exited.append(t))
    with patch.object(om, "_simulate_ltp", return_value=120.0):
        trade = om.receive_signal(sig())
    assert len(entered) == 1
    with patch.object(om, "_get_ltp", return_value=trade.sl_price - 1):
        om.monitor_positions()
    assert len(exited) == 1
    assert exited[0].state == TradeState.SL_HIT
    log.info("  ✅ PASS — Both callbacks fired correctly")


def test_daily_reset():
    """T14: reset_daily() clears all trades and resets counter to 0."""
    log.info("T14: Daily reset")
    om, _ = make_om()
    with patch.object(om, "_simulate_ltp", return_value=120.0):
        om.receive_signal(sig())
    assert len(om.get_today_trades()) == 1
    om.reset_daily()
    assert len(om.get_today_trades())   == 0
    assert len(om.get_open_positions()) == 0
    assert om._trade_counter            == 0
    # New trades after reset should start from -001
    with patch.object(om, "_simulate_ltp", return_value=100.0):
        t2 = om.receive_signal(sig())
    assert t2.trade_id.endswith("-001")
    log.info("  ✅ PASS — Daily reset complete; counter restarts from 001")


# ─────────────────────────────────────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests():
    print("\n" + "=" * 60)
    print("  MODULE 6 — ORDER MANAGER TESTS")
    print("=" * 60 + "\n")

    tests = [
        test_trade_dataclass,
        test_paper_entry,
        test_sl_price,
        test_target_price,
        test_sl_hit,
        test_target_hit,
        test_trailing_sl_to_breakeven,
        test_square_off_all,
        test_duplicate_rejected,
        test_capital_check_blocks,
        test_pnl_calculation,
        test_option_symbol_built,
        test_callbacks_fire,
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
        print("✅ All Module 6 tests passed! Order Manager is ready.\n")
    else:
        print("❌ Some tests failed. Check logs above.\n")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
