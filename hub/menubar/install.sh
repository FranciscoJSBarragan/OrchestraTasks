#!/bin/sh
set -eu
cd "$(dirname "$0")"
./build.sh

APP_TARGET="$HOME/Applications/Orchestra Tasks.app"
AGENT_TARGET="$HOME/Library/LaunchAgents/com.orchestra.tasks.plist"
STATE_ROOT="$HOME/.orchestra"
APP_STAGING="$APP_TARGET.new.$$"
AGENT_STAGING="$AGENT_TARGET.new.$$"
APP_PREVIOUS="$APP_TARGET.previous.$$"
AGENT_PREVIOUS="$AGENT_TARGET.previous.$$"
DOMAIN="gui/$(id -u)"

PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$candidate" ] && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Python 3.11 or newer is required" >&2
  exit 1
fi

mkdir -p "$HOME/Applications" "$HOME/Library/LaunchAgents" "$STATE_ROOT"
if [ -e "$APP_STAGING" ] || [ -e "$AGENT_STAGING" ] || \
   [ -e "$APP_PREVIOUS" ] || [ -e "$AGENT_PREVIOUS" ]; then
  echo "A previous Orchestra Tasks installation attempt needs cleanup" >&2
  exit 1
fi
cp -R build/Orchestra\ Tasks.app "$APP_STAGING"

APP_EXECUTABLE="$APP_TARGET/Contents/MacOS/OrchestraTasks"
sed -e "s|__APP_EXECUTABLE__|$APP_EXECUTABLE|g" -e "s|__STATE_ROOT__|$STATE_ROOT|g" \
  launchd/com.orchestra.tasks.plist.template > "$AGENT_STAGING"
chmod 600 "$AGENT_STAGING"

launchctl bootout "$DOMAIN/com.orchestra.tasks" 2>/dev/null || true
if [ -e "$APP_TARGET" ]; then mv "$APP_TARGET" "$APP_PREVIOUS"; fi
if [ -e "$AGENT_TARGET" ]; then mv "$AGENT_TARGET" "$AGENT_PREVIOUS"; fi
mv "$APP_STAGING" "$APP_TARGET"
mv "$AGENT_STAGING" "$AGENT_TARGET"

if ! launchctl bootstrap "$DOMAIN" "$AGENT_TARGET" || \
   ! launchctl print "$DOMAIN/com.orchestra.tasks" >/dev/null 2>&1; then
  echo "Orchestra Tasks failed to start; restoring the previous installation" >&2
  launchctl bootout "$DOMAIN/com.orchestra.tasks" 2>/dev/null || true
  rm -rf "$APP_TARGET"
  rm -f "$AGENT_TARGET"
  if [ -e "$APP_PREVIOUS" ]; then mv "$APP_PREVIOUS" "$APP_TARGET"; fi
  if [ -e "$AGENT_PREVIOUS" ]; then
    mv "$AGENT_PREVIOUS" "$AGENT_TARGET"
    launchctl bootstrap "$DOMAIN" "$AGENT_TARGET" 2>/dev/null || true
  fi
  exit 1
fi

rm -rf "$APP_PREVIOUS"
rm -f "$AGENT_PREVIOUS"
echo "Installed $APP_TARGET using $PYTHON"
