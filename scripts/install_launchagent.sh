#!/bin/zsh
# Install a launchd job that runs run_daily.sh every day at 08:30 (and at login).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="${KOBO99_LAUNCHD_LABEL:-com.paulyang.kobo99}"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$PROJECT_ROOT/logs"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

sed -e "s|__LABEL__|$LABEL|g" \
    -e "s|__WORKING_DIRECTORY__|$PROJECT_ROOT|g" \
    -e "s|__RUN_SCRIPT__|$PROJECT_ROOT/scripts/run_daily.sh|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$PROJECT_ROOT/launchd/com.paulyang.kobo99.plist.template" > "$TARGET"

launchctl unload "$TARGET" >/dev/null 2>&1 || true
launchctl load "$TARGET"
echo "Installed $TARGET (daily 08:30)"
