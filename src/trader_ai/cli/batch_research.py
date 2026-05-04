from __future__ import annotations

import argparse

from trader_ai.runners.batch_research import run_batch_research


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run multiple Nautilus backtests")
    parser.add_argument("--settings", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--data-source", default="synthetic")
    parser.add_argument("--instruments", nargs="+", required=True)
    parser.add_argument("--strategies", nargs="+", required=True)
    return parser


def main():
    args = build_parser().parse_args()
    result = run_batch_research(
        settings_path=args.settings,
        instruments=args.instruments,
        strategies=args.strategies,
        data_source=args.data_source,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
    )
    print("NautilusTrader batch research completed")
    print(f"cases: {result['cases']}")
    print(f"output_dir: {result['output_dir']}")


if __name__ == "__main__":
    main()
