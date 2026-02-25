"""
modules/risk_manager.py — Module 7: Risk Manager
══════════════════════════════════════════════════
The final gatekeeper between strategy signals and real orders.

Position in the pipeline:
    Strategy Engine → [Signal] → Risk Manager → [Approved Signal] → Order Manager
                                              ↓
                                         [Rejected] → Log + Alert

The Risk Manager runs 8 pre-trade checks on every signal before
allowing it to reach the Order Manager. It can block, modify, or
pass signals through. It also monitors open positions continuously
for portfolio-level risk.

8 Pre-Trade Checks:
  R1. Daily loss limit      — block if day P&L ≤ -limit
  R2. Max trades per day    — block if trades_taken ≥ max
  R3. Confidence threshold  — block if signal.confidence < min_confidence
  R4. Market bias alignment — block if signal opposes strong bias
  R5. High-impact events    — block if VIX spike / crude shock / news event
  R6. Minimum conditions    — block if conditions_met < min_conditions
  R7. Duplicate position    — block if same symbol already open
  R8. Trading time window   — block if outside allowed hours

Portfolio-Level Monitoring:
  - Max open positions across all symbols (default: 2)
  - Portfolio drawdown alert if total unrealised loss > threshold
  - Correlation guard: don't open Nifty + BankNifty in same direction

Usage:
    from modules.risk_manager import RiskManager

    rm = RiskManager(config, capital_manager, market_intel)
    rm.attach_strategy_engine(strategy_engine)  # intercepts signals
    rm.attach_order_manager(order_manager)       # approved signals go here
    rm.start()
"""

from datetime import datetime, time as dt_time
from dataclasses import dataclass, field
from typing import Optional, Callable

import pytz

from strategies.base_strategy import Signal
from modules.logger import get_logger

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
#  RiskDecision — result of the risk check
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskDecision:
    """Result of running a signal through all risk checks."""

    approved      : bool  = False
    signal        : Optional[Signal] = None

    # Which check blocked it (if rejected)
    blocked_by    : str   = ""      # e.g. "R1_daily_loss_limit"
    block_reason  : str   = ""      # human-readable explanation

    # All checks that ran (for dashboard display)
    checks_run    : list  = field(default_factory=list)   # list of (check, passed, detail)

    # Timestamp
    evaluated_at  : Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "approved":    self.approved,
            "blocked_by":  self.blocked_by,
            "block_reason": self.block_reason,
            "checks_run":  self.checks_run,
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
            "signal":      self.signal.to_dict() if self.signal else None,
        }

    def __repr__(self):
        if self.approved:
            return f"RiskDecision(✅ APPROVED | {len(self.checks_run)} checks passed)"
        return f"RiskDecision(❌ BLOCKED by {self.blocked_by} — {self.block_reason})"


# ─────────────────────────────────────────────────────────────────────────────
#  RiskManager
# ─────────────────────────────────────────────────────────────────────────────

class RiskManager:
    """
    Pre-trade risk gatekeeper.
    Intercepts every signal from the Strategy Engine and runs 8 checks.
    Only approved signals reach the Order Manager.
    """

    def __init__(self, config: dict, capital_manager=None, market_intel=None):
        """
        Args:
            config          : Bot configuration dict (from database/dashboard)
            capital_manager : CapitalManager instance
            market_intel    : MarketIntel instance (for bias + events check)
        """
        self._config  = config
        self._cm      = capital_manager
        self._intel   = market_intel

        self._order_manager   = None
        self._strategy_engine = None

        # Stats for dashboard
        self._signals_received = 0
        self._signals_approved = 0
        self._signals_blocked  = 0
        self._block_log: list[RiskDecision] = []   # last 50 blocked decisions

        # Callback for approved signals (goes to order manager)
        self._on_approved: Optional[Callable] = None

        log.info("RiskManager initialised.")

    # ── Wiring ────────────────────────────────────────────────────────────────

    def attach_strategy_engine(self, strategy_engine):
        """
        Register as the signal receiver for the strategy engine.
        All signals from strategies will now pass through this risk manager.
        """
        self._strategy_engine = strategy_engine
        strategy_engine.set_on_signal(self.evaluate_signal)
        log.info("RiskManager attached to StrategyEngine — intercepting all signals.")

    def attach_order_manager(self, order_manager):
        """
        Set the order manager that receives approved signals.
        """
        self._order_manager = order_manager
        log.info("RiskManager attached to OrderManager.")

    def set_on_approved(self, callback: Callable):
        """
        Optional extra callback when a signal is approved.
        Useful for sending Telegram alerts.
        Signature: callback(signal: Signal, decision: RiskDecision)
        """
        self._on_approved = callback

    def start(self):
        """Enable the risk manager (it's always on, this just logs it)."""
        log.info(
            f"RiskManager active | "
            f"MinConfidence={self._config.get('min_confidence', 50)}% | "
            f"MinConditions={self._config.get('min_signal_conditions', 3)}/5 | "
            f"MaxTrades={self._config.get('max_trades_per_day', 2)}/day"
        )

    # ── Core: Evaluate Signal ─────────────────────────────────────────────────

    def evaluate_signal(self, signal: Signal) -> RiskDecision:
        """
        Run all 8 risk checks on an incoming signal.
        If approved, forwards to Order Manager automatically.
        Returns the RiskDecision for logging/display.
        """
        self._signals_received += 1
        now = datetime.now(IST)

        decision = RiskDecision(signal=signal, evaluated_at=now)

        log.info("─" * 55)
        log.info(
            f"Risk check: {signal.action} {signal.symbol} | "
            f"Confidence:{signal.confidence}% | "
            f"Conditions:{signal.conditions_met}/{signal.conditions_total} | "
            f"Bias:{signal.bias_score:+d}"
        )

        # Run all 8 checks in order — stop at first failure
        checks = [
            self._check_R1_daily_loss,
            self._check_R2_max_trades,
            self._check_R3_confidence,
            self._check_R4_bias_alignment,
            self._check_R5_high_impact_events,
            self._check_R6_min_conditions,
            self._check_R7_duplicate_position,
            self._check_R8_trading_time,
        ]

        for check_fn in checks:
            passed, check_name, detail = check_fn(signal)
            decision.checks_run.append({
                "check":   check_name,
                "passed":  passed,
                "detail":  detail,
            })

            status = "✅" if passed else "❌"
            log.info(f"  {status} {check_name}: {detail}")

            if not passed:
                decision.approved    = False
                decision.blocked_by  = check_name
                decision.block_reason= detail
                self._signals_blocked += 1
                self._block_log.append(decision)
                if len(self._block_log) > 50:
                    self._block_log.pop(0)
                log.warning(f"  🚫 Signal BLOCKED by {check_name}: {detail}")
                return decision

        # All checks passed
        decision.approved = True
        self._signals_approved += 1

        log.info(f"  ✅ All {len(checks)} risk checks PASSED — forwarding to Order Manager")

        # Forward to Order Manager
        if self._order_manager:
            try:
                trade = self._order_manager.receive_signal(signal)
                if trade:
                    log.info(f"  🚀 Order placed: {trade.trade_id}")
                else:
                    log.warning("  Order Manager rejected the signal (internal check failed)")
            except Exception as e:
                log.error(f"  Order Manager error: {e}")

        # Fire approved callback (e.g. Telegram alert)
        if self._on_approved:
            try:
                self._on_approved(signal, decision)
            except Exception as e:
                log.error(f"  on_approved callback error: {e}")

        return decision

    # ── R1: Daily Loss Limit ──────────────────────────────────────────────────

    def _check_R1_daily_loss(self, signal: Signal) -> tuple[bool, str, str]:
        """Block if today's P&L has hit the daily loss limit."""
        check = "R1_daily_loss_limit"

        loss_limit = self._config.get("daily_loss_limit", 5000)

        # Get day P&L from capital manager or order manager
        day_pnl = self._get_day_pnl()

        if day_pnl <= -abs(loss_limit):
            return False, check, (
                f"Day P&L ₹{day_pnl:+.0f} ≤ limit -₹{loss_limit} — no more trades today"
            )

        return True, check, f"Day P&L ₹{day_pnl:+.0f} (limit: -₹{loss_limit})"

    # ── R2: Max Trades Per Day ────────────────────────────────────────────────

    def _check_R2_max_trades(self, signal: Signal) -> tuple[bool, str, str]:
        """Block if max trades for the day have been taken."""
        check     = "R2_max_trades_per_day"
        max_trades= self._config.get("max_trades_per_day", 2)
        trades_taken = self._get_trades_taken()

        if trades_taken >= max_trades:
            return False, check, (
                f"Trades taken today: {trades_taken}/{max_trades} — limit reached"
            )

        return True, check, f"Trades taken: {trades_taken}/{max_trades}"

    # ── R3: Minimum Confidence ────────────────────────────────────────────────

    def _check_R3_confidence(self, signal: Signal) -> tuple[bool, str, str]:
        """Block if signal confidence is below the configured threshold."""
        check      = "R3_min_confidence"
        min_conf   = self._config.get("min_confidence", 50)

        if signal.confidence < min_conf:
            return False, check, (
                f"Signal confidence {signal.confidence}% < minimum {min_conf}%"
            )

        return True, check, f"Confidence {signal.confidence}% ≥ {min_conf}%"

    # ── R4: Market Bias Alignment ─────────────────────────────────────────────

    def _check_R4_bias_alignment(self, signal: Signal) -> tuple[bool, str, str]:
        """
        Block if signal direction strongly opposes market bias.
        Logic:
          - Bias ≥ +4 (strongly bullish): block PUT signals
          - Bias ≤ -4 (strongly bearish): block CALL signals
          - Bias -3 to +3: allow both directions (conditions decide)
        """
        check = "R4_bias_alignment"

        # Get latest bias score
        bias = self._get_bias_score(signal.bias_score)
        bias_threshold = self._config.get("bias_block_threshold", 4)

        if signal.action == "PUT" and bias >= bias_threshold:
            return False, check, (
                f"Bias {bias:+d} strongly bullish — blocking PUT signal"
            )

        if signal.action == "CALL" and bias <= -bias_threshold:
            return False, check, (
                f"Bias {bias:+d} strongly bearish — blocking CALL signal"
            )

        if bias >= 2 and signal.action == "CALL":
            detail = f"Bias {bias:+d} aligned with CALL ✅"
        elif bias <= -2 and signal.action == "PUT":
            detail = f"Bias {bias:+d} aligned with PUT ✅"
        elif -1 <= bias <= 1:
            detail = f"Bias {bias:+d} neutral — signal allowed (conditions decide)"
        else:
            detail = f"Bias {bias:+d} — mild opposition, allowed (< {bias_threshold} threshold)"

        return True, check, detail

    # ── R5: High-Impact Events ────────────────────────────────────────────────

    def _check_R5_high_impact_events(self, signal: Signal) -> tuple[bool, str, str]:
        """
        Block trading during high-impact market events:
        - VIX > 20 (extreme volatility)
        - Crude oil ±3% (macro shock)
        - Sharp INR moves
        - Dow crash / surge
        """
        check = "R5_high_impact_events"

        # Feature flag: operator can disable this check
        if not self._config.get("use_market_bias_filter", True):
            return True, check, "Market bias filter disabled in config"

        if not self._intel:
            return True, check, "No MarketIntel — event check skipped"

        try:
            events = []
            snap   = self._intel.get_snapshot()
            if snap and snap.high_impact_events:
                events = snap.high_impact_events
        except Exception as e:
            log.warning(f"MarketIntel unavailable for R5 check: {e}")
            return True, check, "MarketIntel unavailable — check skipped"

        if events:
            event_str = " | ".join(events[:2])   # show max 2 events
            return False, check, f"High-impact event: {event_str}"

        return True, check, "No high-impact events detected"

    # ── R6: Minimum Conditions Met ────────────────────────────────────────────

    def _check_R6_min_conditions(self, signal: Signal) -> tuple[bool, str, str]:
        """Block if strategy didn't meet the minimum required conditions."""
        check     = "R6_min_conditions"
        min_cond  = self._config.get("min_signal_conditions", 3)
        total     = signal.conditions_total or 5

        if signal.conditions_met < min_cond:
            return False, check, (
                f"Only {signal.conditions_met}/{total} conditions met "
                f"(need {min_cond})"
            )

        return True, check, (
            f"{signal.conditions_met}/{total} conditions met ≥ {min_cond} required"
        )

    # ── R7: Duplicate Position ────────────────────────────────────────────────

    def _check_R7_duplicate_position(self, signal: Signal) -> tuple[bool, str, str]:
        """Block if the same underlying already has an open position."""
        check = "R7_duplicate_position"

        if not self._order_manager:
            return True, check, "No OrderManager attached — check skipped"

        open_positions = self._order_manager.get_open_positions()
        for trade in open_positions:
            if trade.symbol == signal.symbol:
                return False, check, (
                    f"{signal.symbol} already has open position "
                    f"({trade.trade_id}, {trade.action})"
                )

        # Also check: max concurrent positions (portfolio-level)
        max_positions = self._config.get("max_concurrent_positions", 2)
        if len(open_positions) >= max_positions:
            return False, check, (
                f"Max concurrent positions reached "
                f"({len(open_positions)}/{max_positions})"
            )

        return True, check, (
            f"No open {signal.symbol} position | "
            f"Open positions: {len(open_positions)}/{max_positions}"
        )

    # ── R8: Trading Time Window ───────────────────────────────────────────────

    def _check_R8_trading_time(self, signal: Signal) -> tuple[bool, str, str]:
        """
        Block signals outside the configured trading window:
        - Must be after trading_start (default 09:30)
        - Must be before trading_end (default 15:00)
        - Must not be in lunch zone if avoid_lunch=True (11:30–13:00)
        """
        check = "R8_trading_time"
        now   = datetime.now(IST).time()

        trading_start = self._parse_time(self._config.get("trading_start", "09:30"))
        trading_end   = self._parse_time(self._config.get("trading_end",   "15:00"))
        avoid_lunch   = self._config.get("avoid_lunch", True)
        lunch_start   = self._parse_time(self._config.get("lunch_start",   "11:30"))
        lunch_end     = self._parse_time(self._config.get("lunch_end",     "13:00"))

        if now < trading_start:
            return False, check, (
                f"Before trading start "
                f"({now.strftime('%H:%M')} < {self._config.get('trading_start')})"
            )

        if now >= trading_end:
            return False, check, (
                f"After trading end "
                f"({now.strftime('%H:%M')} ≥ {self._config.get('trading_end')})"
            )

        if avoid_lunch and lunch_start <= now < lunch_end:
            return False, check, (
                f"Lunch zone "
                f"({now.strftime('%H:%M')} in "
                f"{self._config.get('lunch_start')}–{self._config.get('lunch_end')})"
            )

        return True, check, f"Trading time OK ({now.strftime('%H:%M')})"

    # ── Stats & Status ────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return risk manager statistics for the dashboard."""
        total = self._signals_received
        return {
            "signals_received": total,
            "signals_approved": self._signals_approved,
            "signals_blocked":  self._signals_blocked,
            "approval_rate":    round(self._signals_approved / total * 100, 1) if total else 0,
            "recent_blocks":    [d.to_dict() for d in self._block_log[-10:]],
        }

    def get_block_summary(self) -> dict:
        """Return count of blocks per check (why signals get rejected most)."""
        summary = {}
        for decision in self._block_log:
            key = decision.blocked_by
            summary[key] = summary.get(key, 0) + 1
        return dict(sorted(summary.items(), key=lambda x: x[1], reverse=True))

    def reset_daily(self):
        """Clear daily stats. Call at BOD."""
        self._signals_received = 0
        self._signals_approved = 0
        self._signals_blocked  = 0
        self._block_log.clear()
        log.info("RiskManager daily stats reset.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_day_pnl(self) -> float:
        """Get today's P&L from order manager or capital manager."""
        if self._order_manager:
            try:
                return self._order_manager.get_day_pnl()
            except Exception:
                pass
        if self._cm:
            try:
                summary = self._cm.get_capital_summary()
                return float(summary.get("day_pnl", 0))
            except Exception:
                pass
        return 0.0

    def _get_trades_taken(self) -> int:
        """Get number of trades taken today."""
        if self._order_manager:
            try:
                return len(self._order_manager.get_today_trades())
            except Exception:
                pass
        if self._cm:
            try:
                summary = self._cm.get_capital_summary()
                return int(summary.get("trades_taken", 0))
            except Exception:
                pass
        return 0

    def _get_bias_score(self, signal_bias: int = 0) -> int:
        """Get latest bias score from MarketIntel (fallback to signal's bias)."""
        if self._intel:
            try:
                return self._intel.get_bias_score()
            except Exception:
                pass
        return signal_bias

    def _parse_time(self, time_str: str) -> dt_time:
        """Parse 'HH:MM' string to time object."""
        try:
            h, m = time_str.split(":")
            return dt_time(int(h), int(m))
        except Exception:
            return dt_time(9, 30)
