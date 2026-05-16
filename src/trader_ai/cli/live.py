from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from trader_ai.runners.live import run_live

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run NautilusTrader live trading/paper trading node",
    )
    parser.add_argument("--settings", default=None)
    parser.add_argument("--instrument", default=None)
    parser.add_argument("--strategy", default=None)
    return parser


def load_runtime_env(env_path: str | Path | None = None) -> None:
    load_dotenv(env_path or PROJECT_ROOT / ".env")


def main():
    load_runtime_env()
    args = build_parser().parse_args()
    print(f"Starting NautilusTrader Live Node (Strategy: {args.strategy or 'default'}, Instrument: {args.instrument or 'default'})...")
    run_live(
        settings_path=args.settings,
        instrument_name=args.instrument,
        strategy_name=args.strategy,
    )


if __name__ == "__main__":
    main()
