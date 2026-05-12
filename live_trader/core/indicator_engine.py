"""
Indicator naming and enrichment helpers.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd
import pandas_ta as ta

DEFAULT_ATR_PERIOD = 14
DEFAULT_ADX_PERIOD = 14
DEFAULT_MACD = (12, 26, 9)
DEFAULT_BBANDS = (20, 2.0)
DEFAULT_SUPERTREND = (10, 3.0)
DEFAULT_VOLUME_SMA = 20


def _tag(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace(".", "p")


def ema_col(period: int) -> str:
    return f"ema_{period}"


def sma_col(period: int) -> str:
    return f"sma_{period}"


def rsi_col(period: int) -> str:
    return f"rsi_{period}"


def atr_col(period: int) -> str:
    return "atr" if period == DEFAULT_ATR_PERIOD else f"atr_{period}"


def atr_pct_col(period: int) -> str:
    return "atr_pct" if period == DEFAULT_ATR_PERIOD else f"atr_pct_{period}"


def adx_cols(period: int) -> dict[str, str]:
    if period == DEFAULT_ADX_PERIOD:
        return {
            "adx": "adx",
            "di_plus": "di_plus",
            "di_minus": "di_minus",
        }
    suffix = _tag(period)
    return {
        "adx": f"adx_{suffix}",
        "di_plus": f"di_plus_{suffix}",
        "di_minus": f"di_minus_{suffix}",
    }


def macd_cols(fast: int, slow: int, signal: int) -> dict[str, str]:
    if (fast, slow, signal) == DEFAULT_MACD:
        return {
            "macd": "macd",
            "hist": "macd_hist",
            "signal": "macd_signal",
        }
    suffix = f"{fast}_{slow}_{signal}"
    return {
        "macd": f"macd_{suffix}",
        "hist": f"macd_hist_{suffix}",
        "signal": f"macd_signal_{suffix}",
    }


def bbands_cols(length: int, std_dev: float) -> dict[str, str]:
    if (length, float(std_dev)) == DEFAULT_BBANDS:
        return {
            "lower": "bb_lower",
            "mid": "bb_mid",
            "upper": "bb_upper",
            "bandwidth": "bb_bandwidth",
            "pct": "bb_pct",
        }
    suffix = f"{length}_{_tag(float(std_dev))}"
    return {
        "lower": f"bb_lower_{suffix}",
        "mid": f"bb_mid_{suffix}",
        "upper": f"bb_upper_{suffix}",
        "bandwidth": f"bb_bandwidth_{suffix}",
        "pct": f"bb_pct_{suffix}",
    }


def supertrend_cols(length: int, multiplier: float) -> dict[str, str]:
    if (length, float(multiplier)) == DEFAULT_SUPERTREND:
        return {
            "value": "supertrend",
            "direction": "supertrend_dir",
        }
    suffix = f"{length}_{_tag(float(multiplier))}"
    return {
        "value": f"supertrend_{suffix}",
        "direction": f"supertrend_dir_{suffix}",
    }


def volume_sma_col(period: int) -> str:
    return (
        "volume_sma_20"
        if period == DEFAULT_VOLUME_SMA
        else f"volume_sma_{period}"
    )


def volume_ratio_col(period: int) -> str:
    return "volume_ratio" if period == DEFAULT_VOLUME_SMA else f"volume_ratio_{period}"


class IndicatorPipeline:
    """Compute only the indicators required by the active strategies."""

    def __init__(self, regime_detector=None):
        self.regime_detector = regime_detector

    def build_plan(
        self,
        strategies: Sequence | None = None,
        *,
        include_regime: bool = True,
    ) -> dict[str, object]:
        plan: dict[str, object] = {
            "ema": set(),
            "sma": set(),
            "rsi": set(),
            "atr": set(),
            "adx": set(),
            "macd": set(),
            "bbands": set(),
            "supertrend": set(),
            "volume_sma": set(),
            "stochrsi": set(),
            "vwap": False,
            "microstructure": True,
        }

        if include_regime and self.regime_detector is not None:
            self._merge_requirements(plan, self.regime_detector.indicator_requirements())

        for strategy in strategies or []:
            self._merge_requirements(plan, strategy.indicator_requirements())

        return plan

    def enrich(
        self,
        df: pd.DataFrame,
        strategies: Sequence | None = None,
        *,
        include_regime: bool = True,
    ) -> pd.DataFrame:
        if df.empty or len(df) < 30:
            return df

        enriched = df.copy()
        plan = self.build_plan(strategies, include_regime=include_regime)

        for period in sorted(plan["ema"]):
            enriched[ema_col(period)] = ta.ema(enriched["close"], length=period)

        for period in sorted(plan["sma"]):
            enriched[sma_col(period)] = ta.sma(enriched["close"], length=period)

        for period in sorted(plan["rsi"]):
            enriched[rsi_col(period)] = ta.rsi(enriched["close"], length=period)

        for fast, slow, signal in sorted(plan["macd"]):
            macd = ta.macd(enriched["close"], fast=fast, slow=slow, signal=signal)
            if macd is None or macd.empty:
                continue
            cols = macd_cols(fast, slow, signal)
            enriched[cols["macd"]] = macd.iloc[:, 0]
            enriched[cols["hist"]] = macd.iloc[:, 1]
            enriched[cols["signal"]] = macd.iloc[:, 2]

        for length, std_dev in sorted(plan["bbands"]):
            bbands = ta.bbands(enriched["close"], length=length, std=std_dev)
            if bbands is None or bbands.empty:
                continue
            cols = bbands_cols(length, std_dev)
            enriched[cols["lower"]] = bbands.iloc[:, 0]
            enriched[cols["mid"]] = bbands.iloc[:, 1]
            enriched[cols["upper"]] = bbands.iloc[:, 2]
            if bbands.shape[1] > 3:
                enriched[cols["bandwidth"]] = bbands.iloc[:, 3]
            if bbands.shape[1] > 4:
                enriched[cols["pct"]] = bbands.iloc[:, 4]

        for period in sorted(plan["atr"]):
            atr = ta.atr(
                enriched["high"],
                enriched["low"],
                enriched["close"],
                length=period,
            )
            enriched[atr_col(period)] = atr
            enriched[atr_pct_col(period)] = (atr / enriched["close"]) * 100

        for period in sorted(plan["adx"]):
            adx = ta.adx(
                enriched["high"],
                enriched["low"],
                enriched["close"],
                length=period,
            )
            if adx is None or adx.empty:
                continue
            cols = adx_cols(period)
            enriched[cols["adx"]] = adx.iloc[:, 0]
            enriched[cols["di_plus"]] = adx.iloc[:, 1]
            enriched[cols["di_minus"]] = adx.iloc[:, 2]

        for length, multiplier in sorted(plan["supertrend"]):
            supertrend = ta.supertrend(
                enriched["high"],
                enriched["low"],
                enriched["close"],
                length=length,
                multiplier=multiplier,
            )
            if supertrend is None or supertrend.empty:
                continue
            cols = supertrend_cols(length, multiplier)
            enriched[cols["value"]] = supertrend.iloc[:, 0]
            enriched[cols["direction"]] = supertrend.iloc[:, 1]

        for period in sorted(plan["volume_sma"]):
            vol_sma = ta.sma(enriched["volume"], length=period)
            enriched[volume_sma_col(period)] = vol_sma
            enriched[volume_ratio_col(period)] = enriched["volume"] / vol_sma

        if plan["vwap"]:
            enriched["vwap"] = ta.vwap(
                enriched["high"],
                enriched["low"],
                enriched["close"],
                enriched["volume"],
            )

        for period in sorted(plan["stochrsi"]):
            stoch_rsi = ta.stochrsi(enriched["close"], length=period)
            if stoch_rsi is None or stoch_rsi.empty:
                continue
            suffix = "" if period == 14 else f"_{period}"
            enriched[f"stoch_rsi_k{suffix}"] = stoch_rsi.iloc[:, 0]
            enriched[f"stoch_rsi_d{suffix}"] = stoch_rsi.iloc[:, 1]

        # ─── Microstructure Consciousness (L2.0 Sudden Momentum) ───
        if plan.get("microstructure"):
            # 1. Sudden Burst (Volume Ratio)
            vol_sma_20 = ta.sma(enriched["volume"], length=20)
            enriched["volume_burst_ratio"] = enriched["volume"] / vol_sma_20
            
            # 2. Liquidity Sweep (Wick Detector)
            # A sweep is a candle that penetrates the 20-bar high/low but closes inside
            rolling_high_20 = enriched["high"].shift(1).rolling(20).max()
            rolling_low_20 = enriched["low"].shift(1).rolling(20).min()
            
            # Bullish Sweep: Low below rolling_low but Close above it (Wick Rejection)
            enriched["is_bullish_sweep"] = (enriched["low"] < rolling_low_20) & (enriched["close"] > rolling_low_20)
            # Bearish Sweep: High above rolling_high but Close below it (Wick Rejection)
            enriched["is_bearish_sweep"] = (enriched["high"] > rolling_high_20) & (enriched["close"] < rolling_high_20)

        return enriched

    @staticmethod
    def _merge_requirements(plan: dict[str, object], requirements: dict[str, object]):
        for key, value in requirements.items():
            if key == "vwap":
                plan["vwap"] = bool(plan["vwap"] or value)
                continue

            if key == "microstructure":
                plan["microstructure"] = bool(plan.get("microstructure") or value)
                continue

            if value is None:
                continue

            if not isinstance(value, Iterable) or isinstance(value, (str, bytes, tuple)):
                values = [value]
            else:
                values = list(value)

            for item in values:
                cast_item = tuple(item) if isinstance(item, list) else item
                plan[key].add(cast_item)
