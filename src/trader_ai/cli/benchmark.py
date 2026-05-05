from __future__ import annotations

import argparse

from trader_ai.runners.compare_strategies import run_strategy_comparison


DEFAULT_INSTRUMENT = "btcusdt_perp_binance"
DEFAULT_DATA_SOURCE = "ccxt"
DEFAULT_STRATEGIES = [
    "adaptive_trend_rider",
    "volume_spike_trend",
    "nautilus_ema_cross",
    "demo_ema_cross",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the default strategy benchmark on one instrument",
    )
    parser.add_argument("--settings", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--instrument", default=DEFAULT_INSTRUMENT)
    parser.add_argument("--data-source", default=DEFAULT_DATA_SOURCE)
    parser.add_argument("--strategies", nargs="+", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    strategies = args.strategies or list(DEFAULT_STRATEGIES)
    result = run_strategy_comparison(
        settings_path=args.settings,
        instrument_name=args.instrument,
        strategies=strategies,
        data_source=args.data_source,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
    )
    print("Trader AI benchmark completed")
    print(f"cases: {result['cases']}")
    print(f"output_dir: {result['output_dir']}")


if __name__ == "__main__":
    main()
