from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NAUTILUS_ROOT = PROJECT_ROOT / "nautilus"
DEFAULT_BENCHMARK_STRATEGIES = [
    "volume_spike_trend",
    "nautilus_ema_cross",
    "demo_ema_cross",
]


def build_uv_run_command(command: str, args: list[str]) -> list[str]:
    return ["uv", "run", command, *args]


def build_backtest_command(args: list[str]) -> list[str]:
    return build_uv_run_command("nautilus-backtest", args)


def build_benchmark_command(args: list[str]) -> list[str]:
    if args:
        return build_uv_run_command("nautilus-compare-strategies", args)

    return build_uv_run_command(
        "nautilus-compare-strategies",
        [
            "--instrument",
            "btcusdt_perp_binance",
            "--data-source",
            "ccxt",
            "--strategies",
            *DEFAULT_BENCHMARK_STRATEGIES,
        ],
    )


def build_start_command(args: list[str]) -> list[str]:
    if args:
        return build_uv_run_command("nautilus-papertrade", args)

    return build_uv_run_command(
        "nautilus-papertrade",
        [
            "--instrument",
            "btcusdt_perp_binance",
            "--strategy",
            "volume_spike_trend",
            "--data-source",
            "ccxt",
        ],
    )


def run_in_nautilus(command: list[str]) -> int:
    result = subprocess.run(command, cwd=NAUTILUS_ROOT, check=False)
    return result.returncode


def dispatch_or_exit(command: list[str]) -> None:
    raise SystemExit(run_in_nautilus(command))


def main_backtest(argv: list[str] | None = None) -> None:
    dispatch_or_exit(build_backtest_command(list(sys.argv[1:] if argv is None else argv)))


def main_benchmark(argv: list[str] | None = None) -> None:
    dispatch_or_exit(build_benchmark_command(list(sys.argv[1:] if argv is None else argv)))


def main_start(argv: list[str] | None = None) -> None:
    dispatch_or_exit(build_start_command(list(sys.argv[1:] if argv is None else argv)))
