#!/usr/bin/env bash
#
# scripts/tunnel.sh
#
# Exposes the local Sentinel API server (FastAPI + websocket, default
# port 8000) to the public internet via a Cloudflare Tunnel, so the
# Next.js dashboard (or judges/demo viewers) can reach it without any
# port forwarding or DNS configuration.
#
# Usage:
#   ./scripts/tunnel.sh
#   ./scripts/tunnel.sh 9000   # tunnel a different local port
#
set -euo pipefail

LOCAL_PORT="${1:-8000}"
LOCAL_URL="http://localhost:${LOCAL_PORT}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/cloudflared_tunnel.log"

log() {
  echo "[tunnel] $*"
}

# ---------------------------------------------------------------------
# 1. Verify cloudflared is installed; provide install guidance if not.
# ---------------------------------------------------------------------
if ! command -v cloudflared >/dev/null 2>&1; then
  cat >&2 <<'EOF'
[tunnel] ERROR: 'cloudflared' is not installed or not on PATH.

Install it with one of the following, depending on your platform:

  Debian/Ubuntu (amd64):
    curl -fL -o /tmp/cloudflared.deb \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    sudo dpkg -i /tmp/cloudflared.deb

  Linux (generic binary, amd64):
    curl -fL -o /usr/local/bin/cloudflared \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    chmod +x /usr/local/bin/cloudflared

  macOS (Homebrew):
    brew install cloudflared

  Windows (winget):
    winget install --id Cloudflare.cloudflared

After installing, re-run this script:
    ./scripts/tunnel.sh
EOF
  exit 1
fi

log "cloudflared found: $(command -v cloudflared)"
log "cloudflared version: $(cloudflared --version 2>&1 | head -n 1)"

# ---------------------------------------------------------------------
# 2. Verify the local Sentinel API is actually reachable before
#    tunneling it, so failures are diagnosed locally rather than
#    surfacing as an opaque tunnel error.
# ---------------------------------------------------------------------
if command -v curl >/dev/null 2>&1; then
  if curl --silent --fail --max-time 3 "${LOCAL_URL}/health" >/dev/null 2>&1; then
    log "Local Sentinel API is reachable at ${LOCAL_URL}."
  else
    log "WARNING: could not reach ${LOCAL_URL}/health. The tunnel will still" \
        "be started, but requests will fail until the Sentinel API server" \
        "(main.py / main.py --server-only) is running on port ${LOCAL_PORT}."
  fi
fi

# ---------------------------------------------------------------------
# 3. Start the Cloudflare Tunnel, streaming logs to both stdout and a
#    log file so the generated public URL can be grepped afterward.
# ---------------------------------------------------------------------
log "Starting Cloudflare Tunnel for ${LOCAL_URL}..."
log "Logs are also being written to: ${LOG_FILE}"
log "Press Ctrl+C to stop the tunnel."
echo ""

# cloudflared prints the generated trycloudflare.com URL to stderr on
# startup. We tee combined output to a log file so it can be inspected
# even if the terminal scrolls, while still streaming live to the
# console for immediate visibility.
cloudflared tunnel --url "${LOCAL_URL}" 2>&1 | tee "${LOG_FILE}" &
TUNNEL_PID=$!

cleanup() {
  log "Stopping Cloudflare Tunnel (PID ${TUNNEL_PID})..."
  kill "${TUNNEL_PID}" 2>/dev/null || true
  wait "${TUNNEL_PID}" 2>/dev/null || true
  log "Tunnel stopped."
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------
# 4. Poll the log file for the generated public URL and print explicit
#    instructions for wiring it into the dashboard's environment.
# ---------------------------------------------------------------------
log "Waiting for cloudflared to negotiate a public URL..."

PUBLIC_URL=""
for _ in $(seq 1 30); do
  if [ -f "${LOG_FILE}" ]; then
    PUBLIC_URL="$(grep -oE 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' "${LOG_FILE}" | head -n 1 || true)"
  fi
  if [ -n "${PUBLIC_URL}" ]; then
    break
  fi
  sleep 1
done

if [ -n "${PUBLIC_URL}" ]; then
  WS_URL="$(echo "${PUBLIC_URL}" | sed -E 's#^https://#wss://#')/ws"
  cat <<EOF

===============================================================================
Sentinel API is now publicly reachable via Cloudflare Tunnel.

  Public HTTP URL : ${PUBLIC_URL}
  Public WS URL    : ${WS_URL}

Configure the dashboard to use this tunnel by setting, in dashboard/.env.local:

  NEXT_PUBLIC_API_URL=${PUBLIC_URL}
  NEXT_PUBLIC_WS_URL=${WS_URL}

Then restart the dashboard dev server (npm run dev) for the change to
take effect.
===============================================================================

EOF
else
  log "WARNING: could not detect the public URL automatically within 30s."
  log "Check ${LOG_FILE} for the 'trycloudflare.com' URL printed by cloudflared."
fi

wait "${TUNNEL_PID}"
