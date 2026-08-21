#!/usr/bin/env bash
# session_register.sh — auto-register this Claude Code session on the heartbeat board.
#
# Provenance note, 2026-08-18: originally written against a different repo
# (daniel0tgc/internal-company-tool). The atlas-os/bin/heartbeat.py this script
# registers through does not exist in this repo (emanate-tecum-workflow) — per its
# own "no heartbeat.py" contract below, it logs the skip and exits 0 rather than
# failing.
#
# Fired by the SessionStart and UserPromptSubmit hooks (wiring is STAGED in
# WIRING.md, never applied by an agent: .claude/settings.json is deny-listed,
# and that is correct). Removes the need to ever prompt a session to join the
# board: hooks are physics, not protocol.
#
# Contract — observability must never break the observed system:
#   * exit 0 on EVERY path: not a git repo, heartbeat.py absent, python3
#     absent, board locked, malformed stdin, no session id, cloud VM.
#   * fast when the row already exists: read-only awk over the board file
#     BEFORE any python is spawned. Target <300ms.
#   * stdin may be a real hook payload, empty, or garbage.
#   * failures append to cloud-sessions/state/hook-errors.log, capped at 200
#     lines by truncating the oldest.
#
# Registration uses the documented presence sentinel: register REQUIRES
# --owns, so a presence-only row claims "._presence/<session-id>" — a path
# that does not exist and is disjoint per session, so it can never collide
# with a real claim. Presence makes a session VISIBLE on the board; it does
# NOT make its dirty files MOVABLE — exodus partitions by real owns-claims,
# which this hook deliberately never writes and never overwrites.
#
# bash 3.2 compatible (macOS): no mapfile, no declare -A, no ${var^^}.
# Never `set -e` / `set -u`: any failure below must fall through to exit 0.

REFRESH_MINUTES=10   # refresh last_update at most this often (lease is 30m)
HB_TIMEOUT_TICKS=60  # watchdog on heartbeat.py: 60 * 0.1s = 6s

LOG=""
TAB="$(printf '\t')"

log_fail() {
  # Append one line; cap the file at 200 lines. Every step may fail silently.
  [ -n "$LOG" ] || return 0
  mkdir -p "$(dirname "$LOG")" 2>/dev/null || return 0
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" "$*" >> "$LOG" 2>/dev/null || return 0
  n=$(wc -l < "$LOG" 2>/dev/null | tr -d '[:space:]')
  case "$n" in '' | *[!0-9]*) return 0 ;; esac
  if [ "$n" -gt 200 ]; then
    tail -n 200 "$LOG" > "$LOG.tmp.$$" 2>/dev/null && mv -f "$LOG.tmp.$$" "$LOG" 2>/dev/null
    rm -f "$LOG.tmp.$$" 2>/dev/null
  fi
  return 0
}

abs_git_path() {
  # $1 = directory, $2 = rev-parse flag. Prints an absolute path or fails.
  p=$(git -C "$1" rev-parse --path-format=absolute "$2" 2>/dev/null)
  if [ -z "$p" ]; then
    # git < 2.31: no --path-format. Relative output is relative to $1.
    p=$(git -C "$1" rev-parse "$2" 2>/dev/null) || return 1
    [ -n "$p" ] || return 1
    case "$p" in
      /*) : ;;
      *) p="$(cd "$1" 2>/dev/null && pwd)/$p" ;;
    esac
  fi
  printf '%s\n' "$p"
}

canon_dir() {
  # Resolve symlinks so /tmp vs /private/tmp never breaks an equality check.
  (cd "$1" 2>/dev/null && pwd -P)
}

run_hb() {
  # Run heartbeat.py under a watchdog. BoardLock blocks indefinitely on a held
  # flock, and a hook that blocks holds the user's prompt hostage — so a
  # wedged or locked board is abandoned after 6s, logged, and we still exit 0.
  hb_verb=$1
  tmp="${TMPDIR:-/tmp}/session_register.$$.$RANDOM.out"
  python3 "$HB" "$@" > "$tmp" 2>&1 &
  hb_pid=$!
  ticks=0
  while kill -0 "$hb_pid" 2>/dev/null; do
    if [ "$ticks" -ge "$HB_TIMEOUT_TICKS" ]; then
      kill "$hb_pid" 2>/dev/null
      wait "$hb_pid" 2>/dev/null
      log_fail "heartbeat.py $hb_verb timed out after 6s (board locked?) session=$SID"
      rm -f "$tmp" 2>/dev/null
      return 0
    fi
    sleep 0.1
    ticks=$((ticks + 1))
  done
  wait "$hb_pid"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    out=$(tr '\n' ' ' < "$tmp" 2>/dev/null)
    case "$out" in
      *"already live"*) : ;; # concurrent hook won the register race; the row exists, which is the goal
      *) log_fail "heartbeat.py $hb_verb rc=$rc session=$SID: $out" ;;
    esac
  fi
  rm -f "$tmp" 2>/dev/null
  return 0
}

main() {
  # ---- stdin: payload, empty, or garbage — never hang on a TTY ----
  PAYLOAD=""
  if [ ! -t 0 ]; then
    PAYLOAD=$(head -c 8192 2>/dev/null)
  fi

  # ---- session id: env first, payload session_id as fallback ----
  SID="${CLAUDE_CODE_SESSION_ID:-}"
  if [ -z "$SID" ] && [ -n "$PAYLOAD" ]; then
    SID=$(printf '%s' "$PAYLOAD" \
      | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
      | head -n 1)
  fi
  [ -n "$SID" ] || return 0   # nothing to key a row on

  # ---- cloud VM: skip. Registering there is harmless but useless — the board
  # lives in the VM's clone and is never pushed, so a row written on a VM is
  # invisible to every reader that matters. The dispatch ledger is the record
  # of cloud work; the heartbeat board is for sessions on this machine. ----
  [ "$(id -u 2>/dev/null)" = "0" ] && return 0
  case "${PWD:-}" in /home/user*) return 0 ;; esac
  if env 2>/dev/null | grep -q '^CLAUDE_CODE_REMOTE'; then return 0; fi

  # ---- where are we: script location -> repo root candidate ----
  SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd) || return 0
  REPO_ROOT=${SELF_DIR%/cloud-sessions/bin/hooks}
  [ "$REPO_ROOT" != "$SELF_DIR" ] || return 0   # copied out of place; refuse to guess
  LOG="$REPO_ROOT/cloud-sessions/state/hook-errors.log"

  CWD="${PWD:-}"
  payload_cwd=$(printf '%s' "$PAYLOAD" \
    | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -n 1)
  [ -n "$payload_cwd" ] && [ -d "$payload_cwd" ] && CWD="$payload_cwd"
  [ -n "$CWD" ] || return 0

  # ---- same repo? Compare git common dirs; a linked worktree of this repo
  # shares the main checkout's common dir, so this admits worktrees too. ----
  command -v git > /dev/null 2>&1 || return 0
  cwd_common=$(abs_git_path "$CWD" --git-common-dir) || return 0    # not a git repo
  repo_common=$(abs_git_path "$REPO_ROOT" --git-common-dir) || return 0
  cwd_common=$(canon_dir "$cwd_common") || return 0
  repo_common=$(canon_dir "$repo_common") || return 0
  [ -n "$cwd_common" ] && [ -n "$repo_common" ] || return 0
  [ "$cwd_common" = "$repo_common" ] || return 0   # session belongs to another repo

  # The board lives in the MAIN checkout even when the session is in a linked
  # worktree: the common dir is <main>/.git, so its parent is the main root.
  MAIN_ROOT=$(dirname "$cwd_common")
  LOG="$MAIN_ROOT/cloud-sessions/state/hook-errors.log"
  BOARD="$MAIN_ROOT/atlas-os/heartbeat/STATE.md"
  HB="$MAIN_ROOT/atlas-os/bin/heartbeat.py"
  if [ ! -f "$BOARD" ]; then
    log_fail "no board at $BOARD; skipped session=$SID"
    return 0
  fi

  # ---- worktree label: main checkout vs linked worktree ----
  WT="main"
  git_dir=$(abs_git_path "$CWD" --git-dir)
  git_dir=$(canon_dir "$git_dir")
  if [ -n "$git_dir" ] && [ "$git_dir" != "$cwd_common" ]; then
    top=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
    WT="linked:$(basename "${top:-$git_dir}")"
  fi

  # ---- cheap read-only row check, BEFORE any python is spawned ----
  # Row shape: | session | goal | owns_paths | risk | status | last_update |
  # Scoped to the Active Sessions section so a Completed row never counts.
  row=$(awk -F'|' -v sid="$SID" '
    /^## / { insec = ($0 ~ /^## Active Sessions[ \t]*$/) }
    insec && index($0, "|") == 1 {
      s = $2; gsub(/^[ \t]+|[ \t]+$/, "", s)
      if (s == sid) {
        g = $3; gsub(/^[ \t]+|[ \t]+$/, "", g)
        t = $7; gsub(/^[ \t]+|[ \t]+$/, "", t)
        print g "\t" t
        exit
      }
    }' "$BOARD" 2>/dev/null)

  NOW=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)
  CWD_SAFE=$(printf '%s' "$CWD" | tr '|\n' '__')   # goal must not contain '|'

  if [ -z "$row" ]; then
    # Fresh session: register with the presence sentinel.
    if ! command -v python3 > /dev/null 2>&1; then
      log_fail "python3 not found; cannot register session=$SID"
      return 0
    fi
    if [ ! -f "$HB" ]; then
      log_fail "heartbeat.py absent at $HB; cannot register session=$SID"
      return 0
    fi
    run_hb register --session "$SID" \
      --goal "auto-registered: cwd=$CWD_SAFE worktree=$WT started=$NOW" \
      --owns "._presence/$SID" --risk low
    return 0
  fi

  goal=${row%%"$TAB"*}
  last=${row##*"$TAB"}

  # NEVER clobber a row a session registered deliberately with real
  # owns-claims. Only rows this hook created are this hook's to touch.
  case "$goal" in
    "auto-registered:"*) : ;;
    *) return 0 ;;
  esac

  # Refresh at most every REFRESH_MINUTES; the common case stays awk-only.
  # ISO8601 UTC compares lexically, so string > is a correct time compare.
  cutoff=$(date -u -v-"${REFRESH_MINUTES}"M '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null \
    || date -u -d "${REFRESH_MINUTES} minutes ago" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)
  if [ -n "$cutoff" ] && [ -n "$last" ] && [[ "$last" > "$cutoff" ]]; then
    return 0
  fi
  command -v python3 > /dev/null 2>&1 || return 0
  [ -f "$HB" ] || return 0
  started=$(printf '%s' "$goal" | sed -n 's/.*started=\([^ ]*\).*/\1/p')
  run_hb update --session "$SID" \
    --goal "auto-registered: cwd=$CWD_SAFE worktree=$WT started=${started:-$NOW}"
  return 0
}

main "$@"
exit 0
