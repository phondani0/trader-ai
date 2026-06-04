from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ObservabilitySettings:
    enabled: bool = True
    service_name: str = "trader-ai"
    environment: str = "paper"
    run_id: str = ""
    otlp_endpoint: str = "http://localhost:4317"
    log_json: bool = True

    @classmethod
    def from_env(cls) -> ObservabilitySettings:
        return cls(
            enabled=_env_bool("OBSERVABILITY_ENABLED", True),
            service_name=os.environ.get("OTEL_SERVICE_NAME", "trader-ai"),
            environment=os.environ.get(
                "TRADING_ENV",
                os.environ.get("TRADING_MODE", os.environ.get("ENVIRONMENT", "paper")),
            ),
            run_id=os.environ.get("RUN_ID", _default_run_id()),
            otlp_endpoint=os.environ.get(
                "OTEL_EXPORTER_OTLP_ENDPOINT",
                "http://localhost:4317",
            ),
            log_json=_env_bool("LOG_JSON", True),
        )


def _default_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
