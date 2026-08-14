#!/bin/bash
# Self-update the app image when a new build is pushed to the repo.
#
# To enable automatic updates, add this to your crontab (crontab -e):
#
#   */10 * * * * /home/ubuntu/update-app.sh
#
# Adjust the script path if it lives elsewhere. Cron runs as the user who owns
# the crontab; that user must be able to run `docker compose` (be in the docker
# group). The script compares the registry digest of the app image against the
# local image's digest and only pulls/recreates when they differ, so it is a
# silent no-op (nothing written to the log) when no update exists.
#
# Output (and errors) are appended to $LOG below.
set -euo pipefail

# Where compose.yaml + .env live. Change if your project dir differs, or set
# SWGOH_PROJECT_DIR in the crontab environment.
PROJECT_DIR="${SWGOH_PROJECT_DIR:-/home/ubuntu}"
LOG="$PROJECT_DIR/swgoh-update.log"
cd "$PROJECT_DIR"

# Mirror compose's SWGOH_IMAGE_TAG (default :latest) from .env.
TAG="$(awk -F= '/^SWGOH_IMAGE_TAG=/{v=$2} END{gsub(/[ \t]/, "", v); print v}' .env 2>/dev/null || true)"
TAG="${TAG:-latest}"
IMG="ghcr.io/skiadas/swgoh-republic-tools:${TAG}"

# Registry digest the tag currently points to (top-level "Digest:" of the
# manifest index). Empty if the registry is unreachable or buildx is missing.
REMOTE="$(docker buildx imagetools inspect "$IMG" 2>&1 | sed -n 's/^Digest:[[:space:]]*//p' | head -1)" || REMOTE=""
if [ -z "$REMOTE" ]; then
  echo "$(date -u '+%F %T UTC') update check failed (registry/buildx unavailable)" >> "$LOG"
  exit 0
fi

LOCAL="$(docker image inspect "$IMG" --format '{{index .RepoDigests 0}}' 2>/dev/null | cut -d@ -f2 || true)"
if [ "$REMOTE" = "$LOCAL" ]; then
  exit 0  # no update available; stay silent
fi

echo "$(date -u '+%F %T UTC') updating app (${LOCAL:-none} -> $REMOTE)" >> "$LOG"
docker compose pull app >> "$LOG" 2>&1
docker compose up -d app >> "$LOG" 2>&1
echo "$(date -u '+%F %T UTC') app updated" >> "$LOG"
