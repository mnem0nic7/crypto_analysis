#!/usr/bin/env bash
# Blue-green deploy script.
# Usage: ./scripts/deploy.sh [blue|green]
# Optional env vars:
#   CADDYFILE_HOST_PATH — host path to Caddyfile (default: /workspace/campaign_tracker/docker/Caddyfile)
#   CADDY_CONTAINER     — Docker container running Caddy (default: campaign_tracker-caddy-1)
set -euo pipefail

TARGET="${1:-}"
if [[ "$TARGET" != "blue" && "$TARGET" != "green" ]]; then
  echo "Usage: $0 [blue|green]" >&2
  exit 1
fi

CADDYFILE_HOST_PATH="${CADDYFILE_HOST_PATH:-/workspace/campaign_tracker/docker/Caddyfile}"
CADDY_CONTAINER="${CADDY_CONTAINER:-campaign_tracker-caddy-1}"

# Determine old slot
if [[ "$TARGET" == "blue" ]]; then
  OLD="green"
  API_PORT=8011
  DASHBOARD_PORT=4001
else
  OLD="blue"
  API_PORT=8012
  DASHBOARD_PORT=4002
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

# Rewrite da.ai-al.site block in Caddyfile (add if missing, update if present)
python3 - <<PYEOF
import re, sys
path = "${CADDYFILE_HOST_PATH}"
try:
    content = open(path).read()
except OSError as e:
    print(f"ERROR: Cannot read Caddyfile at {path}: {e}", file=sys.stderr)
    sys.exit(1)
new_block = """da.ai-al.site {
  reverse_proxy ${TARGET}-dashboard:80
}"""
if 'da.ai-al.site' in content:
    updated = re.sub(r'da\.ai-al\.site \{[^\}]+\}', new_block, content)
else:
    updated = content.rstrip() + '\n\n' + new_block + '\n'
open(path, 'w').write(updated)
PYEOF

# Update local state file
cat > "${SCRIPT_DIR}/../caddy/active-slot.caddy" <<EOF
# Active slot: ${TARGET}
# Managed by scripts/deploy.sh — do not edit manually
reverse_proxy ${TARGET}-dashboard:80
EOF

# Pipe updated Caddyfile to caddy reload via stdin (bind mount may track original inode)
cat "${CADDYFILE_HOST_PATH}" | docker exec -i "${CADDY_CONTAINER}" caddy reload --config /dev/stdin --adapter caddyfile

echo "==> Waiting 5s for Caddy to drain in-flight requests..."
sleep 5

echo "==> Stopping ${OLD} slot..."
docker compose -f "${PROJECT_DIR}/docker-compose.${OLD}.yml" down

echo "✓ Deployed to ${TARGET}. Old ${OLD} slot stopped."
