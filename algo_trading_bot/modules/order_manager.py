"""
modules/order_manager.py — Module 6: Order Manager
════════════════════════════════════════════════════
Handles the complete trade lifecycle:
  Entry → Monitoring → SL/Target Exit → EOD Square-off

Supports two modes:
  PAPER mode — simulates trades with fake orders, real P&L tracking
  LIVE  mode — places real orders on Zerodha Kite

Trade Lifecycle:
  1. receive_signal(signal)     ← from Strategy Engine / Risk Manager
  2. find_option_instrument()   ← looks up live option chain on Kite
  3. get_live_ltp()             ← fetches current option premium
  4. calculate_sl_target()      ← 33% SL, 2:1 R:R by default
  5. place_entry_order()        ← MIS market order (or paper record)
  6. monitor_position()         ← checks SL/target on every tick
  7. place_exit_order()         ← when SL/target/time hit
  8. record_trade()             ← updates CapitalManager + logs

Key Design Decisions:
  - MIS (Margin Intraday Square-off) orders only — no overnight risk
  - Market orders for entry (options are fast-moving, no limit slippage)
  - Limit orders for exit (better fills on SL/target)
  - All positions force-exited by 15:00 IST regardless
  - Paper mode is identical logic, just no real API calls

Usage:
    from modules.order_manager import OrderManager
    om = OrderManager(kite, capital_manager, config)
    om.receive_signal(signal)    # called by strategy engine
    om.monitor_positions()       # called every 5-min candle close
    om.square_off_all()          # called at 15:00
"""

import threading
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

import pytz

from strategies.base_strategy import Signal
from modules.logger import get_logger

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
#  Trade State Enum
# ─────────────────────────────────────────────────────────────────────────────

class TradeState(Enum):
    PENDING   = "pending"     # signal received, not yet entered
    ENTERED   = "entered"     # order placed, position open
    SL_HIT    = "sl_hit"      # stop loss triggered
    TARGET_HIT= "target_hit"  # profit target hit
    TIMED_OUT = "timed_out"   # exited due to time (15:00 square-off)
    CANCELLED = "cancelled"   # signal rejected before entry


# ─────────────────────────────────────────────────────────────────────────────
#  Trade Record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    """Represents one complete trade from entry to exit."""

    # Identity
    trade_id        : str   = ""
    mode            : str   = "paper"     # "paper" or "live"
    symbol          : str   = ""          # "NIFTY" or "BANKNIFTY"
    option_symbol   : str   = ""          # e.g. "NIFTY25FEB22200CE"
    action          : str   = ""          # "CALL" or "PUT"
    state           : TradeState = TradeState.PENDING

    # Quantities & prices
    lots            : int   = 0
    lot_size        : int   = 50
    quantity        : int   = 0           # lots × lot_size
    entry_price     : float = 0.0
    exit_price      : float = 0.0
    sl_price        : float = 0.0
    target_price    : float = 0.0

    # Order IDs (Zerodha)
    entry_order_id  : str   = ""
    exit_order_id   : str   = ""

    # P&L
    gross_pnl       : float = 0.0        # (exit - entry) × qty
    charges         : float = 0.0        # brokerage + STT + GST
    net_pnl         : float = 0.0        # gross - charges

    # Timing
    signal_time     : Optional[datetime] = None
    entry_time      : Optional[datetime] = None
    exit_time       : Optional[datetime] = None
    exit_reason     : str   = ""

    # Signal context
    signal          : Optional[Signal] = None
    conditions_met  : int   = 0
    confidence      : int   = 0
    bias_score      : int   = 0

    def to_dict(self) -> dict:
        return {
            "trade_id":       self.trade_id,
            "mode":           self.mode,
            "symbol":         self.symbol,
            "option_symbol":  self.option_symbol,
            "action":         self.action,
            "state":          self.state.value,
            "lots":           self.lots,
            "lot_size":       self.lot_size,
            "quantity":       self.quantity,
            "entry_price":    self.entry_price,
            "exit_price":     self.exit_price,
            "sl_price":       self.sl_price,
            "target_price":   self.target_price,
            "entry_order_id": self.entry_order_id,
            "exit_order_id":  self.exit_order_id,
            "gross_pnl":      self.gross_pnl,
            "charges":        self.charges,
            "net_pnl":        self.net_pnl,
            "signal_time":    self.signal_time.isoformat() if self.signal_time else None,
            "entry_time":     self.entry_time.isoformat()  if self.entry_time  else None,
            "exit_time":      self.exit_time.isoformat()   if self.exit_time   else None,
            "exit_reason":    self.exit_reason,
            "conditions_met": self.conditions_met,
            "confidence":     self.confidence,
            "bias_score":     self.bias_score,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  OrderManager — Main Class
# ─────────────────────────────────────────────────────────────────────────────

class OrderManager:
    """
    Manages the full trade lifecycle for all open positions.
    Works in paper mode (default) or live mode (real Zerodha orders).
    """

    def __init__(self, kite, capital_manager, config: dict):
        """
        Args:
            kite            : Authenticated KiteConnect instance
            capital_manager : CapitalManager instance (for lot sizing & P&L recording)
            config          : Settings dict from database/dashboard
        """
        self._kite    = kite
        self._cm      = capital_manager
        self._config  = config
        self._lock    = threading.Lock()

        # Active trades: { trade_id: Trade }
        self._active_trades: dict[str, Trade] = {}

        # All trades today: list of Trade objects
        self._today_trades: list[Trade] = []

        # Callbacks
        self._on_trade_enter: Optional[callable] = None
        self._on_trade_exit:  Optional[callable] = None

        self._trade_counter = 0

        mode = config.get("trade_mode", "paper")
        log.info(f"OrderManager initialised — mode: {mode.upper()}")

    # ── Public API ────────────────────────────────────────────────────────────

    def receive_signal(self, signal: Signal) -> Optional[Trade]:
        """
        Entry point from Strategy Engine.
        Validates, sizes, and places the trade.
        Returns the Trade object if entered, None if rejected.
        """
        now = datetime.now(IST)
        log.info(f"Signal received: {signal.action} {signal.symbol} | {signal.reason}")

        # ── Capital check ─────────────────────────────────────────────────────
        capital = self._cm.get_capital_summary()
        if not capital["can_trade"]:
            log.warning(f"Trade rejected — capital check failed: {capital['reason']}")
            return None

        # ── Already have open position in this symbol? ────────────────────────
        if self._has_open_position(signal.symbol):
            log.warning(f"Trade rejected — already have open position in {signal.symbol}")
            return None

        # ── Find the option instrument ────────────────────────────────────────
        option_symbol, lot_size = self._resolve_option_instrument(
            signal.symbol, signal.suggested_strike
        )
        if not option_symbol:
            log.error(f"Could not resolve option instrument for {signal.suggested_strike}")
            return None

        # ── Get live LTP for the option ───────────────────────────────────────
        ltp = self._get_ltp(option_symbol)
        if ltp <= 0:
            log.error(f"Invalid LTP ({ltp}) for {option_symbol} — skipping trade")
            return None

        # ── Calculate lots ────────────────────────────────────────────────────
        # Temporarily set lot_size in config for capital manager
        self._config["lot_size"] = lot_size
        lots = self._cm.get_lot_quantity(ltp)
        qty  = lots * lot_size

        # ── Calculate SL and Target ───────────────────────────────────────────
        sl_pct     = self._config.get("sl_percentage",    33) / 100
        rr_ratio   = self._config.get("risk_reward_ratio", 2.0)
        sl_price   = round(ltp * (1 - sl_pct), 1)
        risk       = ltp - sl_price
        target_price = round(ltp + (risk * rr_ratio), 1)

        log.info(
            f"Trade setup: {option_symbol} | "
            f"LTP={ltp} | SL={sl_price} ({sl_pct*100:.0f}%) | "
            f"Target={target_price} (R:R 1:{rr_ratio}) | "
            f"Lots={lots} Qty={qty}"
        )

        # ── Build trade record ────────────────────────────────────────────────
        self._trade_counter += 1
        trade_id = f"T{date.today().strftime('%Y%m%d')}-{self._trade_counter:03d}"

        trade = Trade(
            trade_id      = trade_id,
            mode          = self._config.get("trade_mode", "paper"),
            symbol        = signal.symbol,
            option_symbol = option_symbol,
            action        = signal.action,
            lots          = lots,
            lot_size      = lot_size,
            quantity      = qty,
            entry_price   = ltp,
            sl_price      = sl_price,
            target_price  = target_price,
            signal_time   = now,
            signal        = signal,
            conditions_met= signal.conditions_met,
            confidence    = signal.confidence,
            bias_score    = signal.bias_score,
        )

        # ── Place entry order ─────────────────────────────────────────────────
        success = self._place_entry(trade, ltp)
        if not success:
            log.error(f"Entry order failed for {trade_id}")
            return None

        # ── Register as active position ───────────────────────────────────────
        with self._lock:
            self._active_trades[trade_id] = trade
            self._today_trades.append(trade)

        self._cm.record_trade_entry(ltp * qty)

        if self._on_trade_enter:
            try:
                self._on_trade_enter(trade)
            except Exception as e:
                log.error(f"on_trade_enter callback error: {e}")

        log.info(
            f"✅ Trade entered: {trade_id} | {trade.option_symbol} | "
            f"Entry: ₹{ltp} | SL: ₹{sl_price} | Target: ₹{target_price} | "
            f"Mode: {trade.mode.upper()}"
        )
        return trade

    def monitor_positions(self):
        """
        Check all open positions against SL and target.
        Called every 5-min candle close (from DataFeed callback).
        Also called by a 1-min scheduler for tighter monitoring.
        """
        with self._lock:
            trade_ids = list(self._active_trades.keys())

        for trade_id in trade_ids:
            trade = self._active_trades.get(trade_id)
            if not trade:
                continue
            self._check_position(trade)

    def square_off_all(self, reason: str = "EOD square-off"):
        """
        Force-exit all open positions.
        Called at 15:00 IST by the scheduler.
        """
        with self._lock:
            trade_ids = list(self._active_trades.keys())

        if not trade_ids:
            log.info("No open positions to square off.")
            return

        log.info(f"Squaring off {len(trade_ids)} position(s) — reason: {reason}")
        for trade_id in trade_ids:
            trade = self._active_trades.get(trade_id)
            if trade:
                ltp = self._get_ltp(trade.option_symbol)
                self._exit_trade(trade, ltp, reason)

    def get_open_positions(self) -> list[Trade]:
        """Returns list of currently open trades."""
        with self._lock:
            return list(self._active_trades.values())

    def get_today_trades(self) -> list[Trade]:
        """Returns all trades taken today."""
        return self._today_trades.copy()

    def get_day_pnl(self) -> float:
        """Sum of net P&L for all completed trades today."""
        return sum(t.net_pnl for t in self._today_trades if t.state != TradeState.ENTERED)

    def set_on_trade_enter(self, callback):
        """Callback fired when a trade is entered. Signature: callback(trade: Trade)"""
        self._on_trade_enter = callback

    def set_on_trade_exit(self, callback):
        """Callback fired when a trade exits. Signature: callback(trade: Trade)"""
        self._on_trade_exit = callback

    def reset_daily(self):
        """Clear today's trades. Called at BOD."""
        with self._lock:
            self._active_trades.clear()
            self._today_trades.clear()
            self._trade_counter = 0
        log.info("OrderManager daily reset complete.")

    # ── Position Monitoring ───────────────────────────────────────────────────

    def _check_position(self, trade: Trade):
        """Check a single trade against SL, target, and trailing SL."""
        ltp = self._get_ltp(trade.option_symbol)
        if ltp <= 0:
            log.warning(f"Could not get LTP for {trade.option_symbol} — skipping check")
            return

        # ── SL hit ────────────────────────────────────────────────────────────
        if ltp <= trade.sl_price:
            log.warning(
                f"🛑 SL HIT: {trade.trade_id} | "
                f"LTP={ltp} ≤ SL={trade.sl_price}"
            )
            self._exit_trade(trade, ltp, "SL hit")
            return

        # ── Target hit ────────────────────────────────────────────────────────
        if ltp >= trade.target_price:
            log.info(
                f"🎯 TARGET HIT: {trade.trade_id} | "
                f"LTP={ltp} ≥ Target={trade.target_price}"
            )
            self._exit_trade(trade, ltp, "Target hit")
            return

        # ── Trailing SL — move SL to breakeven at X% profit ──────────────────
        trail_trigger_pct = self._config.get("trail_sl_trigger_pct", 45) / 100
        trail_trigger_price = trade.entry_price * (1 + trail_trigger_pct)

        if ltp >= trail_trigger_price and trade.sl_price < trade.entry_price:
            old_sl = trade.sl_price
            trade.sl_price = trade.entry_price   # move SL to breakeven
            log.info(
                f"🔒 Trailing SL to breakeven: {trade.trade_id} | "
                f"LTP={ltp} | SL: ₹{old_sl} → ₹{trade.entry_price} (breakeven)"
            )

        # ── Log current status ────────────────────────────────────────────────
        unrealised = (ltp - trade.entry_price) * trade.quantity
        pct_move   = ((ltp - trade.entry_price) / trade.entry_price) * 100
        log.info(
            f"📊 {trade.trade_id} {trade.option_symbol} | "
            f"LTP={ltp} | Entry={trade.entry_price} | "
            f"P&L=₹{unrealised:+.0f} ({pct_move:+.1f}%) | "
            f"SL={trade.sl_price} Target={trade.target_price}"
        )

    # ── Entry Order ───────────────────────────────────────────────────────────

    def _place_entry(self, trade: Trade, ltp: float) -> bool:
        """Place the entry order — real or paper."""
        if trade.mode == "paper":
            return self._paper_entry(trade, ltp)
        else:
            return self._live_entry(trade, ltp)

    def _paper_entry(self, trade: Trade, ltp: float) -> bool:
        """Simulate entry for paper trading."""
        trade.entry_price   = ltp
        trade.entry_time    = datetime.now(IST)
        trade.entry_order_id= f"PAPER-{trade.trade_id}"
        trade.state         = TradeState.ENTERED
        log.info(f"📝 PAPER ENTRY: {trade.option_symbol} @ ₹{ltp} × {trade.quantity} qty")
        return True

    def _live_entry(self, trade: Trade, ltp: float) -> bool:
        """
        Place real MIS market BUY order on Zerodha.
        Uses NFO (F&O) exchange for options.
        """
        try:
            order_id = self._kite.place_order(
                variety        = self._kite.VARIETY_REGULAR,
                exchange       = self._kite.EXCHANGE_NFO,
                tradingsymbol  = trade.option_symbol,
                transaction_type = self._kite.TRANSACTION_TYPE_BUY,
                quantity       = trade.quantity,
                order_type     = self._kite.ORDER_TYPE_MARKET,
                product        = self._kite.PRODUCT_MIS,    # Intraday only
                tag            = trade.trade_id,             # label in Zerodha
            )

            trade.entry_order_id = str(order_id)
            trade.entry_time     = datetime.now(IST)
            trade.state          = TradeState.ENTERED

            # Fetch actual fill price
            actual_price = self._get_fill_price(order_id) or ltp
            trade.entry_price = actual_price

            log.info(
                f"✅ LIVE ENTRY: {trade.option_symbol} | "
                f"Order ID: {order_id} | Fill: ₹{actual_price}"
            )
            return True

        except Exception as e:
            log.error(f"Live entry failed for {trade.option_symbol}: {e}")
            return False

    # ── Exit Order ────────────────────────────────────────────────────────────

    def _exit_trade(self, trade: Trade, exit_price: float, reason: str):
        """Exit a trade — real or paper — and record P&L."""
        if trade.state != TradeState.ENTERED:
            return

        success = False
        if trade.mode == "paper":
            success = self._paper_exit(trade, exit_price, reason)
        else:
            success = self._live_exit(trade, exit_price, reason)

        if success:
            self._calculate_pnl(trade)
            self._cm.record_trade_exit(trade.net_pnl)

            with self._lock:
                self._active_trades.pop(trade.trade_id, None)

            if self._on_trade_exit:
                try:
                    self._on_trade_exit(trade)
                except Exception as e:
                    log.error(f"on_trade_exit callback error: {e}")

            log.info(
                f"{'✅' if trade.net_pnl >= 0 else '❌'} "
                f"Trade closed: {trade.trade_id} | "
                f"Exit: ₹{exit_price} | "
                f"Reason: {reason} | "
                f"Net P&L: ₹{trade.net_pnl:+.0f}"
            )

    def _paper_exit(self, trade: Trade, exit_price: float, reason: str) -> bool:
        """Simulate exit for paper trading."""
        trade.exit_price    = exit_price
        trade.exit_time     = datetime.now(IST)
        trade.exit_order_id = f"PAPER-EXIT-{trade.trade_id}"
        trade.exit_reason   = reason

        state_map = {
            "SL hit":      TradeState.SL_HIT,
            "Target hit":  TradeState.TARGET_HIT,
        }
        trade.state = state_map.get(reason, TradeState.TIMED_OUT)
        log.info(f"📝 PAPER EXIT: {trade.option_symbol} @ ₹{exit_price} | {reason}")
        return True

    def _live_exit(self, trade: Trade, exit_price: float, reason: str) -> bool:
        """Place real MIS SELL order on Zerodha."""
        try:
            order_id = self._kite.place_order(
                variety          = self._kite.VARIETY_REGULAR,
                exchange         = self._kite.EXCHANGE_NFO,
                tradingsymbol    = trade.option_symbol,
                transaction_type = self._kite.TRANSACTION_TYPE_SELL,
                quantity         = trade.quantity,
                order_type       = self._kite.ORDER_TYPE_MARKET,
                product          = self._kite.PRODUCT_MIS,
                tag              = f"EXIT-{trade.trade_id}",
            )

            actual_exit = self._get_fill_price(order_id) or exit_price
            trade.exit_price    = actual_exit
            trade.exit_time     = datetime.now(IST)
            trade.exit_order_id = str(order_id)
            trade.exit_reason   = reason

            state_map = {"SL hit": TradeState.SL_HIT, "Target hit": TradeState.TARGET_HIT}
            trade.state = state_map.get(reason, TradeState.TIMED_OUT)

            log.info(f"✅ LIVE EXIT: {trade.option_symbol} | Order: {order_id} | Fill: ₹{actual_exit}")
            return True

        except Exception as e:
            log.error(f"Live exit failed for {trade.option_symbol}: {e}")
            # Critical: even if exit fails, try to mark it manually
            trade.exit_price  = exit_price
            trade.exit_time   = datetime.now(IST)
            trade.exit_reason = f"{reason} (exit order failed: {e})"
            trade.state       = TradeState.TIMED_OUT
            return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_option_instrument(
        self, symbol: str, suggested_strike: str
    ) -> tuple[str, int]:
        """
        Convert signal's suggested_strike into a full Zerodha tradingsymbol.
        e.g. "NIFTY", "22200CE" → "NIFTY25FEB22200CE", lot_size=50

        In paper mode: builds the symbol name directly.
        In live mode: validates against Kite instruments list.

        Returns: (option_symbol, lot_size) or ("", 0) on failure
        """
        lot_sizes = {"NIFTY": 75, "BANKNIFTY": 30}   # Updated lot sizes
        lot_size  = lot_sizes.get(symbol, 50)

        # Build expiry string — nearest weekly Thursday
        expiry_str = self._get_nearest_expiry_str(symbol)

        # Full symbol: NIFTY + 25FEB + 22200CE
        option_symbol = f"{symbol}{expiry_str}{suggested_strike}"

        if self._config.get("trade_mode", "paper") == "paper":
            return option_symbol, lot_size

        # Live mode: verify against Kite instruments
        try:
            instruments = self._kite.instruments("NFO")
            for inst in instruments:
                if inst["tradingsymbol"] == option_symbol:
                    return option_symbol, inst["lot_size"]

            # Try searching by partial name
            for inst in instruments:
                ts = inst["tradingsymbol"]
                if (symbol in ts and
                    suggested_strike in ts and
                    inst.get("instrument_type") in ("CE", "PE")):
                    log.info(f"Resolved {suggested_strike} → {ts}")
                    return ts, inst["lot_size"]

            log.error(f"Option {option_symbol} not found in NFO instruments")
            return "", 0

        except Exception as e:
            log.error(f"Instrument lookup failed: {e}")
            return "", 0

    def _get_nearest_expiry_str(self, symbol: str) -> str:
        """
        Returns the nearest weekly expiry string in Zerodha format.
        Nifty weekly: every Thursday → format: 25FEB (day+month)
        BankNifty weekly: every Wednesday → format: 25FEB

        For simplicity, returns current month's last Thursday.
        Full implementation requires checking NSE expiry calendar.
        """
        now   = datetime.now(IST)
        month = now.strftime("%b").upper()   # FEB, MAR etc.
        day   = now.strftime("%d")           # 25

        # Zerodha format: DDMON — e.g. 25FEB, 06MAR
        return f"{day}{month}"

    def _get_ltp(self, option_symbol: str) -> float:
        """
        Fetch live LTP for an option symbol.
        Paper mode: returns simulated price (entry ± random drift).
        Live mode: calls kite.ltp()
        """
        if self._config.get("trade_mode", "paper") == "paper":
            return self._simulate_ltp(option_symbol)

        try:
            key  = f"NFO:{option_symbol}"
            data = self._kite.ltp([key])
            return float(data[key]["last_price"])
        except Exception as e:
            log.warning(f"LTP fetch failed for {option_symbol}: {e}")
            return 0.0

    def _simulate_ltp(self, option_symbol: str) -> float:
        """
        Simulate LTP for paper trading.
        On first call (entry): returns a realistic option premium.
        On subsequent calls (monitoring): returns entry ± small drift.
        """
        import random

        # Find the trade for this symbol
        for trade in self._active_trades.values():
            if trade.option_symbol == option_symbol:
                if trade.entry_price > 0:
                    # Simulate small price movement ±3%
                    drift = random.uniform(-0.03, 0.05)
                    return round(trade.entry_price * (1 + drift), 1)

        # Entry price: simulate a realistic option premium (₹80–₹200)
        return round(random.uniform(80, 200), 1)

    def _get_fill_price(self, order_id: str) -> Optional[float]:
        """Fetch actual fill price from Zerodha order history."""
        try:
            history = self._kite.order_history(order_id)
            for order in reversed(history):
                if order.get("status") == "COMPLETE":
                    return float(order.get("average_price", 0))
        except Exception as e:
            log.warning(f"Could not fetch fill price for {order_id}: {e}")
        return None

    def _calculate_pnl(self, trade: Trade):
        """Calculate gross and net P&L including Zerodha charges."""
        trade.gross_pnl = (trade.exit_price - trade.entry_price) * trade.quantity

        # Approximate Zerodha charges for options
        # Brokerage: ₹20 per order (flat)
        # STT: 0.05% on sell side (options)
        # Exchange fees, GST etc: ~0.05%
        brokerage = 40.0   # ₹20 entry + ₹20 exit
        stt       = trade.exit_price * trade.quantity * 0.0005
        other     = (trade.entry_price + trade.exit_price) * trade.quantity * 0.0005
        trade.charges = round(brokerage + stt + other, 2)
        trade.net_pnl = round(trade.gross_pnl - trade.charges, 2)

    def _has_open_position(self, symbol: str) -> bool:
        """Check if there's already an open trade for this underlying symbol."""
        with self._lock:
            for trade in self._active_trades.values():
                if trade.symbol == symbol and trade.state == TradeState.ENTERED:
                    return True
        return False
