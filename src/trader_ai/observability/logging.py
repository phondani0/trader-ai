from __future__ import annotations

from datetime import UTC
from datetime import datetime
import json
import sys
from typing import IO
from typing import Any

from trader_ai.observability.settings import ObservabilitySettings


_settings = ObservabilitySettings()
_stdout: IO[str] = sys.stdout


class StructuredLogger:
    def debug(self, message: str, **fields: Any) -> None:
        self._write("DEBUG", message, fields)

    def info(self, message: str, **fields: Any) -> None:
        self._write("INFO", message, fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._write("WARNING", message, fields)

    def error(self, message: str, **fields: Any) -> None:
        self._write("ERROR", message, fields)

    def critical(self, message: str, **fields: Any) -> None:
        self._write("CRITICAL", message, fields)

    def _write(self, level: str, message: str, fields: dict[str, Any]) -> None:
        if fields and "log_type" not in fields:
            raise ValueError("structured logs with extra fields require log_type")

        if not _settings.log_json:
            rendered = f"{level} | {message}"
            print(rendered, file=_stdout, flush=True)
            return

        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
            "service": _settings.service_name,
            "env": _settings.environment,
            "run_id": _settings.run_id,
            **fields,
        }
        print(json.dumps(payload, default=str, separators=(",", ":")), file=_stdout, flush=True)


logger = StructuredLogger()


def configure_observability(
    settings: ObservabilitySettings | None = None,
    *,
    stdout: IO[str] | None = None,
) -> ObservabilitySettings:
    global _settings
    global _stdout

    _settings = settings or ObservabilitySettings.from_env()
    _stdout = stdout or sys.stdout
    return _settings
