"""
modules/telegram_alerts.py — Module 8: Telegram Alerts
════════════════════════════════════════════════════════
Sends real-time alerts to your Telegram for every important bot event:
  📊 Market bias snapshot at session start
  🎯 Signal generated (with all 5 conditions)
  ✅ Trade entered (symbol, strike, entry, SL, target)
  🛑 SL hit (exit price, loss amount)
  💰 Target hit (exit price, profit amount)
  ⏰ EOD square-off (all positions closed summary)
  🚫 Signal blocked by Risk Manager (which check failed)
  ⚠️  High-impact event detected
  📈 Daily P&L summary at session end
  ❌ Critical error (if bot crashes or loses connection)

Setup:
  1. Message @BotFather on Telegram → /newbot → get BOT_TOKEN
  2. Message @userinfobot → get your CHAT_ID
  3. Set environment variables:
       set TELEGRAM_BOT_TOKEN=your_token_here
       set TELEGRAM_CHAT_ID=your_chat_id_here

Usage:
    from modules.telegram_alerts import TelegramAlerts

    ta = TelegramAlerts(bot_token, chat_id)
    ta.send_trade_entry(trade)
    ta.send_trade_exit(trade)
    ta.send_signal(signal, decision)
    ta.send_daily_summary(trades, day_pnl)

    # Wire up automatically:
    ta.attach_order_manager(order_manager)    # auto-sends entry/exit
    ta.attach_risk_manager(risk_manager)      # auto-sends approvals + blocks
"""

import queue
import threading
import time
from datetime import datetime
from typing import Optional

import pytz
import requests

from modules.logger import get_logger

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ─────────────────────────────────────────────────────────────────────────────
#  TelegramAlerts
# ─────────────────────────────────────────────────────────────────────────────

class TelegramAlerts:
    """
    Sends formatted Telegram messages for all bot events.
    Uses a background queue so alerts never block the trading thread.
    """

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        """
        Args:
            bot_token : Telegram bot token from @BotFather
            chat_id   : Your Telegram chat/user ID
            enabled   : Set False to disable all alerts without changing code
        """
        self._token   = bot_token
        self._chat_id = str(chat_id)
        self._enabled = enabled and bool(bot_token) and bool(chat_id)

        # Background send queue — alerts never block trading
        self._queue  = queue.Queue()
        self._worker = threading.Thread(
            target=self._send_worker,
            daemon=True,
            name="TelegramWorker"
        )
        self._running = False

        if self._enabled:
            self._start_worker()
            log.info(f"TelegramAlerts ready — chat_id: {self._chat_id}")
        else:
            if not bot_token:
                log.warning("TelegramAlerts disabled — TELEGRAM_BOT_TOKEN not set")
            elif not chat_id:
                log.warning("TelegramAlerts disabled — TELEGRAM_CHAT_ID not set")
            else:
                log.info("TelegramAlerts disabled by config")

    # ── Wiring ────────────────────────────────────────────────────────────────

    def attach_order_manager(self, order_manager):
        """Auto-send entry and exit alerts when trades happen."""
        order_manager.set_on_trade_enter(self.send_trade_entry)
        order_manager.set_on_trade_exit(self.send_trade_exit)
        log.info("TelegramAlerts attached to OrderManager.")

    def attach_risk_manager(self, risk_manager):
        """Auto-send signal alerts when risk manager approves signals."""
        risk_manager.set_on_approved(
            lambda signal, decision: self.send_signal_approved(signal, decision)
        )
        log.info("TelegramAlerts attached to RiskManager.")

    # ── Alert Methods ─────────────────────────────────────────────────────────

    def send_trade_entry(self, trade):
        """
        🚀 Trade Entry Alert
        Sent when a position is opened.
        """
        mode_tag = "📝 PAPER" if trade.mode == "paper" else "💰 LIVE"
        action_emoji = "📈" if trade.action == "CALL" else "📉"

        msg = (
            f"{action_emoji} *TRADE ENTERED* {mode_tag}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 *{trade.option_symbol}*\n"
            f"Direction : `{trade.action}`\n"
            f"Entry     : ₹`{trade.entry_price:.1f}`\n"
            f"Stop Loss : ₹`{trade.sl_price:.1f}` "
            f"(-{abs(trade.sl_price - trade.entry_price) / trade.entry_price * 100:.0f}%)\n"
            f"Target    : ₹`{trade.target_price:.1f}` "
            f"(+{abs(trade.target_price - trade.entry_price) / trade.entry_price * 100:.0f}%)\n"
            f"Lots      : `{trade.lots}` × {trade.lot_size} = `{trade.quantity}` qty\n"
            f"Capital   : ₹`{trade.entry_price * trade.quantity:,.0f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Confidence: `{trade.confidence}%` | "
            f"Conditions: `{trade.conditions_met}/5`\n"
            f"🆔 `{trade.trade_id}`  "
            f"⏰ `{datetime.now(IST).strftime('%H:%M:%S')}`"
        )
        self._enqueue(msg)

    def send_trade_exit(self, trade):
        """
        ✅/❌ Trade Exit Alert
        Sent when a position is closed (SL hit, target hit, or EOD).
        """
        pnl = trade.net_pnl
        is_profit = pnl >= 0

        exit_emoji = {
            "sl_hit":     "🛑",
            "target_hit": "🎯",
            "timed_out":  "⏰",
        }.get(trade.state.value if hasattr(trade.state, 'value') else str(trade.state), "📤")

        pnl_emoji  = "💰" if is_profit else "💸"
        pnl_sign   = "+" if is_profit else ""
        mode_tag   = "📝 PAPER" if trade.mode == "paper" else "💰 LIVE"

        reason_labels = {
            "SL hit":     "Stop Loss Hit",
            "Target hit": "Target Reached",
            "EOD square-off": "EOD Square-off",
        }
        reason = reason_labels.get(trade.exit_reason, trade.exit_reason or "Closed")

        # Duration
        duration = ""
        if trade.entry_time and trade.exit_time:
            mins = int((trade.exit_time - trade.entry_time).total_seconds() / 60)
            duration = f" ({mins}m)"

        msg = (
            f"{exit_emoji} *TRADE CLOSED* {mode_tag}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 *{trade.option_symbol}*\n"
            f"Reason    : `{reason}`\n"
            f"Entry     : ₹`{trade.entry_price:.1f}`\n"
            f"Exit      : ₹`{trade.exit_price:.1f}`{duration}\n"
            f"Move      : `{(trade.exit_price - trade.entry_price) / trade.entry_price * 100:+.1f}%`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{pnl_emoji} *Net P&L: ₹`{pnl_sign}{pnl:,.0f}`*\n"
            f"Gross: ₹`{pnl_sign}{trade.gross_pnl:,.0f}` | "
            f"Charges: ₹`{trade.charges:.0f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 `{trade.trade_id}`  "
            f"⏰ `{datetime.now(IST).strftime('%H:%M:%S')}`"
        )
        self._enqueue(msg)

    def send_signal_approved(self, signal, decision):
        """
        🎯 Signal Approved by Risk Manager
        Sent just before order is placed.
        """
        action_emoji = "📈" if signal.action == "CALL" else "📉"
        entry_tag    = "🔄 Pullback" if signal.entry_type == "pullback" else "💥 Breakout"

        msg = (
            f"{action_emoji} *SIGNAL APPROVED*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Symbol  : *{signal.symbol}*\n"
            f"Action  : `{signal.action}`  {entry_tag}\n"
            f"Strike  : `{signal.suggested_strike}`\n"
            f"Bias    : `{signal.bias_score:+d}` "
            f"({'Bullish' if signal.bias_score > 0 else 'Bearish' if signal.bias_score < 0 else 'Neutral'})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Conditions met:\n"
        )

        for detail in signal.conditions_detail:
            tick = "✅" if detail.startswith("✅") else "❌"
            clean = detail.replace("✅ ", "").replace("❌ ", "")
            msg += f"  {tick} `{clean}`\n"

        msg += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Confidence : `{signal.confidence}%` | "
            f"Conditions : `{signal.conditions_met}/{signal.conditions_total}`\n"
            f"⏰ `{datetime.now(IST).strftime('%H:%M:%S')}`"
        )
        self._enqueue(msg)

    def send_signal_blocked(self, signal, decision):
        """
        🚫 Signal Blocked by Risk Manager
        Sent when a signal fails a risk check.
        """
        check_map = {
            "R1_daily_loss_limit":    "R1 Daily Loss Limit",
            "R2_max_trades_per_day":  "R2 Max Trades/Day",
            "R3_min_confidence":      "R3 Low Confidence",
            "R4_bias_alignment":      "R4 Bias Misalignment",
            "R5_high_impact_events":  "R5 High-Impact Event",
            "R6_min_conditions":      "R6 Insufficient Conditions",
            "R7_duplicate_position":  "R7 Duplicate Position",
            "R8_trading_time":        "R8 Outside Trading Hours",
        }
        check_name = check_map.get(decision.blocked_by, decision.blocked_by)
        action_emoji = "📈" if signal.action == "CALL" else "📉"

        msg = (
            f"🚫 *SIGNAL BLOCKED*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{action_emoji} {signal.action} {signal.symbol} "
            f"| `{signal.suggested_strike}`\n"
            f"Blocked by : `{check_name}`\n"
            f"Reason     : {decision.block_reason}\n"
            f"⏰ `{datetime.now(IST).strftime('%H:%M:%S')}`"
        )
        self._enqueue(msg)

    def send_market_bias(self, snapshot):
        """
        📊 Market Bias Snapshot
        Sent at session start and after each intel refresh.
        """
        score = snapshot.bias_score
        label = snapshot.bias_label

        bias_emoji = {
            range(6,  11): "🟢🟢",
            range(2,  6):  "🟢",
            range(-1, 2):  "🟡",
            range(-5, 0):  "🔴",
        }
        emoji = "🔴🔴"
        for r, e in bias_emoji.items():
            if score in r:
                emoji = e
                break

        # Format global indices line
        global_line = (
            f"Dow `{snapshot.dow_change_pct:+.1f}%` | "
            f"Nikkei `{snapshot.nikkei_change_pct:+.1f}%`"
        )
        fii_line = (
            f"FII ₹`{snapshot.fii_net_buy:+,.0f}`Cr | "
            f"DII ₹`{snapshot.dii_net_buy:+,.0f}`Cr"
        )

        msg = (
            f"📊 *MARKET BIAS UPDATE*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{emoji} *{label.upper()}* (`{score:+d}`)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🇮🇳 Nifty  : `{snapshot.nifty_value:,.0f}` "
            f"(`{snapshot.nifty_change_pct:+.2f}%`)\n"
            f"🏦 BankNifty: `{snapshot.banknifty_value:,.0f}` "
            f"(`{snapshot.banknifty_change_pct:+.2f}%`)\n"
            f"📉 VIX    : `{snapshot.india_vix:.2f}`\n"
            f"🌏 {global_line}\n"
            f"💹 {fii_line}\n"
            f"💱 USD/INR: `{snapshot.usdinr:.2f}`\n"
        )

        if snapshot.high_impact_events:
            msg += f"━━━━━━━━━━━━━━━━━━━━\n"
            msg += f"⚠️ *HIGH-IMPACT EVENTS:*\n"
            for event in snapshot.high_impact_events:
                msg += f"  • {event}\n"

        if snapshot.bias_reasoning:
            msg += f"━━━━━━━━━━━━━━━━━━━━\n"
            msg += f"*Reasoning:*\n"
            for reason in snapshot.bias_reasoning[:4]:   # max 4 lines
                msg += f"  `{reason}`\n"

        msg += f"⏰ `{datetime.now(IST).strftime('%H:%M:%S IST')}`"
        self._enqueue(msg)

    def send_session_start(self, capital_summary: dict):
        """
        🌅 Session Start Alert
        Sent at bot startup with account balance summary.
        """
        msg = (
            f"🌅 *TRADING SESSION STARTED*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 `{datetime.now(IST).strftime('%d %b %Y')}`  "
            f"⏰ `{datetime.now(IST).strftime('%H:%M IST')}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance    : ₹`{capital_summary.get('available_balance', 0):,.0f}`\n"
            f"📊 Usable     : ₹`{capital_summary.get('usable_capital', 0):,.0f}` "
            f"({capital_summary.get('funds_pct', 80)}%)\n"
            f"🎯 Per Trade  : ₹`{capital_summary.get('per_trade_limit', 0):,.0f}`\n"
            f"🚦 Max Trades : `{capital_summary.get('max_trades_today', 2)}`/day\n"
            f"🛡 Loss Limit : ₹`{capital_summary.get('daily_loss_limit', 5000):,.0f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Bot is live and watching the market!"
        )
        self._enqueue(msg)

    def send_daily_summary(self, trades: list, day_pnl: float):
        """
        📈 End-of-Day Summary
        Sent at session end with full P&L breakdown.
        """
        total  = len(trades)
        wins   = sum(1 for t in trades if t.net_pnl > 0)
        losses = sum(1 for t in trades if t.net_pnl <= 0)
        win_rate = round(wins / total * 100) if total else 0

        pnl_emoji = "📈" if day_pnl >= 0 else "📉"
        sign      = "+" if day_pnl >= 0 else ""

        msg = (
            f"{pnl_emoji} *END OF DAY SUMMARY*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 `{datetime.now(IST).strftime('%d %b %Y')}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Net P&L : ₹`{sign}{day_pnl:,.0f}`*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Trades : `{total}` total | "
            f"`{wins}` wins | `{losses}` losses\n"
            f"Win Rate: `{win_rate}%`\n"
        )

        if trades:
            msg += f"━━━━━━━━━━━━━━━━━━━━\n*Trade Log:*\n"
            for t in trades:
                sign_t = "+" if t.net_pnl >= 0 else ""
                state = getattr(t.state, 'value', str(t.state))
                icon  = "✅" if t.net_pnl >= 0 else "❌"
                msg  += (
                    f"{icon} `{t.option_symbol}` "
                    f"₹`{sign_t}{t.net_pnl:,.0f}` "
                    f"[{state.replace('_',' ').title()}]\n"
                )

        msg += f"━━━━━━━━━━━━━━━━━━━━\n⏰ `{datetime.now(IST).strftime('%H:%M IST')}` Session ended"
        self._enqueue(msg)

    def send_high_impact_event(self, events: list):
        """⚠️ High-impact event warning."""
        event_lines = "\n".join(f"  • {e}" for e in events)
        msg = (
            f"⚠️ *HIGH-IMPACT EVENT DETECTED*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{event_lines}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ Trading paused until event resolves\n"
            f"⏰ `{datetime.now(IST).strftime('%H:%M:%S IST')}`"
        )
        self._enqueue(msg)

    def send_error(self, error_msg: str, context: str = ""):
        """❌ Critical error alert."""
        msg = (
            f"❌ *BOT ERROR*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"`{error_msg[:300]}`\n"
        )
        if context:
            msg += f"Context: `{context[:100]}`\n"
        msg += f"⏰ `{datetime.now(IST).strftime('%H:%M:%S IST')}`"
        self._enqueue(msg)

    def send_message(self, text: str):
        """Send a raw custom message."""
        self._enqueue(text)

    # ── Connection Test ───────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """
        Send a test message to verify bot token and chat ID are correct.
        Returns True if message was delivered successfully.
        """
        if not self._enabled:
            log.warning("Telegram not enabled — test skipped")
            return False

        msg = (
            f"🤖 *Algo Trading Bot Connected*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Telegram alerts are working!\n"
            f"⏰ `{datetime.now(IST).strftime('%d %b %Y %H:%M:%S IST')}`"
        )
        result = self._send_now(msg)
        if result:
            log.info("Telegram test message sent successfully ✅")
        else:
            log.error("Telegram test message FAILED ❌")
        return result

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ── Internal Queue & Worker ───────────────────────────────────────────────

    def _start_worker(self):
        """Start background thread that processes the send queue."""
        self._running = True
        self._worker.start()

    def stop(self):
        """Flush queue and stop the worker thread."""
        self._running = False
        self._queue.put(None)    # sentinel to unblock the worker
        if self._worker.is_alive():
            self._worker.join(timeout=5)
        log.info("TelegramAlerts worker stopped.")

    def _enqueue(self, message: str):
        """Add a message to the send queue (non-blocking)."""
        if not self._enabled:
            log.debug(f"[Telegram DISABLED] {message[:80]}...")
            return
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            log.warning("Telegram queue full — alert dropped")

    def _send_worker(self):
        """Background thread: drain queue and send messages with rate limiting."""
        while self._running:
            try:
                msg = self._queue.get(timeout=1)
                if msg is None:    # stop sentinel
                    break
                self._send_now(msg)
                time.sleep(0.5)   # Telegram rate limit: ~30 messages/sec, we do 2/sec
            except queue.Empty:
                continue
            except Exception as e:
                log.error(f"Telegram worker error: {e}")

    def _send_now(self, message: str) -> bool:
        """
        Actually send message to Telegram API.
        Uses MarkdownV2 parse_mode for bold/code formatting.
        Falls back to plain text if markdown fails.
        """
        for parse_mode in ("Markdown", None):
            try:
                url     = TELEGRAM_API.format(token=self._token, method="sendMessage")
                payload = {
                    "chat_id":    self._chat_id,
                    "text":       message,
                    "parse_mode": parse_mode,
                }
                # Remove None parse_mode from payload
                if parse_mode is None:
                    payload.pop("parse_mode")

                resp = requests.post(url, json=payload, timeout=10)
                data = resp.json()

                if data.get("ok"):
                    return True

                # If markdown failed, try plain text
                if parse_mode == "Markdown":
                    log.debug(f"Markdown send failed, trying plain text: {data.get('description')}")
                    continue

                log.warning(f"Telegram send failed: {data.get('description', 'unknown error')}")
                return False

            except requests.Timeout:
                log.warning("Telegram request timed out")
                return False
            except Exception as e:
                log.error(f"Telegram send error: {e}")
                return False

        return False
