from __future__ import annotations

import argparse
from pathlib import Path

from trader_ai.integration.smoke import build_bybit_demo_environment
from trader_ai.integration.smoke import run_local_sim_runtime_smoke


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run CI-safe and manual live-runtime integration smoke checks",
    )
    parser.add_argument(
        "--mode",
        choices=["local-sim", "bybit-demo"],
        default="local-sim",
    )
    parser.add_argument("--settings", default=None)
    parser.add_argument("--duration", type=int, default=120)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings_path = Path(args.settings) if args.settings else None

    if args.mode == "local-sim":
        report = run_local_sim_runtime_smoke(settings_path=settings_path)
        print(report.to_text())
        return 0 if report.ok else 1

    env = build_bybit_demo_environment()
    print("SAFETY: integration-smoke uses Bybit DEMO/PAPER only")
    print("SAFETY: live/mainnet execution disabled")
    print(f"SAFETY: runtime max duration {args.duration}s")
    print(f"SAFETY: exchange={env['EXCHANGE_ID']} mode={env['TRADING_MODE']}")
    print("Bybit demo node execution is safety-gated; use local-sim mode in CI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
