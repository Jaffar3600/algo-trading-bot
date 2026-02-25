"""
modules/capital_manager.py — Module 2: Live Balance & Capital Manager
══════════════════════════════════════════════════════════════════════
Fetches LIVE account balance from Zerodha and calculates
per-trade capital based on dashboard configuration.

NO hardcoded capital assumptions. Everything is dynamic.

Usage:
    from modules.capital_manager import CapitalManager
    cm = CapitalManager(kite, db_config)

    summary = cm.get_capital_summary()
    # → {
    #     "available_balance": 75000,
    #     "usable_capital":    60000,   (80% of balance)
    #     "per_trade_limit":   15000,   (25% of usable)
    #     "daily_loss_limit":  5000,
    #     "trades_taken":      1,
    #     "day_pnl":          -800,
    #     "can_trade":         True,
    #     "reason":           "OK"
    #   }
"""

from datetime import date, datetime
from kiteconnect import KiteConnect

from modules.logger import get_logger

log = get_logger(__name__)


class CapitalManager:
    """
    Manages capital calculation and daily P&L tracking.
    All settings come from db_config (loaded from SQLite via dashboard).
    """

    def __init__(self, kite: KiteConnect, db_config: dict):
        """
        Args:
            kite      : Authenticated KiteConnect instance
            db_config : Settings dict loaded from database
                        (funds_to_use_pct, max_capital_per_trade_pct,
                         daily_loss_limit, daily_profit_target, etc.)
        """
        self._kite      = kite
        self._config    = db_config
        self._day_pnl   = 0.0      # Running P&L for today
        self._trades_taken = 0     # Trades placed today
        self._last_balance: dict | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_capital_summary(self) -> dict:
        """
        Core method. Fetches live balance and returns full capital summary.
        Call this before every trade decision.
        """
        try:
            balance = self._fetch_live_balance()
        except Exception as e:
            log.error(f"Failed to fetch live balance: {e}")
            return self._error_summary(str(e))

        available  = balance["available_cash"]
        funds_pct  = self._config.get("funds_to_use_pct", 80)
        trade_pct  = self._config.get("max_capital_per_trade_pct", 25)
        loss_limit = self._config.get("daily_loss_limit", 5000)
        profit_tgt = self._config.get("daily_profit_target", 0)

        usable_capital   = round(available * funds_pct / 100, 2)
        per_trade_limit  = round(usable_capital * trade_pct / 100, 2)

        # ── Can we trade? ─────────────────────────────────────────────────────
        can_trade = True
        reason    = "OK"

        if available <= 0:
            can_trade = False
            reason    = f"No available margin (balance: ₹{available})"

        elif self._day_pnl <= -abs(loss_limit):
            can_trade = False
            reason    = (
                f"Daily loss limit breached "
                f"(loss ₹{abs(self._day_pnl):.0f} >= limit ₹{loss_limit})"
            )

        elif profit_tgt > 0 and self._day_pnl >= profit_tgt:
            can_trade = False
            reason    = (
                f"Daily profit target reached "
                f"(profit ₹{self._day_pnl:.0f} >= target ₹{profit_tgt})"
            )

        elif self._trades_taken >= self._config.get("max_trades_per_day", 2):
            can_trade = False
            reason    = (
                f"Max trades per day reached "
                f"({self._trades_taken}/{self._config.get('max_trades_per_day', 2)})"
            )

        summary = {
            "timestamp":        datetime.now().isoformat(),
            "available_balance": available,
            "used_margin":       balance["used_margin"],
            "total_balance":     balance["total_balance"],
            "usable_capital":    usable_capital,
            "per_trade_limit":   per_trade_limit,
            "funds_pct":         funds_pct,
            "per_trade_pct":     trade_pct,
            "daily_loss_limit":  loss_limit,
            "daily_profit_target": profit_tgt,
            "day_pnl":           self._day_pnl,
            "trades_taken":      self._trades_taken,
            "max_trades_today":  self._config.get("max_trades_per_day", 2),
            "can_trade":         can_trade,
            "reason":            reason,
        }

        self._last_balance = summary
        self._log_summary(summary)
        return summary

    def record_trade_entry(self, capital_used: float):
        """Call this when a trade is entered. Updates internal counters."""
        self._trades_taken += 1
        log.info(
            f"Trade #{self._trades_taken} entered. "
            f"Capital used: ₹{capital_used:,.0f}"
        )

    def record_trade_exit(self, pnl: float):
        """Call this when a trade exits. Updates running P&L."""
        self._day_pnl += pnl
        sign = "+" if pnl >= 0 else ""
        log.info(
            f"Trade exited. P&L: {sign}₹{pnl:,.0f} | "
            f"Day P&L: {'+' if self._day_pnl >= 0 else ''}₹{self._day_pnl:,.0f}"
        )

    def reset_daily_counters(self):
        """Reset at start of each trading day."""
        self._day_pnl      = 0.0
        self._trades_taken = 0
        log.info("Daily counters reset.")

    def get_lot_quantity(self, option_premium: float) -> int:
        """
        Calculate how many lots to buy based on per-trade capital limit
        and option premium. Returns minimum 1, never 0.

        Args:
            option_premium : Current price of the option (e.g. ₹105)

        Returns:
            Number of lots (integer)
        """
        if self._last_balance is None:
            self.get_capital_summary()

        per_trade = self._last_balance["per_trade_limit"]
        lot_size  = self._config.get("lot_size", 50)   # Nifty = 50, BankNifty = 15

        cost_per_lot = option_premium * lot_size
        if cost_per_lot <= 0:
            return 1

        lots = int(per_trade // cost_per_lot)
        lots = max(1, lots)   # Always at least 1 lot

        log.info(
            f"Lot calc: ₹{per_trade:,.0f} capital ÷ "
            f"₹{cost_per_lot:,.0f}/lot = {lots} lot(s)"
        )
        return lots

    def get_last_summary(self) -> dict | None:
        """Returns the last fetched capital summary (no API call)."""
        return self._last_balance

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _fetch_live_balance(self) -> dict:
        """
        Fetches margins from Zerodha Kite API.
        Returns dict with available_cash, used_margin, total_balance.
        """
        log.info("Fetching live balance from Zerodha...")
        margins = self._kite.margins()

        # Kite returns equity and commodity segments
        equity = margins.get("equity", {})

        available_cash = float(equity.get("available", {}).get("cash", 0))
        used_margin    = float(equity.get("utilised", {}).get("debits", 0))
        total_balance  = available_cash + used_margin

        log.info(
            f"Balance — Available: ₹{available_cash:,.0f} | "
            f"Used Margin: ₹{used_margin:,.0f} | "
            f"Total: ₹{total_balance:,.0f}"
        )

        return {
            "available_cash": available_cash,
            "used_margin":    used_margin,
            "total_balance":  total_balance,
        }

    def _error_summary(self, error_msg: str) -> dict:
        """Returns a safe 'cannot trade' summary when balance fetch fails."""
        return {
            "timestamp":         datetime.now().isoformat(),
            "available_balance": 0,
            "used_margin":       0,
            "total_balance":     0,
            "usable_capital":    0,
            "per_trade_limit":   0,
            "day_pnl":           self._day_pnl,
            "trades_taken":      self._trades_taken,
            "can_trade":         False,
            "reason":            f"Balance fetch error: {error_msg}",
        }

    def _log_summary(self, s: dict):
        status = "✅ CAN TRADE" if s["can_trade"] else f"🚫 BLOCKED — {s['reason']}"
        log.info(
            f"Capital Summary | "
            f"Available: ₹{s['available_balance']:,.0f} | "
            f"Usable ({s.get('funds_pct',80)}%): ₹{s['usable_capital']:,.0f} | "
            f"Per Trade ({s.get('per_trade_pct',25)}%): ₹{s['per_trade_limit']:,.0f} | "
            f"Day P&L: ₹{s['day_pnl']:,.0f} | "
            f"Trades: {s['trades_taken']}/{s['max_trades_today']} | "
            f"{status}"
        )
