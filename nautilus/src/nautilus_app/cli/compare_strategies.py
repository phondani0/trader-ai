from __future__ import annotations

import argparse

from nautilus_app.runners.compare_strategies import run_strategy_comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare strategies on one instrument")
    parser.add_argument("--settings", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--data-source", default="synthetic")
    parser.add_argument("--strategies", nargs="+", required=True)
    return parser


def main():
    args = build_parser().parse_args()
    result = run_strategy_comparison(
        settings_path=args.settings,
        instrument_name=args.instrument,
        strategies=args.strategies,
        data_source=args.data_source,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
    )
    print("NautilusTrader strategy comparison completed")
    print(f"cases: {result['cases']}")
    print(f"output_dir: {result['output_dir']}")


if __name__ == "__main__":
    main()
