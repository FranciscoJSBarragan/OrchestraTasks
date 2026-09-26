#!/bin/sh
# Build Orchestra Tasks.app without an Xcode project or external dependencies.
set -eu
cd "$(dirname "$0")"
BUILD_DIR="${TMPDIR:-/tmp}/orchestra-tasks-build"
CACHE_DIR="$BUILD_DIR/module-cache"
APP="build/Orchestra Tasks.app"
rm -rf "$BUILD_DIR" "$APP"
mkdir -p "$CACHE_DIR" "$APP/Contents/MacOS" \
  "$APP/Contents/Resources/es.lproj" "$APP/Contents/Resources/en.lproj"
swiftc -O -parse-as-library -module-cache-path "$CACHE_DIR" \
  -framework AppKit -framework SwiftUI -framework UserNotifications \
  Models.swift Clients.swift TaskStore.swift Views.swift OrchestraTasksApp.swift \
  -o "$BUILD_DIR/OrchestraTasks"
cp Info.plist "$APP/Contents/Info.plist"
cp "$BUILD_DIR/OrchestraTasks" "$APP/Contents/MacOS/OrchestraTasks"
cp en.lproj/Localizable.strings "$APP/Contents/Resources/en.lproj/Localizable.strings"
cp es.lproj/Localizable.strings "$APP/Contents/Resources/es.lproj/Localizable.strings"
mkdir -p "$APP/Contents/Resources/control/orchestra_control" "$APP/Contents/Resources/scripts"
cp ../../scripts/task_control.py "$APP/Contents/Resources/scripts/task_control.py"
cp ../../scripts/git_identity.py "$APP/Contents/Resources/scripts/git_identity.py"
cp ../../control/orchestra_control/*.py "$APP/Contents/Resources/control/orchestra_control/"
codesign --force --sign - "$APP"
echo "Built $APP"
