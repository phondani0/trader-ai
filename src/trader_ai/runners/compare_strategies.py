from __future__ import annotations

from pathlib import Path

from trader_ai.runners.batch_research import run_batch_research


def build_compare_payload(
    *,
    instrument_name: str,
    strategies: list[str],
    data_source: str,
) -> dict[str, object]:
    return {
        "instrument_name": instrument_name,
        "strategies": strategies,
        "data_source": data_source,
    }


def run_strategy_comparison(
    *,
    settings_path: str | Path | None,
    instrument_name: str,
    strategies: list[str],
    data_source: str,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    use_strategy_config: bool = False,
    starting_balance: str | None = None,
) -> dict[str, object]:
    return run_batch_research(
        settings_path=settings_path,
        instruments=[instrument_name],
        strategies=strategies,
        data_source=data_source,
        output_dir=output_dir,
        cache_dir=cache_dir,
        use_strategy_config=use_strategy_config,
        starting_balance=starting_balance,
    )
