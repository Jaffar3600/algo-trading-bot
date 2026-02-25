"""
modules/data_feed.py — Module 3: Data Feed & Candle Builder
════════════════════════════════════════════════════════════════
Connects to Zerodha KiteTicker WebSocket for live tick data
and builds real-time OHLCV candles for 5-min and 15-min timeframes.

How it works:
  1. Subscribes to Nifty/BankNifty instrument tokens via WebSocket
  2. Every incoming tick updates the "current" (open) candle
  3. When a candle's timeframe closes, it's moved to completed candles
  4. Strategy engine reads completed candles to generate signals

Usage:
    from modules.data_feed import DataFeed
    from modules.auth import AuthManager

    auth = AuthManager()
    kite = auth.ensure_session()

    feed = DataFeed(kite)
    feed.start()                          # connects WebSocket

    candles = feed.get_candles("NIFTY", 15)   # last N 15-min candles
    feed.stop()
"""

import threading
from collections import defaultdict
from datetime import datetime, time
from typing import Callable, Optional

import pandas as pd
import pytz

from kiteconnect import KiteTicker

import config
from modules.logger import get_logger

log = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ── Candle timeframes supported ───────────────────────────────────────────────
TIMEFRAMES = [5, 15]   # minutes

# ── Instrument token → symbol mapping (from config) ──────────────────────────
TOKEN_TO_SYMBOL = {v: k for k, v in config.INSTRUMENT_TOKENS.items()}

# Friendly short names
SYMBOL_SHORT = {
    "NIFTY 50":   "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Candle — single OHLCV bar
# ─────────────────────────────────────────────────────────────────────────────

class Candle:
    """Represents a single OHLCV candle."""

    def __init__(self, symbol: str, timeframe: int, open_time: datetime, open_price: float):
        self.symbol    = symbol
        self.timeframe = timeframe      # minutes
        self.open_time = open_time      # candle start time
        self.open      = open_price
        self.high      = open_price
        self.low       = open_price
        self.close     = open_price
        self.volume    = 0
        self.ticks     = 1              # constructor sets open price = 1st tick
        self.is_closed = False

    def update(self, price: float, volume: int = 0):
        """Update candle with a new tick."""
        self.high   = max(self.high, price)
        self.low    = min(self.low,  price)
        self.close  = price
        self.volume += volume
        self.ticks  += 1

    def close_candle(self):
        """Mark candle as complete."""
        self.is_closed = True

    def to_dict(self) -> dict:
        return {
            "symbol":    self.symbol,
            "timeframe": self.timeframe,
            "open_time": self.open_time,
            "open":      self.open,
            "high":      self.high,
            "low":       self.low,
            "close":     self.close,
            "volume":    self.volume,
            "ticks":     self.ticks,
        }

    def __repr__(self):
        return (
            f"Candle({self.symbol} {self.timeframe}m "
            f"{self.open_time.strftime('%H:%M')} "
            f"O:{self.open:.1f} H:{self.high:.1f} "
            f"L:{self.low:.1f} C:{self.close:.1f})"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  CandleStore — manages candles for one symbol + timeframe
# ─────────────────────────────────────────────────────────────────────────────

class CandleStore:
    """
    Holds completed candles and the current open candle
    for a single symbol + timeframe combination.
    Max 100 completed candles kept in memory.
    """

    MAX_CANDLES = 100

    def __init__(self, symbol: str, timeframe: int):
        self.symbol    = symbol
        self.timeframe = timeframe
        self.completed: list[Candle] = []
        self.current:   Optional[Candle] = None
        self._lock = threading.Lock()

    def process_tick(self, price: float, volume: int, tick_time: datetime):
        """
        Main tick processing. Creates new candle or updates existing one.
        Closes candle when timeframe boundary is crossed.
        Returns the closed candle if one just completed, else None.
        """
        with self._lock:
            candle_open_time = self._get_candle_open_time(tick_time)

            # ── No open candle yet — create first one ─────────────────────────
            if self.current is None:
                self.current = Candle(self.symbol, self.timeframe,
                                      candle_open_time, price)
                self.current.update(price, volume)
                return None

            # ── Still in same candle timeframe — update ───────────────────────
            if candle_open_time == self.current.open_time:
                self.current.update(price, volume)
                return None

            # ── New candle timeframe — close current, open new ────────────────
            closed = self.current
            closed.close_candle()
            self.completed.append(closed)

            # Keep only last MAX_CANDLES
            if len(self.completed) > self.MAX_CANDLES:
                self.completed = self.completed[-self.MAX_CANDLES:]

            # Open new candle
            self.current = Candle(self.symbol, self.timeframe,
                                  candle_open_time, price)
            self.current.update(price, volume)

            return closed

    def get_completed(self, n: int = 50) -> list[Candle]:
        """Returns last N completed candles (oldest first)."""
        with self._lock:
            return self.completed[-n:] if self.completed else []

    def get_dataframe(self, n: int = 50) -> pd.DataFrame:
        """
        Returns last N completed candles as a pandas DataFrame.
        Columns: open_time, open, high, low, close, volume
        """
        candles = self.get_completed(n)
        if not candles:
            return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])

        rows = [c.to_dict() for c in candles]
        df   = pd.DataFrame(rows)
        df   = df[["open_time", "open", "high", "low", "close", "volume"]]
        df   = df.set_index("open_time")
        return df

    def count(self) -> int:
        """Number of completed candles available."""
        return len(self.completed)

    def _get_candle_open_time(self, tick_time: datetime) -> datetime:
        """
        Floors tick_time to the nearest timeframe boundary.
        E.g. 09:37 → 09:35 for 5-min candles
             09:37 → 09:30 for 15-min candles
        """
        minute  = tick_time.minute
        floored = (minute // self.timeframe) * self.timeframe
        return tick_time.replace(minute=floored, second=0, microsecond=0)


# ─────────────────────────────────────────────────────────────────────────────
#  DataFeed — main public class
# ─────────────────────────────────────────────────────────────────────────────

class DataFeed:
    """
    Manages WebSocket connection to Zerodha KiteTicker.
    Builds real-time 5-min and 15-min candles for all subscribed instruments.

    Callbacks:
        on_candle_close(candle) — called every time a candle completes
        on_tick(symbol, price)  — called on every tick (optional)
    """

    def __init__(self, kite, instruments: list[str] = None):
        """
        Args:
            kite        : Authenticated KiteConnect instance
            instruments : List of instrument short names to subscribe to
                          e.g. ["NIFTY", "BANKNIFTY"]
                          Defaults to both from config
        """
        self._kite        = kite
        self._ticker: Optional[KiteTicker] = None
        self._is_running  = False

        # Which instruments to subscribe
        self._instruments = instruments or ["NIFTY", "BANKNIFTY"]

        # Build token list from config
        self._tokens = self._resolve_tokens()

        # Candle stores: { "NIFTY_5": CandleStore, "NIFTY_15": CandleStore, ... }
        self._stores: dict[str, CandleStore] = {}
        for symbol in self._instruments:
            for tf in TIMEFRAMES:
                key = f"{symbol}_{tf}"
                self._stores[key] = CandleStore(symbol, tf)

        # Latest prices: { "NIFTY": 22150.5, "BANKNIFTY": 48200.0 }
        self._last_price: dict[str, float] = {}

        # User callbacks
        self._on_candle_close: Optional[Callable] = None
        self._on_tick:         Optional[Callable] = None

        # Opening range: set after first 15-min candle
        # { "NIFTY": {"high": 22200, "low": 22000}, ... }
        self._opening_range: dict[str, dict] = {}
        self._opening_range_set: dict[str, bool] = {s: False for s in self._instruments}

        log.info(f"DataFeed initialised for: {self._instruments}")
        log.info(f"Subscribed tokens: {self._tokens}")

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Connect to KiteTicker WebSocket and start receiving ticks."""
        if self._is_running:
            log.warning("DataFeed already running.")
            return

        log.info("Starting DataFeed WebSocket connection...")
        api_key      = config.KITE_API_KEY
        access_token = self._kite.access_token

        self._ticker = KiteTicker(api_key, access_token)

        # Register callbacks
        self._ticker.on_connect    = self._on_connect
        self._ticker.on_ticks      = self._on_ticks
        self._ticker.on_close      = self._on_close
        self._ticker.on_error      = self._on_error
        self._ticker.on_reconnect  = self._on_reconnect
        self._ticker.on_noreconnect = self._on_noreconnect

        # Start in background thread — non-blocking
        self._ticker.connect(threaded=True)
        self._is_running = True
        log.info("DataFeed WebSocket started (threaded).")

    def stop(self):
        """Disconnect WebSocket and stop data feed."""
        if self._ticker and self._is_running:
            self._ticker.close()
            self._is_running = False
            log.info("DataFeed stopped.")

    def set_on_candle_close(self, callback: Callable):
        """
        Register callback for when a candle closes.
        Signature: callback(candle: Candle)
        """
        self._on_candle_close = callback
        log.info("on_candle_close callback registered.")

    def set_on_tick(self, callback: Callable):
        """
        Register callback for every tick.
        Signature: callback(symbol: str, price: float, tick_time: datetime)
        """
        self._on_tick = callback

    def get_candles(self, symbol: str, timeframe: int, n: int = 50) -> list[Candle]:
        """
        Get last N completed candles for a symbol and timeframe.
        Args:
            symbol    : "NIFTY" or "BANKNIFTY"
            timeframe : 5 or 15
            n         : number of candles to return
        """
        key   = f"{symbol}_{timeframe}"
        store = self._stores.get(key)
        if not store:
            log.warning(f"No candle store for {symbol} {timeframe}m")
            return []
        return store.get_completed(n)

    def get_dataframe(self, symbol: str, timeframe: int, n: int = 50) -> pd.DataFrame:
        """
        Get last N completed candles as pandas DataFrame.
        Returns DataFrame with columns: open, high, low, close, volume
        Index: open_time (datetime)
        """
        key   = f"{symbol}_{timeframe}"
        store = self._stores.get(key)
        if not store:
            return pd.DataFrame()
        return store.get_dataframe(n)

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Returns latest tick price for a symbol."""
        return self._last_price.get(symbol)

    def get_opening_range(self, symbol: str) -> Optional[dict]:
        """
        Returns the Opening Range (first 15-min candle H/L) for a symbol.
        Returns None if not yet established.
        Format: {"high": 22200.0, "low": 22000.0, "open": 22050.0}
        """
        if self._opening_range_set.get(symbol):
            return self._opening_range.get(symbol)
        return None

    def get_candle_count(self, symbol: str, timeframe: int) -> int:
        """Returns number of completed candles available."""
        key = f"{symbol}_{timeframe}"
        return self._stores[key].count() if key in self._stores else 0

    def is_running(self) -> bool:
        return self._is_running

    # ── WebSocket Callbacks ───────────────────────────────────────────────────

    def _on_connect(self, ws, response):
        """Called when WebSocket connects successfully."""
        log.info("✅ WebSocket connected! Subscribing to instruments...")
        ws.subscribe(self._tokens)
        ws.set_mode(ws.MODE_FULL, self._tokens)
        log.info(f"Subscribed to tokens: {self._tokens} in FULL mode")

    def _on_ticks(self, ws, ticks: list):
        """Called on every tick. Core processing happens here."""
        now = datetime.now(IST)

        # Only process during market hours
        if not self._is_market_hours(now):
            return

        for tick in ticks:
            token     = tick.get("instrument_token")
            price     = tick.get("last_price", 0)
            volume    = tick.get("volume_traded", 0)

            if not price or token not in TOKEN_TO_SYMBOL:
                continue

            full_symbol  = TOKEN_TO_SYMBOL[token]
            symbol       = SYMBOL_SHORT.get(full_symbol, full_symbol)

            # Update last price
            self._last_price[symbol] = price

            # Fire on_tick callback
            if self._on_tick:
                try:
                    self._on_tick(symbol, price, now)
                except Exception as e:
                    log.error(f"on_tick callback error: {e}")

            # Process tick into candles
            self._process_tick_for_candles(symbol, price, volume, now)

    def _process_tick_for_candles(self, symbol: str, price: float,
                                   volume: int, tick_time: datetime):
        """Route tick to all timeframe candle stores for this symbol."""
        for tf in TIMEFRAMES:
            key    = f"{symbol}_{tf}"
            store  = self._stores.get(key)
            if not store:
                continue

            closed = store.process_tick(price, volume, tick_time)

            if closed:
                log.info(f"🕯️  Candle closed: {closed}")

                # Set opening range from first 15-min candle
                if tf == 15 and not self._opening_range_set.get(symbol):
                    self._opening_range[symbol] = {
                        "high":  closed.high,
                        "low":   closed.low,
                        "open":  closed.open,
                        "close": closed.close,
                        "time":  closed.open_time,
                    }
                    self._opening_range_set[symbol] = True
                    log.info(
                        f"📊 Opening Range SET for {symbol}: "
                        f"High={closed.high:.1f} Low={closed.low:.1f}"
                    )

                # Fire candle close callback
                if self._on_candle_close:
                    try:
                        self._on_candle_close(closed)
                    except Exception as e:
                        log.error(f"on_candle_close callback error: {e}")

    def _on_close(self, ws, code, reason):
        """Called when WebSocket connection closes."""
        self._is_running = False
        log.warning(f"WebSocket closed — Code: {code} | Reason: {reason}")

    def _on_error(self, ws, code, reason):
        """Called on WebSocket error."""
        log.error(f"WebSocket error — Code: {code} | Reason: {reason}")

    def _on_reconnect(self, ws, attempts_count):
        """Called when WebSocket is attempting to reconnect."""
        log.warning(f"WebSocket reconnecting... Attempt #{attempts_count}")

    def _on_noreconnect(self, ws):
        """Called when WebSocket gives up reconnecting."""
        self._is_running = False
        log.error("WebSocket gave up reconnecting. Please restart the bot.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_tokens(self) -> list[int]:
        """Convert instrument short names to Zerodha token integers."""
        tokens = []
        # Reverse map: "NIFTY" → 256265
        short_to_token = {}
        for full_name, token in config.INSTRUMENT_TOKENS.items():
            short = SYMBOL_SHORT.get(full_name, full_name)
            short_to_token[short] = token

        for instrument in self._instruments:
            token = short_to_token.get(instrument)
            if token:
                tokens.append(token)
            else:
                log.warning(f"No token found for instrument: {instrument}")
        return tokens

    def _is_market_hours(self, now: datetime) -> bool:
        """Returns True if current time is within NSE market hours."""
        market_open  = time(9, 15)
        market_close = time(15, 30)
        current_time = now.time()
        return market_open <= current_time <= market_close
