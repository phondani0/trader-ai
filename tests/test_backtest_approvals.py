from __future__ import annotations

import unittest

from scripts.backtest import apply_strategy_approvals
from src.core.models import MarketRegime
from src.strategies.base import BaseStrategy


class _DummyStrategy(BaseStrategy):
    def __init__(self, name: str):
        super().__init__({"enabled": True, "regime": [MarketRegime.TRENDING_LOW_VOL.value]})
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def generate_signal(self, symbol: str, df, regime: MarketRegime):
        return None


class BacktestApprovalsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
