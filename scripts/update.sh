#!/bin/zsh
# Ingest bookmarklet downloads (~/Downloads/weekly-*.json) and publish to GitHub Pages.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON_BIN:-$(command -v python3)}"
cd "$PROJECT_ROOT"

git pull --quiet --rebase origin main
slugs=$("$PYTHON" src/ingest.py "$@")
git add data
if git diff --cached --quiet; then
  echo "No data changes; nothing to publish."
  exit 0
fi
git commit --quiet -m "data: ${slugs//$'\n'/ }"
git push --quiet origin main
echo "Published -> https://zapion.github.io/kobo99-digest/"
