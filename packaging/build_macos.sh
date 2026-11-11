#!/usr/bin/env bash
# QUOT-525: build the self-contained macOS app (py2app).
#
# Layout produced:
#   dist/LadderStock.app        <- install_root = dist/
#   dist/TickHub-CN/...         <- peers (optional) live beside the .app
#
# Double-click LadderStock.app: starts the board on 127.0.0.1:5950 and opens
# the default browser. The app carries its own CPython; no system Python needed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/build/pack-mac"
PYTHON="${PYTHON:-python3}"
VENV="$BUILD_DIR/venv"

if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
  if [[ ! -x "$VENV/bin/python" ]]; then
    echo "== create venv: $VENV"
    "$PYTHON" -m venv "$VENV"
  fi
  echo "== install build dependencies"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -e "${ROOT}[all]" "py2app>=0.28,<1"
fi

PY="$VENV/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$PYTHON"
fi

if [[ "${SKIP_FRONTEND:-0}" != "1" ]]; then
  echo "== build frontend (frontend/dist)"
  ( cd "$ROOT/frontend" && npm ci && npm run build )
fi

if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  echo "frontend/dist/index.html missing; run without SKIP_FRONTEND=1 first" >&2
  exit 1
fi

mkdir -p "$ROOT/dist" "$BUILD_DIR/bdist"
echo "== py2app"
( cd "$ROOT/packaging" && "$PY" macos/setup_py2app.py py2app \
    --dist-dir "$ROOT/dist" --bdist-base "$BUILD_DIR/bdist" )

APP="$ROOT/dist/LadderStock.app"
if [[ ! -x "$APP/Contents/MacOS/LadderStock" ]]; then
  echo "expected artifact missing: $APP/Contents/MacOS/LadderStock" >&2
  exit 1
fi

echo
echo "OK: $APP"
echo "smoke test: packaging/smoke_posix.sh '$APP/Contents/MacOS/LadderStock' 5966"
