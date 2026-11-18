#!/usr/bin/env bash
# Local pilot without Docker and without SSO: 5 users, 5 rooms, process sandboxes, web UI from web/dist.
#   web: (cd web && npm ci && npm run build)
#   run: scripts/dev-pilot.sh [port]
# Sign in with a one-time link (printed below), or simulate the SSO proxy with the two headers
#   X-Auth-Request-User: user1   X-Nixie-Proxy-Secret: $NIXIE_PROXY_SECRET
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8080}"
ROOT="${NIXIE_DEV_ROOT:-/tmp/nixie-dev-pilot}"
export NIXIE_SESSION_SECRET="${NIXIE_SESSION_SECRET:-dev-session-secret-change-me}"
export NIXIE_PROXY_SECRET="${NIXIE_PROXY_SECRET:-dev-proxy-secret}"
export NIXIE_ADMIN_TOKEN="${NIXIE_ADMIN_TOKEN:-dev-admin}"
python -m nixie.cli pilot init --plan examples/pilot/plan.yaml --users-root "$ROOT/users" --gateway-url "http://127.0.0.1:$PORT" >/dev/null
for u in user1 user2 user3 user4 user5; do
  echo "$u: $(python -m nixie.cli login-link --users-root "$ROOT/users" --user "$u" --base "http://127.0.0.1:$PORT" --ttl 3600)"
done
exec python -m nixie.cli gateway --users-root "$ROOT/users" --backend process --host 127.0.0.1 --port "$PORT" \
  --auth both --web-root web/dist --insecure-cookie --idle-seconds 120
