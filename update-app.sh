#!/bin/bash
# Self-update the deployment: sync compose.yaml from the repo and apply the
# latest app image when a new build is pushed.
#
# To enable automatic updates, add this to your crontab (crontab -e):
#
#   */10 * * * * /home/ubuntu/update-app.sh
#
# Adjust the script path if it lives elsewhere. Cron runs as the user who owns
# the crontab; that user must be able to run `docker compose` (be in the docker
# group). The script syncs compose.yaml (validated with `docker compose config`;
# a broken file is rejected and the current one kept), then compares the
# registry digest of the app image against the local image's digest. It only
# pulls/recreates when the app image changed OR compose.yaml changed, so it is
# a silent no-op (nothing written to the log) when nothing is different.
#
# Output (and errors) are appended to $LOG below.
set -euo pipefail

# Where compose.yaml + .env live. Change if your project dir differs, or set
# SWGOH_PROJECT_DIR in the crontab environment.
PROJECT_DIR="${SWGOH_PROJECT_DIR:-/home/ubuntu}"
LOG="$PROJECT_DIR/swgoh-update.log"
BASE="https://raw.githubusercontent.com/skiadas/swgoh-republic-tools/main"
cd "$PROJECT_DIR"

# 1. Sync compose.yaml from the repo (fetch to a temp file, validate, apply).
COMPOSE_CHANGED=0
if curl -fsSL -o /tmp/compose.yaml.new "$BASE/compose.yaml" 2>/dev/null; then
  if docker compose -f /tmp/compose.yaml.new config --quiet >/dev/null 2>&1; then
    if ! cmp -s /tmp/compose.yaml.new compose.yaml; then
      mv /tmp/compose.yaml.new compose.yaml
      COMPOSE_CHANGED=1
      echo "$(date -u '+%F %T UTC') compose.yaml updated" >> "$LOG"
    else
      rm -f /tmp/compose.yaml.new
    fi
  else
    rm -f /tmp/compose.yaml.new
    echo "$(date -u '+%F %T UTC') fetched compose.yaml failed validation; keeping current" >> "$LOG"
  fi
fi

# 2. App image digest guard.
TAG="$(awk -F= '/^SWGOH_IMAGE_TAG=/{v=$2} END{gsub(/[ \t]/, "", v); print v}' .env 2>/dev/null || true)"
TAG="${TAG:-latest}"
IMG="ghcr.io/skiadas/swgoh-republic-tools:${TAG}"

# Registry digest the tag currently points to (top-level "Digest:" of the
# manifest index). Empty if the registry is unreachable or buildx is missing.
REMOTE="$(docker buildx imagetools inspect "$IMG" 2>&1 | sed -n 's/^Digest:[[:space:]]*//p' | head -1)" || REMOTE=""
if [ -z "$REMOTE" ]; then
  if [ "$COMPOSE_CHANGED" = "1" ]; then
    docker compose --profile web up -d >> "$LOG" 2>&1
    echo "$(date -u '+%F %T UTC') compose change applied (registry check unavailable)" >> "$LOG"
  else
    echo "$(date -u '+%F %T UTC') update check failed (registry/buildx unavailable)" >> "$LOG"
  fi
  exit 0
fi

LOCAL="$(docker image inspect "$IMG" --format '{{index .RepoDigests 0}}' 2>/dev/null | cut -d@ -f2 || true)"
if [ "$REMOTE" = "$LOCAL" ] && [ "$COMPOSE_CHANGED" = "0" ]; then
  exit 0  # nothing changed; stay silent
fi

# 3. Apply: pull the new image and let compose reconcile services.
echo "$(date -u '+%F %T UTC') applying update (${LOCAL:-none} -> $REMOTE)" >> "$LOG"
docker compose --profile web pull app >> "$LOG" 2>&1
docker compose --profile web up -d >> "$LOG" 2>&1
echo "$(date -u '+%F %T UTC') app updated" >> "$LOG"
