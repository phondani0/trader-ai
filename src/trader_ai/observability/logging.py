from __future__ import annotations

from datetime import UTC
from datetime import datetime
import json
import sys
from typing import IO
from typing import Any

from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk._logs.export import LogExporter
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import SpanExporter

from trader_ai.observability.metrics import metrics
from trader_ai.observability.settings import ObservabilitySettings
from trader_ai.observability.tracing import tracer


_settings = ObservabilitySettings()
_stdout: IO[str] = sys.stdout
_otel_logger = None


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

        payload = _build_payload(level, message, fields)
        _emit_otel_log(level, message, payload)

        if not _settings.log_json:
            rendered = f"{level} | {message}"
            print(rendered, file=_stdout, flush=True)
            return

        print(json.dumps(payload, default=str, separators=(",", ":")), file=_stdout, flush=True)


logger = StructuredLogger()


def configure_observability(
    settings: ObservabilitySettings | None = None,
    *,
    log_exporter: LogExporter | None = None,
    metric_reader: MetricReader | None = None,
    span_exporter: SpanExporter | None = None,
    stdout: IO[str] | None = None,
) -> ObservabilitySettings:
    global _settings
    global _stdout
    global _otel_logger

    _settings = settings or ObservabilitySettings.from_env()
    _stdout = stdout or sys.stdout
    _otel_logger = _build_otel_logger(_settings, log_exporter=log_exporter)
    metrics.configure(_settings, metric_reader=metric_reader)
    tracer.configure(_settings, span_exporter=span_exporter)
    return _settings


def _build_payload(level: str, message: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "message": message,
        "service": _settings.service_name,
        "env": _settings.environment,
        "run_id": _settings.run_id,
        **fields,
    }


def _build_otel_logger(
    settings: ObservabilitySettings,
    *,
    log_exporter: LogExporter | None = None,
):
    if not settings.enabled:
        return None

    provider = LoggerProvider(
        resource=Resource.create(
            {
                "service.name": settings.service_name,
                "deployment.environment": settings.environment,
                "trader.run_id": settings.run_id,
            }
        )
    )
    exporter = log_exporter or OTLPLogExporter(endpoint=settings.otlp_endpoint or None)
    processor = (
        SimpleLogRecordProcessor(exporter)
        if log_exporter is not None
        else BatchLogRecordProcessor(exporter)
    )
    provider.add_log_record_processor(processor)
    return provider.get_logger("trader_ai")


def _emit_otel_log(level: str, message: str, payload: dict[str, Any]) -> None:
    if _otel_logger is None:
        return

    attributes = {
        key: value
        for key, value in payload.items()
        if key not in {"timestamp", "level", "message"}
    }
    _otel_logger.emit(
        body=message,
        severity_text=level,
        severity_number=_severity_number(level),
        attributes=attributes,
    )


def _severity_number(level: str) -> SeverityNumber:
    return {
        "DEBUG": SeverityNumber.DEBUG,
        "INFO": SeverityNumber.INFO,
        "WARNING": SeverityNumber.WARN,
        "ERROR": SeverityNumber.ERROR,
        "CRITICAL": SeverityNumber.FATAL,
    }[level]
