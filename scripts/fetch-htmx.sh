#!/usr/bin/env bash
# Fetch the pinned htmx build into server/static/ (also used by the Dockerfile).
set -euo pipefail
HTMX_VERSION="${HTMX_VERSION:-2.0.4}"
URL="https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js"
OUT="server/static/htmx.min.js"
mkdir -p "$(dirname "$OUT")"
curl -fsSL "$URL" -o "$OUT"
echo "wrote $OUT ($(wc -c < "$OUT") bytes) from htmx $HTMX_VERSION"
