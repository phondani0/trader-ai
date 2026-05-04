from __future__ import annotations

from pathlib import Path

import pandas as pd


def build_walk_forward_windows(
    *,
    timestamps,
    train_bars: int,
    test_bars: int,
    step_bars: int,
) -> list[dict[str, str]]:
    windows: list[dict[str, str]] = []
    start = 0
    last_index = len(timestamps)
    while start + train_bars + test_bars <= last_index:
        train_start = timestamps[start]
        train_end = timestamps[start + train_bars - 1]
        test_start = timestamps[start + train_bars]
        test_end = timestamps[start + train_bars + test_bars - 1]
        windows.append(
            {
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
            }
        )
        start += step_bars
    return windows


def load_walk_forward_windows_from_csv(
    *,
    csv_path: str | Path,
    train_bars: int,
    test_bars: int,
    step_bars: int,
) -> list[dict[str, str]]:
    bars = pd.read_csv(csv_path)
    timestamps = pd.to_datetime(bars["timestamp"], utc=True)
    return build_walk_forward_windows(
        timestamps=timestamps,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
    )
