from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY_ROOT = PROJECT_ROOT / "observability"


def test_observability_stack_declares_required_services():
    compose_path = PROJECT_ROOT / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    assert compose["name"] == "observability"
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
    assert "otlp_http/loki" in collector["exporters"]
    assert "otlp_grpc/tempo" in collector["exporters"]
    assert collector["service"]["pipelines"]["metrics"]["exporters"] == [
        "prometheus"
    ]
    assert collector["service"]["pipelines"]["logs"]["exporters"] == [
        "otlp_http/loki"
    ]
    assert collector["service"]["pipelines"]["traces"]["exporters"] == [
        "otlp_grpc/tempo"
    ]


def test_observability_stack_avoids_common_local_grafana_port_conflict():
    compose_path = PROJECT_ROOT / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    grafana_ports = compose["services"]["grafana"]["ports"]

    assert "${GRAFANA_PORT:-3300}:3000" in grafana_ports
    assert "3000:3000" not in grafana_ports


def test_tempo_config_is_compatible_with_latest_local_stack():
    tempo_path = OBSERVABILITY_ROOT / "tempo.yaml"
    tempo = yaml.safe_load(tempo_path.read_text(encoding="utf-8"))

    assert "compactor" not in tempo
    assert tempo["distributor"]["receivers"]["otlp"]["protocols"]["grpc"][
        "endpoint"
    ] == "0.0.0.0:4317"


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
    uids = {datasource["uid"] for datasource in datasources["datasources"]}
    assert names >= {"Prometheus", "Loki", "Tempo"}
    assert uids >= {"prometheus", "loki", "tempo"}


def test_trader_ai_dashboard_uses_provisioned_datasource_uids():
    dashboard_path = OBSERVABILITY_ROOT / "grafana" / "dashboards" / "trader-ai-live.json"
    dashboard = yaml.safe_load(dashboard_path.read_text(encoding="utf-8"))

    datasource_uids = {
        panel["datasource"]["uid"]
        for panel in dashboard["panels"]
        if "datasource" in panel
    }

    assert datasource_uids >= {"prometheus", "loki"}
    assert "Prometheus" not in datasource_uids


def test_trader_ai_dashboard_has_live_logs_panel():
    dashboard_path = OBSERVABILITY_ROOT / "grafana" / "dashboards" / "trader-ai-live.json"
    dashboard = yaml.safe_load(dashboard_path.read_text(encoding="utf-8"))

    log_panels = [
        panel
        for panel in dashboard["panels"]
        if panel.get("type") == "logs" and panel["datasource"]["uid"] == "loki"
    ]

    assert log_panels
    assert log_panels[0]["targets"][0]["expr"] == '{service_name="trader-ai"}'
