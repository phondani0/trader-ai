"""
Market Intelligence — discovery Scanner.
Scans the entire exchange for momentum, volume surges, and price breakouts.
"""

from __future__ import annotations

from typing import Dict, List

from loguru import logger

from src.core.base_broker import BaseBroker


class MarketScanner:
    """
    Scans the market for high-quality momentum opportunities.
    Focuses on Volume-Weighted Breakouts (VWBC) to ensure quality.
    """

    def __init__(self, broker: BaseBroker, config: dict):
        self.broker = broker
        self.config = config

        # Scanner settings
        scan_cfg = config.get("discovery", {})
        self.min_24h_volume = scan_cfg.get("min_24h_volume", 10_000_000)
        self.min_price_change = scan_cfg.get("min_price_change", 3.0)
        self.top_n = scan_cfg.get("top_n", 5)

    def scan_for_momentum(self) -> List[Dict]:
        """
        One-shot scan of the entire exchange to find hot coins.
        Returns a ranked list of "High Quality" movers.
        """
        logger.info("🔍 Starting market scan for hot momentum...")

        try:
            tickers = self.broker.fetch_tickers()
            if not tickers:
                return []

            movers = []
            for symbol, t in tickers.items():
                # Filter 1: Only USDT pairs (standardize discovery)
                if not symbol.endswith("/USDT:USDT") and not symbol.endswith("/USDT"):
                    continue

                # Filter 2: Liquidity Floor ($10M 24h volume)
                volume = t.get("quoteVolume", 0.0)
                if volume < self.min_24h_volume:
                    continue

                # Filter 3: Momentum Strength (Price Change %)
                price_change = t.get("change", 0.0)
                if abs(price_change) < self.min_price_change:
                    continue

                # Calculation: Discovery Score
                # Simple version: (abs_change * log10(volume))
                # We prioritize price movement but weight it by institutional interest (volume)
                score = abs(price_change) * (volume / self.min_24h_volume)

                movers.append(
                    {
                        "symbol": symbol,
                        "change": price_change,
                        "volume_24h": volume,
                        "last_price": t.get("last"),
                        "score": score,
                    }
                )

            # Sort by score descending
            movers = sorted(movers, key=lambda x: x["score"], reverse=True)
            subset = movers[: self.top_n]

            if subset:
                self._log_discovery(subset)
            else:
                logger.info("ℹ️ No high-quality movers found in this scan.")

            return subset

        except Exception as e:
            logger.error(f"Discovery scanner error: {e}")
            return []

    def _log_discovery(self, movers: List[Dict]):
        """Visual terminal alert for the user."""
        print("\n" + "═" * 60)
        print("🔥  HOT MOMENTUM DISCOVERED")
        print("═" * 60)
        print(f"{'SYMBOL':<15} | {'CHANGE %':<10} | {'24H VOL':<12} | {'PRICE'}")
        print("-" * 60)

        for m in movers:
            change_str = f"{m['change']:+.2f}%"
            vol_str = f"${m['volume_24h']/1_000_000:.1f}M"
            print(
                f"{m['symbol']:<15} | {change_str:<10} | {vol_str:<12} | {m['last_price']}"
            )

        print("═" * 60 + "\n")
