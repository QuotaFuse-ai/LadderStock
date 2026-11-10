#!/usr/bin/env bash
# QUOT-525: build the self-contained Linux AppImage.
#
# Layout produced:
#   dist/LadderStock-x86_64.AppImage   <- install_root = dist/
#
# Double-click / execute the AppImage: starts the board on 127.0.0.1:5950 and
# opens the default browser. The AppImage carries CPython; no system Python.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/build/pack-linux"
PYTHON="${PYTHON:-python3}"
VENV="$BUILD_DIR/venv"
APPDIR="$BUILD_DIR/AppDir"
PYINSTALLER_DIST="$ROOT/dist/LadderStock"

if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
  if [[ ! -x "$VENV/bin/python" ]]; then
    echo "== create venv: $VENV"
    "$PYTHON" -m venv "$VENV"
  fi
  echo "== install build dependencies"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -e "${ROOT}[all]" "pyinstaller>=6.11,<7"
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

echo "== PyInstaller (onedir)"
"$PY" -m PyInstaller --noconfirm --clean \
  --distpath "$ROOT/dist" \
  --workpath "$BUILD_DIR/work" \
  "$ROOT/packaging/LadderStock.spec"

if [[ ! -x "$PYINSTALLER_DIST/LadderStock" ]]; then
  echo "expected PyInstaller artifact missing: $PYINSTALLER_DIST/LadderStock" >&2
  exit 1
fi

echo "== assemble AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a "$PYINSTALLER_DIST/." "$APPDIR/usr/bin/"
install -m 0644 "$ROOT/packaging/linux/ladderstock.desktop" "$APPDIR/ladderstock.desktop"
install -m 0644 "$ROOT/packaging/linux/ladderstock.svg" "$APPDIR/ladderstock.svg"
install -m 0644 "$ROOT/packaging/linux/ladderstock.svg" \
  "$APPDIR/usr/share/icons/hicolor/scalable/apps/ladderstock.svg"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/LadderStock" "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "== appimagetool"
TOOL="${APPIMAGETOOL:-$BUILD_DIR/appimagetool-x86_64.AppImage}"
if [[ ! -x "$TOOL" ]]; then
  curl -fL --retry 3 -o "$TOOL" \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi

OUT="$ROOT/dist/LadderStock-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUT"
chmod +x "$OUT"

echo
echo "OK: $OUT"
echo "smoke test: packaging/smoke_posix.sh '$OUT' 5967"
