#!/bin/zsh
# Commit data/ and push to GitHub Pages if anything changed. Usage: publish_data.sh "<commit message>"
set -euo pipefail

cd "$(dirname "$0")/.."
git add data
if git diff --cached --quiet; then
  echo "No data changes; nothing to publish."
  exit 0
fi
git commit --quiet -m "$1"
git pull --rebase --quiet origin main
git push --quiet origin main
echo "Published -> https://zapion.github.io/kobo99-digest/"
