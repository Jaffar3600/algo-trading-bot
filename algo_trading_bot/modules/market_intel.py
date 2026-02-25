"""
modules/market_intel.py — Module 4: AI Market Intelligence Engine
══════════════════════════════════════════════════════════════════
Fetches live data every 10 minutes and computes a Market Bias Score
(-10 to +10) that influences trade decisions.

Data Sources:
  1. Zerodha Kite API  — Nifty, BankNifty, VIX, USD/INR (live, reliable)
  2. Global Markets    — Dow, S&P 500, Nasdaq, Nikkei, Hang Seng (Yahoo Finance)
  3. FII/DII           — Net buy/sell activity (NSE API — not in Kite)
  4. Commodities       — Crude Oil Brent, Gold USD (Yahoo Finance)
  5. Currency          — EUR/INR (Yahoo Finance — Kite has USD/INR)
  6. News & Events     — NSE headlines + high-impact event detection

Why Zerodha for Indian indices?
  - Official API, already authenticated, never breaks
  - Real-time data, same feed the bot uses for trading
  - NSE website scraping is fragile and gets blocked frequently
  - No reason to scrape what we already have access to via Kite

Bias Score:
  +6 to +10 → Strongly Bullish  → Prioritise Call setups
  +2 to +5  → Mildly Bullish    → Allow Call setups
  -1 to +1  → Neutral/Choppy   → Stricter signal filter
  -2 to -5  → Mildly Bearish   → Allow Put setups
  -6 to -10 → Strongly Bearish → Prioritise Put setups

Usage:
    from modules.market_intel import MarketIntel
    intel = MarketIntel(kite)              # pass authenticated KiteConnect
    intel.refresh()
    score = intel.get_bias_score()         # -10 to +10
    intel.start_auto_refresh(interval=10)  # background refresh every 10 min
"""

import threading
import time as time_module
from datetime import datetime, date
from typing import Optional

import requests
import pytz

from modules.logger import get_logger

log = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/html, */*",
}
TIMEOUT = 10

# ── Zerodha instrument keys for live quote ────────────────────────────────────
KITE_QUOTE_INSTRUMENTS = {
    "NSE:NIFTY 50":   "nifty",
    "NSE:NIFTY BANK": "banknifty",
    "NSE:INDIA VIX":  "india_vix",
    "NSE:USDINR":     "usdinr",     # USD/INR spot on NSE
}


# ─────────────────────────────────────────────────────────────────────────────
#  IntelSnapshot
# ─────────────────────────────────────────────────────────────────────────────

class IntelSnapshot:
    """Holds one complete market intelligence snapshot."""

    def __init__(self):
        self.timestamp            = datetime.now(IST)

        # ── Indian Indices (from Zerodha) ─────────────────────────────────────
        self.nifty_value          = 0.0
        self.nifty_change_pct     = 0.0
        self.banknifty_value      = 0.0
        self.banknifty_change_pct = 0.0
        self.india_vix            = 0.0

        # ── Global Markets (Yahoo Finance) ────────────────────────────────────
        self.dow_jones            = 0.0
        self.dow_change_pct       = 0.0
        self.sp500                = 0.0
        self.sp500_change_pct     = 0.0
        self.nasdaq               = 0.0
        self.nasdaq_change_pct    = 0.0
        self.nikkei               = 0.0
        self.nikkei_change_pct    = 0.0
        self.hangseng             = 0.0
        self.hangseng_change_pct  = 0.0
        self.gift_nifty           = 0.0
        self.gift_nifty_change_pct = 0.0

        # ── FII / DII (NSE API) ───────────────────────────────────────────────
        self.fii_net_buy          = 0.0
        self.dii_net_buy          = 0.0
        self.fii_dii_date         = ""

        # ── Commodities (Yahoo Finance) ───────────────────────────────────────
        self.crude_brent          = 0.0
        self.crude_change_pct     = 0.0
        self.gold_usd             = 0.0
        self.gold_change_pct      = 0.0

        # ── Currency ──────────────────────────────────────────────────────────
        self.usdinr               = 0.0   # from Zerodha
        self.usdinr_change_pct    = 0.0
        self.eurinr               = 0.0   # from Yahoo Finance
        self.eurinr_change_pct    = 0.0

        # ── Events & News ─────────────────────────────────────────────────────
        self.high_impact_events   : list = []
        self.news_headlines       : list = []

        # ── Computed ──────────────────────────────────────────────────────────
        self.bias_score           = 0
        self.bias_label           = "Neutral"
        self.bias_reasoning       : list = []

        # ── Fetch status ──────────────────────────────────────────────────────
        self.sources_ok           : dict = {}
        self.errors               : list = []

    def to_dict(self) -> dict:
        return {
            "timestamp":               self.timestamp.isoformat(),
            "nifty_value":             self.nifty_value,
            "nifty_change_pct":        self.nifty_change_pct,
            "banknifty_value":         self.banknifty_value,
            "banknifty_change_pct":    self.banknifty_change_pct,
            "india_vix":               self.india_vix,
            "dow_jones":               self.dow_jones,
            "dow_change_pct":          self.dow_change_pct,
            "sp500":                   self.sp500,
            "sp500_change_pct":        self.sp500_change_pct,
            "nasdaq":                  self.nasdaq,
            "nasdaq_change_pct":       self.nasdaq_change_pct,
            "nikkei":                  self.nikkei,
            "nikkei_change_pct":       self.nikkei_change_pct,
            "hangseng":                self.hangseng,
            "hangseng_change_pct":     self.hangseng_change_pct,
            "gift_nifty":              self.gift_nifty,
            "gift_nifty_change_pct":   self.gift_nifty_change_pct,
            "fii_net_buy":             self.fii_net_buy,
            "dii_net_buy":             self.dii_net_buy,
            "crude_brent":             self.crude_brent,
            "crude_change_pct":        self.crude_change_pct,
            "gold_usd":                self.gold_usd,
            "gold_change_pct":         self.gold_change_pct,
            "usdinr":                  self.usdinr,
            "usdinr_change_pct":       self.usdinr_change_pct,
            "eurinr":                  self.eurinr,
            "eurinr_change_pct":       self.eurinr_change_pct,
            "bias_score":              self.bias_score,
            "bias_label":              self.bias_label,
            "bias_reasoning":          self.bias_reasoning,
            "high_impact_events":      self.high_impact_events,
            "news_headlines":          self.news_headlines,
            "sources_ok":              self.sources_ok,
            "errors":                  self.errors,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  MarketIntel
# ─────────────────────────────────────────────────────────────────────────────

class MarketIntel:
    """
    Fetches and aggregates live market intelligence.
    Pass an authenticated KiteConnect instance for Indian market data.
    Falls back to NSE API if kite is None (e.g. during testing).
    """

    def __init__(self, kite=None):
        """
        Args:
            kite : Authenticated KiteConnect instance (recommended)
                   If None, falls back to NSE web API for Indian indices
        """
        self._kite    = kite
        self._snapshot: Optional[IntelSnapshot] = None
        self._lock    = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

        source = "Zerodha Kite API" if kite else "NSE fallback (no Kite provided)"
        log.info(f"MarketIntel initialised — Indian indices source: {source}")

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self) -> IntelSnapshot:
        """Fetch all sources and compute bias score."""
        log.info("=" * 55)
        log.info("  Market Intelligence Refresh...")
        log.info("=" * 55)

        snap = IntelSnapshot()

        self._fetch_indian_indices(snap)
        self._fetch_global_markets(snap)
        self._fetch_fii_dii(snap)
        self._fetch_commodities(snap)
        self._fetch_currency(snap)
        self._fetch_news_events(snap)
        self._compute_bias_score(snap)

        with self._lock:
            self._snapshot = snap

        ok = sum(1 for v in snap.sources_ok.values() if v)
        log.info(
            f"Refresh done — {ok}/{len(snap.sources_ok)} sources OK | "
            f"Bias: {snap.bias_score:+d} ({snap.bias_label})"
        )
        return snap

    def get_snapshot(self) -> Optional[IntelSnapshot]:
        with self._lock:
            return self._snapshot

    def get_bias_score(self) -> int:
        with self._lock:
            return self._snapshot.bias_score if self._snapshot else 0

    def get_bias_label(self) -> str:
        with self._lock:
            return self._snapshot.bias_label if self._snapshot else "Unknown"

    def should_trade_calls(self) -> bool:
        return self.get_bias_score() >= 0

    def should_trade_puts(self) -> bool:
        return self.get_bias_score() <= 0

    def has_high_impact_event(self) -> bool:
        with self._lock:
            return bool(self._snapshot and self._snapshot.high_impact_events)

    def start_auto_refresh(self, interval_minutes: int = 10):
        if self._running:
            log.warning("Auto-refresh already running.")
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._auto_refresh_loop,
            args=(interval_minutes,),
            daemon=True,
            name="MarketIntelRefresh"
        )
        self._thread.start()
        log.info(f"Auto-refresh started — every {interval_minutes} min.")

    def stop_auto_refresh(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Auto-refresh stopped.")

    # ── Auto Refresh Loop ─────────────────────────────────────────────────────

    def _auto_refresh_loop(self, interval_minutes: int):
        try:
            self.refresh()
        except Exception as e:
            log.error(f"Initial intel refresh failed: {e}")

        while self._running:
            for _ in range(interval_minutes * 60):
                if not self._running:
                    break
                time_module.sleep(1)
            if self._running:
                try:
                    self.refresh()
                except Exception as e:
                    log.error(f"Auto intel refresh failed: {e}")

    # ── Source 1: Indian Indices via ZERODHA ──────────────────────────────────

    def _fetch_indian_indices(self, snap: IntelSnapshot):
        """
        Fetch Nifty 50, BankNifty, India VIX and USD/INR directly from
        Zerodha Kite API using kite.quote(). This is the preferred method —
        official, real-time, authenticated, never gets blocked.
        Falls back to NSE web API only if kite is not available.
        """
        source = "indian_indices"

        # ── Primary: Zerodha kite.quote() ────────────────────────────────────
        if self._kite:
            try:
                instruments = ["NSE:NIFTY 50", "NSE:NIFTY BANK", "NSE:INDIA VIX"]
                quotes = self._kite.quote(instruments)

                # Nifty 50
                n = quotes.get("NSE:NIFTY 50", {})
                snap.nifty_value      = float(n.get("last_price", 0))
                prev                  = float(n.get("ohlc", {}).get("close", snap.nifty_value) or snap.nifty_value)
                snap.nifty_change_pct = round(((snap.nifty_value - prev) / prev * 100) if prev else 0, 2)

                # BankNifty
                b = quotes.get("NSE:NIFTY BANK", {})
                snap.banknifty_value      = float(b.get("last_price", 0))
                prev                      = float(b.get("ohlc", {}).get("close", snap.banknifty_value) or snap.banknifty_value)
                snap.banknifty_change_pct = round(((snap.banknifty_value - prev) / prev * 100) if prev else 0, 2)

                # India VIX
                v = quotes.get("NSE:INDIA VIX", {})
                snap.india_vix = float(v.get("last_price", 0))

                # USD/INR from Zerodha
                try:
                    fx = self._kite.quote(["NSE:USDINR"])
                    u  = fx.get("NSE:USDINR", {})
                    snap.usdinr      = float(u.get("last_price", 0))
                    prev_fx          = float(u.get("ohlc", {}).get("close", snap.usdinr) or snap.usdinr)
                    snap.usdinr_change_pct = round(((snap.usdinr - prev_fx) / prev_fx * 100) if prev_fx else 0, 2)
                except Exception:
                    pass   # USD/INR will be fetched from Yahoo as fallback

                snap.sources_ok[source] = True
                log.info(
                    f"  [Zerodha] Nifty: {snap.nifty_value:,.1f} ({snap.nifty_change_pct:+.2f}%) | "
                    f"BankNifty: {snap.banknifty_value:,.1f} ({snap.banknifty_change_pct:+.2f}%) | "
                    f"VIX: {snap.india_vix:.2f} | USD/INR: {snap.usdinr:.2f}"
                )
                return   # ✅ Done — no need for fallback

            except Exception as e:
                log.warning(f"  [Zerodha] kite.quote() failed: {e} — trying NSE fallback")
                snap.errors.append(f"Zerodha quote failed: {e}")

        # ── Fallback: NSE web API (used when kite not available / failed) ─────
        self._fetch_indian_indices_nse_fallback(snap)

    def _fetch_indian_indices_nse_fallback(self, snap: IntelSnapshot):
        """NSE web API fallback for Indian indices. Used only when Kite unavailable."""
        source = "indian_indices"
        try:
            url  = "https://www.nseindia.com/api/allIndices"
            resp = self._session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("data", []):
                name = item.get("index", "")
                if name == "NIFTY 50":
                    snap.nifty_value      = float(item.get("last", 0))
                    snap.nifty_change_pct = float(item.get("percentChange", 0))
                elif name == "NIFTY BANK":
                    snap.banknifty_value      = float(item.get("last", 0))
                    snap.banknifty_change_pct = float(item.get("percentChange", 0))
                elif name == "INDIA VIX":
                    snap.india_vix = float(item.get("last", 0))

            snap.sources_ok[source] = True
            log.info(
                f"  [NSE fallback] Nifty: {snap.nifty_value:,.1f} | "
                f"BankNifty: {snap.banknifty_value:,.1f} | VIX: {snap.india_vix:.2f}"
            )
        except Exception as e:
            snap.sources_ok[source] = False
            snap.errors.append(f"NSE fallback failed: {e}")
            log.warning(f"  [NSE fallback] Failed: {e}")

    # ── Source 2: Global Markets (Yahoo Finance) ──────────────────────────────

    def _fetch_global_markets(self, snap: IntelSnapshot):
        """Global indices from Yahoo Finance. Zerodha doesn't carry US/Asia markets."""
        source  = "global_markets"
        symbols = {
            "^DJI":  ("dow_jones",  "dow_change_pct"),
            "^GSPC": ("sp500",      "sp500_change_pct"),
            "^IXIC": ("nasdaq",     "nasdaq_change_pct"),
            "^N225": ("nikkei",     "nikkei_change_pct"),
            "^HSI":  ("hangseng",   "hangseng_change_pct"),
        }

        fetched = False
        for symbol, (price_key, change_key) in symbols.items():
            try:
                url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
                resp = self._session.get(url, timeout=TIMEOUT)
                resp.raise_for_status()
                meta       = resp.json()["chart"]["result"][0]["meta"]
                price      = float(meta.get("regularMarketPrice", 0))
                prev_close = float(meta.get("chartPreviousClose", meta.get("previousClose", price)))
                change_pct = round(((price - prev_close) / prev_close * 100) if prev_close else 0, 2)
                setattr(snap, price_key,  price)
                setattr(snap, change_key, change_pct)
                fetched = True
            except Exception as e:
                log.debug(f"  [Global] {symbol}: {e}")

        snap.sources_ok[source] = fetched
        log.info(
            f"  [Global] Dow: {snap.dow_jones:,.0f} ({snap.dow_change_pct:+.2f}%) | "
            f"S&P: {snap.sp500:,.0f} ({snap.sp500_change_pct:+.2f}%) | "
            f"Nasdaq: {snap.nasdaq:,.0f} ({snap.nasdaq_change_pct:+.2f}%)"
        )

    # ── Source 3: FII / DII (NSE API — not available in Kite) ────────────────

    def _fetch_fii_dii(self, snap: IntelSnapshot):
        """
        FII/DII net buy/sell from NSE API.
        This data is NOT available via Zerodha Kite — NSE is the only source.
        """
        source = "fii_dii"
        try:
            url  = "https://www.nseindia.com/api/fiidiiTradeReact"
            resp = self._session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            if data and len(data) > 0:
                latest       = data[0]
                fii_buy      = float(latest.get("fiiBuyValue",  0) or 0)
                fii_sell     = float(latest.get("fiiSellValue", 0) or 0)
                dii_buy      = float(latest.get("diiBuyValue",  0) or 0)
                dii_sell     = float(latest.get("diiSellValue", 0) or 0)
                snap.fii_net_buy  = round(fii_buy - fii_sell, 2)
                snap.dii_net_buy  = round(dii_buy - dii_sell, 2)
                snap.fii_dii_date = latest.get("date", str(date.today()))

            snap.sources_ok[source] = True
            log.info(
                f"  [FII/DII] FII: ₹{snap.fii_net_buy:+,.0f}Cr | "
                f"DII: ₹{snap.dii_net_buy:+,.0f}Cr"
            )
        except Exception as e:
            snap.sources_ok[source] = False
            snap.errors.append(f"{source}: {e}")
            log.warning(f"  [FII/DII] Failed: {e}")

    # ── Source 4: Commodities (Yahoo Finance) ─────────────────────────────────

    def _fetch_commodities(self, snap: IntelSnapshot):
        """Crude Oil and Gold from Yahoo Finance."""
        source  = "commodities"
        symbols = {
            "BZ=F": ("crude_brent", "crude_change_pct"),
            "GC=F": ("gold_usd",    "gold_change_pct"),
        }
        fetched = False
        for symbol, (pk, ck) in symbols.items():
            try:
                url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
                resp = self._session.get(url, timeout=TIMEOUT)
                resp.raise_for_status()
                meta       = resp.json()["chart"]["result"][0]["meta"]
                price      = float(meta.get("regularMarketPrice", 0))
                prev_close = float(meta.get("chartPreviousClose", meta.get("previousClose", price)))
                change_pct = round(((price - prev_close) / prev_close * 100) if prev_close else 0, 2)
                setattr(snap, pk, price)
                setattr(snap, ck, change_pct)
                fetched = True
            except Exception as e:
                log.debug(f"  [Commodities] {symbol}: {e}")

        snap.sources_ok[source] = fetched
        log.info(
            f"  [Commodities] Crude: ${snap.crude_brent:.2f} ({snap.crude_change_pct:+.2f}%) | "
            f"Gold: ${snap.gold_usd:,.0f} ({snap.gold_change_pct:+.2f}%)"
        )

    # ── Source 5: Currency ────────────────────────────────────────────────────

    def _fetch_currency(self, snap: IntelSnapshot):
        """
        USD/INR: fetched from Zerodha in _fetch_indian_indices if kite available.
        EUR/INR: from Yahoo Finance (not in Kite).
        This method fetches EUR/INR and USD/INR fallback if Zerodha didn't get it.
        """
        source  = "currency"
        symbols = {}

        # Only fetch USD/INR from Yahoo if Zerodha didn't get it
        if snap.usdinr == 0:
            symbols["USDINR=X"] = ("usdinr", "usdinr_change_pct")

        symbols["EURINR=X"] = ("eurinr", "eurinr_change_pct")

        fetched = snap.usdinr > 0   # already got it from Zerodha
        for symbol, (pk, ck) in symbols.items():
            try:
                url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
                resp = self._session.get(url, timeout=TIMEOUT)
                resp.raise_for_status()
                meta       = resp.json()["chart"]["result"][0]["meta"]
                price      = float(meta.get("regularMarketPrice", 0))
                prev_close = float(meta.get("chartPreviousClose", meta.get("previousClose", price)))
                change_pct = round(((price - prev_close) / prev_close * 100) if prev_close else 0, 2)
                setattr(snap, pk, price)
                setattr(snap, ck, change_pct)
                fetched = True
            except Exception as e:
                log.debug(f"  [Currency] {symbol}: {e}")

        snap.sources_ok[source] = fetched
        src_label = "Zerodha" if self._kite and snap.usdinr > 0 else "Yahoo"
        log.info(
            f"  [Currency] USD/INR: {snap.usdinr:.2f} ({snap.usdinr_change_pct:+.2f}%) [{src_label}] | "
            f"EUR/INR: {snap.eurinr:.2f} ({snap.eurinr_change_pct:+.2f}%) [Yahoo]"
        )

    # ── Source 6: News & Events ───────────────────────────────────────────────

    def _fetch_news_events(self, snap: IntelSnapshot):
        """NSE news headlines. High-impact events detected from market data."""
        source = "news_events"
        try:
            url  = "https://www.nseindia.com/api/latest-circular"
            resp = self._session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            snap.news_headlines = [
                (item.get("subject") or item.get("headline") or "").strip()
                for item in data[:5]
                if item.get("subject") or item.get("headline")
            ]
            snap.sources_ok[source] = True
            log.info(f"  [News] {len(snap.news_headlines)} headlines fetched")
        except Exception as e:
            snap.sources_ok[source] = False
            snap.errors.append(f"{source}: {e}")
            log.warning(f"  [News] Failed: {e}")

    # ── High-Impact Event Detection ───────────────────────────────────────────

    def _check_high_impact_events(self, snap: IntelSnapshot):
        """Detect high-impact conditions from market data."""
        events = []

        if snap.india_vix > 20:
            events.append(f"High VIX Alert: {snap.india_vix:.1f} (above 20 — volatile)")
        if abs(snap.usdinr_change_pct) > 0.5:
            direction = "weakening" if snap.usdinr_change_pct > 0 else "strengthening"
            events.append(f"INR {direction} sharply: {snap.usdinr_change_pct:+.2f}%")
        if abs(snap.crude_change_pct) > 3.0:
            direction = "surge" if snap.crude_change_pct > 0 else "crash"
            events.append(f"Crude oil {direction}: {snap.crude_change_pct:+.2f}%")
        if snap.dow_change_pct < -1.5:
            events.append(f"Dow fell {snap.dow_change_pct:.2f}% — expect gap down")
        if snap.dow_change_pct > 1.5:
            events.append(f"Dow rose {snap.dow_change_pct:+.2f}% — expect gap up")

        snap.high_impact_events = events
        if events:
            log.warning(f"  [Events] ⚠️  {len(events)} high-impact event(s):")
            for e in events:
                log.warning(f"     → {e}")

    # ── Bias Score ────────────────────────────────────────────────────────────

    def _compute_bias_score(self, snap: IntelSnapshot):
        """Compute Market Bias Score -10 to +10 from all data sources."""
        score   = 0
        reasons = []

        # Always run event detection before dampening
        self._check_high_impact_events(snap)

        # 1. Gift Nifty / Dow direction
        if snap.gift_nifty_change_pct > 0.5:
            score += 2; reasons.append(f"+2: Gift Nifty positive ({snap.gift_nifty_change_pct:+.2f}%)")
        elif snap.gift_nifty_change_pct < -0.5:
            score -= 2; reasons.append(f"-2: Gift Nifty negative ({snap.gift_nifty_change_pct:+.2f}%)")

        if snap.dow_change_pct > 0.5:
            score += 1; reasons.append(f"+1: Dow Jones positive ({snap.dow_change_pct:+.2f}%)")
        elif snap.dow_change_pct < -0.5:
            score -= 1; reasons.append(f"-1: Dow Jones negative ({snap.dow_change_pct:+.2f}%)")

        # 2. Nifty / BankNifty momentum (from Zerodha)
        if snap.nifty_change_pct > 0.5:
            score += 2; reasons.append(f"+2: Nifty strong ({snap.nifty_change_pct:+.2f}%)")
        elif snap.nifty_change_pct < -0.5:
            score -= 2; reasons.append(f"-2: Nifty weak ({snap.nifty_change_pct:+.2f}%)")

        if snap.banknifty_change_pct > 0.5:
            score += 1; reasons.append(f"+1: BankNifty positive ({snap.banknifty_change_pct:+.2f}%)")
        elif snap.banknifty_change_pct < -0.5:
            score -= 1; reasons.append(f"-1: BankNifty negative ({snap.banknifty_change_pct:+.2f}%)")

        # 3. FII activity
        if snap.fii_net_buy > 1000:
            score += 2; reasons.append(f"+2: FII net buying ₹{snap.fii_net_buy:,.0f}Cr")
        elif snap.fii_net_buy > 0:
            score += 1; reasons.append(f"+1: FII mild buying ₹{snap.fii_net_buy:,.0f}Cr")
        elif snap.fii_net_buy < -1000:
            score -= 2; reasons.append(f"-2: FII net selling ₹{snap.fii_net_buy:,.0f}Cr")
        elif snap.fii_net_buy < 0:
            score -= 1; reasons.append(f"-1: FII mild selling ₹{snap.fii_net_buy:,.0f}Cr")

        # 4. Asian markets
        asia = 0
        if snap.nikkei_change_pct   > 0.5: asia += 1
        elif snap.nikkei_change_pct < -0.5: asia -= 1
        if snap.hangseng_change_pct  > 0.5: asia += 1
        elif snap.hangseng_change_pct < -0.5: asia -= 1
        if asia > 0:
            score += 1; reasons.append(f"+1: Asian markets positive (Nikkei {snap.nikkei_change_pct:+.2f}%)")
        elif asia < 0:
            score -= 1; reasons.append(f"-1: Asian markets negative (Nikkei {snap.nikkei_change_pct:+.2f}%)")

        # 5. India VIX
        if snap.india_vix > 20:
            score -= 2; reasons.append(f"-2: High India VIX {snap.india_vix:.1f} (volatile)")
        elif snap.india_vix > 15:
            score -= 1; reasons.append(f"-1: Elevated VIX {snap.india_vix:.1f}")

        # 6. USD/INR (from Zerodha if available)
        if snap.usdinr_change_pct > 0.3:
            score -= 1; reasons.append(f"-1: INR weakening (USD/INR {snap.usdinr:.2f})")
        elif snap.usdinr_change_pct < -0.3:
            score += 1; reasons.append(f"+1: INR strengthening (USD/INR {snap.usdinr:.2f})")

        # 7. Dampen score if high-impact events detected
        if snap.high_impact_events:
            dampened = int(score * 0.5)
            if score != dampened:
                reasons.append(
                    f"⚠️  Score dampened {score:+d}→{dampened:+d} "
                    f"({len(snap.high_impact_events)} high-impact event(s))"
                )
                score = dampened

        score = max(-10, min(10, score))

        if score >= 6:    label = "Strongly Bullish"
        elif score >= 2:  label = "Mildly Bullish"
        elif score >= -1: label = "Neutral"
        elif score >= -5: label = "Mildly Bearish"
        else:             label = "Strongly Bearish"

        snap.bias_score     = score
        snap.bias_label     = label
        snap.bias_reasoning = reasons

        log.info(f"  [Bias] Score: {score:+d} → {label}")
        for r in reasons:
            log.info(f"    {r}")

    # ── Print Snapshot ────────────────────────────────────────────────────────

    def print_snapshot(self, snap: Optional[IntelSnapshot] = None):
        s = snap or self.get_snapshot()
        if not s:
            print("No snapshot available. Call refresh() first.")
            return

        print("\n" + "═" * 60)
        print(f"  MARKET INTELLIGENCE SNAPSHOT")
        print(f"  {s.timestamp.strftime('%Y-%m-%d %H:%M:%S IST')}")
        print("═" * 60)

        src = "Zerodha ✅" if self._kite else "NSE fallback"
        print(f"\n📈 INDIAN INDICES  [{src}]")
        print(f"   Nifty 50  : {s.nifty_value:>10,.1f}  ({s.nifty_change_pct:+.2f}%)")
        print(f"   BankNifty : {s.banknifty_value:>10,.1f}  ({s.banknifty_change_pct:+.2f}%)")
        print(f"   India VIX : {s.india_vix:>10.2f}")

        print(f"\n🌏 GLOBAL MARKETS  [Yahoo Finance]")
        print(f"   Dow Jones : {s.dow_jones:>10,.0f}  ({s.dow_change_pct:+.2f}%)")
        print(f"   S&P 500   : {s.sp500:>10,.0f}  ({s.sp500_change_pct:+.2f}%)")
        print(f"   Nasdaq    : {s.nasdaq:>10,.0f}  ({s.nasdaq_change_pct:+.2f}%)")
        print(f"   Nikkei    : {s.nikkei:>10,.0f}  ({s.nikkei_change_pct:+.2f}%)")
        print(f"   Hang Seng : {s.hangseng:>10,.0f}  ({s.hangseng_change_pct:+.2f}%)")

        print(f"\n💹 FII / DII  [NSE]  ({s.fii_dii_date})")
        print(f"   FII Net   : ₹{s.fii_net_buy:+,.0f} Cr")
        print(f"   DII Net   : ₹{s.dii_net_buy:+,.0f} Cr")

        print(f"\n🛢️  COMMODITIES  [Yahoo Finance]")
        print(f"   Crude(Brent): ${s.crude_brent:.2f}  ({s.crude_change_pct:+.2f}%)")
        print(f"   Gold (USD)  : ${s.gold_usd:,.0f}  ({s.gold_change_pct:+.2f}%)")

        fx_src = "Zerodha ✅" if self._kite and s.usdinr > 0 else "Yahoo Finance"
        print(f"\n💱 CURRENCY")
        print(f"   USD/INR [{fx_src}]: {s.usdinr:.2f}  ({s.usdinr_change_pct:+.2f}%)")
        print(f"   EUR/INR [Yahoo]  : {s.eurinr:.2f}  ({s.eurinr_change_pct:+.2f}%)")

        if s.high_impact_events:
            print(f"\n⚠️  HIGH-IMPACT EVENTS")
            for e in s.high_impact_events:
                print(f"   → {e}")

        if s.news_headlines:
            print(f"\n📰 LATEST NSE NEWS")
            for h in s.news_headlines[:3]:
                print(f"   • {h[:75]}{'...' if len(h) > 75 else ''}")

        bias_emoji = "🟢" if s.bias_score > 1 else "🔴" if s.bias_score < -1 else "🟡"
        print(f"\n{'═'*60}")
        print(f"  {bias_emoji} MARKET BIAS: {s.bias_score:+d} — {s.bias_label.upper()}")
        print(f"{'═'*60}")
        for r in s.bias_reasoning:
            print(f"    {r}")

        ok  = sum(1 for v in s.sources_ok.values() if v)
        tot = len(s.sources_ok)
        print(f"\n  Sources: {ok}/{tot} OK | Errors: {len(s.errors)}")
        print()
