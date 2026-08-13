#!/usr/bin/env bash
# Start the swgoh-comlink service (local gateway to EA's read-only game APIs).
# Requires Docker. Listens on http://localhost:3200 (container port 3000).
set -euo pipefail

APP_NAME="${APP_NAME:-swgoh-reviewer}"
PORT="${PORT:-3200}"

docker network create swgoh-comlink 2>/dev/null || true
docker rm -f swgoh-comlink 2>/dev/null || true

docker pull ghcr.io/swgoh-utils/swgoh-comlink:latest

docker run --name swgoh-comlink -d --restart always \
  --network swgoh-comlink \
  --env "APP_NAME=${APP_NAME}" \
  -p "${PORT}:3000" \
  ghcr.io/swgoh-utils/swgoh-comlink:latest

echo "swgoh-comlink starting on http://localhost:${PORT} ..."
for i in $(seq 1 30); do
  if curl -sf -o /dev/null "http://localhost:${PORT}/enums"; then
    echo "ready (${i}s)"
    exit 0
  fi
  sleep 1
done
echo "timed out waiting for swgoh-comlink" >&2
exit 1
