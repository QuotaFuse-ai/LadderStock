#!/usr/bin/env bash
# disk-guard.sh — QUOT-329 C: drive guardrail.
# Source of truth: LadderStock docs/ops/disk-guard.sh. Runs unattended on a schedule,
# so every deletion here is scope-bound by the QUOT-327/QUOT-329 red lines:
#   reclaim ONLY: node_modules / dist / __pycache__ / .venv inside task dirs whose
#                 issue is done|cancelled, plus npm/yarn/pip caches via their own
#                 official commands.
#   NEVER touch : in_progress / in_review / todo / blocked task dirs, anything written
#                 in the last ACTIVE_WINDOW_MIN minutes, docs/decisions/, screenshot
#                 assets, .qoder-cn config & credentials, disk-warden, C:/ProgramData.
set -uo pipefail

TRIGGER_FREE_GB=${TRIGGER_FREE_GB:-6}
TARGET_FREE_GB=${TARGET_FREE_GB:-12}
WS_ROOT="$HOME/multica_workspaces"
LOG_DIR="$HOME/logs/disk-guard"
LOG_FILE="$LOG_DIR/disk-guard.log"
ACTIVE_WINDOW_MIN=${ACTIVE_WINDOW_MIN:-60}
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

mkdir -p "$LOG_DIR"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s %s\n' "$(ts)" "$*" | tee -a "$LOG_FILE"; }

# "time | path | size | justification" — the audit line required before any delete.
audit() { printf '%s | %s | %s | %s\n' "$(ts)" "$1" "$2" "$3" | tee -a "$LOG_FILE"; }

free_gb() {
  df -Pk /c 2>/dev/null | awk 'NR==2 {printf "%.2f", $4/1024/1024}'
}

mb() { awk -v k="$1" 'BEGIN {printf "%.1f MB", k/1024}'; }

# below <limit> <value> -> exit 0 when value is under the limit
below() { awk -v a="$2" -v b="$1" 'BEGIN {exit (a < b) ? 0 : 1}'; }

# --------------------------------------------------------------------------------
# 0. trigger check
# --------------------------------------------------------------------------------
FREE_BEFORE=$(free_gb)
log "=== disk-guard start (dry_run=$DRY_RUN) free=${FREE_BEFORE} GB trigger=${TRIGGER_FREE_GB} target=${TARGET_FREE_GB} ==="

if ! below "$TRIGGER_FREE_GB" "$FREE_BEFORE"; then
  log "check: free=${FREE_BEFORE} GB >= trigger=${TRIGGER_FREE_GB} GB — no reclamation needed, exiting."
  exit 0
fi
log "check: free=${FREE_BEFORE} GB < trigger=${TRIGGER_FREE_GB} GB — reclaiming."

# --------------------------------------------------------------------------------
# 1. terminal-issue set (done / cancelled). Anything not in this set is untouchable.
# --------------------------------------------------------------------------------
TERMINAL=$(mktemp); trap 'rm -f "$TERMINAL"' EXIT
for st in done cancelled; do
  off=0
  while : ; do
    resp=$(multica issue list --status "$st" --limit 100 --offset "$off" --fields number --output json 2>/dev/null) || break
    echo "$resp" | grep -o '"number": *[0-9]*' | grep -o '[0-9]*' >> "$TERMINAL"
    [ "$(echo "$resp" | grep -c '"has_more": *true')" -gt 0 ] || break
    off=$((off + 100))
  done
done
sort -nu "$TERMINAL" -o "$TERMINAL"
log "loaded $(wc -l < "$TERMINAL") terminal issues"

is_terminal() { grep -qx "$1" "$TERMINAL"; }

# A task dir is off-limits if it was written to inside ACTIVE_WINDOW_MIN — an
# in-flight run can sit on a terminal issue (reopened, follow-up wakeup).
# Fails closed: if the probe itself errors, treat the dir as active and skip it.
recently_active() {
  local out
  out=$(find "$1" -newermt "-${ACTIVE_WINDOW_MIN} minutes" -print -quit 2>/dev/null) || return 0
  [ -n "$out" ]
}

RECLAIMED_KB=0
rm -f "$LOG_DIR/.kb" "$LOG_DIR/.taskdirs"

# --------------------------------------------------------------------------------
# 2. category 1+2: build artefacts and dependency dirs inside TERMINAL task dirs
# --------------------------------------------------------------------------------
find "$WS_ROOT" -mindepth 2 -maxdepth 2 -type d -name 'quot-*' 2>/dev/null | sort > "$LOG_DIR/.taskdirs"
n_tasks=$(wc -l < "$LOG_DIR/.taskdirs")
log "scanning $n_tasks task dirs"

while IFS= read -r taskdir; do
  case "$taskdir" in
    *ProgramData*) continue ;;                      # hard exclusion, needs human auth
  esac
  [ "$(basename "$taskdir")" = "$(basename "$PWD")" ] && continue
  num=$(basename "$taskdir" | sed -n 's|^quot-\([0-9]*\)-.*|\1|p')
  [ -z "$num" ] && continue
  is_terminal "$num" || continue
  recently_active "$taskdir" && { log "skip (active <${ACTIVE_WINDOW_MIN}m): $taskdir"; continue; }

  find "$taskdir" -type d \( -name node_modules -o -name '.venv' -o -name __pycache__ -o -name dist \) \
       -prune 2>/dev/null | sort | while IFS= read -r d; do
    case "$d" in
      *docs/decisions*|*disk-warden*|*/.qoder-cn*|*ProgramData*)
        log "skip (protected path): $d"; continue ;;
    esac
    kb=$(du -sk "$d" 2>/dev/null | awk '{print $1+0}')
    [ -z "$kb" ] && kb=0
    audit "$d" "$(mb "$kb")" "issue QUOT-$num is terminal (done/cancelled); build artefact / dependency dir"
    if [ "$DRY_RUN" -eq 0 ]; then
      rm -rf -- "$d" 2>>"$LOG_FILE" || log "DELETE FAILED: $d"
    fi
    echo "$kb" >> "$LOG_DIR/.kb"
  done
done < "$LOG_DIR/.taskdirs"

if [ -f "$LOG_DIR/.kb" ]; then
  RECLAIMED_KB=$(awk '{s+=$1} END {printf "%d", s}' "$LOG_DIR/.kb")
  rm -f "$LOG_DIR/.kb"
fi
log "artefact pass reclaimed $(mb "$RECLAIMED_KB")"

# --------------------------------------------------------------------------------
# 3. pure caches, via their own official commands (still short of target?)
#    Only cache-clean commands — never a manual rm on a cache tree.
# --------------------------------------------------------------------------------
FREE_MID=$(free_gb)
if below "$TARGET_FREE_GB" "$FREE_MID"; then
  if [ "$DRY_RUN" -eq 0 ]; then
    if command -v npm >/dev/null 2>&1; then
      before=$(du -sk "$HOME/npm-cache" 2>/dev/null | awk '{print $1+0}'); before=${before:-0}
      npm cache clean --force >>"$LOG_FILE" 2>&1 && \
        audit "$HOME/npm-cache" "$(mb "$before")" "npm cache clean --force (official command, pure cache)"
    fi
    if command -v yarn >/dev/null 2>&1; then
      before=$(du -sk "$HOME/AppData/Local/Yarn/cache" 2>/dev/null | awk '{print $1+0}'); before=${before:-0}
      yarn cache clean >>"$LOG_FILE" 2>&1 && \
        audit "yarn cache" "$(mb "$before")" "yarn cache clean (official command, pure cache)"
    fi
    if command -v pip >/dev/null 2>&1; then
      before=$(du -sk "$HOME/AppData/Local/pip/cache" 2>/dev/null | awk '{print $1+0}'); before=${before:-0}
      pip cache purge >>"$LOG_FILE" 2>&1 && \
        audit "pip http cache" "$(mb "$before")" "pip cache purge (official command, pure cache)"
    fi
  else
    log "dry-run: would run npm cache clean --force / yarn cache clean / pip cache purge"
  fi
fi

# --------------------------------------------------------------------------------
# 4. result + residual big consumers
# --------------------------------------------------------------------------------
FREE_AFTER=$(free_gb)
log "=== disk-guard done free_before=${FREE_BEFORE} GB free_after=${FREE_AFTER} GB reclaimed~$(mb "$RECLAIMED_KB") ==="

# Out-of-scope consumers, listed for the alert only — this guard never deletes them.
# Plain `du` is not usable here: Windows junctions (AppData\Local\Application Data,
# History, Temporary Internet Files, AppData\Roaming\Qoder) make du count the same
# bytes repeatedly — it reported AppData at 43 GB when the junction-free total is ~20,
# and QUOT-327's "ProgramData 80 GB" was the same artifact (junction-free: ~8 GB).
# This walker skips reparse points instead of descending through them.
residual_report() {
  powershell -NoProfile -Command '
$acc = 0L
$skipped = @()
function Walk($p) {
  $script:acc = 0L
  $st = New-Object System.Collections.Stack
  $st.Push($p)
  while ($st.Count -gt 0) {
    $cur = $st.Pop()
    $items = $null
    try { $items = [System.IO.DirectoryInfo]::new($cur).GetFileSystemInfos() }
    catch { $script:skipped += $cur; continue }
    foreach ($i in $items) {
      if ($i.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
      # PSIsContainer does not exist on the FileSystemInfo base type GetFileSystemInfos
      # returns, and a $null there silently turns every directory into a no-op.
      if ($i -is [IO.DirectoryInfo]) { $st.Push($i.FullName) } else { $script:acc += $i.Length }
    }
  }
  $script:acc
}
$targets = @(
  "C:\ProgramData",
  "C:\Users\Administrator\AppData",
  "C:\Users\Administrator\multica_workspaces",
  "C:\Users\Administrator\.qoder-cn",
  "C:\Users\Administrator\.qoder",
  "C:\Users\Administrator\.rustup",
  "C:\Users\Administrator\.cargo",
  "C:\Users\Administrator\Documents",
  "C:\Users\Administrator\Desktop",
  "C:\Users\Administrator\Downloads"
)
foreach ($t in $targets) {
  if (-not (Test-Path -LiteralPath $t)) { continue }
  $script:skipped = @()
  $s = Walk $t
  "{0,9:N2} GB  {1}  (unreadable subpaths: {2})" -f ($s/1GB), $t, $script:skipped.Count
}
' 2>>"$LOG_FILE" | sort -nr | head -5 | while IFS= read -r line; do
    log "  RESIDUAL $line -- outside QUOT-329 scope; not clearable by this guard, needs human authorisation"
  done
}

if below "$TARGET_FREE_GB" "$FREE_AFTER"; then
  log "TARGET NOT MET (free=${FREE_AFTER} < ${TARGET_FREE_GB}). Residual consumers, measured junction-safe:"
  residual_report
  log "ALERT LEVEL: if free keeps falling below 3 GB with reclamation failing, the residual paths above are the reason — escalate to a human, do not delete silently."
fi

below 3 "$FREE_AFTER" && { log "CRITICAL: free=${FREE_AFTER} GB"; exit 2; }
exit 0
