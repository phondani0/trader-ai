from __future__ import annotations

import argparse

from trader_ai.runners.papertrade import write_papertrade_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap Nautilus paper-trading configuration",
    )
    parser.add_argument("--settings", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--instrument", default=None)
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--data-source", default=None)
    return parser


def main():
    args = build_parser().parse_args()
    result = write_papertrade_plan(
        settings_path=args.settings,
        output_dir=args.output_dir,
        instrument_name=args.instrument,
        strategy_name=args.strategy,
        data_source=args.data_source,
    )
    print("NautilusTrader paper bootstrap generated")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
