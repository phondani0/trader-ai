from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY_ROOT = PROJECT_ROOT / "deploy" / "observability"


def test_observability_stack_declares_required_services():
    compose_path = OBSERVABILITY_ROOT / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    assert set(compose["services"]) >= {
        "otel-collector",
        "prometheus",
        "loki",
        "tempo",
        "grafana",
    }


def test_otel_collector_routes_to_prometheus_loki_and_tempo():
    collector_path = OBSERVABILITY_ROOT / "otel-collector.yaml"
    collector = yaml.safe_load(collector_path.read_text(encoding="utf-8"))

    assert "otlp" in collector["receivers"]
    assert "prometheus" in collector["exporters"]
    assert "otlphttp/loki" in collector["exporters"]
    assert "otlp/tempo" in collector["exporters"]
    assert collector["service"]["pipelines"]["metrics"]["exporters"] == [
        "prometheus"
    ]
    assert collector["service"]["pipelines"]["logs"]["exporters"] == [
        "otlphttp/loki"
    ]
    assert collector["service"]["pipelines"]["traces"]["exporters"] == [
        "otlp/tempo"
    ]


def test_grafana_datasources_include_prometheus_loki_and_tempo():
    datasource_path = (
        OBSERVABILITY_ROOT
        / "grafana"
        / "provisioning"
        / "datasources"
        / "datasources.yaml"
    )
    datasources = yaml.safe_load(datasource_path.read_text(encoding="utf-8"))

    names = {datasource["name"] for datasource in datasources["datasources"]}
    assert names >= {"Prometheus", "Loki", "Tempo"}
