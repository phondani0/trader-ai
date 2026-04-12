from __future__ import annotations

import unittest

from scripts.benchmark import derive_strategy_deployment


class BenchmarkDeploymentTest(unittest.TestCase):
    def test_curated_execution_regimes_drive_deployable_runtime_regimes(self):
        deployment = derive_strategy_deployment(
            "supertrend",
            {"supertrend": ["trending_low_vol", "trending_high_vol"]},
            [
                {
                    "name": "Bullish Trend (Early 2024)",
                    "market_profile": "trend",
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


if __name__ == "__main__":
    unittest.main()
