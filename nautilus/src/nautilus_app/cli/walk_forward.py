from __future__ import annotations

import argparse
import json
from pathlib import Path

from nautilus_app.runners.walk_forward import load_walk_forward_windows_from_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate walk-forward windows from CSV bars")
    parser.add_argument("--bars-csv", required=True)
    parser.add_argument("--output-path", default=None)
    parser.add_argument("--train-bars", type=int, required=True)
    parser.add_argument("--test-bars", type=int, required=True)
    parser.add_argument("--step-bars", type=int, required=True)
    return parser


def main():
    args = build_parser().parse_args()
    windows = load_walk_forward_windows_from_csv(
        csv_path=args.bars_csv,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        step_bars=args.step_bars,
    )
    if args.output_path:
        Path(args.output_path).write_text(json.dumps(windows, indent=2), encoding="utf-8")
    print("NautilusTrader walk-forward windows generated")
    print(f"windows: {len(windows)}")


if __name__ == "__main__":
    main()
