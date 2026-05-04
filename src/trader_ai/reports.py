from __future__ import annotations

from pathlib import Path
import json

import pandas as pd


def build_leaderboard(summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        summaries,
        key=lambda item: (
            float(item.get("return_pct", 0.0)),
            int(item.get("fills", 0)),
        ),
        reverse=True,
    )


def write_leaderboard(
    *,
    summaries: list[dict[str, object]],
    output_dir: str | Path,
) -> dict[str, str]:
    raw_output_dir = Path(output_dir)
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    leaderboard = build_leaderboard(summaries)
    json_path = raw_output_dir / "leaderboard.json"
    csv_path = raw_output_dir / "leaderboard.csv"
    html_path = raw_output_dir / "index.html"
    json_path.write_text(json.dumps(leaderboard, indent=2), encoding="utf-8")
    pd.DataFrame(leaderboard).to_csv(csv_path, index=False)
    html_path.write_text(_build_index_html(leaderboard), encoding="utf-8")
    return {
        "leaderboard_json_path": str(json_path),
        "leaderboard_csv_path": str(csv_path),
        "index_html_path": str(html_path),
    }


def _build_index_html(leaderboard: list[dict[str, object]]) -> str:
    rows = []
    for entry in leaderboard:
        tearsheet = entry.get("tearsheet_path", "")
        link = f'<a href="{tearsheet}">tearsheet</a>' if tearsheet else ""
        rows.append(
            "<tr>"
            f"<td>{entry.get('strategy', '')}</td>"
            f"<td>{entry.get('instrument_id', '')}</td>"
            f"<td>{entry.get('return_pct', 0.0):.4f}</td>"
            f"<td>{entry.get('fills', 0)}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    table_rows = "\n".join(rows)
    return (
        "<html><head><title>Nautilus Research Index</title></head><body>"
        "<h1>Nautilus Research Index</h1>"
        "<table border='1' cellspacing='0' cellpadding='6'>"
        "<thead><tr><th>Strategy</th><th>Instrument</th><th>Return %</th><th>Fills</th><th>Report</th></tr></thead>"
        f"<tbody>{table_rows}</tbody>"
        "</table></body></html>"
    )
