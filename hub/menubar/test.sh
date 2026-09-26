#!/bin/sh
set -eu
cd "$(dirname "$0")"
BUILD_DIR="${TMPDIR:-/tmp}/orchestra-tasks-tests"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/module-cache"
swiftc -parse-as-library -module-cache-path "$BUILD_DIR/module-cache" Models.swift Clients.swift Tests.swift -o "$BUILD_DIR/OrchestraTasksTests"
"$BUILD_DIR/OrchestraTasksTests" &
TEST_PID=$!
ELAPSED=0
while kill -0 "$TEST_PID" 2>/dev/null; do
  if [ "$ELAPSED" -ge 15 ]; then
    kill "$TEST_PID" 2>/dev/null || true
    wait "$TEST_PID" 2>/dev/null || true
    echo "Orchestra Tasks tests timed out" >&2
    exit 1
  fi
  sleep 1
  ELAPSED=$((ELAPSED + 1))
done
wait "$TEST_PID"
