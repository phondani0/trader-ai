from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from nautilus_app.reports import write_leaderboard


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output" / "parameter_sweep"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"


def build_parameter_grid(
    *,
    fast_periods: list[int],
    slow_periods: list[int],
) -> list[dict[str, int]]:
    return [
        {"fast_ema_period": fast_period, "slow_ema_period": slow_period}
        for fast_period in fast_periods
        for slow_period in slow_periods
        if fast_period < slow_period
    ]


def run_parameter_sweep(
    *,
    settings_path: str | Path | None,
    instrument_name: str,
    strategy_name: str,
    data_source: str,
    fast_periods: list[int],
    slow_periods: list[int],
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> dict[str, object]:
    resolved_output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    grid = build_parameter_grid(
        fast_periods=fast_periods,
        slow_periods=slow_periods,
    )
    summaries: list[dict[str, object]] = []
    for params in grid:
        case_dir = resolved_output_dir / f"fast_{params['fast_ema_period']}__slow_{params['slow_ema_period']}"
        command = [
            sys.executable,
            "-m",
            "nautilus_app.cli.backtest",
            "--output-dir",
            str(case_dir),
            "--instrument",
            instrument_name,
            "--strategy",
            strategy_name,
            "--data-source",
            data_source,
            "--cache-dir",
            str(resolved_cache_dir),
            "--fast-ema-period",
            str(params["fast_ema_period"]),
            "--slow-ema-period",
            str(params["slow_ema_period"]),
        ]
        if settings_path is not None:
            command.extend(["--settings", str(settings_path)])
        subprocess.run(command, cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
        summary_path = case_dir / "summary.json"
        summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))

    sweep_summary = {
        "cases": len(grid),
        "output_dir": str(resolved_output_dir),
        "cache_dir": str(resolved_cache_dir),
        "summaries": summaries,
    }
    sweep_summary.update(write_leaderboard(summaries=summaries, output_dir=resolved_output_dir))
    (resolved_output_dir / "sweep_summary.json").write_text(
        json.dumps(sweep_summary, indent=2),
        encoding="utf-8",
    )
    return sweep_summary
