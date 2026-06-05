#!/usr/bin/env bash
set -euo pipefail

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale is not installed on this host" >&2
  exit 1
fi

tailscale status >/dev/null
tailscale serve --bg --yes --https=443 http://127.0.0.1:3300
tailscale serve status
