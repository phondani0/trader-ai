"""
Signal Aggregator.

Collects signals from all strategies, filters by regime,
ranks by confidence, and resolves conflicts.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from loguru import logger

from src.core.models import Signal, SignalType, Side, MarketRegime
from src.strategies.base import BaseStrategy
from src.intelligence.regime_detector import RegimeDetector


class SignalAggregator:
    """
    Orchestrates multi-strategy signal generation.

    For each symbol:
        1. Detect market regime
        2. Filter strategies to only those suited for this regime
        3. Run eligible strategies
        4. Rank resulting signals by confidence
        5. Return the best signal (or None)
    """

    def __init__(
        self,
        strategies: list[BaseStrategy],
        regime_detector: RegimeDetector,
    ):
        self.strategies = strategies
        self.regime_detector = regime_detector
        self._active_strategies: list[str] = [s.name for s in strategies if s.enabled]
        self._market_profile_lookback_days = 45

        logger.info(
            f"Signal aggregator initialized with {len(self._active_strategies)} strategies: "
            f"{', '.join(self._active_strategies)}"
        )

    def market_profile_window(self, df: pd.DataFrame) -> pd.DataFrame:
        """Use a time-based lookback so macro profiling scales across timeframes."""
        if df.empty or len(df) <= 2:
            return df

        deltas = df.index.to_series().diff().dropna()
        if deltas.empty:
            return df

        median_delta = deltas.median()
        if pd.isna(median_delta):
            return df

        bar_minutes = max(median_delta.total_seconds() / 60, 1)
        bars_per_day = max(int(round((24 * 60) / bar_minutes)), 1)
        lookback_bars = max(48, self._market_profile_lookback_days * bars_per_day)

        if len(df) <= lookback_bars:
            return df
        return df.tail(lookback_bars)

    def diagnose_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        market_profile: str | None = None,
        market_bias: str | None = None,
    ) -> dict:
        """
        Explain why a symbol did or did not produce an executable signal.
        """
        if df.empty or len(df) < 30:
            return {
                "symbol": symbol,
                "status": "insufficient_data",
                "regime": MarketRegime.UNKNOWN.value,
                "market_profile": market_profile or "unknown",
                "market_bias": market_bias or "unknown",
                "eligible_strategies": [],
                "generated_signals": [],
                "blocked_reasons": {"insufficient_data": self._active_strategy_names()},
            }

        regime = self.regime_detector.detect(df)
        if market_profile is None or market_bias is None:
            profile_df = self.market_profile_window(df)
            market_context = self.regime_detector.profile_window(profile_df)
            if market_profile is None:
                market_profile = market_context.get("detected_market_profile")
            if market_bias is None:
                market_bias = market_context.get("market_bias")

        allowed = self.regime_detector.get_allowed_strategies(regime)
        eligible_strategies: list[str] = []
        generated_signals: list[str] = []
        blocked_reasons: dict[str, list[str]] = {}

        for strategy in self.strategies:
            reason = self._strategy_skip_reason(
                strategy,
                regime,
                market_profile,
                market_bias,
                allowed,
            )
            if reason:
                blocked_reasons.setdefault(reason, []).append(strategy.name)
                continue

            eligible_strategies.append(strategy.name)
            try:
                signal = strategy.generate_signal(symbol, df, regime)
            except Exception as exc:
                blocked_reasons.setdefault("strategy_error", []).append(
                    f"{strategy.name}({exc})"
                )
                continue

            if signal:
                generated_signals.append(strategy.name)

        status = "signal_ready" if generated_signals else "no_entry"
        if not eligible_strategies and blocked_reasons:
            status = "fully_gated"

        return {
            "symbol": symbol,
            "status": status,
            "regime": regime.value,
            "market_profile": market_profile or "unknown",
            "market_bias": market_bias or "unknown",
            "eligible_strategies": eligible_strategies,
            "generated_signals": generated_signals,
            "blocked_reasons": blocked_reasons,
        }

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        *,
        log_signals: bool = True,
        market_profiles: dict[str, str] | None = None,
        market_biases: dict[str, str] | None = None,
    ) -> list[Signal]:
        """
        Generate signals for all symbols using all eligible strategies.

        Args:
            data: Dict of symbol -> enriched DataFrame

        Returns:
            List of signals, sorted by confidence (highest first)
        """
        all_signals: list[Signal] = []

        for symbol, df in data.items():
            if df.empty or len(df) < 30:
                continue

            # 1. Detect regime for this symbol
            regime = self.regime_detector.detect(df)
            market_profile = (market_profiles or {}).get(symbol)
            market_bias = (market_biases or {}).get(symbol)
            if market_profile is None or market_bias is None:
                profile_df = self.market_profile_window(df)
                market_context = self.regime_detector.profile_window(profile_df)
                if market_profile is None:
                    market_profile = market_context.get("detected_market_profile")
                if market_bias is None:
                    market_bias = market_context.get("market_bias")
            allowed = self.regime_detector.get_allowed_strategies(regime)

            # 2. Run eligible strategies
            symbol_signals = []
            for strategy in self.strategies:
                if not strategy.enabled:
                    continue
                if not strategy.should_run(regime):
                    continue
                if not strategy.allows_market_profile(market_profile):
                    continue
                if not strategy.allows_market_bias(market_bias):
                    continue
                if strategy.name not in allowed:
                    continue

                try:
                    signal = strategy.generate_signal(symbol, df, regime)
                    if signal:
                        symbol_signals.append(signal)
                        if log_signals:
                            logger.info(
                                f"📊 Signal: {signal.strategy_name} → {signal.side.value} "
                                f"{symbol} @ {signal.entry_price:.2f} | "
                                f"Conf: {signal.confidence:.0%} | {signal.reason}"
                            )
                except Exception as e:
                    logger.error(f"Strategy {strategy.name} error on {symbol}: {e}")

            # 3. Resolve conflicts — if multiple strategies agree, boost confidence
            if len(symbol_signals) > 1:
                best = self._resolve_conflicts(symbol_signals, log_signals=log_signals)
                if best:
                    all_signals.append(best)
            elif len(symbol_signals) == 1:
                all_signals.append(symbol_signals[0])

        # Sort by confidence (highest first)
        all_signals.sort(key=lambda s: s.confidence, reverse=True)

        if all_signals and log_signals:
            logger.info(f"Generated {len(all_signals)} signals across all symbols")

        return all_signals

    def _strategy_skip_reason(
        self,
        strategy: BaseStrategy,
        regime: MarketRegime,
        market_profile: str | None,
        market_bias: str | None,
        allowed: list[str],
    ) -> str | None:
        if not strategy.enabled:
            return "disabled"

        if None not in strategy._supported_regimes and regime not in strategy.supported_regimes:
            return "config_regime_mismatch"

        approved_regimes = strategy._benchmark_approved_regimes
        if approved_regimes is not None and regime not in approved_regimes:
            return "benchmark_regime_mismatch"

        if not strategy.allows_market_profile(market_profile):
            return "market_profile_mismatch"

        if not strategy.allows_market_bias(market_bias):
            return "market_bias_mismatch"

        if strategy.name not in allowed:
            return "regime_matrix_mismatch"

        return None

    def _active_strategy_names(self) -> list[str]:
        return [strategy.name for strategy in self.strategies if strategy.enabled]

    def _resolve_conflicts(
        self,
        signals: list[Signal],
        *,
        log_signals: bool = True,
    ) -> Optional[Signal]:
        """
        When multiple strategies generate signals for the same symbol:
        - If they agree on direction → boost confidence (confluence!)
        - If they disagree → take the higher confidence signal
        """
        if not signals:
            return None

        # Group by direction
        buy_signals = [s for s in signals if s.side == Side.BUY]
        sell_signals = [s for s in signals if s.side == Side.SELL]

        if buy_signals and sell_signals:
            # Conflicting signals — take the majority or highest confidence
            if len(buy_signals) >= len(sell_signals):
                best = max(buy_signals, key=lambda s: s.confidence)
            else:
                best = max(sell_signals, key=lambda s: s.confidence)
            if log_signals:
                logger.warning(
                    f"⚠️ Conflicting signals for {best.symbol}: "
                    f"{len(buy_signals)} BUY vs {len(sell_signals)} SELL — "
                    f"going with {best.side.value} (conf: {best.confidence:.0%})"
                )
            return best

        # All agree on direction — CONFLUENCE! Boost confidence
        all_same_dir = buy_signals or sell_signals
        best = max(all_same_dir, key=lambda s: s.confidence)

        # Boost confidence based on number of confirming strategies
        confluence_boost = min(0.15, 0.05 * (len(all_same_dir) - 1))
        best.confidence = min(1.0, best.confidence + confluence_boost)
        best.reason += f" | 🎯 CONFLUENCE: {len(all_same_dir)} strategies agree"
        best.metadata["confluence_count"] = len(all_same_dir)
        best.metadata["confirming_strategies"] = [s.strategy_name for s in all_same_dir]

        if log_signals:
            logger.info(
                f"🎯 Confluence detected for {best.symbol}: "
                f"{len(all_same_dir)} strategies agree on {best.side.value} "
                f"(boosted conf: {best.confidence:.0%})"
            )

        return best
