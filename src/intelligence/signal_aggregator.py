"""
Signal Aggregator.

Collects signals from all strategies, filters by regime,
ranks by confidence, and resolves conflicts.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from loguru import logger

from src.core.models import Signal, Side, MarketRegime
from src.core.timeframes import canonical_timeframe
from src.intelligence.regime_detector import RegimeDetector
from src.strategies.base import BaseStrategy


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
        self._strategy_by_name = {strategy.name: strategy for strategy in strategies}
        self._active_strategies: list[str] = [s.name for s in strategies if s.enabled]
        self._market_profile_lookback_days = 45
        self._last_processed_bars: dict[tuple[str, str], pd.Timestamp] = {}

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
        df: pd.DataFrame | dict[str, pd.DataFrame],
        *,
        market_profile: str | None = None,
        market_bias: str | None = None,
    ) -> dict:
        """
        Explain why a symbol did or did not produce an executable signal.
        """
        if isinstance(df, pd.DataFrame):
            return self._diagnose_single_timeframe(
                symbol,
                df,
                market_profile=market_profile,
                market_bias=market_bias,
            )
        return self._diagnose_multi_timeframe(
            symbol,
            df,
            market_profile=market_profile,
            market_bias=market_bias,
        )

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame | dict[str, pd.DataFrame]],
        *,
        log_signals: bool = True,
        market_profiles: dict[str, str] | None = None,
        market_biases: dict[str, str] | None = None,
        enforce_fresh_bars: bool = False,
    ) -> list[Signal]:
        """
        Generate signals for all symbols using all eligible strategies.

        Args:
            data: Dict of symbol -> enriched DataFrame or timeframe -> DataFrame map

        Returns:
            List of signals, sorted by confidence (highest first)
        """
        all_signals: list[Signal] = []

        for symbol, symbol_data in data.items():
            if isinstance(symbol_data, pd.DataFrame):
                symbol_signals = self._generate_single_timeframe_signals(
                    symbol,
                    symbol_data,
                    log_signals=log_signals,
                    market_profile=(market_profiles or {}).get(symbol),
                    market_bias=(market_biases or {}).get(symbol),
                    enforce_fresh_bars=enforce_fresh_bars,
                )
            else:
                symbol_signals = self._generate_multi_timeframe_signals(
                    symbol,
                    symbol_data,
                    log_signals=log_signals,
                    market_profile=(market_profiles or {}).get(symbol),
                    market_bias=(market_biases or {}).get(symbol),
                    enforce_fresh_bars=enforce_fresh_bars,
                )

            if len(symbol_signals) > 1:
                best = self._resolve_conflicts(symbol_signals, log_signals=log_signals)
                if best:
                    all_signals.append(best)
            elif len(symbol_signals) == 1:
                all_signals.append(symbol_signals[0])

        all_signals.sort(key=lambda s: s.confidence, reverse=True)

        if all_signals and log_signals:
            logger.info(f"Generated {len(all_signals)} signals across all symbols")

        return all_signals

    def _diagnose_single_timeframe(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        market_profile: str | None = None,
        market_bias: str | None = None,
    ) -> dict:
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

        regime_state = self.regime_detector.detect(df)
        regime = regime_state.regime
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
                symbol,
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

    def _diagnose_multi_timeframe(
        self,
        symbol: str,
        data: dict[str, pd.DataFrame],
        *,
        market_profile: str | None = None,
        market_bias: str | None = None,
    ) -> dict:
        eligible_strategies: list[str] = []
        generated_signals: list[str] = []
        blocked_reasons: dict[str, list[str]] = {}
        last_regime = MarketRegime.UNKNOWN.value

        for strategy in self.strategies:
            strategy_df = self._select_strategy_frame(data, strategy)
            if strategy_df is None or strategy_df.empty or len(strategy_df) < 30:
                blocked_reasons.setdefault("insufficient_data", []).append(strategy.name)
                continue

            regime_state = self.regime_detector.detect(strategy_df)
            regime = regime_state.regime
            last_regime = regime.value
            strategy_market_profile = market_profile
            strategy_market_bias = market_bias
            if strategy_market_profile is None or strategy_market_bias is None:
                profile_df = self.market_profile_window(strategy_df)
                market_context = self.regime_detector.profile_window(profile_df)
                if strategy_market_profile is None:
                    strategy_market_profile = market_context.get("detected_market_profile")
                if strategy_market_bias is None:
                    strategy_market_bias = market_context.get("market_bias")

            allowed = self.regime_detector.get_allowed_strategies(regime)
            reason = self._strategy_skip_reason(
                strategy,
                symbol,
                regime,
                strategy_market_profile,
                strategy_market_bias,
                allowed,
            )
            if reason:
                blocked_reasons.setdefault(reason, []).append(strategy.name)
                continue

            eligible_strategies.append(strategy.name)
            try:
                signal = strategy.generate_signal(symbol, strategy_df, regime)
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
            "regime": last_regime,
            "market_profile": market_profile or "unknown",
            "market_bias": market_bias or "unknown",
            "eligible_strategies": eligible_strategies,
            "generated_signals": generated_signals,
            "blocked_reasons": blocked_reasons,
        }

    def _generate_single_timeframe_signals(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        log_signals: bool,
        market_profile: str | None,
        market_bias: str | None,
        enforce_fresh_bars: bool,
    ) -> list[Signal]:
        if df.empty or len(df) < 30:
            return []

        regime_state = self.regime_detector.detect(df)
        regime = regime_state.regime
        if market_profile is None or market_bias is None:
            profile_df = self.market_profile_window(df)
            market_context = self.regime_detector.profile_window(profile_df)
            if market_profile is None:
                market_profile = market_context.get("detected_market_profile")
            if market_bias is None:
                market_bias = market_context.get("market_bias")
        allowed = self.regime_detector.get_allowed_strategies(regime)

        symbol_signals: list[Signal] = []
        for strategy in self.strategies:
            if not self._has_fresh_bar(symbol, strategy, df, enforce_fresh_bars):
                continue
            if self._strategy_skip_reason(
                strategy,
                symbol,
                regime,
                market_profile,
                market_bias,
                allowed,
            ):
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
            except Exception as exc:
                logger.error(f"Strategy {strategy.name} error on {symbol}: {exc}")

        return symbol_signals

    def _generate_multi_timeframe_signals(
        self,
        symbol: str,
        data: dict[str, pd.DataFrame],
        *,
        log_signals: bool,
        market_profile: str | None,
        market_bias: str | None,
        enforce_fresh_bars: bool,
    ) -> list[Signal]:
        symbol_signals: list[Signal] = []

        for strategy in self.strategies:
            strategy_df = self._select_strategy_frame(data, strategy)
            if strategy_df is None or strategy_df.empty or len(strategy_df) < 30:
                continue
            if not self._has_fresh_bar(
                symbol,
                strategy,
                strategy_df,
                enforce_fresh_bars,
            ):
                continue

            regime_state = self.regime_detector.detect(strategy_df)
            regime = regime_state.regime
            strategy_market_profile = market_profile
            strategy_market_bias = market_bias
            if strategy_market_profile is None or strategy_market_bias is None:
                profile_df = self.market_profile_window(strategy_df)
                market_context = self.regime_detector.profile_window(profile_df)
                if strategy_market_profile is None:
                    strategy_market_profile = market_context.get("detected_market_profile")
                if strategy_market_bias is None:
                    strategy_market_bias = market_context.get("market_bias")

            allowed = self.regime_detector.get_allowed_strategies(regime)
            if self._strategy_skip_reason(
                strategy,
                symbol,
                regime,
                strategy_market_profile,
                strategy_market_bias,
                allowed,
            ):
                continue

            try:
                signal = strategy.generate_signal(symbol, strategy_df, regime)
                if signal:
                    symbol_signals.append(signal)
                    if log_signals:
                        logger.info(
                            f"📊 Signal: {signal.strategy_name} → {signal.side.value} "
                            f"{symbol} @ {signal.entry_price:.2f} | "
                            f"Conf: {signal.confidence:.0%} | {signal.reason}"
                        )
            except Exception as exc:
                logger.error(f"Strategy {strategy.name} error on {symbol}: {exc}")

        return symbol_signals

    def _strategy_skip_reason(
        self,
        strategy: BaseStrategy,
        symbol: str,
        regime: MarketRegime,
        market_profile: str | None,
        market_bias: str | None,
        allowed: list[str],
    ) -> str | None:
        if not strategy.enabled:
            return "disabled"

        if None not in strategy._supported_regimes and regime not in strategy.supported_regimes:
            return "config_regime_mismatch"

        if not strategy.should_run(regime, symbol=symbol):
            return "benchmark_regime_mismatch"

        if not strategy.allows_market_profile(market_profile, symbol=symbol):
            return "market_profile_mismatch"

        if not strategy.allows_market_bias(market_bias, symbol=symbol):
            return "market_bias_mismatch"

        if strategy.name not in allowed:
            return "regime_matrix_mismatch"

        return None

    def _active_strategy_names(self) -> list[str]:
        return [strategy.name for strategy in self.strategies if strategy.enabled]

    def _select_strategy_frame(
        self,
        data: dict[str, pd.DataFrame],
        strategy: BaseStrategy,
    ) -> pd.DataFrame | None:
        strategy_timeframe = canonical_timeframe(strategy.timeframe)
        if strategy_timeframe in data:
            return data[strategy_timeframe]

        if strategy_timeframe:
            for timeframe, frame in data.items():
                if canonical_timeframe(timeframe) == strategy_timeframe:
                    return frame

        if len(data) == 1:
            return next(iter(data.values()))
        return None

    def _has_fresh_bar(
        self,
        symbol: str,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        enforce_fresh_bars: bool,
    ) -> bool:
        if not enforce_fresh_bars:
            return True
        if df.empty:
            return False

        last_bar = pd.Timestamp(df.index[-1])
        cache_key = (symbol, strategy.name)
        if self._last_processed_bars.get(cache_key) == last_bar:
            return False
        self._last_processed_bars[cache_key] = last_bar
        return True

    def _resolve_conflicts(
        self,
        signals: list[Signal],
        *,
        log_signals: bool = True,
    ) -> Optional[Signal]:
        """
        When multiple strategies generate signals for the same symbol:
        - If they agree on direction → boost confidence
        - If they disagree → take the higher confidence signal
        """
        if not signals:
            return None

        buy_signals = [s for s in signals if s.side == Side.BUY]
        sell_signals = [s for s in signals if s.side == Side.SELL]

        if buy_signals and sell_signals:
            best = max(signals, key=self._signal_priority_key)
            if log_signals:
                logger.warning(
                    f"⚠️ Conflicting signals for {best.symbol}: "
                    f"{len(buy_signals)} BUY vs {len(sell_signals)} SELL — "
                    f"going with {best.side.value} (conf: {best.confidence:.0%})"
                )
            return best

        all_same_dir = buy_signals or sell_signals
        best = max(all_same_dir, key=self._signal_priority_key)

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

    def _signal_priority_key(self, signal: Signal) -> tuple[int, float, float, float, float, float]:
        strategy = self._strategy_by_name.get(signal.strategy_name)
        selector_rank = strategy.selector_rank(signal.symbol) if strategy else (0, 0.0, 0.0, 0.0, 0.0)
        return (*selector_rank, signal.confidence)
