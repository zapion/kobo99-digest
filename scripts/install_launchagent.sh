#!/bin/zsh
# Compile the OCR helper and install the Thu/Mon 10:30 launchd job.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="${KOBO99_LAUNCHD_LABEL:-com.paulyang.kobo99}"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

mkdir -p "$PROJECT_ROOT/bin" "$PROJECT_ROOT/logs" "$HOME/Library/LaunchAgents"
swiftc -O -o "$PROJECT_ROOT/bin/ocr_bars" "$PROJECT_ROOT/scripts/ocr_bars.swift"

sed -e "s|__LABEL__|$LABEL|g" \
    -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
    -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
    "$PROJECT_ROOT/launchd/com.paulyang.kobo99.plist.template" > "$TARGET"
plutil -lint "$TARGET" >/dev/null

launchctl unload "$TARGET" >/dev/null 2>&1 || true
launchctl load "$TARGET"
echo "Installed $TARGET (Thu/Mon 10:30, and at login)"
