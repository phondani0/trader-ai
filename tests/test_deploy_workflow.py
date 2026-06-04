from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_deploy_workflow_reconciles_observability_stack_before_pm2_restart():
    workflow_path = PROJECT_ROOT / ".github" / "workflows" / "deploy.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    deploy_steps = workflow["jobs"]["deploy"]["steps"]

    restart_step = next(step for step in deploy_steps if step["name"] == "Build & Restart Trader AI")
    workflow_script = restart_step["run"]
    deploy_script_path = PROJECT_ROOT / "scripts" / "deploy_remote.sh"
    deploy_script = deploy_script_path.read_text(encoding="utf-8")

    assert "bash scripts/deploy_remote.sh" in workflow_script
    assert "configure_docker_command" in deploy_script
    assert 'DOCKER_CMD=(sudo docker)' in deploy_script
    assert '"${DOCKER_CMD[@]}" compose up -d --remove-orphans' in deploy_script
    assert '"${DOCKER_CMD[@]}" compose ps' in deploy_script
    assert deploy_script.index("configure_docker_command") < deploy_script.index(
        "pm2 restart trader-ai"
    )
    assert 'wait_for_http "Loki" "http://127.0.0.1:3100/ready"' in deploy_script
    assert 'wait_for_http "Tempo" "http://127.0.0.1:3200/ready"' in deploy_script
    assert 'wait_for_http "Prometheus" "http://127.0.0.1:9090/-/healthy"' in deploy_script
    assert 'wait_for_http "Grafana" "http://127.0.0.1:3300/login"' in deploy_script
