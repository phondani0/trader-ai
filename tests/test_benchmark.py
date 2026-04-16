from __future__ import annotations

import unittest

from scripts.benchmark import (
    build_approvals_payload,
    derive_strategy_deployment,
    init_strategies,
    merge_approvals_payload,
    simulate_strategy_window,
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

    def test_approvals_payload_includes_symbol_specific_deployments(self):
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
                    "trades": [4.5, -2.0],
                    "windows": [],
                    "window_names": ["BTC Bull", "ETH Bull"],
                    "profiles": [],
                    "killed_by_drawdown": False,
                    "kill_reasons": [],
                }
            },
            strategy_window_rollups={
                "supertrend": {
                    "BTC Bull": {
                        "trades": [4.5],
                        "windows": [("2024-01-01", "2024-03-31")],
                        "window_names": ["BTC Bull"],
                        "profiles": [],
                        "killed_by_drawdown": False,
                        "kill_reasons": [],
                    },
                    "ETH Bull": {
                        "trades": [-2.0],
                        "windows": [("2024-01-01", "2024-03-31")],
                        "window_names": ["ETH Bull"],
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
                    "name": "BTC Bull",
                    "symbol": "BTC/USDT:USDT",
                    "market_profile": "trend",
                    "market_bias": "bullish",
                    "expected_execution_regimes": ["trending_low_vol", "trending_high_vol"],
                    "strategies": {"supertrend": {"approved": True, "reason": "approved"}},
                },
                {
                    "name": "ETH Bull",
                    "symbol": "ETH/USDT:USDT",
                    "market_profile": "trend",
                    "market_bias": "bullish",
                    "expected_execution_regimes": ["trending_low_vol", "trending_high_vol"],
                    "strategies": {"supertrend": {"approved": False, "reason": "bad"}},
                },
            ],
        )

        self.assertTrue(
            approvals["symbols"]["BTC/USDT:USDT"]["strategies"]["supertrend"]["approved"]
        )
        self.assertFalse(
            approvals["symbols"]["ETH/USDT:USDT"]["strategies"]["supertrend"]["approved"]
        )

    def test_deployment_ignores_non_deployable_windows(self):
        deployment = derive_strategy_deployment(
            "episodic_pivot_short",
            {"episodic_pivot_short": ["trending_high_vol", "sideways_low_vol"]},
            [
                {
                    "name": "Recent Performance (180 Days)",
                    "market_profile": "mixed",
                    "market_bias": "bearish",
                    "expected_execution_regimes": [
                        "trending_high_vol",
                        "sideways_low_vol",
                    ],
                    "deployment_eligible": False,
                    "strategies": {
                        "episodic_pivot_short": {
                            "approved": True,
                            "reason": "approved",
                        }
                    },
                }
            ],
        )

        self.assertFalse(deployment["approved"])
        self.assertEqual(deployment["approved_windows"], [])
        self.assertEqual(deployment["deployable_market_profiles"], [])

    def test_benchmark_window_simulation_uses_selector_path(self):
        class _Engine:
            def __init__(self):
                self.called = None

            def run(self, symbol, df, strategy):
                self.called = "run"
                return "portfolio"

            def run_selector(self, symbol, df, strategies):
                self.called = "run_selector"
                return "selector"

        engine = _Engine()
        result = simulate_strategy_window(
            engine,
            "BTC/USDT:USDT",
            object(),
            object(),
        )

        self.assertEqual(result, "selector")
        self.assertEqual(engine.called, "run_selector")

    def test_filtered_benchmark_merge_preserves_other_strategies(self):
        existing = {
            "generated_at": "2026-04-14T00:00:00",
            "thresholds": {"min_trades": 3},
            "benchmark_windows": [
                {
                    "name": "Bull",
                    "symbol": "BTC/USDT:USDT",
                    "strategies": {
                        "donchian_breakout": {"approved": True},
                        "range_reversion": {"approved": False},
                    },
                }
            ],
            "market_profile_capabilities": {
                "donchian_breakout": {"trend": {"approved": True}},
                "range_reversion": {"range": {"approved": False}},
            },
            "strategies": {
                "donchian_breakout": {
                    "approved": True,
                    "deployable_regimes": ["trending_low_vol"],
                },
                "range_reversion": {
                    "approved": False,
                    "deployable_regimes": [],
                },
            },
            "symbols": {
                "BTC/USDT:USDT": {
                    "strategies": {
                        "donchian_breakout": {
                            "approved": True,
                            "deployable_regimes": ["trending_low_vol"],
                        },
                        "range_reversion": {
                            "approved": False,
                            "deployable_regimes": [],
                        },
                    }
                }
            },
        }
        filtered = {
            "generated_at": "2026-04-14T01:00:00",
            "thresholds": {"min_trades": 3},
            "benchmark_windows": [
                {
                    "name": "Bull",
                    "symbol": "BTC/USDT:USDT",
                    "strategies": {
                        "range_reversion": {"approved": True},
                    },
                }
            ],
            "market_profile_capabilities": {
                "range_reversion": {"range": {"approved": True}},
            },
            "strategies": {
                "range_reversion": {
                    "approved": True,
                    "deployable_regimes": ["sideways_low_vol"],
                }
            },
            "symbols": {
                "BTC/USDT:USDT": {
                    "strategies": {
                        "range_reversion": {
                            "approved": True,
                            "deployable_regimes": ["sideways_low_vol"],
                        }
                    }
                }
            },
        }

        merged = merge_approvals_payload(existing, filtered, {"range_reversion"})

        self.assertTrue(merged["strategies"]["donchian_breakout"]["approved"])
        self.assertTrue(merged["strategies"]["range_reversion"]["approved"])
        self.assertIn("donchian_breakout", merged["symbols"]["BTC/USDT:USDT"]["strategies"])
        self.assertTrue(
            merged["benchmark_windows"][0]["strategies"]["range_reversion"]["approved"]
        )


if __name__ == "__main__":
    unittest.main()
