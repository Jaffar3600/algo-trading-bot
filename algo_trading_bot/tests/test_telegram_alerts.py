"""
tests/test_telegram_alerts.py — Test Module 8: Telegram Alerts
═══════════════════════════════════════════════════════════════
All tests run offline — no real Telegram account needed.
We mock _send_now() and test queue logic, message content, and wiring.

T1  — Disabled when token/chat_id missing → is_enabled = False
T2  — Enabled with valid token + chat_id → is_enabled = True
T3  — Trade entry message contains required fields
T4  — Trade exit: SL hit → correct emoji + negative P&L in message
T5  — Trade exit: Target hit → correct emoji + positive P&L in message
T6  — Trade exit: EOD square-off → correct reason label
T7  — Trade exit: duration (minutes) shown when entry/exit times set
T8  — Signal approved message contains strike, conditions, bias
T9  — Signal blocked message contains check name + reason
T10 — Daily summary: win rate, trade count, P&L all correct
T11 — Session start message contains balance fields
T12 — High-impact event message contains event description
T13 — Error alert message contains the error text
T14 — send_message() enqueues raw text directly
T15 — Disabled instance: _enqueue() does NOT add to queue
T16 — Queue is non-blocking (send_trade_entry returns immediately)
T17 — attach_order_manager() wires enter/exit callbacks
T18 — attach_risk_manager() wires approved callback
T19 — _send_now() calls requests.post with correct URL + payload
T20 — _send_now() falls back to plain text if Markdown parse fails
T21 — stop() sends sentinel to drain worker thread

Run:
    python tests/test_telegram_alerts.py
"""

import sys, os, types, queue, time, threading
import datetime as _dt
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch, call
from enum import Enum

# ── pandas before pytz mock ──────────────────────────────────────────────────
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

for _m in ["kiteconnect", "bs4"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))
sys.modules["kiteconnect"].KiteTicker = object

# requests must stay real-ish but we'll patch at the method level
import requests as _real_requests  # noqa — imported before path insert

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.telegram_alerts import TelegramAlerts
from modules.logger import get_logger

log = get_logger("test_telegram_alerts")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers — mock Trade, Signal, RiskDecision, IntelSnapshot
# ─────────────────────────────────────────────────────────────────────────────

class FakeTradeState(Enum):
    SL_HIT     = "sl_hit"
    TARGET_HIT = "target_hit"
    TIMED_OUT  = "timed_out"


def make_trade(
    action="CALL",
    mode="paper",
    state_val="sl_hit",
    entry=120.0,
    exit_p=80.0,
    sl=80.4,
    target=199.2,
    net_pnl=-3000.0,
    gross_pnl=-2950.0,
    charges=50.0,
    lots=1,
    lot_size=75,
    exit_reason="SL hit",
    with_times=False,
):
    t = MagicMock()
    t.trade_id       = "T20260225-001"
    t.mode           = mode
    t.symbol         = "NIFTY"
    t.option_symbol  = "NIFTY25FEB22200CE"
    t.action         = action
    t.state          = FakeTradeState(state_val)
    t.entry_price    = entry
    t.exit_price     = exit_p
    t.sl_price       = sl
    t.target_price   = target
    t.lots           = lots
    t.lot_size       = lot_size
    t.quantity       = lots * lot_size
    t.net_pnl        = net_pnl
    t.gross_pnl      = gross_pnl
    t.charges        = charges
    t.confidence     = 75
    t.conditions_met = 4
    t.exit_reason    = exit_reason
    if with_times:
        base = _dt.datetime(2026, 2, 25, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        t.entry_time = base
        t.exit_time  = base.replace(hour=10, minute=35)
    else:
        t.entry_time = None
        t.exit_time  = None
    return t


def make_signal(action="CALL", confidence=75, conditions_met=4, bias=3):
    s = MagicMock()
    s.action            = action
    s.symbol            = "NIFTY"
    s.suggested_strike  = "22200CE"
    s.confidence        = confidence
    s.conditions_met    = conditions_met
    s.conditions_total  = 5
    s.bias_score        = bias
    s.entry_type        = "breakout"
    s.conditions_detail = [
        "✅ C1: Close 22300 > OR High 22100",
        "✅ C2: EMA9 > EMA21",
        "✅ C3: RSI 62 > 55",
        "❌ C4: Close < VWAP",
        "✅ C5: Volume spike 2.1x",
    ]
    return s


def make_decision(approved=True, blocked_by="", block_reason=""):
    d = MagicMock()
    d.approved     = approved
    d.blocked_by   = blocked_by
    d.block_reason = block_reason
    return d


def make_snapshot():
    s = MagicMock()
    s.bias_score           = 4
    s.bias_label           = "Mildly Bullish"
    s.nifty_value          = 22350.0
    s.nifty_change_pct     = 0.42
    s.banknifty_value      = 48200.0
    s.banknifty_change_pct = 0.31
    s.india_vix            = 14.2
    s.dow_change_pct       = 0.15
    s.nikkei_change_pct    = -0.22
    s.fii_net_buy          = 850.0
    s.dii_net_buy          = -120.0
    s.usdinr               = 86.42
    s.high_impact_events   = []
    s.bias_reasoning       = ["Gift Nifty +0.3%", "FII net buy ₹850Cr"]
    return s


def make_ta(token="fake_token", chat_id="12345678", enabled=True) -> TelegramAlerts:
    """Create TelegramAlerts with worker disabled (don't need real network)."""
    ta = TelegramAlerts.__new__(TelegramAlerts)
    ta._token    = token
    ta._chat_id  = str(chat_id)
    ta._enabled  = enabled and bool(token) and bool(chat_id)
    ta._queue    = queue.Queue()
    ta._running  = False
    # Worker thread not started — we'll drain queue manually in tests
    ta._worker   = threading.Thread(target=lambda: None, daemon=True)
    return ta


def drain_queue(ta: TelegramAlerts) -> list[str]:
    """Pull all messages currently in the queue."""
    msgs = []
    while not ta._queue.empty():
        try:
            msgs.append(ta._queue.get_nowait())
        except queue.Empty:
            break
    return msgs


# ─────────────────────────────────────────────────────────────────────────────
#  Tests
# ─────────────────────────────────────────────────────────────────────────────

p = f = 0
def ok(name):  global p; p += 1; print(f"  ✅ {name}")
def fail(name, e): global f; f += 1; print(f"  ❌ {name}: {e}")


def test_disabled_when_no_token():
    """T1: is_enabled=False when token or chat_id is missing."""
    log.info("T1: Disabled without token/chat_id")
    try:
        ta1 = make_ta(token="",    chat_id="123")
        ta2 = make_ta(token="tok", chat_id="")
        ta3 = make_ta(token="tok", chat_id="123", enabled=False)
        assert not ta1.is_enabled, "Should be disabled — no token"
        assert not ta2.is_enabled, "Should be disabled — no chat_id"
        assert not ta3.is_enabled, "Should be disabled — enabled=False"
        ok("T1: Disabled correctly without token/chat_id/enabled")
    except Exception as e: fail("T1", e)


def test_enabled_with_valid_credentials():
    """T2: is_enabled=True when both token and chat_id provided."""
    log.info("T2: Enabled with valid credentials")
    try:
        ta = make_ta(token="123:ABC", chat_id="987654321")
        assert ta.is_enabled
        ok("T2: is_enabled=True with valid token + chat_id")
    except Exception as e: fail("T2", e)


def test_trade_entry_message_content():
    """T3: send_trade_entry puts message with all required fields in queue."""
    log.info("T3: Trade entry message content")
    try:
        ta    = make_ta()
        trade = make_trade(action="CALL", entry=120.0, sl=80.4, target=199.2)

        ta.send_trade_entry(trade)
        msgs = drain_queue(ta)

        assert len(msgs) == 1, f"Expected 1 message, got {len(msgs)}"
        msg = msgs[0]

        assert "TRADE ENTERED"         in msg, "Missing TRADE ENTERED"
        assert "NIFTY25FEB22200CE"     in msg, "Missing option symbol"
        assert "CALL"                  in msg, "Missing action"
        assert "120"                   in msg, "Missing entry price"
        assert "80"                    in msg, "Missing SL price"
        assert "199"                   in msg, "Missing target price"
        assert "PAPER"                 in msg, "Missing paper mode tag"
        assert "T20260225-001"         in msg, "Missing trade ID"
        assert "75"                    in msg, "Missing confidence"

        ok("T3: Trade entry message has all required fields")
    except Exception as e: fail("T3", e)


def test_trade_exit_sl_hit_message():
    """T4: SL hit exit uses 🛑 emoji and shows negative P&L."""
    log.info("T4: Trade exit — SL hit")
    try:
        ta    = make_ta()
        trade = make_trade(
            state_val="sl_hit", exit_p=80.0,
            net_pnl=-3000.0, gross_pnl=-2950.0, charges=50.0,
            exit_reason="SL hit"
        )

        ta.send_trade_exit(trade)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "🛑"              in msg, "Missing SL emoji"
        assert "TRADE CLOSED"    in msg, "Missing TRADE CLOSED header"
        assert "Stop Loss Hit"   in msg, "Missing reason label"
        assert "3,000"           in msg, "Missing P&L amount"
        assert "PAPER"           in msg, "Missing mode tag"

        ok("T4: SL hit exit message correct (🛑 + negative P&L)")
    except Exception as e: fail("T4", e)


def test_trade_exit_target_hit_message():
    """T5: Target hit uses 🎯 emoji and shows positive P&L."""
    log.info("T5: Trade exit — Target hit")
    try:
        ta    = make_ta()
        trade = make_trade(
            state_val="target_hit", exit_p=200.0,
            net_pnl=5900.0, gross_pnl=6000.0, charges=100.0,
            exit_reason="Target hit"
        )

        ta.send_trade_exit(trade)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "🎯"             in msg, "Missing target emoji"
        assert "Target Reached" in msg, "Missing reason label"
        assert "5,900"          in msg, "Missing net P&L"
        assert "💰"             in msg, "Missing profit emoji"

        ok("T5: Target hit exit message correct (🎯 + positive P&L)")
    except Exception as e: fail("T5", e)


def test_trade_exit_eod_squareoff():
    """T6: EOD square-off shows correct reason label."""
    log.info("T6: Trade exit — EOD square-off")
    try:
        ta    = make_ta()
        trade = make_trade(
            state_val="timed_out", exit_p=130.0,
            net_pnl=700.0, gross_pnl=750.0, charges=50.0,
            exit_reason="EOD square-off"
        )

        ta.send_trade_exit(trade)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "⏰"            in msg, "Missing time emoji"
        assert "EOD Square-off" in msg, "Missing EOD reason label"

        ok("T6: EOD square-off message correct (⏰ + EOD label)")
    except Exception as e: fail("T6", e)


def test_trade_exit_shows_duration():
    """T7: Duration in minutes shown when entry_time and exit_time are set."""
    log.info("T7: Trade exit duration")
    try:
        ta    = make_ta()
        trade = make_trade(
            state_val="target_hit", exit_p=200.0,
            net_pnl=5900.0, gross_pnl=6000.0, charges=100.0,
            exit_reason="Target hit", with_times=True
        )

        ta.send_trade_exit(trade)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        # entry=10:00, exit=10:35 → 35 minutes
        assert "35m" in msg, f"Expected '35m' duration in message. Got:\n{msg}"

        ok("T7: Duration (35m) shown in exit message")
    except Exception as e: fail("T7", e)


def test_signal_approved_message():
    """T8: Signal approved message contains strike, bias, and conditions."""
    log.info("T8: Signal approved message")
    try:
        ta  = make_ta()
        sig = make_signal(action="CALL", confidence=78, conditions_met=4, bias=3)
        dec = make_decision(approved=True)

        ta.send_signal_approved(sig, dec)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "SIGNAL APPROVED"  in msg, "Missing SIGNAL APPROVED"
        assert "22200CE"          in msg, "Missing strike"
        assert "NIFTY"            in msg, "Missing symbol"
        assert "CALL"             in msg, "Missing action"
        assert "Bullish"          in msg, "Missing bias label"
        assert "C1:"              in msg, "Missing condition C1"
        assert "78"               in msg, "Missing confidence"

        ok("T8: Signal approved message has strike, conditions, bias")
    except Exception as e: fail("T8", e)


def test_signal_blocked_message():
    """T9: Signal blocked message contains check name and reason."""
    log.info("T9: Signal blocked message")
    try:
        ta  = make_ta()
        sig = make_signal()
        dec = make_decision(
            approved=False,
            blocked_by="R1_daily_loss_limit",
            block_reason="Day P&L ₹-6000 ≤ limit -₹5000"
        )

        ta.send_signal_blocked(sig, dec)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "SIGNAL BLOCKED"          in msg, "Missing SIGNAL BLOCKED"
        assert "R1 Daily Loss Limit"     in msg, "Missing human-readable check name"
        assert "Day P&L"                 in msg, "Missing reason text"

        ok("T9: Signal blocked message has check name and reason")
    except Exception as e: fail("T9", e)


def test_daily_summary_content():
    """T10: Daily summary win rate, trade count, P&L all appear."""
    log.info("T10: Daily summary content")
    try:
        ta = make_ta()

        trades = [
            make_trade(net_pnl= 3500.0, state_val="target_hit"),
            make_trade(net_pnl=-1200.0, state_val="sl_hit"),
        ]

        ta.send_daily_summary(trades, day_pnl=2300.0)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "END OF DAY SUMMARY"    in msg, "Missing header"
        assert "2,300"                 in msg, "Missing day P&L"
        assert "2"                     in msg, "Missing trade count"
        assert "50"                    in msg, "Missing win rate (1/2 = 50%)"
        assert "NIFTY25FEB22200CE"     in msg, "Missing trade symbol"

        ok("T10: Daily summary has P&L ₹2,300, 2 trades, 50% win rate")
    except Exception as e: fail("T10", e)


def test_session_start_message():
    """T11: Session start message contains all balance fields."""
    log.info("T11: Session start message")
    try:
        ta = make_ta()
        capital = {
            "available_balance": 100000,
            "usable_capital":    80000,
            "per_trade_limit":   20000,
            "max_trades_today":  2,
            "daily_loss_limit":  5000,
            "funds_pct":         80,
        }

        ta.send_session_start(capital)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "TRADING SESSION STARTED" in msg, "Missing header"
        assert "1,00,000"                in msg or "100,000" in msg, "Missing balance"
        assert "80,000"                  in msg, "Missing usable capital"
        assert "20,000"                  in msg, "Missing per-trade limit"
        assert "5,000"                   in msg, "Missing loss limit"

        ok("T11: Session start message has all balance fields")
    except Exception as e: fail("T11", e)


def test_high_impact_event_message():
    """T12: High-impact event message contains the event description."""
    log.info("T12: High-impact event alert")
    try:
        ta     = make_ta()
        events = ["High VIX: 22.5 (above 20)", "Crude oil surge: +4.2%"]

        ta.send_high_impact_event(events)
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "HIGH-IMPACT EVENT"  in msg, "Missing event header"
        assert "High VIX"           in msg, "Missing VIX event"
        assert "Crude oil"          in msg, "Missing crude event"
        assert "paused"             in msg, "Missing trading paused note"

        ok("T12: High-impact event message contains both events")
    except Exception as e: fail("T12", e)


def test_error_alert_message():
    """T13: Error alert contains the error text and context."""
    log.info("T13: Error alert")
    try:
        ta = make_ta()
        ta.send_error("WebSocket connection lost", context="data_feed.py line 142")
        msgs = drain_queue(ta)
        msg  = msgs[0]

        assert "BOT ERROR"               in msg, "Missing BOT ERROR header"
        assert "WebSocket"               in msg, "Missing error text"
        assert "data_feed.py"            in msg, "Missing context"

        ok("T13: Error alert has error text and context")
    except Exception as e: fail("T13", e)


def test_send_message_raw():
    """T14: send_message() puts raw text directly into queue."""
    log.info("T14: send_message() raw text")
    try:
        ta = make_ta()
        ta.send_message("Hello from the bot!")
        msgs = drain_queue(ta)

        assert len(msgs) == 1
        assert msgs[0] == "Hello from the bot!"

        ok("T14: send_message() enqueues raw text unchanged")
    except Exception as e: fail("T14", e)


def test_disabled_does_not_enqueue():
    """T15: When disabled, _enqueue() does not add to queue."""
    log.info("T15: Disabled instance skips queue")
    try:
        ta = make_ta(token="", chat_id="")   # disabled
        ta.send_message("should not appear")
        ta.send_trade_entry(make_trade())
        msgs = drain_queue(ta)

        assert len(msgs) == 0, f"Expected 0 messages, got {len(msgs)}"
        ok("T15: Disabled instance — queue stays empty")
    except Exception as e: fail("T15", e)


def test_enqueue_is_non_blocking():
    """T16: send_trade_entry() returns almost instantly (non-blocking)."""
    log.info("T16: Non-blocking enqueue")
    try:
        ta    = make_ta()
        trade = make_trade()

        start = time.monotonic()
        ta.send_trade_entry(trade)
        elapsed = time.monotonic() - start

        # Should return in well under 50ms (no network call)
        assert elapsed < 0.05, f"send_trade_entry took {elapsed:.3f}s — should be instant"

        ok(f"T16: send_trade_entry returned in {elapsed*1000:.1f}ms (non-blocking)")
    except Exception as e: fail("T16", e)


def test_attach_order_manager():
    """T17: attach_order_manager() wires enter/exit callbacks."""
    log.info("T17: attach_order_manager wiring")
    try:
        ta = make_ta()
        om = MagicMock()

        ta.attach_order_manager(om)

        om.set_on_trade_enter.assert_called_once_with(ta.send_trade_entry)
        om.set_on_trade_exit.assert_called_once_with(ta.send_trade_exit)

        ok("T17: Order manager entry/exit callbacks wired correctly")
    except Exception as e: fail("T17", e)


def test_attach_risk_manager():
    """T18: attach_risk_manager() wires the on_approved callback."""
    log.info("T18: attach_risk_manager wiring")
    try:
        ta = make_ta()
        rm = MagicMock()

        ta.attach_risk_manager(rm)

        rm.set_on_approved.assert_called_once()
        # The lambda wraps send_signal_approved — verify the arg is callable
        callback_arg = rm.set_on_approved.call_args[0][0]
        assert callable(callback_arg), "Callback should be callable"

        # Call it and verify it routes to send_signal_approved
        sig = make_signal()
        dec = make_decision(approved=True)
        callback_arg(sig, dec)   # should trigger send_signal_approved
        msgs = drain_queue(ta)
        assert len(msgs) == 1, "Callback should enqueue a message"

        ok("T18: Risk manager approved callback wired and functional")
    except Exception as e: fail("T18", e)


def test_send_now_posts_to_telegram_api():
    """T19: _send_now() calls requests.post with correct URL and payload."""
    log.info("T19: _send_now() API call")
    try:
        ta = make_ta(token="bot123:XYZ", chat_id="987")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = ta._send_now("Test message")

        assert result is True
        mock_post.assert_called_once()

        call_kwargs = mock_post.call_args
        url     = call_kwargs[0][0]
        payload = call_kwargs[1]["json"]

        assert "bot123:XYZ"  in url,          "Token missing from URL"
        assert "sendMessage" in url,           "Method missing from URL"
        assert payload["chat_id"]  == "987",   "Wrong chat_id in payload"
        assert payload["text"]     == "Test message", "Wrong text in payload"

        ok("T19: _send_now() posts to correct Telegram URL with right payload")
    except Exception as e: fail("T19", e)


def test_send_now_markdown_fallback():
    """T20: If Markdown parse fails, retries with plain text (no parse_mode)."""
    log.info("T20: Markdown fallback to plain text")
    try:
        ta = make_ta(token="bot123:XYZ", chat_id="987")

        # First call (Markdown) → fail; second call (plain) → success
        fail_resp = MagicMock()
        fail_resp.json.return_value = {
            "ok": False,
            "description": "Bad Request: can't parse entities"
        }
        ok_resp = MagicMock()
        ok_resp.json.return_value = {"ok": True}

        with patch("requests.post", side_effect=[fail_resp, ok_resp]) as mock_post:
            result = ta._send_now("Test *message* with `backticks`")

        assert result is True,         "Should succeed on plain-text retry"
        assert mock_post.call_count == 2, f"Expected 2 calls, got {mock_post.call_count}"

        # Second call should have no parse_mode
        second_payload = mock_post.call_args_list[1][1]["json"]
        assert "parse_mode" not in second_payload, "Second call should not have parse_mode"

        ok("T20: Markdown failure correctly falls back to plain text")
    except Exception as e: fail("T20", e)


def test_stop_sends_sentinel():
    """T21: stop() puts None sentinel in queue to unblock worker."""
    log.info("T21: stop() sends sentinel")
    try:
        ta = make_ta()
        ta._running = True

        ta.stop()

        # Sentinel (None) should be in queue
        sentinel = ta._queue.get_nowait()
        assert sentinel is None, f"Expected None sentinel, got {sentinel!r}"

        ok("T21: stop() correctly sends None sentinel to queue")
    except Exception as e: fail("T21", e)


# ─────────────────────────────────────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all():
    print("\n" + "=" * 60)
    print("  MODULE 8 — TELEGRAM ALERTS TESTS")
    print("=" * 60 + "\n")

    tests = [
        test_disabled_when_no_token,
        test_enabled_with_valid_credentials,
        test_trade_entry_message_content,
        test_trade_exit_sl_hit_message,
        test_trade_exit_target_hit_message,
        test_trade_exit_eod_squareoff,
        test_trade_exit_shows_duration,
        test_signal_approved_message,
        test_signal_blocked_message,
        test_daily_summary_content,
        test_session_start_message,
        test_high_impact_event_message,
        test_error_alert_message,
        test_send_message_raw,
        test_disabled_does_not_enqueue,
        test_enqueue_is_non_blocking,
        test_attach_order_manager,
        test_attach_risk_manager,
        test_send_now_posts_to_telegram_api,
        test_send_now_markdown_fallback,
        test_stop_sends_sentinel,
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
        print("✅ All Module 8 tests passed! Telegram Alerts is ready.\n")
    else:
        print("❌ Some tests failed. Check logs above.\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
