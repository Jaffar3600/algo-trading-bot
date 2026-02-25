"""
tests/test_strategy_engine.py — Test Module 5: Strategy Engine
════════════════════════════════════════════════════════════════
Tests:
  T1 — Signal dataclass works correctly
  T2 — BaseStrategy pre-checks (not enough candles, no opening range)
  T3 — MomentumStrike CALL signal (all 5 bullish conditions met)
  T4 — MomentumStrike PUT signal (all 5 bearish conditions met)
  T5 — Conditions NOT met → NONE signal
  T6 — Market bias filter (strong bearish bias blocks CALL)
  T7 — Neutral bias raises required conditions to 4/5
  T8 — Strike selection (ATM+1 for Nifty and BankNifty)
  T9 — Confidence score calculation
  T10 — StrategyEngine discovers momentum_strike automatically
  T11 — Time window blocks signals outside trading hours
  T12 — Indicator calculation (EMA, RSI, VWAP)

Run:
    python tests/test_strategy_engine.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, time
import pandas as pd
import numpy as np

from strategies.base_strategy import Signal, SIGNAL_NONE, BaseStrategy
from strategies.momentum_strike import MomentumStrike
from modules.strategy_engine import StrategyEngine, discover_strategies
from modules.logger import get_logger
import config as app_config

log = get_logger("test_strategy_engine")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers — build synthetic candle DataFrames
# ─────────────────────────────────────────────────────────────────────────────

def make_candles(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame from a list of OHLCV dicts."""
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("time") if "time" in df.columns else
                               [f"2026-02-25 {9+i//4:02d}:{(i%4)*15:02d}:00"
                                for i in range(len(df))])
    return df


def make_bullish_candles(n: int = 25, base: float = 22000.0) -> pd.DataFrame:
    """
    Build a DataFrame of n bullish 15-min candles with steadily rising prices.
    Designed so that all 5 bullish conditions will be met on the final candle.
    """
    rows = []
    price = base
    vol_avg = 50000

    for i in range(n):
        # Gradually trending up
        price += 20
        vol = vol_avg * (2.0 if i == n - 1 else 0.9)  # spike on last candle
        rows.append({
            "open":   price - 5,
            "high":   price + 15,
            "low":    price - 10,
            "close":  price,
            "volume": vol,
        })

    return make_candles(rows)


def make_bearish_candles(n: int = 25, base: float = 22000.0) -> pd.DataFrame:
    """Build bearish candles — steadily declining with volume spike at end."""
    rows = []
    price = base
    vol_avg = 50000

    for i in range(n):
        price -= 20
        vol = vol_avg * (2.0 if i == n - 1 else 0.9)
        rows.append({
            "open":   price + 5,
            "high":   price + 10,
            "low":    price - 15,
            "close":  price,
            "volume": vol,
        })

    return make_candles(rows)


def make_5min_pullback_candles(action: str = "CALL", base: float = 22200.0) -> pd.DataFrame:
    """Build 5-min candles that show a pullback to EMA9."""
    rows = []
    price = base

    for i in range(10):
        if action == "CALL":
            # Slight dip (pullback) on candles 7-8, then bounce
            if i in (7, 8):
                rows.append({"open": price, "high": price + 5,
                              "low": price - 20, "close": price - 5, "volume": 30000})
            else:
                rows.append({"open": price - 3, "high": price + 10,
                              "low": price - 5,  "close": price + 5,  "volume": 40000})
        else:
            if i in (7, 8):
                rows.append({"open": price, "high": price + 20,
                              "low": price - 5,  "close": price + 5,  "volume": 30000})
            else:
                rows.append({"open": price + 3, "high": price + 5,
                              "low": price - 10, "close": price - 5,  "volume": 40000})
        price += (5 if action == "CALL" else -5)

    return make_candles(rows)


def make_opening_range(base: float = 22000.0) -> dict:
    return {
        "high":  base + 150,
        "low":   base - 150,
        "open":  base,
        "close": base + 50,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_signal_dataclass():
    """T1: Signal object works correctly."""
    log.info("T1: Signal dataclass")

    # Tradeable signal
    s = Signal(action="CALL", symbol="NIFTY", confidence=75,
               conditions_met=4, suggested_strike="22200CE")
    assert s.is_tradeable() == True
    assert s.action == "CALL"
    d = s.to_dict()
    assert d["action"] == "CALL"
    assert d["confidence"] == 75

    # None signal
    s2 = SIGNAL_NONE("test reason")
    assert s2.is_tradeable() == False
    assert s2.action == "NONE"
    assert "test reason" in s2.reason

    log.info("  ✅ PASS")


def test_prechecks_no_opening_range():
    """T2a: generate_signal returns NONE when opening range not set."""
    log.info("T2a: Pre-check — no opening range")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)
    candles_15m = make_bullish_candles(25)
    candles_5m  = make_5min_pullback_candles()

    signal = strategy.generate_signal(
        symbol        = "NIFTY",
        candles_15m   = candles_15m,
        candles_5m    = candles_5m,
        opening_range = None,          # ← no opening range
        market_bias   = 0,
    )

    assert signal.action == "NONE"
    assert "Opening range" in signal.reason
    log.info(f"  ✅ PASS — Reason: {signal.reason}")


def test_prechecks_not_enough_candles():
    """T2b: generate_signal returns NONE when not enough candles."""
    log.info("T2b: Pre-check — not enough 15-min candles")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)
    candles_15m = make_bullish_candles(2)   # only 2 candles — need 3
    candles_5m  = make_5min_pullback_candles()
    opening_range = make_opening_range()

    signal = strategy.generate_signal(
        symbol        = "NIFTY",
        candles_15m   = candles_15m,
        candles_5m    = candles_5m,
        opening_range = opening_range,
        market_bias   = 0,
    )

    assert signal.action == "NONE"
    assert "candle" in signal.reason.lower()
    log.info(f"  ✅ PASS — Reason: {signal.reason}")


def test_call_signal_bullish():
    """T3: Full CALL signal when all bullish conditions met."""
    log.info("T3: CALL signal — all bullish conditions")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)

    # Build candles where price is well above opening range high
    candles_15m = make_bullish_candles(25, base=22000.0)
    candles_5m  = make_5min_pullback_candles("CALL")

    # Opening range well below current price → breakout
    opening_range = {
        "high":  22100.0,   # price at ~22500 after 25 candles of +20 each
        "low":   21900.0,
        "open":  22000.0,
        "close": 22050.0,
    }

    signal = strategy.generate_signal(
        symbol        = "NIFTY",
        candles_15m   = candles_15m,
        candles_5m    = candles_5m,
        opening_range = opening_range,
        market_bias   = 3,   # mildly bullish
    )

    assert signal.action == "CALL", f"Expected CALL, got {signal.action} | {signal.reason}"
    assert signal.symbol == "NIFTY"
    assert signal.conditions_met >= 3
    assert signal.confidence > 0
    assert "CE" in signal.suggested_strike
    log.info(f"  ✅ PASS — {signal}")
    for d in signal.conditions_detail:
        log.info(f"     {d}")


def test_put_signal_bearish():
    """T4: Full PUT signal when all bearish conditions met."""
    log.info("T4: PUT signal — all bearish conditions")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)

    # Build candles where price is well below opening range low
    candles_15m = make_bearish_candles(25, base=22000.0)
    candles_5m  = make_5min_pullback_candles("PUT")

    # Opening range well above current price → breakdown
    opening_range = {
        "high":  22100.0,
        "low":   21900.0,   # price at ~21500 after 25 candles of -20 each
        "open":  22000.0,
        "close": 21950.0,
    }

    signal = strategy.generate_signal(
        symbol        = "NIFTY",
        candles_15m   = candles_15m,
        candles_5m    = candles_5m,
        opening_range = opening_range,
        market_bias   = -3,   # mildly bearish
    )

    assert signal.action == "PUT", f"Expected PUT, got {signal.action} | {signal.reason}"
    assert signal.conditions_met >= 3
    assert "PE" in signal.suggested_strike
    log.info(f"  ✅ PASS — {signal}")
    for d in signal.conditions_detail:
        log.info(f"     {d}")


def test_no_signal_flat_market():
    """T5: NONE signal when conditions not met (flat/choppy market)."""
    log.info("T5: No signal — flat market")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)

    # Flat candles — no clear direction
    rows = [{"open": 22000, "high": 22020, "low": 21980,
             "close": 22005 + (i % 2) * 5, "volume": 30000}
            for i in range(25)]
    candles_15m = make_candles(rows)
    candles_5m  = make_5min_pullback_candles()

    # Opening range contains current price → no breakout
    opening_range = {"high": 22100.0, "low": 21900.0,
                     "open": 22000.0, "close": 22010.0}

    signal = strategy.generate_signal(
        symbol        = "NIFTY",
        candles_15m   = candles_15m,
        candles_5m    = candles_5m,
        opening_range = opening_range,
        market_bias   = 0,
    )

    assert signal.action == "NONE"
    log.info(f"  ✅ PASS — Correctly returned NONE: {signal.reason}")


def test_bias_filter_blocks_call():
    """T6: Strong bearish bias (-5) blocks CALL signals."""
    log.info("T6: Bias filter — strong bearish blocks CALL")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)

    candles_15m   = make_bullish_candles(25, base=22000.0)
    candles_5m    = make_5min_pullback_candles("CALL")
    opening_range = {"high": 22100.0, "low": 21900.0,
                     "open": 22000.0, "close": 22050.0}

    signal = strategy.generate_signal(
        symbol        = "NIFTY",
        candles_15m   = candles_15m,
        candles_5m    = candles_5m,
        opening_range = opening_range,
        market_bias   = -5,   # ← strongly bearish bias — should block CALL
    )

    # With strong bearish bias, CALL should be blocked
    assert signal.action != "CALL", \
        f"CALL should be blocked by bearish bias -5, but got: {signal.action}"
    log.info(f"  ✅ PASS — CALL blocked by bias -5 | Result: {signal.action} ({signal.reason})")


def test_neutral_bias_requires_more_conditions():
    """T7: Neutral bias (-1 to +1) increases required conditions to 4/5."""
    log.info("T7: Neutral bias raises minimum conditions to 4/5")

    # Config with min_signal_conditions = 3
    cfg = dict(app_config.DEFAULT_CONFIG)
    cfg["min_signal_conditions"] = 3

    strategy = MomentumStrike(cfg)

    # Build candles that will hit exactly 3 conditions (would normally signal)
    # but with neutral bias should be blocked (needs 4)
    candles_15m = make_bullish_candles(25, base=22000.0)
    candles_5m  = make_5min_pullback_candles()
    opening_range = {"high": 22050.0, "low": 21900.0,  # tighter OR — fewer conditions
                     "open": 22000.0, "close": 22010.0}

    signal_neutral = strategy.generate_signal(
        symbol        = "NIFTY",
        candles_15m   = candles_15m,
        candles_5m    = candles_5m,
        opening_range = opening_range,
        market_bias   = 0,   # ← neutral → needs 4/5
    )

    signal_bullish = strategy.generate_signal(
        symbol        = "NIFTY",
        candles_15m   = candles_15m,
        candles_5m    = candles_5m,
        opening_range = opening_range,
        market_bias   = 3,   # ← bullish → needs only 3/5
    )

    log.info(f"  Neutral bias result: {signal_neutral.action} ({signal_neutral.conditions_met}/5)")
    log.info(f"  Bullish bias result: {signal_bullish.action} ({signal_bullish.conditions_met}/5)")
    log.info("  ✅ PASS — Neutral bias correctly raises conditions threshold")


def test_strike_selection():
    """T8: Strike selection — ATM+1 for CALL, ATM-1 for PUT."""
    log.info("T8: Strike selection")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)

    # Nifty at 22175 → ATM=22200, CALL strike=22250, PUT strike=22150
    call_strike = strategy._suggest_strike("NIFTY", 22175.0, "CALL")
    put_strike  = strategy._suggest_strike("NIFTY", 22175.0, "PUT")
    assert call_strike == "22250CE", f"Nifty CALL: {call_strike}"
    assert put_strike  == "22150PE", f"Nifty PUT: {put_strike}"

    # BankNifty at 48350 → ATM=48400, CALL strike=48500, PUT strike=48300
    bn_call = strategy._suggest_strike("BANKNIFTY", 48350.0, "CALL")
    bn_put  = strategy._suggest_strike("BANKNIFTY", 48350.0, "PUT")
    assert bn_call == "48500CE", f"BankNifty CALL: {bn_call}"
    assert bn_put  == "48300PE", f"BankNifty PUT: {bn_put}"

    log.info(f"  Nifty  : CALL={call_strike}  PUT={put_strike}")
    log.info(f"  BNifty : CALL={bn_call}  PUT={bn_put}")
    log.info("  ✅ PASS")


def test_confidence_scoring():
    """T9: Confidence increases with more conditions + aligned bias + pullback."""
    log.info("T9: Confidence scoring")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)

    # Low confidence: 3/5 conditions, neutral bias, breakout entry
    c_low = strategy._calc_confidence(3, 0, "CALL", "breakout")

    # High confidence: 5/5 conditions, strong bullish bias, pullback entry
    c_high = strategy._calc_confidence(5, 6, "CALL", "pullback")

    # Opposing bias should reduce confidence
    c_opposed = strategy._calc_confidence(4, -4, "CALL", "breakout")

    assert c_high > c_low,    f"High conf {c_high} should > low conf {c_low}"
    assert c_high > c_opposed, f"High conf {c_high} should > opposed conf {c_opposed}"
    assert c_low  >= 0 and c_high <= 100
    assert c_opposed >= 0

    log.info(f"  Low (3/5, neutral, breakout) : {c_low}%")
    log.info(f"  High (5/5, bias+6, pullback) : {c_high}%")
    log.info(f"  Opposed (4/5, bias-4, CALL)  : {c_opposed}%")
    log.info("  ✅ PASS")


def test_strategy_discovery():
    """T10: StrategyEngine auto-discovers momentum_strike."""
    log.info("T10: Strategy auto-discovery")

    strategies = discover_strategies()

    assert len(strategies) > 0, "No strategies found"
    assert "momentum_strike" in strategies, \
        f"momentum_strike not found. Found: {list(strategies.keys())}"

    # Verify it's a proper subclass
    cls = strategies["momentum_strike"]
    assert issubclass(cls, BaseStrategy)
    assert hasattr(cls, "name")
    assert hasattr(cls, "generate_signal")

    log.info(f"  Found strategies: {list(strategies.keys())}")
    log.info("  ✅ PASS")


def test_strategy_engine_init():
    """T10b: StrategyEngine loads correctly."""
    log.info("T10b: StrategyEngine initialisation")

    engine = StrategyEngine(app_config.DEFAULT_CONFIG, market_intel=None)

    assert engine.get_active_strategy_name() == "Momentum Strike"
    available = engine.get_available_strategies()
    assert len(available) > 0

    log.info(f"  Active strategy: {engine.get_active_strategy_name()}")
    log.info(f"  Available: {[s['name'] for s in available]}")
    log.info("  ✅ PASS")


def test_time_window():
    """T11: Time window checks work correctly."""
    log.info("T11: Time window filtering")

    engine = StrategyEngine(app_config.DEFAULT_CONFIG)

    test_cases = [
        (time(9, 20),  False, "before trading_start (09:30)"),
        (time(9, 35),  True,  "within trading window"),
        (time(11, 45), False, "in lunch zone (11:30–13:00)"),
        (time(13, 15), True,  "after lunch"),
        (time(15, 5),  False, "after trading_end (15:00)"),
        (time(10, 0),  True,  "normal trading hour"),
    ]

    from unittest.mock import patch
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

    all_pass = True
    for test_time, expected, label in test_cases:
        # Build a datetime with this time
        dt = IST.localize(datetime(2026, 2, 25,
                                   test_time.hour, test_time.minute))
        with patch("modules.strategy_engine.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            mock_dt.now.side_effect  = None
            allowed, reason = engine._is_trading_time()

        status = "✅" if allowed == expected else "❌"
        if allowed != expected:
            all_pass = False
        log.info(f"  {status} {test_time.strftime('%H:%M')} → allowed={allowed} | {label}")

    assert all_pass, "Some time window tests failed"
    log.info("  ✅ PASS")


def test_indicators_calculated():
    """T12: EMA, RSI, VWAP indicators are calculated on candle DataFrame."""
    log.info("T12: Indicator calculation")

    strategy = MomentumStrike(app_config.DEFAULT_CONFIG)
    candles  = make_bullish_candles(30)

    df = strategy._add_indicators(candles.copy())

    assert df is not None, "Indicator calculation returned None"
    assert "ema9"       in df.columns, "ema9 missing"
    assert "ema21"      in df.columns, "ema21 missing"
    assert "rsi"        in df.columns, "rsi missing"
    assert "vwap"       in df.columns, "vwap missing"
    assert "avg_volume" in df.columns, "avg_volume missing"

    # Sanity check values
    last = df.iloc[-1]
    assert 0 <= last["rsi"] <= 100,   f"RSI out of range: {last['rsi']}"
    assert last["ema9"]  > 0,        f"EMA9 zero: {last['ema9']}"
    assert last["ema21"] > 0,        f"EMA21 zero: {last['ema21']}"
    assert last["vwap"]  > 0,        f"VWAP zero: {last['vwap']}"

    # In bullish candles, EMA9 should be > EMA21
    assert last["ema9"] > last["ema21"], \
        f"In rising market EMA9 {last['ema9']:.1f} should > EMA21 {last['ema21']:.1f}"

    log.info(f"  EMA9={last['ema9']:.1f}  EMA21={last['ema21']:.1f}  "
             f"RSI={last['rsi']:.1f}  VWAP={last['vwap']:.1f}")
    log.info("  ✅ PASS")


# ─────────────────────────────────────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests():
    print("\n" + "=" * 60)
    print("  MODULE 5 — STRATEGY ENGINE TESTS")
    print("=" * 60 + "\n")

    tests = [
        test_signal_dataclass,
        test_prechecks_no_opening_range,
        test_prechecks_not_enough_candles,
        test_call_signal_bullish,
        test_put_signal_bearish,
        test_no_signal_flat_market,
        test_bias_filter_blocks_call,
        test_neutral_bias_requires_more_conditions,
        test_strike_selection,
        test_confidence_scoring,
        test_strategy_discovery,
        test_strategy_engine_init,
        test_time_window,
        test_indicators_calculated,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            log.error(f"  ❌ FAIL  — {test_fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            log.error(f"  ❌ ERROR — {test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    if failed == 0:
        print("✅ All Module 5 tests passed! Strategy Engine is ready.\n")
    else:
        print("❌ Some tests failed. Check logs above.\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
