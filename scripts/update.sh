#!/bin/zsh
# Manual / backup path: ingest bookmarklet downloads (~/Downloads/weekly-*.json) and publish.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON_BIN:-$(command -v python3)}"
cd "$PROJECT_ROOT"

git pull --quiet --rebase origin main
slugs=$("$PYTHON" src/ingest.py "$@")
scripts/publish_data.sh "data: ${slugs//$'\n'/ } (bookmarklet)"
