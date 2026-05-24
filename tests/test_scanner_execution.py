from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.runners.scanner_execution import ScannerExecutionConfig  # noqa: E402
from trader_ai.runners.scanner_execution import (  # noqa: E402
    ccxt_symbol_to_bybit_instrument_name,
)
from trader_ai.runners.scanner_execution import (  # noqa: E402
    select_scanner_execution_symbols,
)


class ScannerExecutionTest(unittest.TestCase):
    def test_ccxt_symbol_to_bybit_instrument_name(self):
        self.assertEqual(
            ccxt_symbol_to_bybit_instrument_name("NEAR/USDT:USDT"),
            "nearusdt_linear_bybit",
        )

    def test_select_scanner_execution_symbols_filters_and_limits(self):
        candidates = [
            {"symbol": "NEAR/USDT:USDT", "score": 14.0, "volume_24h": 100_000_000},
            {"symbol": "BTC/USDT:USDT", "score": 13.0, "volume_24h": 4_000_000_000},
            {"symbol": "XAU/USDT:USDT", "score": 12.0, "volume_24h": 200_000_000},
            {"symbol": "ETH/USDT", "score": 15.0, "volume_24h": 3_000_000_000},
            {"symbol": "LOW/USDT:USDT", "score": 9.0, "volume_24h": 200_000_000},
            {"symbol": "THIN/USDT:USDT", "score": 20.0, "volume_24h": 1_000_000},
        ]

        selected = select_scanner_execution_symbols(
            candidates,
            configured_symbols={"BTC/USDT:USDT"},
            config=ScannerExecutionConfig(
                enabled=True,
                max_symbols=1,
                min_score=10.0,
                min_24h_volume=50_000_000.0,
                blacklist={"XAU/USDT:USDT"},
            ),
        )

        self.assertEqual(selected, ["NEAR/USDT:USDT"])

    def test_select_scanner_execution_symbols_disabled_returns_empty(self):
        selected = select_scanner_execution_symbols(
            [{"symbol": "NEAR/USDT:USDT", "score": 14.0, "volume_24h": 100_000_000}],
            configured_symbols=set(),
            config=ScannerExecutionConfig(enabled=False),
        )

        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()
