"""
strategies/momentum_strike.py — Momentum Strike Strategy
══════════════════════════════════════════════════════════
Our primary intraday options trading strategy for Nifty/BankNifty.

Strategy Logic:
  1. Wait for Opening Range to be established (first 15-min candle)
  2. On each new 15-min candle, evaluate 5 conditions
  3. Need 3/5 conditions for a signal (configurable)
  4. Then drop to 5-min chart and wait for pullback to EMA9 for entry

5 Conditions — Bullish (CALL):
  C1. Price broke above Opening Range High on 15-min close
  C2. EMA9 > EMA21 on 15-min chart  (trend direction)
  C3. RSI > 55 on 15-min chart      (momentum)
  C4. Price > VWAP on 15-min chart  (institutional bias)
  C5. Volume on breakout candle > 1.5x average volume (conviction)

5 Conditions — Bearish (PUT):
  C1. Price broke below Opening Range Low on 15-min close
  C2. EMA9 < EMA21 on 15-min chart
  C3. RSI < 45 on 15-min chart
  C4. Price < VWAP on 15-min chart
  C5. Volume spike on breakdown candle

Entry Refinement (5-min chart):
  - After 15-min signal, wait for 5-min pullback to EMA9
  - Enter on first 5-min candle that touches EMA9 and bounces
  - If no pullback within 3 candles, enter at market (momentum entry)

Market Bias Integration:
  - If bias_score >= 2: only look for CALL setups
  - If bias_score <= -2: only look for PUT setups
  - If -1 to +1 (neutral): require 4/5 conditions instead of 3/5
"""

import pandas as pd
import numpy as np
from typing import Optional

from strategies.base_strategy import BaseStrategy, Signal, SIGNAL_NONE
from modules.logger import get_logger

log = get_logger(__name__)


class MomentumStrike(BaseStrategy):
    """
    Momentum Strike — Opening Range Breakout with EMA + RSI + VWAP + Volume.
    Primary strategy for Nifty/BankNifty intraday options buying.
    """

    name        = "Momentum Strike"
    description = "Opening Range Breakout with EMA9/21 + RSI + VWAP + Volume confirmation"
    instruments = ["NIFTY", "BANKNIFTY"]
    option_type = "BUY"
    version     = "1.0"

    min_candles_15m = 3
    min_candles_5m  = 5

    def generate_signal(
        self,
        symbol       : str,
        candles_15m  : pd.DataFrame,
        candles_5m   : pd.DataFrame,
        opening_range: Optional[dict],
        market_bias  : int = 0,
    ) -> Signal:
        """
        Main signal generation method.
        Called by the StrategyEngine on every new 15-min candle close.
        """
        # ── Pre-checks ────────────────────────────────────────────────────────
        can, reason = self.can_generate_signal(candles_15m, candles_5m, opening_range)
        if not can:
            return SIGNAL_NONE(reason)

        # ── Calculate indicators on 15-min chart ──────────────────────────────
        df15 = self._add_indicators(candles_15m.copy())
        if df15 is None or df15.empty:
            return SIGNAL_NONE("Indicator calculation failed")

        latest       = df15.iloc[-1]          # most recent completed 15-min candle
        current_price = float(latest["close"])
        or_high      = float(opening_range["high"])
        or_low       = float(opening_range["low"])

        min_conditions = self.config.get("min_signal_conditions", 3)

        # ── Adjust required conditions based on market bias ───────────────────
        # Neutral market → be stricter (require 4/5 instead of 3/5)
        if -1 <= market_bias <= 1:
            min_conditions = max(min_conditions, 4)
            log.debug(f"Neutral bias ({market_bias}) — requiring {min_conditions}/5 conditions")

        # Strong bias → only look for aligned direction
        force_call = market_bias >= 4
        force_put  = market_bias <= -4

        # ── Score bullish conditions ──────────────────────────────────────────
        bull_score, bull_detail = self._score_bullish(df15, latest, or_high, or_low)

        # ── Score bearish conditions ──────────────────────────────────────────
        bear_score, bear_detail = self._score_bearish(df15, latest, or_high, or_low)

        log.debug(
            f"[{symbol}] Bull:{bull_score}/5 Bear:{bear_score}/5 | "
            f"Bias:{market_bias:+d} | MinCond:{min_conditions}"
        )

        # ── Determine signal ──────────────────────────────────────────────────
        action     = "NONE"
        score      = 0
        detail     = []
        entry_type = ""

        if force_put:
            # Strong bearish bias — only trade puts
            if bear_score >= min_conditions:
                action, score, detail = "PUT", bear_score, bear_detail
        elif force_call:
            # Strong bullish bias — only trade calls
            if bull_score >= min_conditions:
                action, score, detail = "CALL", bull_score, bull_detail
        elif bull_score >= min_conditions and bull_score >= bear_score:
            action, score, detail = "CALL", bull_score, bull_detail
        elif bear_score >= min_conditions and bear_score > bull_score:
            action, score, detail = "PUT", bear_score, bear_detail

        if action == "NONE":
            reason = (
                f"Conditions not met — "
                f"Bull:{bull_score}/5 Bear:{bear_score}/5 "
                f"(need {min_conditions})"
            )
            return SIGNAL_NONE(reason)

        # ── Refine entry using 5-min chart ────────────────────────────────────
        entry_type, entry_note = self._find_entry_type(candles_5m, action)

        # ── Suggest strike ────────────────────────────────────────────────────
        suggested_strike = self._suggest_strike(symbol, current_price, action)

        # ── Build confidence score (0-100) ────────────────────────────────────
        confidence = self._calc_confidence(score, market_bias, action, entry_type)

        reason = (
            f"{score}/5 conditions met | "
            f"Bias:{market_bias:+d} | "
            f"Entry:{entry_type} | "
            f"{entry_note}"
        )

        signal = Signal(
            action           = action,
            symbol           = symbol,
            timeframe        = 15,
            confidence       = confidence,
            conditions_met   = score,
            conditions_total = 5,
            conditions_detail= detail,
            entry_type       = entry_type,
            suggested_strike = suggested_strike,
            reason           = reason,
            bias_score       = market_bias,
            opening_range_high = or_high,
            opening_range_low  = or_low,
            current_price    = current_price,
        )

        log.info(f"🎯 Signal generated: {signal}")
        return signal

    # ── Indicator Calculation ─────────────────────────────────────────────────

    def _add_indicators(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """
        Add EMA9, EMA21, RSI14, VWAP to the 15-min DataFrame.
        Uses the 'ta' library (pandas-ta replacement).
        """
        try:
            import ta

            if len(df) < 14:
                return None

            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]

            # EMA 9 and EMA 21
            df["ema9"]  = ta.trend.ema_indicator(close, window=9)
            df["ema21"] = ta.trend.ema_indicator(close, window=21)

            # RSI 14
            df["rsi"] = ta.momentum.rsi(close, window=14)

            # VWAP — calculated manually (ta library VWAP needs intraday reset)
            df["vwap"] = self._calc_vwap(df)

            # Average volume (20-candle rolling)
            df["avg_volume"] = volume.rolling(window=20, min_periods=1).mean()

            return df

        except ImportError:
            # Fallback: calculate manually without ta library
            return self._add_indicators_manual(df)
        except Exception as e:
            log.error(f"Indicator calculation error: {e}")
            return None

    def _add_indicators_manual(self, df: pd.DataFrame) -> pd.DataFrame:
        """Manual EMA, RSI, VWAP calculation — no external library needed."""
        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]

        # EMA using pandas ewm
        df["ema9"]  = close.ewm(span=9,  adjust=False).mean()
        df["ema21"] = close.ewm(span=21, adjust=False).mean()

        # RSI manual
        df["rsi"] = self._calc_rsi(close, 14)

        # VWAP
        df["vwap"] = self._calc_vwap(df)

        # Average volume
        df["avg_volume"] = volume.rolling(window=20, min_periods=1).mean()

        return df

    def _calc_rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI manually — handles zero-loss (all gains) correctly."""
        delta = close.diff()
        gain  = delta.where(delta > 0, 0.0)
        loss  = -delta.where(delta < 0, 0.0)

        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

        # When avg_loss is 0 (no losing candles), RSI = 100
        # When avg_gain is 0 (no gaining candles), RSI = 0
        rsi = avg_gain.copy()
        mask_loss_zero = avg_loss == 0
        mask_both_zero = (avg_gain == 0) & (avg_loss == 0)

        rs = avg_gain / avg_loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        rsi[mask_loss_zero & ~mask_both_zero] = 100.0
        rsi[mask_both_zero] = 50.0
        return rsi.fillna(50)

    def _calc_vwap(self, df: pd.DataFrame) -> pd.Series:
        """Calculate VWAP = cumulative(typical_price * volume) / cumulative(volume)."""
        tp  = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"].replace(0, 1)   # avoid division by zero
        return (tp * vol).cumsum() / vol.cumsum()

    # ── Condition Scoring ─────────────────────────────────────────────────────

    def _score_bullish(
        self, df: pd.DataFrame, latest: pd.Series,
        or_high: float, or_low: float
    ) -> tuple[int, list]:
        """Score 5 bullish conditions. Returns (score, detail_list)."""
        score  = 0
        detail = []

        close      = float(latest["close"])
        ema9       = float(latest.get("ema9",  0) or 0)
        ema21      = float(latest.get("ema21", 0) or 0)
        rsi        = float(latest.get("rsi",   50) or 50)
        vwap       = float(latest.get("vwap",  0) or 0)
        volume     = float(latest.get("volume",0) or 0)
        avg_volume = float(latest.get("avg_volume", volume) or volume)

        # C1: Price closed above Opening Range High
        if close > or_high:
            score += 1
            detail.append(f"✅ C1: Close {close:.1f} > OR High {or_high:.1f}")
        else:
            detail.append(f"❌ C1: Close {close:.1f} ≤ OR High {or_high:.1f}")

        # C2: EMA9 > EMA21 (bullish trend)
        if ema9 > 0 and ema21 > 0 and ema9 > ema21:
            score += 1
            detail.append(f"✅ C2: EMA9 {ema9:.1f} > EMA21 {ema21:.1f}")
        else:
            detail.append(f"❌ C2: EMA9 {ema9:.1f} ≤ EMA21 {ema21:.1f}")

        # C3: RSI > 55 (bullish momentum)
        if rsi > 55:
            score += 1
            detail.append(f"✅ C3: RSI {rsi:.1f} > 55")
        else:
            detail.append(f"❌ C3: RSI {rsi:.1f} ≤ 55")

        # C4: Price > VWAP (institutional bullish bias)
        if vwap > 0 and close > vwap:
            score += 1
            detail.append(f"✅ C4: Close {close:.1f} > VWAP {vwap:.1f}")
        else:
            detail.append(f"❌ C4: Close {close:.1f} ≤ VWAP {vwap:.1f}")

        # C5: Volume spike (conviction)
        vol_ratio = volume / avg_volume if avg_volume > 0 else 1
        if vol_ratio >= 1.5:
            score += 1
            detail.append(f"✅ C5: Volume spike {vol_ratio:.1f}x avg")
        else:
            detail.append(f"❌ C5: Volume {vol_ratio:.1f}x avg (need 1.5x)")

        return score, detail

    def _score_bearish(
        self, df: pd.DataFrame, latest: pd.Series,
        or_high: float, or_low: float
    ) -> tuple[int, list]:
        """Score 5 bearish conditions. Returns (score, detail_list)."""
        score  = 0
        detail = []

        close      = float(latest["close"])
        ema9       = float(latest.get("ema9",  0) or 0)
        ema21      = float(latest.get("ema21", 0) or 0)
        rsi        = float(latest.get("rsi",   50) or 50)
        vwap       = float(latest.get("vwap",  0) or 0)
        volume     = float(latest.get("volume",0) or 0)
        avg_volume = float(latest.get("avg_volume", volume) or volume)

        # C1: Price closed below Opening Range Low
        if close < or_low:
            score += 1
            detail.append(f"✅ C1: Close {close:.1f} < OR Low {or_low:.1f}")
        else:
            detail.append(f"❌ C1: Close {close:.1f} ≥ OR Low {or_low:.1f}")

        # C2: EMA9 < EMA21 (bearish trend)
        if ema9 > 0 and ema21 > 0 and ema9 < ema21:
            score += 1
            detail.append(f"✅ C2: EMA9 {ema9:.1f} < EMA21 {ema21:.1f}")
        else:
            detail.append(f"❌ C2: EMA9 {ema9:.1f} ≥ EMA21 {ema21:.1f}")

        # C3: RSI < 45 (bearish momentum)
        if rsi < 45:
            score += 1
            detail.append(f"✅ C3: RSI {rsi:.1f} < 45")
        else:
            detail.append(f"❌ C3: RSI {rsi:.1f} ≥ 45")

        # C4: Price < VWAP (institutional bearish bias)
        if vwap > 0 and close < vwap:
            score += 1
            detail.append(f"✅ C4: Close {close:.1f} < VWAP {vwap:.1f}")
        else:
            detail.append(f"❌ C4: Close {close:.1f} ≥ VWAP {vwap:.1f}")

        # C5: Volume spike (conviction)
        vol_ratio = volume / avg_volume if avg_volume > 0 else 1
        if vol_ratio >= 1.5:
            score += 1
            detail.append(f"✅ C5: Volume spike {vol_ratio:.1f}x avg")
        else:
            detail.append(f"❌ C5: Volume {vol_ratio:.1f}x avg (need 1.5x)")

        return score, detail

    # ── Entry Refinement (5-min chart) ────────────────────────────────────────

    def _find_entry_type(
        self, candles_5m: pd.DataFrame, action: str
    ) -> tuple[str, str]:
        """
        Analyse 5-min chart to determine best entry type.

        Returns:
            ("pullback", "Price pulled back to EMA9 — ideal entry") or
            ("breakout", "No pullback — entering at momentum breakout")
        """
        if candles_5m.empty or len(candles_5m) < 3:
            return "breakout", "Insufficient 5-min data — breakout entry"

        df5 = candles_5m.copy()
        df5["ema9"] = df5["close"].ewm(span=9, adjust=False).mean()

        # Look at last 3 five-min candles
        recent = df5.tail(3)

        for _, row in recent.iterrows():
            low   = float(row["low"])
            high  = float(row["high"])
            close = float(row["close"])
            ema9  = float(row.get("ema9", 0) or 0)

            if ema9 == 0:
                continue

            if action == "CALL":
                # Pullback: candle low touched EMA9 but closed above it
                touched_ema  = low <= ema9 * 1.002   # within 0.2%
                closed_above = close > ema9
                if touched_ema and closed_above:
                    return "pullback", f"5-min pulled back to EMA9 ({ema9:.1f}) and bounced ✅"

            elif action == "PUT":
                # Pullback: candle high touched EMA9 but closed below it
                touched_ema  = high >= ema9 * 0.998
                closed_below = close < ema9
                if touched_ema and closed_below:
                    return "pullback", f"5-min pulled back to EMA9 ({ema9:.1f}) and rejected ✅"

        return "breakout", "No pullback to EMA9 — entering at momentum breakout"

    # ── Strike Selection ──────────────────────────────────────────────────────

    def _suggest_strike(self, symbol: str, current_price: float, action: str) -> str:
        """
        Suggest ATM+1 strike (slightly OTM).
        Nifty strikes are in multiples of 50.
        BankNifty strikes are in multiples of 100.
        """
        if symbol == "NIFTY":
            strike_gap = 50
        elif symbol == "BANKNIFTY":
            strike_gap = 100
        else:
            strike_gap = 50

        # Round to nearest strike
        atm = round(current_price / strike_gap) * strike_gap

        if action == "CALL":
            strike = atm + strike_gap    # 1 strike OTM call
            return f"{int(strike)}CE"
        elif action == "PUT":
            strike = atm - strike_gap    # 1 strike OTM put
            return f"{int(strike)}PE"

        return ""

    # ── Confidence Score ──────────────────────────────────────────────────────

    def _calc_confidence(
        self,
        conditions_met: int,
        market_bias   : int,
        action        : str,
        entry_type    : str,
    ) -> int:
        """
        Calculate confidence score 0-100.
        Higher conditions + aligned bias + pullback entry = higher confidence.
        """
        # Base: conditions out of 5 → 0-60 points
        base = (conditions_met / 5) * 60

        # Bias alignment: +20 if bias strongly aligned, +10 if mildly, -10 if opposing
        if action == "CALL":
            if market_bias >= 4:   base += 20
            elif market_bias >= 2: base += 10
            elif market_bias <= -2: base -= 10
        elif action == "PUT":
            if market_bias <= -4:  base += 20
            elif market_bias <= -2: base += 10
            elif market_bias >= 2:  base -= 10

        # Entry type: pullback entry is better → +15
        if entry_type == "pullback":
            base += 15

        # Clamp to 0-100
        return max(0, min(100, int(base)))
