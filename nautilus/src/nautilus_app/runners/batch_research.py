from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

from nautilus_app.reports import write_leaderboard


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output" / "batch_research"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"


@dataclass(frozen=True)
class ResearchCase:
    instrument_name: str
    strategy_name: str
    data_source: str


def build_research_cases(
    *,
    instruments: list[str],
    strategies: list[str],
    data_source: str,
) -> list[ResearchCase]:
    return [
        ResearchCase(
            instrument_name=instrument_name,
            strategy_name=strategy_name,
            data_source=data_source,
        )
        for instrument_name in instruments
        for strategy_name in strategies
    ]


def run_batch_research(
    *,
    settings_path: str | Path | None,
    instruments: list[str],
    strategies: list[str],
    data_source: str,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> dict[str, object]:
    resolved_output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_cache_dir.mkdir(parents=True, exist_ok=True)
    cases = build_research_cases(
        instruments=instruments,
        strategies=strategies,
        data_source=data_source,
    )
    summaries: list[dict[str, object]] = []
    for case in cases:
        case_dir = resolved_output_dir / f"{case.instrument_name}__{case.strategy_name}"
        command = [
            sys.executable,
            "-m",
            "nautilus_app.cli.backtest",
            "--output-dir",
            str(case_dir),
            "--instrument",
            case.instrument_name,
            "--strategy",
            case.strategy_name,
            "--data-source",
            case.data_source,
            "--cache-dir",
            str(resolved_cache_dir),
        ]
        if settings_path is not None:
            command.extend(["--settings", str(settings_path)])
        subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        summary_path = case_dir / "summary.json"
        summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))

    batch_summary = {
        "cases": len(cases),
        "output_dir": str(resolved_output_dir),
        "cache_dir": str(resolved_cache_dir),
        "summaries": summaries,
    }
    batch_summary.update(write_leaderboard(summaries=summaries, output_dir=resolved_output_dir))
    (resolved_output_dir / "batch_summary.json").write_text(
        json.dumps(batch_summary, indent=2),
        encoding="utf-8",
    )
    return batch_summary
