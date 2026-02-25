"""
tests/test_data_feed.py — Test Module 3: Data Feed & Candle Builder
════════════════════════════════════════════════════════════════════
Tests the candle building logic WITHOUT needing a live Zerodha connection.
Simulates ticks and verifies candles are built correctly.

Run with:
    python tests/test_data_feed.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import pytz

from modules.data_feed import Candle, CandleStore, TIMEFRAMES
from modules.logger import get_logger

log = get_logger("test_data_feed")
IST = pytz.timezone("Asia/Kolkata")


def make_time(h, m, s=0):
    """Helper to create IST datetime."""
    return IST.localize(datetime(2026, 2, 25, h, m, s))


def test_candle_basic():
    """Test basic candle OHLCV tracking."""
    log.info("TEST 1: Basic candle OHLCV tracking")

    candle = Candle("NIFTY", 5, make_time(9, 30), 22000.0)
    candle.update(22050.0)
    candle.update(21980.0)
    candle.update(22030.0)

    assert candle.open  == 22000.0, f"Open wrong: {candle.open}"
    assert candle.high  == 22050.0, f"High wrong: {candle.high}"
    assert candle.low   == 21980.0, f"Low wrong:  {candle.low}"
    assert candle.close == 22030.0, f"Close wrong:{candle.close}"
    assert candle.ticks == 4,       f"Ticks wrong:{candle.ticks}"

    log.info(f"  ✅ PASS — {candle}")


def test_candle_store_5min():
    """Test 5-min candle building with simulated ticks."""
    log.info("TEST 2: 5-min candle building")

    store = CandleStore("NIFTY", 5)

    # Simulate ticks across 3 five-minute candles
    # Candle 1: 09:30 – 09:34
    ticks_c1 = [
        (make_time(9, 30,  0), 22000.0),
        (make_time(9, 31,  0), 22050.0),
        (make_time(9, 32,  0), 21980.0),
        (make_time(9, 33,  0), 22030.0),
        (make_time(9, 34, 59), 22020.0),
    ]
    # Candle 2: 09:35 – 09:39
    ticks_c2 = [
        (make_time(9, 35,  0), 22025.0),
        (make_time(9, 36,  0), 22100.0),
        (make_time(9, 37,  0), 22010.0),
        (make_time(9, 39, 59), 22080.0),
    ]
    # Candle 3: 09:40 — this tick closes candle 2 and opens candle 3
    ticks_c3 = [
        (make_time(9, 40,  0), 22085.0),
    ]

    closed_candles = []

    for t, p in ticks_c1:
        c = store.process_tick(p, 100, t)
        if c:
            closed_candles.append(c)

    for t, p in ticks_c2:
        c = store.process_tick(p, 100, t)
        if c:
            closed_candles.append(c)

    for t, p in ticks_c3:
        c = store.process_tick(p, 100, t)
        if c:
            closed_candles.append(c)

    assert len(closed_candles) >= 1, "Expected at least 1 closed candle"

    c1 = closed_candles[0]
    assert c1.open  == 22000.0,  f"C1 Open wrong:  {c1.open}"
    assert c1.high  == 22050.0,  f"C1 High wrong:  {c1.high}"
    assert c1.low   == 21980.0,  f"C1 Low wrong:   {c1.low}"
    assert c1.close == 22020.0,  f"C1 Close wrong: {c1.close}"
    assert c1.is_closed == True, "C1 should be marked closed"

    log.info(f"  ✅ PASS — Closed candle 1: {c1}")
    if len(closed_candles) > 1:
        log.info(f"  ✅ PASS — Closed candle 2: {closed_candles[1]}")


def test_candle_store_15min():
    """Test 15-min candle building and opening range detection."""
    log.info("TEST 3: 15-min candle building + Opening Range")

    store = CandleStore("NIFTY", 15)

    # Candle 1: 09:15 – 09:29 (first 15-min candle = Opening Range)
    ticks = [
        (make_time(9, 15,  0), 22100.0),
        (make_time(9, 18,  0), 22200.0),   # high
        (make_time(9, 22,  0), 21900.0),   # low
        (make_time(9, 28,  0), 22050.0),
        (make_time(9, 29, 59), 22080.0),
    ]
    # This tick at 09:30 closes the first 15-min candle
    ticks.append((make_time(9, 30,  0), 22090.0))

    closed = None
    for t, p in ticks:
        c = store.process_tick(p, 500, t)
        if c:
            closed = c

    assert closed is not None, "Expected first 15-min candle to close"
    assert closed.open  == 22100.0, f"Open wrong:  {closed.open}"
    assert closed.high  == 22200.0, f"High wrong:  {closed.high}"
    assert closed.low   == 21900.0, f"Low wrong:   {closed.low}"
    assert closed.close == 22080.0, f"Close wrong: {closed.close}"

    log.info(f"  ✅ PASS — 15-min candle: {closed}")
    log.info(f"  Opening Range would be: High={closed.high} Low={closed.low}")


def test_dataframe_output():
    """Test that DataFrame output is correctly formatted."""
    log.info("TEST 4: DataFrame output format")

    store = CandleStore("BANKNIFTY", 5)

    # Create 3 closed candles
    all_ticks = [
        # Candle 1: 09:30
        (make_time(9, 30, 0), 48000.0),
        (make_time(9, 34, 0), 48200.0),
        # Candle 2: 09:35
        (make_time(9, 35, 0), 48150.0),
        (make_time(9, 39, 0), 48300.0),
        # Candle 3: 09:40 — closes candle 2
        (make_time(9, 40, 0), 48250.0),
        (make_time(9, 44, 0), 48400.0),
        # Candle 4: 09:45 — closes candle 3
        (make_time(9, 45, 0), 48350.0),
    ]

    for t, p in all_ticks:
        store.process_tick(p, 1000, t)

    df = store.get_dataframe(10)

    assert not df.empty,               "DataFrame should not be empty"
    assert "open"   in df.columns,     "Missing 'open' column"
    assert "high"   in df.columns,     "Missing 'high' column"
    assert "low"    in df.columns,     "Missing 'low' column"
    assert "close"  in df.columns,     "Missing 'close' column"
    assert "volume" in df.columns,     "Missing 'volume' column"

    log.info(f"  ✅ PASS — DataFrame shape: {df.shape}")
    log.info(f"\n{df.to_string()}\n")


def test_candle_boundary():
    """Test that candle boundaries are correct for different timeframes."""
    log.info("TEST 5: Candle boundary calculation")

    store_5  = CandleStore("NIFTY", 5)
    store_15 = CandleStore("NIFTY", 15)

    test_times = [
        (make_time(9, 37, 23), "09:35", "09:30"),
        (make_time(9, 41,  0), "09:40", "09:30"),
        (make_time(9, 59, 59), "09:55", "09:45"),
        (make_time(10, 0,  0), "10:00", "10:00"),
        (make_time(10, 16, 0), "10:15", "10:15"),
    ]

    for tick_time, expected_5m, expected_15m in test_times:
        open_5  = store_5._get_candle_open_time(tick_time)
        open_15 = store_15._get_candle_open_time(tick_time)

        assert open_5.strftime("%H:%M")  == expected_5m,  \
            f"5m boundary wrong for {tick_time}: got {open_5.strftime('%H:%M')} expected {expected_5m}"
        assert open_15.strftime("%H:%M") == expected_15m, \
            f"15m boundary wrong for {tick_time}: got {open_15.strftime('%H:%M')} expected {expected_15m}"

    log.info("  ✅ PASS — All candle boundaries correct")


def run_all_tests():
    print("\n" + "="*60)
    print("  MODULE 3 — DATA FEED TESTS")
    print("="*60 + "\n")

    tests = [
        test_candle_basic,
        test_candle_store_5min,
        test_candle_store_15min,
        test_dataframe_output,
        test_candle_boundary,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            log.error(f"  ❌ FAIL — {test_fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            log.error(f"  ❌ ERROR — {test_fn.__name__}: {e}")
            failed += 1

    print("\n" + "="*60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("="*60 + "\n")

    if failed == 0:
        print("✅ All Module 3 tests passed! Data Feed is ready.\n")
    else:
        print("❌ Some tests failed. Check logs above.\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
