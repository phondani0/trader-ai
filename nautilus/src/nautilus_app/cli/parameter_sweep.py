from __future__ import annotations

import argparse

from nautilus_app.runners.parameter_sweep import run_parameter_sweep


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an EMA parameter sweep")
    parser.add_argument("--settings", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--data-source", default="synthetic")
    parser.add_argument("--fast-periods", nargs="+", type=int, required=True)
    parser.add_argument("--slow-periods", nargs="+", type=int, required=True)
    return parser


def main():
    args = build_parser().parse_args()
    result = run_parameter_sweep(
        settings_path=args.settings,
        instrument_name=args.instrument,
        strategy_name=args.strategy,
        data_source=args.data_source,
        fast_periods=args.fast_periods,
        slow_periods=args.slow_periods,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
    )
    print("NautilusTrader parameter sweep completed")
    print(f"cases: {result['cases']}")
    print(f"output_dir: {result['output_dir']}")


if __name__ == "__main__":
    main()
