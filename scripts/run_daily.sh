#!/bin/zsh
# Fetch today's Kobo 99 digest, then publish it to GitHub Pages.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON_BIN:-$(command -v python3)}"
NETWORK_PROBE_URL="https://api.github.com"
NETWORK_WAIT_ATTEMPTS=12
NETWORK_WAIT_SECONDS=5

# After wake-from-sleep the network may not be up yet.
for ((i = 1; i <= NETWORK_WAIT_ATTEMPTS; i++)); do
  curl -fsS -m 5 -o /dev/null "$NETWORK_PROBE_URL" && break
  echo "[$(date '+%F %T')] waiting for network ($i/$NETWORK_WAIT_ATTEMPTS)" >&2
  sleep "$NETWORK_WAIT_SECONDS"
done

cd "$PROJECT_ROOT"
"$PYTHON" src/kobo99.py
"$PYTHON" src/publisher.py
