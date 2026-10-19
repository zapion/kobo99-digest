#!/bin/zsh
# Scheduled path (launchd, Thu/Mon 10:30): ingest the latest Kobo emails from Gmail and publish.
# On failure, shows a macOS notification so the bookmarklet backup can be used instead.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON_BIN:-$(command -v python3)}"
NETWORK_PROBE_URL="https://imap.gmail.com"
NETWORK_WAIT_ATTEMPTS=12
NETWORK_WAIT_SECONDS=10

notify() {
  osascript -e "display notification \"$1\" with title \"Kobo 書單\"" >/dev/null 2>&1 || true
}

log() {
  echo "[$(date '+%F %T')] $*"
}

cd "$PROJECT_ROOT" || exit 1

# After wake-from-sleep the network may not be up yet.
for ((i = 1; i <= NETWORK_WAIT_ATTEMPTS; i++)); do
  curl -sS -m 5 -o /dev/null "$NETWORK_PROBE_URL" 2>/dev/null && break
  log "waiting for network ($i/$NETWORK_WAIT_ATTEMPTS)"
  sleep "$NETWORK_WAIT_SECONDS"
done

if ! git pull --quiet --rebase origin main; then
  log "git pull failed"
  notify "git pull 失敗，請查看 logs/auto_update.log"
  exit 1
fi

slugs=$("$PYTHON" src/mail_ingest.py)
ingest_status=$?
log "ingested: ${slugs//$'\n'/ }"

if [[ -n "$slugs" ]] && ! scripts/publish_data.sh "data: ${slugs//$'\n'/ } (email)"; then
  log "publish failed"
  notify "推送 GitHub 失敗，請查看 logs/auto_update.log"
  exit 1
fi

if (( ingest_status != 0 )); then
  notify "Email 自動匯入失敗，請改用書籤備援（logs/auto_update.log）"
  exit "$ingest_status"
fi
log "done"
