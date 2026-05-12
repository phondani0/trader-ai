from __future__ import annotations

import argparse

from trader_ai.runners.live import run_live


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run NautilusTrader live trading/paper trading node",
    )
    parser.add_argument("--settings", default=None)
    parser.add_argument("--instrument", default=None)
    parser.add_argument("--strategy", default=None)
    return parser


def main():
    args = build_parser().parse_args()
    print(f"Starting NautilusTrader Live Node (Strategy: {args.strategy or 'default'}, Instrument: {args.instrument or 'default'})...")
    run_live(
        settings_path=args.settings,
        instrument_name=args.instrument,
        strategy_name=args.strategy,
    )


if __name__ == "__main__":
    main()
