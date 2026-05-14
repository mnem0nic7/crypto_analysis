#!/usr/bin/env bash
# Blue-green deploy script.
# Usage: ./scripts/deploy.sh [blue|green]
# Required env vars: CADDY_SNIPPET_PATH, CADDYFILE_PATH
set -euo pipefail

TARGET="${1:-}"
if [[ "$TARGET" != "blue" && "$TARGET" != "green" ]]; then
  echo "Usage: $0 [blue|green]" >&2
  exit 1
fi

if [[ -z "${CADDY_SNIPPET_PATH:-}" ]]; then
  echo "ERROR: CADDY_SNIPPET_PATH is not set" >&2
  exit 1
fi

if [[ -z "${CADDYFILE_PATH:-}" ]]; then
  echo "ERROR: CADDYFILE_PATH is not set" >&2
  exit 1
fi

# Determine old slot
if [[ "$TARGET" == "blue" ]]; then
  OLD="green"
  API_PORT=8011
  DASHBOARD_PORT=3001
else
  OLD="blue"
  API_PORT=8012
  DASHBOARD_PORT=3002
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Building and starting ${TARGET} slot..."
docker compose -f "${PROJECT_DIR}/docker-compose.${TARGET}.yml" up -d --build

echo "==> Waiting for ${TARGET} slot to be healthy (up to 120s)..."
DEADLINE=$(( $(date +%s) + 120 ))
while true; do
  if [[ $(date +%s) -gt $DEADLINE ]]; then
    echo "ERROR: Health check timed out after 120s. Caddy unchanged; ${OLD} slot stays live." >&2
    exit 1
  fi
  API_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${API_PORT}/health" || echo "000")
  DASH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${DASHBOARD_PORT}/" || echo "000")
  if [[ "$API_STATUS" == "200" && "$DASH_STATUS" == "200" ]]; then
    echo "   API: ${API_STATUS}  Dashboard: ${DASH_STATUS} — healthy"
    break
  fi
  echo "   API: ${API_STATUS}  Dashboard: ${DASH_STATUS} — waiting..."
  sleep 5
done

echo "==> Switching Caddy to ${TARGET} slot..."
cat > "${CADDY_SNIPPET_PATH}" <<EOF
# Active slot: ${TARGET}
# Managed by scripts/deploy.sh — do not edit manually
reverse_proxy /api/* localhost:${API_PORT}
reverse_proxy localhost:${DASHBOARD_PORT}
EOF

caddy reload --config "${CADDYFILE_PATH}"

echo "==> Waiting 5s for Caddy to drain in-flight requests..."
sleep 5

echo "==> Stopping ${OLD} slot..."
docker compose -f "${PROJECT_DIR}/docker-compose.${OLD}.yml" down

echo "✓ Deployed to ${TARGET}. Old ${OLD} slot stopped."
