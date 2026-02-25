"""
tests/test_market_intel.py — Test Module 4: Market Intelligence Engine
════════════════════════════════════════════════════════════════════════
Tests:
  - Bias score calculation logic (no internet needed)
  - Live data fetch (needs internet — optional, skip with --offline)
  - Snapshot structure validation

Run:
    python tests/test_market_intel.py            # all tests (needs internet)
    python tests/test_market_intel.py --offline  # logic tests only
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.market_intel import MarketIntel, IntelSnapshot
from modules.logger import get_logger

log = get_logger("test_market_intel")
OFFLINE = "--offline" in sys.argv


# ── Helper: build a snapshot with custom values for bias testing ──────────────
def make_snap(**kwargs) -> IntelSnapshot:
    s = IntelSnapshot()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def test_bias_strongly_bullish():
    """All green: Gift Nifty up, Nifty up, FII buying, Asia up → high positive score."""
    log.info("TEST 1: Strongly bullish scenario")
    intel = MarketIntel(kite=None)

    snap = make_snap(
        gift_nifty_change_pct =  1.2,
        dow_change_pct        =  0.8,
        nifty_change_pct      =  0.9,
        banknifty_change_pct  =  1.1,
        fii_net_buy           =  2500.0,
        nikkei_change_pct     =  0.7,
        hangseng_change_pct   =  0.6,
        india_vix             =  12.0,
        usdinr_change_pct     = -0.2,
    )
    intel._compute_bias_score(snap)

    assert snap.bias_score > 5,   f"Expected score > 5, got {snap.bias_score}"
    assert "Bullish" in snap.bias_label, f"Expected bullish label, got {snap.bias_label}"
    log.info(f"  ✅ PASS — Score: {snap.bias_score:+d} ({snap.bias_label})")


def test_bias_strongly_bearish():
    """All red: Dow down, Nifty down, FII selling, VIX high → negative score."""
    log.info("TEST 2: Strongly bearish scenario")
    intel = MarketIntel(kite=None)

    snap = make_snap(
        gift_nifty_change_pct = -1.5,
        dow_change_pct        = -2.0,
        nifty_change_pct      = -0.8,
        banknifty_change_pct  = -1.2,
        fii_net_buy           = -3000.0,
        nikkei_change_pct     = -0.9,
        hangseng_change_pct   = -1.1,
        india_vix             =  22.0,   # High VIX
        usdinr_change_pct     =   0.6,   # INR weakening
    )
    intel._compute_bias_score(snap)

    assert snap.bias_score < -4, f"Expected score < -4, got {snap.bias_score}"
    assert "Bearish" in snap.bias_label, f"Expected bearish label, got {snap.bias_label}"
    log.info(f"  ✅ PASS — Score: {snap.bias_score:+d} ({snap.bias_label})")


def test_bias_neutral():
    """Mixed signals → neutral score near 0."""
    log.info("TEST 3: Neutral/mixed scenario")
    intel = MarketIntel(kite=None)

    snap = make_snap(
        gift_nifty_change_pct =  0.1,    # flat
        dow_change_pct        = -0.1,    # flat
        nifty_change_pct      =  0.2,    # flat
        banknifty_change_pct  = -0.1,    # flat
        fii_net_buy           =  100.0,  # tiny buy
        nikkei_change_pct     =  0.1,
        hangseng_change_pct   = -0.1,
        india_vix             =  14.0,
        usdinr_change_pct     =  0.0,
    )
    intel._compute_bias_score(snap)

    assert -3 <= snap.bias_score <= 3, f"Expected neutral score (-3 to 3), got {snap.bias_score}"
    log.info(f"  ✅ PASS — Score: {snap.bias_score:+d} ({snap.bias_label})")


def test_high_impact_event_dampens_score():
    """High impact events should dampen an extreme score toward zero."""
    log.info("TEST 4: High-impact event dampens bias score")
    intel = MarketIntel(kite=None)

    snap = make_snap(
        gift_nifty_change_pct =  2.0,
        nifty_change_pct      =  1.5,
        fii_net_buy           =  5000.0,
        india_vix             =  25.0,   # triggers high-impact event
        crude_change_pct      =  4.0,    # triggers crude oil shock event
    )
    intel._compute_bias_score(snap)

    # Score should be dampened (not at maximum)
    assert snap.bias_score <= 8, f"Score should be dampened by events, got {snap.bias_score}"
    assert len(snap.high_impact_events) > 0, "Should have detected high-impact events"
    log.info(f"  ✅ PASS — Score dampened to {snap.bias_score:+d} | Events: {snap.high_impact_events}")


def test_score_clamped():
    """Score should never exceed -10 or +10."""
    log.info("TEST 5: Score clamping to [-10, +10]")
    intel = MarketIntel(kite=None)

    # Extreme bullish
    snap = make_snap(
        gift_nifty_change_pct = 5.0,
        dow_change_pct        = 5.0,
        nifty_change_pct      = 5.0,
        banknifty_change_pct  = 5.0,
        fii_net_buy           = 99999.0,
        nikkei_change_pct     = 5.0,
        hangseng_change_pct   = 5.0,
        india_vix             = 5.0,
        usdinr_change_pct     = -5.0,
    )
    intel._compute_bias_score(snap)
    assert snap.bias_score <= 10, f"Score should be clamped to 10, got {snap.bias_score}"

    # Extreme bearish
    snap2 = make_snap(
        gift_nifty_change_pct = -5.0,
        dow_change_pct        = -5.0,
        nifty_change_pct      = -5.0,
        banknifty_change_pct  = -5.0,
        fii_net_buy           = -99999.0,
        india_vix             = 50.0,
        usdinr_change_pct     = 5.0,
    )
    intel._compute_bias_score(snap2)
    assert snap2.bias_score >= -10, f"Score should be clamped to -10, got {snap2.bias_score}"

    log.info(f"  ✅ PASS — Bullish clamped: {snap.bias_score:+d} | Bearish clamped: {snap2.bias_score:+d}")


def test_snapshot_structure():
    """Verify IntelSnapshot has all required fields and to_dict works."""
    log.info("TEST 6: Snapshot structure validation")
    snap = IntelSnapshot()

    required_fields = [
        "nifty_value", "banknifty_value", "india_vix",
        "dow_jones", "sp500", "nasdaq", "nikkei", "hangseng",
        "fii_net_buy", "dii_net_buy",
        "crude_brent", "gold_usd",
        "usdinr", "eurinr",
        "bias_score", "bias_label", "bias_reasoning",
        "high_impact_events", "news_headlines",
        "sources_ok", "errors"
    ]

    d = snap.to_dict()
    for field in required_fields:
        assert field in d, f"Missing field in snapshot: {field}"

    log.info(f"  ✅ PASS — All {len(required_fields)} required fields present")


def test_live_fetch():
    """Live fetch test — needs internet connection."""
    log.info("TEST 7: Live data fetch (internet required)")

    intel = MarketIntel(kite=None)
    snap  = intel.refresh()

    # At least some sources should succeed
    ok_count = sum(1 for v in snap.sources_ok.values() if v)
    assert ok_count > 0, "Expected at least 1 source to succeed"

    # Bias score should be a valid integer in range
    assert -10 <= snap.bias_score <= 10, f"Bias score out of range: {snap.bias_score}"

    intel.print_snapshot(snap)

    log.info(f"  ✅ PASS — {ok_count}/{len(snap.sources_ok)} sources OK | "
             f"Score: {snap.bias_score:+d} ({snap.bias_label})")


def run_all_tests():
    print("\n" + "=" * 60)
    print("  MODULE 4 — MARKET INTELLIGENCE TESTS")
    if OFFLINE:
        print("  Mode: OFFLINE (logic tests only)")
    print("=" * 60 + "\n")

    # Logic tests — no internet needed
    logic_tests = [
        test_bias_strongly_bullish,
        test_bias_strongly_bearish,
        test_bias_neutral,
        test_high_impact_event_dampens_score,
        test_score_clamped,
        test_snapshot_structure,
    ]

    # Live tests — needs internet
    live_tests = [] if OFFLINE else [test_live_fetch]

    passed = 0
    failed = 0

    for test_fn in logic_tests + live_tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            log.error(f"  ❌ FAIL — {test_fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            log.error(f"  ❌ ERROR — {test_fn.__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    if failed == 0:
        print("✅ All Module 4 tests passed! Market Intelligence is ready.\n")
    else:
        print("❌ Some tests failed. Check logs above.\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
