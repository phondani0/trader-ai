from __future__ import annotations

import unittest

from scripts.benchmark import (
    build_approvals_payload,
    derive_strategy_deployment,
    init_strategies,
)


class BenchmarkDeploymentTest(unittest.TestCase):
    def test_init_strategies_can_construct_bearish_breakdown_continuation(self):
        strategies = init_strategies("bearish_breakdown_continuation")

        self.assertEqual(len(strategies), 1)
        self.assertEqual(strategies[0].name, "bearish_breakdown_continuation")

    def test_curated_execution_regimes_drive_deployable_runtime_regimes(self):
        deployment = derive_strategy_deployment(
            "supertrend",
            {"supertrend": ["trending_low_vol", "trending_high_vol"]},
            [
                {
                    "name": "Bullish Trend (Early 2024)",
                    "market_profile": "trend",
                    "market_bias": "bullish",
                    "expected_execution_regimes": [
                        "trending_low_vol",
                        "trending_high_vol",
                    ],
                    "strategies": {
                        "supertrend": {
                            "approved": True,
                            "reason": "approved",
                        }
                    },
                }
            ],
        )

        self.assertTrue(deployment["approved"])
        self.assertEqual(
            deployment["deployable_regimes"],
            ["trending_high_vol", "trending_low_vol"],
        )
        self.assertEqual(deployment["deployable_market_profiles"], ["trend"])
        self.assertEqual(deployment["deployable_market_biases"], ["bullish"])

    def test_approvals_payload_uses_deployable_metrics_for_approved_windows(self):
        class _Summary:
            def __init__(self, trades, roi, expectancy, pf, mdd):
                self.trades = trades
                self.net_roi_pct = roi
                self.expectancy_pct = expectancy
                self.profit_factor = pf
                self.max_drawdown_pct = mdd
                self.win_rate_pct = 50.0

        class _Engine:
            def summarize(
                self,
                symbol,
                period_start,
                period_end,
                trades,
                killed_by_drawdown=False,
                kill_reason="",
            ):
                roi = sum(trades)
                return _Summary(
                    trades=trades,
                    roi=roi,
                    expectancy=(roi / len(trades)) if trades else 0.0,
                    pf=1.5 if roi > 0 else 0.5,
                    mdd=-2.0 if roi > 0 else -10.0,
                )

        approvals = build_approvals_payload(
            engine=_Engine(),
            strategy_rollups={
                "supertrend": {
                    "trades": [-5.0, 4.5],
                    "windows": [],
                    "window_names": ["Bull", "Breakout"],
                    "profiles": [],
                    "killed_by_drawdown": False,
                    "kill_reasons": [],
                }
            },
            strategy_window_rollups={
                "supertrend": {
                    "Bull": {
                        "trades": [-5.0],
                        "windows": [("2024-01-01", "2024-03-31")],
                        "window_names": ["Bull"],
                        "profiles": [],
                        "killed_by_drawdown": False,
                        "kill_reasons": [],
                    },
                    "Breakout": {
                        "trades": [4.5],
                        "windows": [("2024-11-01", "2024-12-01")],
                        "window_names": ["Breakout"],
                        "profiles": [],
                        "killed_by_drawdown": False,
                        "kill_reasons": [],
                    },
                }
            },
            market_profile_capabilities={},
            strategy_targets={"supertrend": ["trending_low_vol", "trending_high_vol"]},
            benchmark_cfg={},
            benchmark_windows=[
                {
                    "name": "Bull",
                    "market_profile": "trend",
                    "market_bias": "bullish",
                    "expected_execution_regimes": ["trending_low_vol", "trending_high_vol"],
                    "strategies": {"supertrend": {"approved": False, "reason": "bad"}},
                },
                {
                    "name": "Breakout",
                    "market_profile": "breakout",
                    "market_bias": "bullish",
                    "expected_execution_regimes": ["trending_low_vol", "trending_high_vol"],
                    "strategies": {"supertrend": {"approved": True, "reason": "approved"}},
                },
            ],
        )

        metrics = approvals["strategies"]["supertrend"]["deployable_metrics"]
        self.assertEqual(metrics["net_roi_pct"], 4.5)
        self.assertEqual(
            approvals["strategies"]["supertrend"]["deployable_market_biases"],
            ["bullish"],
        )
        self.assertEqual(
            approvals["strategies"]["supertrend"]["global_metrics"]["net_roi_pct"],
            -0.5,
        )


if __name__ == "__main__":
    unittest.main()
