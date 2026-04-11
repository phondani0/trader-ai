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

        logger.info(
            f"Signal aggregator initialized with {len(self._active_strategies)} strategies: "
            f"{', '.join(self._active_strategies)}"
        )

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
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
            allowed = self.regime_detector.get_allowed_strategies(regime)

            # 2. Run eligible strategies
            symbol_signals = []
            for strategy in self.strategies:
                if not strategy.enabled:
                    continue
                if not strategy.should_run(regime):
                    continue
                if strategy.name not in allowed:
                    continue

                try:
                    signal = strategy.generate_signal(symbol, df, regime)
                    if signal:
                        symbol_signals.append(signal)
                        logger.info(
                            f"📊 Signal: {signal.strategy_name} → {signal.side.value} "
                            f"{symbol} @ {signal.entry_price:.2f} | "
                            f"Conf: {signal.confidence:.0%} | {signal.reason}"
                        )
                except Exception as e:
                    logger.error(f"Strategy {strategy.name} error on {symbol}: {e}")

            # 3. Resolve conflicts — if multiple strategies agree, boost confidence
            if len(symbol_signals) > 1:
                best = self._resolve_conflicts(symbol_signals)
                if best:
                    all_signals.append(best)
            elif len(symbol_signals) == 1:
                all_signals.append(symbol_signals[0])

        # Sort by confidence (highest first)
        all_signals.sort(key=lambda s: s.confidence, reverse=True)

        if all_signals:
            logger.info(f"Generated {len(all_signals)} signals across all symbols")

        return all_signals

    def _resolve_conflicts(self, signals: list[Signal]) -> Optional[Signal]:
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

        logger.info(
            f"🎯 Confluence detected for {best.symbol}: "
            f"{len(all_same_dir)} strategies agree on {best.side.value} "
            f"(boosted conf: {best.confidence:.0%})"
        )

        return best
