# QUOT-549: create versioned release archives (zip / tar.gz / dmg-ready app dir)
# for non-Windows platforms. Output: dist/release/LadderStock-<ver>-<platform>.
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="${LDST_DIST_DIR:-$ROOT/dist}"
OUT_DIR="$ROOT/dist/release"
mkdir -p "$OUT_DIR"

VERSION=""
if [ -n "${LDST_RELEASE_VERSION:-}" ]; then
    VERSION="${LDST_RELEASE_VERSION#v}"
fi
if [ -z "$VERSION" ]; then
    VERSION="$(sed -n 's/__version__ *= *"\([^"]*\)"/\1/p' "$ROOT/src/ladderstock/__init__.py")"
fi
if [ -z "$VERSION" ]; then
    echo "cannot determine version" >&2
    exit 1
fi

case "$(uname -s)" in
    Darwin) PLATFORM="macos" ;;
    Linux)  PLATFORM="linux" ;;
    *)      PLATFORM="unknown" ;;
esac

if [ "$PLATFORM" = "macos" ]; then
    APP="$DIST_DIR/LadderStock.app"
    [ -d "$APP" ] || { echo "bundle missing: $APP" >&2; exit 1; }
    OUT="$OUT_DIR/LadderStock-$VERSION-macos.dmg.tmp"
    rm -rf "$OUT"
    mkdir -p "$OUT"
    cp -R "$APP" "$OUT/"
    echo "OK (staged, dmg packaging happens on macOS runner): $OUT (version $VERSION)"
else
    APPIMAGE="$DIST_DIR/LadderStock-x86_64.AppImage"
    [ -f "$APPIMAGE" ] || { echo "bundle missing: $APPIMAGE" >&2; exit 1; }
    OUT="$OUT_DIR/LadderStock-$VERSION-linux-x86_64.AppImage"
    cp "$APPIMAGE" "$OUT"
    chmod +x "$OUT"
    echo "OK: $OUT (version $VERSION)"
fi
