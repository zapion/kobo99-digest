#!/bin/zsh
# Rate current books on Readmoo from this machine, then publish. Optional: BRAVE_API_KEY in .env.
set -euo pipefail

cd "$(dirname "$0")/.."
git pull --rebase --quiet origin main
if [[ -f .env ]]; then
  set -a; source ./.env; set +a
fi
python3 src/readmoo_local.py
scripts/publish_data.sh "data: readmoo ratings (local)"
