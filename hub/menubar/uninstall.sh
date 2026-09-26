#!/bin/sh
set -eu
APP_TARGET="$HOME/Applications/Orchestra Tasks.app"
AGENT_TARGET="$HOME/Library/LaunchAgents/com.orchestra.tasks.plist"
launchctl bootout "gui/$(id -u)/com.orchestra.tasks" 2>/dev/null || true
if [ -e "$APP_TARGET" ]; then mv "$APP_TARGET" "$HOME/.Trash/Orchestra Tasks.app"; fi
if [ -e "$AGENT_TARGET" ]; then mv "$AGENT_TARGET" "$HOME/.Trash/com.orchestra.tasks.plist"; fi
echo "Removed the app and LaunchAgent. ~/.orchestra and Git resources were preserved."
