#!/usr/bin/env bash
# QUOT-525: clean-machine smoke test for macOS .app / Linux AppImage.
#
# Checks (with a sanitized environment -- no PYTHONPATH/PYTHONHOME, isolated
# HOME/data/config, non-production port):
#   1. __runtime-info reports frozen=true and bundled resources present;
#   2. __desktop serves /api/health with ok=true;
#   3. "/" returns the real dashboard (#root), not the "frontend not built" hint;
#   4. process exits cleanly on SIGTERM.
#
# Usage: packaging/smoke_posix.sh <exe> [port] [work-dir]
set -euo pipefail

EXE="$(cd "$(dirname "${1:?usage: smoke_posix.sh <exe> [port]}")" && pwd)/$(basename "$1")"
PORT="${2:-5966}"
TMP="${3:-$(mktemp -d "${TMPDIR:-/tmp}/ldst-pack-smoke.XXXXXX")}"
mkdir -p "$TMP/home" "$TMP/localappdata" "$TMP/data" "$TMP/config"

run_env() {
  env -i \
    HOME="$TMP/home" \
    TMPDIR="$TMP" \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    PYTHONHOME="/nonexistent-python-home" \
    PYTHONPATH="/nonexistent-python-path" \
    APPIMAGE_EXTRACT_AND_RUN="${APPIMAGE_EXTRACT_AND_RUN:-0}" \
    LDST_DATA_DIR="$TMP/data" \
    LDST_CONFIG_DIR="$TMP/config" \
    LDST_PAGE_AUTOSTART=0 \
    LDST_NO_BROWSER=1 \
    "$@"
}

echo "exe: $EXE"
echo "temp: $TMP"
echo "python on PATH: $(command -v python3 || echo '<none>')  (frozen app must not need it)"

INFO="$TMP/runtime-info.json"
run_env "$EXE" __runtime-info --output json > "$INFO"
if ! grep -Eq '"frozen"[[:space:]]*:[[:space:]]*true' "$INFO"; then
  echo "FAIL: runtime is not frozen" >&2
  cat "$INFO" >&2
  exit 1
fi
if ! grep -Eq '"frontend_dist_ready"[[:space:]]*:[[:space:]]*true' "$INFO"; then
  echo "FAIL: bundled frontend/dist missing" >&2
  cat "$INFO" >&2
  exit 1
fi
if ! grep -Eq '"bundle_config_ready"[[:space:]]*:[[:space:]]*true' "$INFO"; then
  echo "FAIL: bundled config templates missing" >&2
  cat "$INFO" >&2
  exit 1
fi
echo "runtime-info: frozen + bundled resources OK"

LOG="$TMP/app.log"
run_env "$EXE" __desktop --port "$PORT" > "$LOG" 2>&1 &
PID=$!
cleanup() {
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

HEALTH=""
for _ in $(seq 1 180); do
  if HEALTH="$(curl -fsS "http://127.0.0.1:$PORT/api/health" 2>/dev/null)"; then
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "FAIL: app exited before health endpoint was ready" >&2
    cat "$LOG" >&2
    exit 1
  fi
  sleep 0.5
done

if ! printf '%s' "$HEALTH" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'; then
  echo "FAIL: /api/health not ok: $HEALTH" >&2
  cat "$LOG" >&2
  exit 1
fi

HTML="$TMP/index.html"
curl -fsS "http://127.0.0.1:$PORT/" -o "$HTML"
if ! grep -q 'id="root"' "$HTML"; then
  echo "FAIL: dashboard HTML missing #root (frontend not bundled?)" >&2
  exit 1
fi
if grep -q '前端尚未构建' "$HTML"; then
  echo "FAIL: served the 'frontend not built' hint page" >&2
  exit 1
fi

echo "health: $HEALTH"
echo "html: $(wc -c < "$HTML") bytes, #root present"

kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true
trap - EXIT
if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "FAIL: port still served after shutdown" >&2
  exit 1
fi

echo "OK: packaged app starts the dashboard without system Python"
rm -rf "$TMP"
