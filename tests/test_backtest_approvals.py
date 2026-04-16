from __future__ import annotations

import unittest

from scripts.backtest import apply_strategy_approvals, load_strategies
from src.core.models import MarketRegime, StrategyType
from src.strategies.base import BaseStrategy


class _DummyStrategy(BaseStrategy):
    def __init__(self, name: str):
        super().__init__({"enabled": True, "regime": [MarketRegime.TRENDING_LOW_VOL.value]})
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.UNKNOWN

    def generate_signal(self, symbol: str, df, regime: MarketRegime):
        return None


class BacktestApprovalsTest(unittest.TestCase):
    def test_load_strategies_includes_bearish_breakdown_continuation(self):
        strategies = load_strategies()

        self.assertIn("bearish_breakdown_continuation", strategies)

    def test_apply_strategy_approvals_blocks_missing_strategies_when_payload_is_partial(self):
        approved = _DummyStrategy("donchian_breakout")
        missing = _DummyStrategy("volume_weighted_tsmom")

        filtered = apply_strategy_approvals(
            [approved, missing],
            {
                "donchian_breakout": {
                    "approved": True,
                    "deployable_regimes": ["trending_low_vol"],
                    "deployable_market_profiles": ["trend"],
                }
            },
        )

        self.assertEqual([strategy.name for strategy in filtered], ["donchian_breakout"])

    def test_apply_strategy_approvals_does_not_reuse_btc_approvals_for_other_symbols(self):
        approved = _DummyStrategy("donchian_breakout")

        filtered = apply_strategy_approvals(
            [approved],
            {
                "symbols": {
                    "BTC/USDT:USDT": {
                        "strategies": {
                            "donchian_breakout": {
                                "approved": True,
                                "deployable_regimes": ["trending_low_vol"],
                                "deployable_market_profiles": ["trend"],
                                "deployable_market_biases": ["bullish"],
                            }
                        }
                    }
                }
            },
            symbol="ETH/USDT:USDT",
        )

        self.assertEqual(filtered, [])


if __name__ == "__main__":
    unittest.main()
