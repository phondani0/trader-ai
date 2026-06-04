#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

export PATH="$HOME/.local/bin:$PATH"

DOCKER_CMD=(docker)

configure_docker_command() {
  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
    return 0
  fi

  if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    DOCKER_CMD=(sudo docker)
    return 0
  fi

  cat >&2 <<'EOF'
Docker is installed, but this deploy user cannot access the Docker daemon.

Fix on the VPS with one of these:
  sudo usermod -aG docker "$USER"
  # then log out and log back in

Or configure passwordless sudo for docker compose deploys.
EOF
  return 1
}

wait_for_http() {
  local name="$1"
  local url="$2"

  for _ in $(seq 1 30); do
    if curl -fsS --max-time 5 "$url" >/dev/null; then
      return 0
    fi
    sleep 2
  done

  echo "$name did not become ready at $url" >&2
  return 1
}

uv sync --frozen

configure_docker_command
"${DOCKER_CMD[@]}" compose up -d --remove-orphans
"${DOCKER_CMD[@]}" compose ps

wait_for_http "Loki" "http://127.0.0.1:3100/ready"
wait_for_http "Tempo" "http://127.0.0.1:3200/ready"
wait_for_http "Prometheus" "http://127.0.0.1:9090/-/healthy"
wait_for_http "Grafana" "http://127.0.0.1:3300/login"

pm2 restart trader-ai --update-env 2>/dev/null || pm2 start "uv run start" --name trader-ai --time
pm2 save
pm2 status trader-ai
