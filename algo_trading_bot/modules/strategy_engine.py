"""
modules/strategy_engine.py — Module 5: Strategy Engine
════════════════════════════════════════════════════════
Orchestrates signal generation. Loads strategies dynamically
from the strategies/ folder, runs the active strategy on every
new candle close, and applies time-window filters.

Key features:
  - Plug-and-play: drop a new .py in strategies/ → auto-detected
  - Calls active strategy on every 15-min candle close
  - Applies time window rules (no trades 11:30–13:00, stop at 15:00)
  - Integrates market bias from MarketIntel
  - Passes signals to callback (Risk Manager → Order Manager)

Usage:
    from modules.strategy_engine import StrategyEngine
    from modules.data_feed import DataFeed
    from modules.market_intel import MarketIntel

    engine = StrategyEngine(config, market_intel)
    engine.set_on_signal(callback)   # called when tradeable signal found
    engine.attach_feed(data_feed)    # auto-called on candle close
    engine.start()
    engine.stop()
"""

import importlib
import inspect
import os
import sys
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Callable, Optional

import pytz

import config as app_config
from strategies.base_strategy import BaseStrategy, Signal, SIGNAL_NONE
from modules.logger import get_logger

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"


# ─────────────────────────────────────────────────────────────────────────────
#  Strategy Loader
# ─────────────────────────────────────────────────────────────────────────────

def discover_strategies() -> dict[str, type]:
    """
    Scan strategies/ folder and return all available strategy classes.
    Returns dict: { "momentum_strike": MomentumStrike, ... }
    Only loads classes that inherit BaseStrategy (ignores base_strategy.py itself).
    """
    found = {}

    for py_file in STRATEGIES_DIR.glob("*.py"):
        if py_file.name.startswith("_") or py_file.name == "base_strategy.py":
            continue

        module_name = f"strategies.{py_file.stem}"
        try:
            module = importlib.import_module(module_name)
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseStrategy)
                    and obj is not BaseStrategy
                ):
                    key = py_file.stem   # e.g. "momentum_strike"
                    found[key] = obj
                    log.debug(f"Discovered strategy: {key} → {obj.__name__}")
        except Exception as e:
            log.warning(f"Could not load strategy from {py_file.name}: {e}")

    log.info(f"Strategies available: {list(found.keys())}")
    return found


# ─────────────────────────────────────────────────────────────────────────────
#  StrategyEngine
# ─────────────────────────────────────────────────────────────────────────────

class StrategyEngine:
    """
    Orchestrates strategy execution.
    Listens to DataFeed candle-close events and runs the active strategy.
    """

    def __init__(self, bot_config: dict, market_intel=None):
        """
        Args:
            bot_config   : Settings from database/dashboard
            market_intel : MarketIntel instance (optional — used for bias score)
        """
        self._config       = bot_config
        self._market_intel = market_intel
        self._data_feed    = None
        self._active_strategy: Optional[BaseStrategy] = None
        self._on_signal_cb: Optional[Callable]        = None
        self._running      = False

        # Load all available strategies
        self._available = discover_strategies()

        # Load the configured active strategy
        self._load_active_strategy()

        log.info(f"StrategyEngine initialised — active: {self._active_strategy}")

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Start the strategy engine (enables signal generation)."""
        if not self._active_strategy:
            log.error("Cannot start — no active strategy loaded.")
            return
        self._running = True
        log.info(f"StrategyEngine started — strategy: {self._active_strategy.name}")

    def stop(self):
        """Stop signal generation."""
        self._running = False
        log.info("StrategyEngine stopped.")

    def attach_feed(self, data_feed):
        """
        Attach a DataFeed instance. Strategy engine registers itself
        as the candle-close callback so it's called automatically.
        """
        self._data_feed = data_feed
        data_feed.set_on_candle_close(self._on_candle_close)
        log.info("DataFeed attached to StrategyEngine.")

    def set_on_signal(self, callback: Callable):
        """
        Register callback for when a tradeable signal is generated.
        Signature: callback(signal: Signal)
        This callback goes to the Risk Manager before Order Manager.
        """
        self._on_signal_cb = callback
        log.info("on_signal callback registered.")

    def set_strategy(self, strategy_key: str) -> bool:
        """
        Switch active strategy by key (e.g. "momentum_strike").
        Returns True if successful.
        """
        if strategy_key not in self._available:
            log.error(f"Strategy '{strategy_key}' not found. Available: {list(self._available.keys())}")
            return False

        self._config["active_strategy"] = strategy_key
        self._load_active_strategy()
        log.info(f"Strategy switched to: {strategy_key}")
        return True

    def get_available_strategies(self) -> list[dict]:
        """Returns list of available strategies with metadata."""
        result = []
        for key, cls in self._available.items():
            result.append({
                "key":         key,
                "name":        getattr(cls, "name",        key),
                "description": getattr(cls, "description", ""),
                "instruments": getattr(cls, "instruments", []),
                "option_type": getattr(cls, "option_type", "BUY"),
                "version":     getattr(cls, "version",     "1.0"),
            })
        return result

    def get_active_strategy_name(self) -> str:
        if self._active_strategy:
            return self._active_strategy.name
        return "None"

    def evaluate_now(self, symbol: str) -> Signal:
        """
        Manually trigger signal evaluation for a symbol right now.
        Useful for testing or manual override.
        """
        if not self._data_feed:
            return SIGNAL_NONE("No data feed attached")
        if not self._active_strategy:
            return SIGNAL_NONE("No active strategy")

        return self._evaluate_symbol(symbol)

    # ── Candle Close Handler ──────────────────────────────────────────────────

    def _on_candle_close(self, candle):
        """
        Called by DataFeed every time a candle closes.
        Only acts on 15-min candles (primary signal timeframe).
        """
        if not self._running:
            return

        # Only trigger on 15-min candle close
        if candle.timeframe != 15:
            return

        symbol = candle.symbol
        log.info(f"15-min candle closed for {symbol} — evaluating strategy...")

        # Check time window
        allowed, reason = self._is_trading_time()
        if not allowed:
            log.info(f"  ⏰ Outside trading window: {reason}")
            return

        # Evaluate strategy
        signal = self._evaluate_symbol(symbol)

        # Fire callback if tradeable
        if signal.is_tradeable() and self._on_signal_cb:
            log.info(f"  🚀 Firing signal callback: {signal}")
            try:
                self._on_signal_cb(signal)
            except Exception as e:
                log.error(f"Signal callback error: {e}")

    # ── Core Evaluation ───────────────────────────────────────────────────────

    def _evaluate_symbol(self, symbol: str) -> Signal:
        """
        Run active strategy for a specific symbol.
        Fetches candles from DataFeed and calls strategy.generate_signal().
        """
        if not self._data_feed:
            return SIGNAL_NONE("No data feed")

        # Get candles
        candles_15m   = self._data_feed.get_dataframe(symbol, 15, n=50)
        candles_5m    = self._data_feed.get_dataframe(symbol, 5,  n=30)
        opening_range = self._data_feed.get_opening_range(symbol)

        # Get market bias
        bias_score = 0
        if self._market_intel:
            try:
                bias_score = self._market_intel.get_bias_score()
            except Exception:
                pass

        log.debug(
            f"Evaluating {symbol} | "
            f"15m candles:{len(candles_15m)} "
            f"5m candles:{len(candles_5m)} | "
            f"Bias:{bias_score:+d}"
        )

        # Check instrument is active
        active_instruments = self._config.get("active_instruments", ["NIFTY", "BANKNIFTY"])
        if symbol not in active_instruments:
            return SIGNAL_NONE(f"{symbol} not in active instruments")

        # Run strategy
        try:
            signal = self._active_strategy.generate_signal(
                symbol       = symbol,
                candles_15m  = candles_15m,
                candles_5m   = candles_5m,
                opening_range= opening_range,
                market_bias  = bias_score,
            )
        except Exception as e:
            log.error(f"Strategy error for {symbol}: {e}")
            return SIGNAL_NONE(f"Strategy error: {e}")

        # Log result
        if signal.is_tradeable():
            log.info(
                f"✅ SIGNAL [{symbol}]: {signal.action} | "
                f"Strike:{signal.suggested_strike} | "
                f"Confidence:{signal.confidence}% | "
                f"{signal.reason}"
            )
            for detail in signal.conditions_detail:
                log.info(f"   {detail}")
        else:
            log.info(f"  No signal for {symbol}: {signal.reason}")

        return signal

    # ── Time Window Check ─────────────────────────────────────────────────────

    def _is_trading_time(self) -> tuple[bool, str]:
        """
        Check if current time is within allowed trading window.
        Rules from config:
          - trading_start: 09:30 (don't trade before this)
          - trading_end:   15:00 (no new trades after this)
          - avoid_lunch:   True  (skip 11:30–13:00)
        """
        now = datetime.now(IST).time()

        # Parse times from config
        trading_start = self._parse_time(self._config.get("trading_start", "09:30"))
        trading_end   = self._parse_time(self._config.get("trading_end",   "15:00"))
        avoid_lunch   = self._config.get("avoid_lunch", True)
        lunch_start   = self._parse_time(self._config.get("lunch_start",   "11:30"))
        lunch_end     = self._parse_time(self._config.get("lunch_end",     "13:00"))

        if now < trading_start:
            return False, f"Before trading start ({self._config.get('trading_start')})"

        if now >= trading_end:
            return False, f"After trading end ({self._config.get('trading_end')})"

        if avoid_lunch and lunch_start <= now < lunch_end:
            return False, f"Lunch zone ({self._config.get('lunch_start')}–{self._config.get('lunch_end')})"

        return True, "OK"

    def _parse_time(self, time_str: str) -> dt_time:
        """Parse "HH:MM" string to time object."""
        try:
            h, m = time_str.split(":")
            return dt_time(int(h), int(m))
        except Exception:
            return dt_time(9, 30)

    # ── Strategy Loader ───────────────────────────────────────────────────────

    def _load_active_strategy(self):
        """Load the strategy specified in config."""
        key = self._config.get("active_strategy", "momentum_strike")

        if key not in self._available:
            log.warning(
                f"Strategy '{key}' not found. "
                f"Available: {list(self._available.keys())}. "
                f"Trying 'momentum_strike'..."
            )
            key = "momentum_strike"

        if key not in self._available:
            log.error("No strategies available!")
            self._active_strategy = None
            return

        strategy_class        = self._available[key]
        self._active_strategy = strategy_class(self._config)
        log.info(f"Loaded strategy: {self._active_strategy.name} v{self._active_strategy.version}")
