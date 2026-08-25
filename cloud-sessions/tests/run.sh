#!/usr/bin/env bash
# run.sh — regression suite for cloud-sessions/bin/cs.
#
# Provenance note, 2026-08-18: originally written against a different repo
# (daniel0tgc/internal-company-tool). This suite is self-contained and repo-agnostic
# except sections 12-15 (cs board/cs exodus), which symlink to a real
# atlas-os/bin/heartbeat.py + worktree.py — neither exists in this repo
# (emanate-tecum-workflow), so those sections' fixture setup will not find them here.
# See ../tests/README.md and ../playbooks/09-porting-to-another-repo.md.
#
# Design rules, inherited from atlas-os/bin/verify_system.sh and from this folder's
# own AGENT.md. Read them before adding a check:
#
#   - $0 AND OFFLINE BY CONSTRUCTION. No check may invoke a billable path. The real
#     `claude` CLI is unreachable: CS_CLAUDE_BIN points at fixtures/fake-claude AND
#     the same stub shadows `claude` on PATH, so both resolution routes land on the
#     stub. `gh` and `open` are shadowed for the same reason.
#   - NOTHING REAL IS WRITTEN. `cs` is copied into a temp fixture root, so the ledger
#     under test is $WORK/csroot/state/sessions.jsonl, never the repo's. $HOME is a
#     temp dir, so `cs env set` cannot reach the operator's ~/.claude/settings.json.
#     Both are checksummed before and after and asserted unchanged (section 11).
#   - REFUSALS ARE PROBED WITH IMPOSSIBLE INPUTS. A guard nobody has seen go red is
#     unproven, so most of this suite asserts a NON-zero exit and the exact reason.
#   - DETERMINISTIC. Fixture git repos are built from scratch under $WORK with
#     GIT_CONFIG_GLOBAL/SYSTEM neutralised; no test depends on the operator's config,
#     login state, or working tree.
#   - A SKIPPED CHECK IS NOT A PASSED CHECK. Skips are printed and counted.
#   - A KNOWN-OPEN DEFECT IS NOT A PASSED CHECK EITHER. Checks written against a
#     defect that is still open are `xfail`: printed, counted separately, and listed
#     in the summary. If one starts passing it is reported as a FAILURE, so a fixed
#     defect cannot sit quietly mislabelled as broken.
#
# Usage:  bash cloud-sessions/tests/run.sh [--keep] [-v]
#   --keep  leave $WORK in place for inspection
#   -v      echo each command's output as it runs
#
# Every run rewrites cloud-sessions/tests/RESULTS.md, appending as it goes so a
# crashed run still leaves the log of everything up to the crash.

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
CS_REAL="$CS_DIR/bin/cs"
RESULTS="$TESTS_DIR/RESULTS.md"
FIXTURES="$TESTS_DIR/fixtures"

KEEP=no; VERBOSE=no
for a in "$@"; do
  case "$a" in
    --keep) KEEP=yes ;;
    -v|--verbose) VERBOSE=yes ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

[[ -f "$CS_REAL" ]] || { echo "cannot find $CS_REAL" >&2; exit 2; }

# ---------------------------------------------------------- real-world baselines
# Captured BEFORE $HOME is redirected, so section 11 can prove the suite touched
# neither the operator's ledger nor their settings. A previous ad-hoc verification
# script wrote six junk rows into the real ledger; that is what this guards.
sha() { if [[ -e "$1" ]]; then shasum -a 256 "$1" | awk '{print $1}'; else echo "ABSENT"; fi; }
REAL_HOME="$HOME"
REAL_LEDGER="$CS_DIR/state/sessions.jsonl"
REAL_SETTINGS="$REAL_HOME/.claude/settings.json"
REAL_LEDGER_SHA="$(sha "$REAL_LEDGER")"
REAL_SETTINGS_SHA="$(sha "$REAL_SETTINGS")"
# The new surface (sections 12-16) reaches three more real files that must never
# move: the heartbeat board (the hook and exodus fixtures use throwaway symlinked
# roots, and these hashes prove the isolation held), the default manifest path
# (exodus must only ever write it in the sandbox), and the hook script itself.
REPO_REAL="$(cd "$CS_DIR/.." && pwd)"
REAL_BOARD="$REPO_REAL/atlas-os/heartbeat/STATE.md"
REAL_MANIFEST="$CS_DIR/state/exodus-manifest.json"
REAL_HOOK="$CS_DIR/bin/hooks/session_register.sh"
REAL_BOARD_SHA="$(sha "$REAL_BOARD")"
REAL_MANIFEST_SHA="$(sha "$REAL_MANIFEST")"
REAL_HOOK_SHA="$(sha "$REAL_HOOK")"
# bin/cs itself, by content hash rather than `git diff` against HEAD: the guard's
# claim is "this RUN modified nothing", and diff-vs-HEAD conflates that with
# "somebody's in-flight work is uncommitted", which is a normal state mid-wave.
REAL_CS_SHA="$(sha "$CS_REAL")"

# --------------------------------------------------------------------- sandbox
# `mktemp -d -t cs-tests` works on BSD/macOS but GNU coreutils rejects it with
# "too few X's in template". The cloud VM is Ubuntu, so on 2026-08-14 this failed there
# and every downstream check cascaded. The XXXXXX form is accepted by both.
WORK="$(mktemp -d -t cs-tests.XXXXXX)"
cleanup() {
  chmod -R u+rwX "$WORK" 2>/dev/null
  if [[ "$KEEP" == yes ]]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

mkdir -p "$WORK/stub" "$WORK/shellstub" "$WORK/home/.claude" "$WORK/repos" "$WORK/remotes" \
         "$WORK/csroot/bin" "$WORK/csroot/state" "$WORK/logs" "$WORK/notarepo"

cp "$FIXTURES/fake-claude" "$WORK/stub/claude"
cp "$FIXTURES/fake-gh"     "$WORK/stub/gh"
cp "$FIXTURES/fake-open"   "$WORK/stub/open"
# A second stand-in that records argv and does nothing else. Sections 7 and 8 need to know
# WHICH branch ran and need the ledger to stay empty as proof that none of them dispatched;
# fake-claude prints a `View:` line, which would record one. See fixtures/argv-recorder.
cp "$FIXTURES/argv-recorder" "$WORK/stub/argv-recorder"
cp "$FIXTURES/argv-recorder" "$WORK/shellstub/claude"
chmod +x "$WORK/stub"/* "$WORK/shellstub"/*
cp "$CS_REAL" "$WORK/csroot/bin/cs"
chmod +x "$WORK/csroot/bin/cs"

CS="$WORK/csroot/bin/cs"
LEDGER="$WORK/csroot/state/sessions.jsonl"

export HOME="$WORK/home"
export PATH="$WORK/stub:$PATH"
export CS_CLAUDE_BIN="$WORK/stub/claude"
export FAKE_LOG="$WORK/logs/argv.log"
export FAKE_ENV_LOG="$WORK/logs/env.log"
export FAKE_OPEN_LOG="$WORK/logs/open.log"
export FAKE_VIEW_ID="" FAKE_DECOY_ID="" FAKE_NO_VIEW="" FAKE_TRAILING_DECOY=""
export FAKE_AUTH_METHOD="" FAKE_AUTH_FAIL="" FAKE_VERSION="" FAKE_EXIT="" FAKE_GH_FAIL=""
export ARGV_LOG="$WORK/logs/argv-recorder.log"
# The operator's own environment can decide two of the answers under test, so neutralise it:
#   CS_DEFAULT              answers `cs new`'s chooser. If the operator has exported it, every
#                           chooser check would measure their preference instead of the code.
#   CLAUDE_CODE_SESSION_ID  is set by Claude Code in every session it spawns, and the
#                           shell-init wrapper passes straight through when it is set. Running
#                           this suite from inside Claude Code therefore made the wrapper's
#                           interception untestable — and silently, because the passthrough
#                           looks like a pass. Section 8 sets it explicitly per check instead.
#   CLAUDE_CODE_REMOTE*     marks the *invoking* process as a cloud session. G24, VERIFIED
#                           2026-08-18: cloud-sessions/bin/hooks/session_register.sh:123 no-ops
#                           whenever ANY env var starting with CLAUDE_CODE_REMOTE is present.
#                           section 15's hook_null()/hook_garbage() run the hook via
#                           `env VAR=val bash "$HOOK"`, which only ADDS variables — it never
#                           clears the parent shell's own exports. Every "Linux cloud VM" run
#                           of this suite has, by definition, been invoked from inside a real
#                           Claude Code Remote session, so CLAUDE_CODE_REMOTE=true leaked into
#                           every hook_null/hook_garbage call and pinned the hook onto its
#                           no-op branch — turning checks that expect a real write into false
#                           FAILs (fresh-register, idempotent, garbage-stdin, stale-refresh)
#                           and, worse, false silent PASSes for checks that merely expect "no
#                           change" (never-clobber), which cannot tell a correctly-guarded
#                           write-refusal from a hook that never even tried. This was NEVER a
#                           macOS-vs-Linux difference; it is cloud-session-vs-laptop, and it
#                           reproduces on any platform the suite is run from inside a `cs`
#                           dispatch. `${!CLAUDE_CODE_REMOTE@}` is bash's prefix-match name
#                           expansion, so this stays correct if more such vars are added later.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL \
      CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX \
      CS_DEFAULT CLAUDE_CODE_SESSION_ID 2>/dev/null
for _v in "${!CLAUDE_CODE_REMOTE@}"; do unset "$_v"; done
unset _v

# Fixture repos must not inherit the operator's git identity, aliases, hooks or
# init.defaultBranch — any of which could flip a gate's answer.
: > "$WORK/gitconfig"
export GIT_CONFIG_GLOBAL="$WORK/gitconfig" GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME="cs tests" GIT_AUTHOR_EMAIL="tests@example.invalid"
export GIT_COMMITTER_NAME="cs tests" GIT_COMMITTER_EMAIL="tests@example.invalid"
export GIT_TERMINAL_PROMPT=0

# ------------------------------------------------------------------- reporting
PASS=0; FAIL=0; SKIP=0; XFAIL=0
FAILED_NAMES=(); OPEN_DEFECTS=()
# G24: when a whole section collapses to one loud "/ALL" skip because its fixture
# gate failed (EXODUS_OK, REAL_HOOK), COLLAPSED tracks how many individual checks
# that one skip line stands in for, so the final summary can say so out loud
# instead of a bare "N skipped" reading as N missing checks when it is really
# N missing skip *lines* hiding many times that many missing *checks*. See
# section 18 for the self-consistency check that keeps the declared numbers honest.
COLLAPSED=0
if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[34m'; D=$'\033[2m'; O=$'\033[0m'
else G=""; R=""; Y=""; B=""; D=""; O=""; fi

LASTCMD=""; OUT=""; RC=0

log() { printf '%s\n' "$*" >> "$RESULTS"; }
log_block() {  # indent whatever is in $OUT under a fenced block
  log '  - command: `'"$LASTCMD"'`'
  log "  - exit code: $RC"
  log '  - actual output:'
  log '    ```'
  if [[ -z "$OUT" ]]; then log '    (no output)'; else printf '%s\n' "$OUT" | sed 's/^/    /' >> "$RESULTS"; fi
  log '    ```'
}

_pass()  { PASS=$((PASS+1));   printf '%s  ok  %s%s\n' "$G" "$O" "$1"; log "- **PASS** \`$1\` — $2"; }
# A multi-line needle would break both the console layout and the markdown bullet.
flat()   { printf '%s' "$1" | tr '\n' ' '; }
_fail()  { FAIL=$((FAIL+1)); FAILED_NAMES+=("$1"); local w; w="$(flat "$3")"
           printf '%sFAIL  %s%s\n      %s%s%s\n' "$R" "$O" "$1" "$D" "$w" "$O"
           log "- **FAIL** \`$1\` — $2"; log "  - why: $w"; log_block; }
_xfail() { XFAIL=$((XFAIL+1)); OPEN_DEFECTS+=("$1"); local w; w="$(flat "$3")"
           printf '%sxfail %s%s  %s(known-open defect)%s\n' "$B" "$O" "$1" "$D" "$O"
           log "- **XFAIL (known-open defect in cs)** \`$1\` — $2"; log "  - why: $w"; log_block; }
_xpass() { FAIL=$((FAIL+1)); FAILED_NAMES+=("$1(xpass)")
           printf '%sXPASS %s%s  %sdefect appears FIXED — promote to a normal check%s\n' "$R" "$O" "$1" "$D" "$O"
           log "- **XPASS** \`$1\` — $2"; log "  - the defect this documented appears FIXED. Change \`xhas\` to \`has\` so it is enforced."; }
_skip()  { SKIP=$((SKIP+1));   printf '%sskip  %s%s  %s(%s)%s\n' "$Y" "$O" "$1" "$D" "$2" "$O"
           log "- **SKIP** \`$1\` — $2"; }
sec()    { printf '\n%s%s%s\n' "$D" "$1" "$O"; log ""; log "## $1"; log ""; }

# run a command, capture combined output and exit code
run() {
  LASTCMD="$*"
  OUT="$("$@" 2>&1)"; RC=$?
  OUT="$(printf '%s' "$OUT" | tr -d '\r\004')"
  [[ "$VERBOSE" == yes ]] && { printf '%s$ %s\n%s%s\n' "$D" "$LASTCMD" "$OUT" "$O"; }
  return 0
}
# run a command under a pty. `cs start` refuses without a TTY, and `cs` itself wraps
# the CLI in `script`, so this exercises the nested-script path exactly as in real use.
#
# PORTABILITY: the two `script` implementations take incompatible arguments.
#   BSD/macOS      script [-q] file command args...
#   GNU util-linux script [-q] -c "command args" file      <- rejects a trailing command
#                                                             with "unexpected number of
#                                                             arguments"
# Probed once at startup rather than assumed. Getting this wrong is silent: GNU `script`
# errors, $OUT comes back empty, and every trun-based assertion fails for a reason that
# has nothing to do with the thing under test. Measured on a cloud VM 2026-08-14, where
# it accounted for 58 of 58 remaining failures.
if script -q -c true /dev/null >/dev/null 2>&1; then SCRIPT_FLAVOUR=gnu; else SCRIPT_FLAVOUR=bsd; fi
trun() {
  LASTCMD="script(tty) $*"
  if [[ "$SCRIPT_FLAVOUR" == gnu ]]; then
    # -e/--return is REQUIRED: without it GNU `script` exits 0 no matter what the child
    # did, so every rc_nonz refusal check silently passed-as-zero. BSD `script` has no -e
    # and propagates the child's status already. Measured on a cloud VM 2026-08-14, where
    # this accounted for 12 of 14 remaining failures.
    local _cmd; _cmd="$(printf '%q ' "$@")"
    OUT="$(script -q -e -c "$_cmd" /dev/null </dev/null 2>&1)"; RC=$?
  else
    OUT="$(script -q /dev/null "$@" </dev/null 2>&1)"; RC=$?
  fi
  OUT="$(printf '%s' "$OUT" | tr -d '\r\004' | sed -E 's/'$'\033''\[[0-9;?]*[a-zA-Z]//g')"
  [[ "$VERBOSE" == yes ]] && { printf '%s$ %s\n%s%s\n' "$D" "$LASTCMD" "$OUT" "$O"; }
  return 0
}
readf() { LASTCMD="cat $1"; RC=0; if [[ -e "$1" ]]; then OUT="$(cat "$1")"; else OUT=""; RC=1; fi; }

# assertions — all read $OUT / $RC set by the last run/trun/readf
has()    { case "$OUT" in *"$3"*) _pass "$1" "$2" ;; *) _fail "$1" "$2" "expected output to contain: $3" ;; esac; }
hasnt()  { case "$OUT" in *"$3"*) _fail "$1" "$2" "output must NOT contain: $3" ;; *) _pass "$1" "$2" ;; esac; }
xhas()   { case "$OUT" in *"$3"*) _xpass "$1" "$2" ;; *) _xfail "$1" "$2" "correct behaviour would contain: $3" ;; esac; }
xhasnt() { case "$OUT" in *"$3"*) _xfail "$1" "$2" "correct behaviour would NOT contain: $3" ;; *) _xpass "$1" "$2" ;; esac; }
rc_zero(){ local d="${2:-the command exits 0}"
           if [[ "$RC" -eq 0 ]]; then _pass "$1" "$d"; else _fail "$1" "$d" "expected exit 0, got $RC"; fi; }
rc_nonz(){ local d="${2:-the command refuses with a non-zero exit}"
           if [[ "$RC" -ne 0 ]]; then _pass "$1" "$d"; else _fail "$1" "$d" "expected a NON-zero exit (a refusal), got 0"; fi; }
eq()     { if [[ "$3" == "$4" ]]; then _pass "$1" "$2"; else OUT="$3"; _fail "$1" "$2" "expected [$4], got [$3]"; fi; }

# ledger helpers
ledger_reset() { : > "$LEDGER"; }
ledger_field() { python3 -c 'import json,sys
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print(rows[int(sys.argv[3])].get(sys.argv[2],"<missing>"))' "$LEDGER" "$1" "${2:--1}" 2>/dev/null; }
logs_reset()   { : > "$FAKE_LOG"; : > "$FAKE_ENV_LOG"; : > "$FAKE_OPEN_LOG"; }
# Build the exact <arg>…</arg> sequence the stub logs for a given argv. Asserting on
# this rather than on a loose substring is what distinguishes "one argument
# containing a space" from "two arguments" — the difference that quoting bugs live in.
argseq() { local a s=""; for a in "$@"; do s="$s<arg>$a</arg>"$'\n'; done; printf '%s' "$s"; }
# the argv log as one line, so a whole invocation can be compared with `eq` rather than
# with a substring — the difference between "the here branch ran" and "some branch ran
# that happens to mention the task".
argflat() { tr -d '\n' < "${1:-$ARGV_LOG}"; }

# Run a command on a real pty and answer ONE prompt. `trun` cannot do this: it feeds the
# child /dev/null, which is exactly the abandoned-prompt case. See fixtures/pty-drive.py
# for why piping into `script` is not an alternative.
#   ptyrun <expect> <reply> <timeout> -- <cmd>…
ptyrun() {
  LASTCMD="pty-drive $*"
  OUT="$(python3 "$FIXTURES/pty-drive.py" "$@" 2>&1)"; RC=$?
  OUT="$(printf '%s' "$OUT" | tr -d '\r\004' | sed -E 's/'$'\033''\[[0-9;?]*[a-zA-Z]//g')"
  [[ "$VERBOSE" == yes ]] && { printf '%s$ %s\n%s%s\n' "$D" "$LASTCMD" "$OUT" "$O"; }
  return 0
}

# "nothing changed on disk" needs a definition. This one: every file under the given
# directories, by path and content hash. Used by section 10 to hold a read-only command to
# its word.
manifest() {
  local d f
  for d in "$@"; do
    [[ -d "$d" ]] || continue
    find "$d" -type f 2>/dev/null | LC_ALL=C sort | while IFS= read -r f; do
      printf '%s  %s\n' "${f#$WORK/}" "$(sha "$f")"
    done
  done
}

# ------------------------------------------------------------- fixture builders
new_repo() { # name -> path; one commit whose MESSAGE names a decoy session id
  local d="$WORK/repos/$1"
  mkdir -p "$d"; git init -q -b main "$d" >/dev/null
  git -C "$d" commit -q --allow-empty -m "base work (follow-up to session_01CommitMsgDecoy)"
  printf '%s\n' "$d"
}
attach_origin() { # dir [url] — bare origin, pushed, upstream set; url may fake GitHub
  local d="$1" n; n="$(basename "$d")"
  git init -q --bare "$WORK/remotes/$n.git" >/dev/null
  git -C "$d" remote add origin "$WORK/remotes/$n.git"
  git -C "$d" push -q -u origin main
  [[ -n "${2:-}" ]] && git -C "$d" remote set-url origin "$2"
  return 0
}

# ------------------------------------------------------------------ results log
{
  printf '# cs regression suite — results\n\n'
  printf 'Generated by `cloud-sessions/tests/run.sh` on %s.\n\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
  printf 'Host bash `%s`, git `%s`, python3 `%s`.\n\n' \
    "$(bash --version | head -1 | sed -E 's/.*version ([^ ]+).*/\1/')" \
    "$(git --version | awk '{print $3}')" "$(python3 -V 2>&1 | awk '{print $2}')"
  printf 'The `claude` CLI was **not** invoked: `CS_CLAUDE_BIN` and PATH both resolve to\n'
  printf '`fixtures/fake-claude`. No cloud session was dispatched and no rate limit consumed.\n'
} > "$RESULTS"

printf '%scs regression suite%s   %s\n' "$D" "$O" "$(date '+%Y-%m-%d %H:%M:%S')"

# =============================================================================
sec "1 — harness self-guards (a suite that tests the wrong file passes forever)"
# =============================================================================

if cmp -s "$CS_REAL" "$CS"; then _pass "harness/cs-copy-is-identical" "the cs under test is a byte-identical copy of bin/cs"
else OUT="$(diff "$CS_REAL" "$CS" | head -20)"; LASTCMD="cmp $CS_REAL $CS"; RC=1
     _fail "harness/cs-copy-is-identical" "the cs under test is a byte-identical copy of bin/cs" "the copy differs from the real script"; fi

REPO_MAIN="$(new_repo main-clean)"; attach_origin "$REPO_MAIN" "https://github.com/example/main-clean.git"
cd "$REPO_MAIN"

run "$CS" doctor
has "harness/resolves-the-stub" "cs resolves the fake claude, not a real CLI" "$WORK/stub/claude"
hasnt "harness/no-real-claude-path" "no real claude binary path appears in doctor output" "/.local/bin/claude"

LASTCMD="grep LEDGER path"; RC=0; OUT="$LEDGER"
case "$LEDGER" in "$WORK"/*) _pass "harness/ledger-is-sandboxed" "the ledger under test lives in the temp work dir" ;;
                  *) _fail "harness/ledger-is-sandboxed" "the ledger under test lives in the temp work dir" "ledger resolved outside the sandbox" ;; esac
LASTCMD="printenv HOME"; OUT="$HOME"
case "$HOME" in "$WORK"/*) _pass "harness/home-is-sandboxed" "\$HOME is redirected into the temp work dir" ;;
                *) _fail "harness/home-is-sandboxed" "\$HOME is redirected into the temp work dir" "HOME is still the real home" ;; esac

# The quietest harness failure: a stub that is never called still lets every
# dispatch test "pass" by refusing early. Prove the stub actually ran.
logs_reset
trun "$CS" start "harness probe"
readf "$FAKE_LOG"
has "harness/stub-actually-invoked" "the fake claude is genuinely executed by cs start" "$(argseq --cloud 'harness probe')"

# =============================================================================
sec "2 — the ten reviewed defects (each must stay fixed)"
# =============================================================================

# --- D1: session ID must come from the View: line only -----------------------
ledger_reset; logs_reset
export FAKE_VIEW_ID="session_01ViewLineWins" FAKE_DECOY_ID="session_01DecoyMustLose"
trun "$CS" start "continue the work started in session_01DecoyMustLose"
eq "D1a/id-not-from-task-echo" "cs start records the View: id, not one named in the task title" \
   "$(ledger_field id)" "session_01ViewLineWins"

ledger_reset; logs_reset; export FAKE_TRAILING_DECOY=1
trun "$CS" start "a task"
eq "D1b/id-not-from-trailing-line" "a session id printed AFTER the View: line does not win" \
   "$(ledger_field id)" "session_01ViewLineWins"
export FAKE_TRAILING_DECOY=""

# The realistic vector: cs handoff embeds `git log -5`, and this repo's commit
# messages name session ids. The fixture's base commit does too.
ledger_reset; logs_reset
trun "$CS" handoff "finish the migration"
eq "D1c/id-not-from-embedded-git-log" "handoff's embedded git log cannot supply the recorded id" \
   "$(ledger_field id)" "session_01ViewLineWins"
readf "$FAKE_LOG"
has "D1c/git-log-really-was-in-the-brief" "the handoff brief really did carry the decoy id (else D1c is vacuous)" \
    "session_01CommitMsgDecoy"

ledger_reset; logs_reset; export FAKE_NO_VIEW=1
trun "$CS" start "a task mentioning session_01DecoyMustLose"
has "D1d/no-view-line-warns" "with no View: line, cs warns instead of guessing an id" "no session ID was found"
eq "D1d/no-view-line-records-nothing" "with no View: line, nothing is written to the ledger" \
   "$( [[ -s "$LEDGER" ]] && echo nonempty || echo empty )" "empty"
export FAKE_NO_VIEW=""

# --- D2: doctor must not say ready. when dispatch would refuse ---------------
REPO_DIRTY="$(new_repo doctor-dirty)"; attach_origin "$REPO_DIRTY" "https://github.com/example/d.git"
touch "$REPO_DIRTY/uncommitted.txt"
cd "$REPO_DIRTY"
trun "$CS" doctor
has   "D2a/doctor-blocked-verdict" "doctor reports 'not dispatchable from here' on a dirty tree" "not dispatchable from here"
hasnt "D2b/doctor-not-green-when-blocked" "doctor never prints 'ready.' while a gate would refuse" "ready.  Dispatch"
rc_nonz "D2c/doctor-exits-nonzero-when-blocked" "doctor exits non-zero when a gate would refuse"

cd "$REPO_MAIN"
run env FAKE_AUTH_METHOD=apiKey "$CS" doctor
has     "D2d/doctor-fails-on-api-key-auth" "API-key auth is a failure, not a warning" "API-key auth cannot use"
rc_nonz "D2e/doctor-nonzero-on-broken-auth" "doctor exits non-zero when the account/install is broken"

run env ANTHROPIC_API_KEY=sk-not-a-real-key "$CS" doctor
has     "D2f/doctor-flags-anthropic-env" "an ANTHROPIC_* override is reported as a failure" "ANTHROPIC_API_KEY is set"
rc_nonz "D2g/doctor-nonzero-on-env-override" "doctor exits non-zero when an env var disables cloud sessions"

# Positive control: without one, every check above would pass on a doctor that
# always says "not dispatchable".
trun "$CS" doctor
has     "D2h/doctor-green-when-everything-passes" "doctor still reaches 'ready.' when nothing is wrong" "ready."
rc_zero "D2i/doctor-zero-when-ready"

# --- D3: cs env set must never truncate settings.json ------------------------
printf '{"model":"opus","effortLevel":"high","remote":{"defaultEnvironmentId":"env_ORIGINAL"}}\n' > "$HOME/.claude/settings.json"
BEFORE_SHA="$(sha "$HOME/.claude/settings.json")"
# The injection must make the directory GENUINELY unwritable. chmod alone does not do that
# for root - root ignores mode bits, and every cloud VM runs as root - so on the one host
# that matters most, D3a/D3b used to skip rather than measure anything (G11). A read-only
# bind mount is enforced by the VFS layer, not by the permission check root is exempt from,
# so it defeats root the same way it defeats anyone else. Only actually fall back to chmod,
# and only actually skip, when the bind-mount route itself is unavailable (no CAP_SYS_ADMIN).
D3_MOUNTED=0
if [[ "$(id -u)" -eq 0 ]] \
   && mount --bind "$HOME/.claude" "$HOME/.claude" >/dev/null 2>&1; then
  if mount -o remount,ro,bind "$HOME/.claude" >/dev/null 2>&1; then
    D3_MOUNTED=1
  else
    umount "$HOME/.claude" >/dev/null 2>&1 || true
  fi
fi

if [[ "$(id -u)" -ne 0 ]]; then
  chmod 500 "$HOME/.claude"   # mkstemp cannot create; a naive open(p,"w") still could truncate
fi

if [[ "$(id -u)" -ne 0 || "$D3_MOUNTED" -eq 1 ]]; then
  # Positive control: prove the injection itself refused a plain write, so a PASS below
  # means the failure path was really exercised and not merely that nothing errored.
  CANARY_RC=0
  ( : > "$HOME/.claude/D3-canary-$$" ) >/dev/null 2>&1 || CANARY_RC=$?
  run "$CS" env set env_NEWVALUE

  if [[ "$D3_MOUNTED" -eq 1 ]]; then
    mount -o remount,rw,bind "$HOME/.claude" >/dev/null 2>&1
    umount "$HOME/.claude" >/dev/null 2>&1 || true
  else
    chmod 700 "$HOME/.claude"
  fi

  eq "D3z/env-set-injection-genuinely-fired" \
     "the positive control: the injection refused a plain write on its own, so D3a/D3b below measured a real failure, not a no-op ($([[ "$D3_MOUNTED" -eq 1 ]] && echo 'read-only bind mount' || echo 'chmod 500'))" \
     "$( [[ "$CANARY_RC" -ne 0 ]] && echo blocked || echo wrote )" "blocked"
  eq "D3a/env-set-write-failure-preserves-file" \
     "a failed 'env set' leaves settings.json byte-identical (atomic temp-file + replace)" \
     "$(sha "$HOME/.claude/settings.json")" "$BEFORE_SHA"
  rc_nonz "D3b/env-set-reports-write-failure" "a failed 'env set' exits non-zero rather than claiming success"
else
  _skip "D3a/env-set-write-failure-preserves-file" "root and no CAP_SYS_ADMIN on this host - neither chmod (root ignores mode bits) nor a read-only bind mount (mount refused) can make the directory unwritable; injection impossible"
  _skip "D3b/env-set-reports-write-failure" "root and no CAP_SYS_ADMIN on this host - neither chmod (root ignores mode bits) nor a read-only bind mount (mount refused) can make the directory unwritable; injection impossible"
fi

printf 'this is not json\n' > "$HOME/.claude/settings.json"
BEFORE_SHA="$(sha "$HOME/.claude/settings.json")"
run "$CS" env set env_X
has "D3c/env-set-refuses-unparseable-settings" "an unparseable settings.json is refused, with the path named" "cannot read"
eq "D3d/env-set-leaves-unparseable-file-alone" "refusing to read settings.json does not overwrite it" \
   "$(sha "$HOME/.claude/settings.json")" "$BEFORE_SHA"
run "$CS" env clear
has "D3e/env-clear-refuses-unparseable-settings" "env clear refuses an unparseable settings.json too" "cannot read"

# --- D4: env set/clear must not report success while doing nothing -----------
rm -f "$HOME/.claude/settings.json"
run "$CS" env set env_USERLAYER
has "D4a/env-set-from-nothing" "env set works when no settings file exists yet" "remote.defaultEnvironmentId = env_USERLAYER"
LASTCMD="python3 read settings"; RC=0; OUT="$(cat "$HOME/.claude/settings.json")"
eq "D4b/env-set-writes-nested-form" "env set writes the nested remote.defaultEnvironmentId form" \
   "$(python3 -c 'import json;print(json.load(open("'"$HOME"'/.claude/settings.json"))["remote"]["defaultEnvironmentId"])' 2>&1)" \
   "env_USERLAYER"

printf '{"remote.defaultEnvironmentId":"env_FLATFORM","model":"opus"}\n' > "$HOME/.claude/settings.json"
run "$CS" env set env_AFTERFLAT
eq "D4c/env-set-drops-the-flat-key" "env set removes the flat key form so set and clear cannot disagree" \
   "$(python3 -c 'import json;print(json.load(open("'"$HOME"'/.claude/settings.json")).get("remote.defaultEnvironmentId","<absent>"))' 2>&1)" \
   "<absent>"

printf '{"remote.defaultEnvironmentId":"env_FLATFORM","model":"opus"}\n' > "$HOME/.claude/settings.json"
run "$CS" env clear
has "D4d/env-clear-reports-cleared" "env clear reports success once nothing is pinned" "cleared"
eq "D4e/env-clear-removes-flat-key" "env clear removes the FLAT key form, not only the nested one" \
   "$(python3 -c 'import json;print(json.load(open("'"$HOME"'/.claude/settings.json")).get("remote.defaultEnvironmentId","<absent>"))' 2>&1)" \
   "<absent>"
eq "D4f/env-clear-preserves-other-settings" "env clear does not discard unrelated settings" \
   "$(python3 -c 'import json;print(json.load(open("'"$HOME"'/.claude/settings.json")).get("model"))' 2>&1)" "opus"

# Higher-precedence project layer: the write is a no-op for dispatch and must say so.
mkdir -p "$REPO_MAIN/.claude"
printf '{"remote":{"defaultEnvironmentId":"env_PROJECTLAYER"}}\n' > "$REPO_MAIN/.claude/settings.json"
run "$CS" env set env_IGNORED
has "D4g/env-set-warns-when-outranked" "env set warns that a project layer outranks the user layer" "NOT IN EFFECT"
run "$CS" env clear
has "D4h/env-clear-warns-when-still-pinned" "env clear warns that a higher layer still pins an environment" "still pinned"
run "$CS" env show
has "D4i/env-show-names-the-winning-layer" "env show attributes the pin to the layer that actually wins" "env_PROJECTLAYER"
has "D4j/env-show-names-layer-name" "env show names which settings layer supplied it" "project"
rm -rf "$REPO_MAIN/.claude"

# --- D5: an upstream on a fork is not "pushed" -------------------------------
REPO_FORK="$(new_repo fork-upstream)"
attach_origin "$REPO_FORK"
git init -q --bare "$WORK/remotes/fork-upstream-fork.git" >/dev/null
git -C "$REPO_FORK" remote add fork "$WORK/remotes/fork-upstream-fork.git"
git -C "$REPO_FORK" commit -q --allow-empty -m "only pushed to the fork"
git -C "$REPO_FORK" push -q fork main
git -C "$REPO_FORK" branch --set-upstream-to=fork/main main >/dev/null 2>&1
git -C "$REPO_FORK" remote set-url origin "https://github.com/example/upstream.git"
cd "$REPO_FORK"
trun "$CS" start "dispatch me"
has     "D5a/fork-upstream-refused" "a branch tracking a fork is refused even though it is fully pushed" "tracks 'fork', not origin"
rc_nonz "D5b/fork-upstream-exit-nonzero"
readf "$FAKE_LOG"; logs_reset
cd "$REPO_FORK"; logs_reset; trun "$CS" start "dispatch me"; readf "$FAKE_LOG"
eq "D5c/fork-upstream-dispatched-nothing" "the refusal happens before the CLI is invoked" \
   "$( [[ -s "$FAKE_LOG" ]] && echo invoked || echo not-invoked )" "not-invoked"

# --- D6: one malformed ledger byte must not break `last` ---------------------
cd "$REPO_MAIN"
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01GoodRow","title":"t","repo":"-","branch":"b","mode":"clone","url":"u"}' > "$LEDGER"
printf '%s' '{"ts":"2026-08-13T00:01:00-07:00","id":"session_01Trunca' >> "$LEDGER"
run "$CS" ls
has "D6a/ls-reports-damage" "cs ls reports damaged lines rather than dying on them" "unreadable line(s) skipped"
has "D6b/ls-still-lists-good-rows" "cs ls still prints the readable rows" "session_01GoodRow"
logs_reset
run "$CS" open last
readf "$FAKE_OPEN_LOG"
has "D6c/last-survives-a-truncated-line" "\`last\` resolves through a truncated final ledger line" \
    "https://claude.ai/code/session_01GoodRow"

printf 'not json at all\n' > "$LEDGER"
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01AfterGarbage","title":"t","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"
logs_reset
run "$CS" open last
readf "$FAKE_OPEN_LOG"
has "D6d/last-survives-leading-garbage" "\`last\` skips an unparseable line earlier in the file" \
    "https://claude.ai/code/session_01AfterGarbage"

# --- D7: a flag with no value must refuse, not exit silently -----------------
run "$CS" start --env
has     "D7a/start-env-needs-a-value" "cs start --env with no value refuses with a reason" "--env needs an environment id"
rc_nonz "D7b/start-env-no-value-nonzero"
run "$CS" handoff --plan
has     "D7c/handoff-plan-needs-a-value" "cs handoff --plan with no value refuses with a reason" "--plan needs a file path"
rc_nonz "D7d/handoff-plan-no-value-nonzero"

# --- D8: --plan must be rewritten repo-root-relative -------------------------
REPO_PLAN="$(new_repo plan-relative)"
mkdir -p "$REPO_PLAN/docs" "$REPO_PLAN/src"
printf '# the plan\n' > "$REPO_PLAN/docs/plan.md"
git -C "$REPO_PLAN" add docs/plan.md
git -C "$REPO_PLAN" commit -q -m "add plan"
attach_origin "$REPO_PLAN" "https://github.com/example/plan.git"
cd "$REPO_PLAN/src"
trun "$CS" handoff --plan ../docs/plan.md "carry on"
has   "D8a/plan-path-is-repo-root-relative" "a ../ plan path is rewritten to a repo-root-relative one" 'committed at `docs/plan.md`'
hasnt "D8b/plan-path-not-left-relative-to-cwd" "the brief never hands the session a path relative to the operator's cwd" '`../docs/plan.md`'

printf 'draft\n' > "$REPO_PLAN/docs/uncommitted-plan.md"
cd "$REPO_PLAN"
run "$CS" handoff --plan docs/uncommitted-plan.md "carry on"
has     "D8c/uncommitted-plan-refused" "an uncommitted plan file is refused — the VM would not see it" "is not committed"
rc_nonz "D8d/uncommitted-plan-nonzero"
rm -f "$REPO_PLAN/docs/uncommitted-plan.md"
run "$CS" handoff --plan docs/nope.md "carry on"
has     "D8e/missing-plan-refused" "a nonexistent plan file is refused" "plan file not found"

# --- D9: handoff rows must carry a short title, not the whole brief ----------
cd "$REPO_MAIN"; ledger_reset; logs_reset
trun "$CS" handoff "rotate the credentials"
eq "D9a/handoff-title-is-short" "the ledger title for a handoff is 'handoff: <instruction>', not the 1.5KB brief" \
   "$(ledger_field title)" "handoff: rotate the credentials"
run "$CS" ls
has   "D9b/ls-shows-the-handoff-instruction" "cs ls shows what the handoff was about" "handoff: rotate the credentials"
hasnt "D9c/ls-row-is-not-the-brief" "cs ls does not print the brief's boilerplate as the row title" "You are continuing work"

# --- D10: resolve_id must reject malformed input, not mangle it --------------
ledger_reset; logs_reset
for bad in "notasession" "session_" "session_01!!bad" "https://claude.ai/code/" "sess_01abc" "session 01abc" "-x"; do
  run "$CS" track "$bad" "probe"
  has     "D10/rejects[$bad]" "malformed session id '$bad' is rejected, not silently accepted" "not a session id"
done
LASTCMD="check ledger after malformed ids"; RC=0; OUT="$(cat "$LEDGER" 2>/dev/null)"
eq "D10a/nothing-recorded-for-malformed-ids" "no malformed id reached the ledger" \
   "$( [[ -s "$LEDGER" ]] && echo nonempty || echo empty )" "empty"

logs_reset
for good in "session_01AbcXyz" "cse_01AbcXyz" "https://claude.ai/code/session_01AbcXyz" \
            "https://claude.ai/code/session_01AbcXyz?from=cli&m=0" "claude.ai/code/session_01AbcXyz/" \
            "http://claude.ai/code/session_01AbcXyz#frag"; do
  case "$good" in cse_*) want="cse_01AbcXyz" ;; *) want="session_01AbcXyz" ;; esac
  run "$CS" track "$good" "probe"
  has "D10/normalises[$good]" "a valid id or claude.ai/code URL is normalised to the bare id" "recorded $want"
done
eq "D10b/normalised-id-stored-bare" "the ledger stores the bare id, not the URL" \
   "$(ledger_field id)" "session_01AbcXyz"

# =============================================================================
sec "3 — every command in bin/README.md, refusal paths first"
# =============================================================================

cd "$REPO_MAIN"; ledger_reset; logs_reset

# --- help / dispatcher -------------------------------------------------------
run "$CS" help;    has "help/lists-commands" "cs help lists the commands" "cs doctor"; rc_zero "help/exit-zero"
run "$CS" ;        has "noargs/prints-usage" "no arguments prints usage" "deliberate driver"
run "$CS" --help;  rc_zero "help/--help-exit-zero" "--help exits 0"
run "$CS" -h;      rc_zero "help/-h-exit-zero" "-h exits 0"
run "$CS" nosuchcommand
has     "help/unknown-command-refused" "an unknown command is an error, not a fallthrough" "unknown command"
rc_nonz "help/unknown-command-nonzero"
run "$CS" help; has "help/documents-no-stop" "help states plainly that there is no way to stop a session" "There is no \`cs stop\`"

# --- doctor ------------------------------------------------------------------
cd "$WORK/notarepo"
run "$CS" doctor
has "doctor/outside-a-repo-is-blocking" "outside a git repo, doctor reports it and refuses to say ready" "not in a git repository"
rc_nonz "doctor/outside-a-repo-nonzero"
run env FAKE_GH_FAIL=1 "$CS" doctor
has "doctor/unauthenticated-gh-fails" "an unauthenticated gh is a failure" "gh not authenticated"
run "$CS" doctor
has "doctor/says-cloud-github-uncheckable" "doctor never implies it checked the cloud-side GitHub grant" "NOT CHECKABLE"

# --- start -------------------------------------------------------------------
cd "$REPO_MAIN"
run "$CS" start "a task"
has     "start/refuses-without-a-tty" "cs start refuses without a TTY instead of running the work locally" "needs an interactive terminal"
rc_nonz "start/no-tty-nonzero"
trun "$CS" start
has     "start/refuses-empty-task" "cs start with no task prints usage and refuses" "usage: cs start"
rc_nonz "start/empty-task-nonzero"
trun "$CS" start --bundle
has     "start/refuses-flag-only" "a flag with no task is still a missing task" "usage: cs start"
trun "$CS" start --nosuchflag "task"
has     "start/refuses-unknown-flag" "an unknown flag is refused rather than passed through" "unknown flag: --nosuchflag"
rc_nonz "start/unknown-flag-nonzero"

logs_reset; ledger_reset
trun "$CS" start --env env_NotSelfHosted "task"
has "start/warns-on-non-ccpool-env" "--env with a non-ccpool id warns that the effect is unverified" "UNVERIFIED"
readf "$FAKE_LOG"
has "start/env-passed-through" "--env is passed to the CLI as --environment" "$(argseq --environment env_NotSelfHosted)"

logs_reset; ledger_reset
trun "$CS" start --env ccpool_selfhosted "task"
hasnt "start/no-warning-for-ccpool" "a self-hosted ccpool id does not trigger the unverified warning" "UNVERIFIED"

logs_reset; ledger_reset
trun "$CS" start "a plain task"
eq "start/records-mode-clone" "a normal dispatch is recorded with mode=clone" "$(ledger_field mode)" "clone"
eq "start/records-repo"   "the ledger row names the repo"   "$(ledger_field repo)"   "main-clean"
eq "start/records-branch" "the ledger row names the branch" "$(ledger_field branch)" "main"
readf "$FAKE_ENV_LOG"
hasnt "start/no-force-bundle-without-flag" "CCR_FORCE_BUNDLE is not set for a normal dispatch" "CCR_FORCE_BUNDLE=1"

logs_reset; ledger_reset
trun "$CS" start --bundle "a bundled task"
eq "start/records-mode-bundle" "--bundle is recorded as mode=bundle" "$(ledger_field mode)" "bundle"
readf "$FAKE_ENV_LOG"
has "start/sets-force-bundle" "--bundle sets CCR_FORCE_BUNDLE=1 for the CLI" "CCR_FORCE_BUNDLE=1"

# --- handoff -----------------------------------------------------------------
run "$CS" handoff
has     "handoff/refuses-empty-instruction" "cs handoff with no instruction prints usage and refuses" "usage: cs handoff"
rc_nonz "handoff/empty-instruction-nonzero"
run "$CS" handoff --nosuchflag "x"
has     "handoff/refuses-unknown-flag" "an unknown flag is refused" "unknown flag: --nosuchflag"
run "$CS" handoff "do the thing" --plan docs/plan.md
has     "handoff/refuses-trailing-plan-flag" "a --plan after the instruction is refused, not silently swallowed" "--plan must come before"
rc_nonz "handoff/trailing-plan-nonzero"
cd "$WORK/notarepo"
run "$CS" handoff "do the thing"
has "handoff/refuses-outside-a-repo" "cs handoff outside a git repository is refused" "not inside a git repository"

cd "$REPO_MAIN"; logs_reset; ledger_reset
trun "$CS" handoff "finish the thing"
has "handoff/brief-says-autonomous" "the brief tells the session nobody is watching" "Nobody is watching"
has "handoff/brief-requires-branch-and-push" "the brief requires commit-and-push as you go" "commit and push as you go"
has "handoff/brief-requires-pr"  "the brief asks for a pull request" "Open a pull request"
has "handoff/brief-asks-for-unverified" "the brief demands the session state what it could NOT verify" "could NOT verify"
# The blocked-host signal is a plain-text 403 BODY, not an x-deny-reason header - measured
# in a real cloud VM 2026-08-14 (probe 1). These two checks
# pinned the header claim and correctly went red when it was corrected.
has "handoff/brief-covers-blocked-hosts" "the brief tells the session what to do with a blocked-host 403" "request blocked: no rule or"
has "handoff/brief-warns-curl-hides-body" "the brief warns that plain curl swallows the 403 body, so a block can look like a hang" "SWALLOWS that body"
has "handoff/brief-forbids-force-push" "the brief forbids force-pushing and touching the base branch" "Do not force-push"
# A cloud session's telemetry lands in gitignored raw/ on a VM that gets reclaimed. Proven
# by probe 6: hooks DO fire there, and the record dies with the machine unless promoted.
has "handoff/brief-forbids-committing-telemetry" \
    "the brief tells the session NOT to commit its own telemetry: the committed ledger is read as involuntary, and a self-committed trace is cooperative and truncated" \
    "Do NOT promote or commit"

# --- new: the local-vs-cloud chooser -----------------------------------------
# `cs new` is the front door and the only interactive command. Its refusals matter more
# than its happy path: guessing "cloud" dispatches real work, guessing "local" quietly ties
# the work to a laptop that is about to close.

run "$CS" new "a task"
has     "new/no-tty-refuses-to-guess" \
        "without a terminal to ask in, cs new refuses rather than picking a side" \
        "needs a terminal to ask in"
rc_nonz "new/no-tty-nonzero" "that refusal exits non-zero"

run "$CS" new --cloud
has     "new/cloud-needs-a-task" \
        "a cloud session runs unattended, so --cloud without a task is refused" \
        "runs unattended, so it needs a task"
rc_nonz "new/cloud-needs-a-task-nonzero" "that refusal exits non-zero"

run "$CS" new --bogus "a task"
has     "new/unknown-flag-refused" "an unknown flag is an error, not a task word" "unknown flag"
rc_nonz "new/unknown-flag-nonzero" "that refusal exits non-zero"

run "$CS" new --plan
has     "new/plan-needs-a-value" "--plan with no value is refused rather than shifting past the end" "--plan needs a file path"
rc_nonz "new/plan-needs-a-value-nonzero" "that refusal exits non-zero"

# --cloud must inherit the SAME git gate as handoff. If it did not, the chooser would be a
# way to bypass every refusal the rest of the tool exists to make.
has_help="$("$CS" help 2>&1)"
LASTCMD="cs help"; OUT="$has_help"; RC=0
has "new/listed-first-in-help" "cs new is presented as the starting point" "START HERE"

# CS_DEFAULT answers the chooser once in the environment. An explicit flag must still win,
# and a bogus value must refuse rather than silently falling back to a side.
CS_DEFAULT=nonsense run "$CS" new "a task"
has     "new/bogus-default-refused" "an unrecognised CS_DEFAULT is refused, not silently ignored" "expected here, cloud, rc, or ask"
rc_nonz "new/bogus-default-nonzero" "that refusal exits non-zero"

CS_DEFAULT=ask run "$CS" new "a task"
has     "new/default-ask-still-asks" "CS_DEFAULT=ask falls through to the prompt, and without a TTY refuses" "needs a terminal to ask in"

CS_DEFAULT=here run "$CS" new --cloud
has     "new/flag-beats-default" "an explicit --cloud overrides CS_DEFAULT=here" "runs unattended, so it needs a task"

run "$CS" shell-init
has "shell-init/emits-a-function" "shell-init prints a claude() wrapper" "claude() {"
has "shell-init/passes-through-args" "the wrapper passes any argument straight through, so claude --version etc are untouched" '$# -gt 0'
# `command -v claude` returns the FUNCTION NAME once the wrapper is defined, so it can never
# find the real binary and the ~/.local/bin fallback becomes dead code. whence -p / type -P
# return only a PATH executable. Silent until the day claude is not on PATH.
hasnt "shell-init/does-not-resolve-via-command-v" \
      "the wrapper must not resolve the binary with command -v, which would return itself" \
      'real="$(command -v claude'
has  "shell-init/resolves-via-whence-or-type-P" \
     "the wrapper resolves the real binary with whence -p or type -P" "whence -p claude"
has "shell-init/does-not-write-files" "shell-init only prints; it never edits a shell config" "Add to ~/.zshrc"
rc_zero "shell-init/exits-zero"

# What is asserted here is that every non-interactive path refuses correctly. The prompt
# itself, the full flag x CS_DEFAULT x TTY cross product, and what the emitted shell-init
# function actually DOES are sections 7 and 8.

# --- env ---------------------------------------------------------------------
run "$CS" env bogus
has     "env/refuses-unknown-subcommand" "an unknown env subcommand is refused" "usage: cs env"
rc_nonz "env/unknown-subcommand-nonzero"
run "$CS" env set
has     "env/set-refuses-without-an-id" "env set with no id is refused" "usage: cs env set"
rc_nonz "env/set-no-id-nonzero"
rm -f "$HOME/.claude/settings.json"
run "$CS" env
has "env/show-is-the-default" "bare 'cs env' shows the current pin" "cloud environment"
has "env/show-reports-no-pin" "with nothing pinned, env show says so explicitly" "pinned        none"
has "env/show-explains-network-tiers" "env show documents the network-access tiers" "plain-text 403 body naming the host"
run "$CS" env set env_PinnedForShow
run "$CS" env show
has "env/show-warns-about-bridge" "with something pinned, env show warns that a bridge environment routes work back to this Mac" "routes work back to THIS Mac"
run "$CS" env clear
run "$CS" env set ccpool_selfhosted
has "env/warns-on-self-hosted" "pinning a ccpool_ id warns it is Team/Enterprise self-hosted" "self-hosted environment"
run "$CS" env clear
rm -f "$HOME/.claude/settings.json"
run "$CS" env clear
rc_zero "env/clear-is-idempotent" "env clear with no settings file at all succeeds quietly"

# --- send --------------------------------------------------------------------
ledger_reset; logs_reset
run "$CS" send
has     "send/refuses-without-an-id" "cs send with no id is refused" "missing session id"
rc_nonz "send/no-id-nonzero"
run "$CS" send session_01AbcXyz
has     "send/refuses-without-a-message" "cs send with no message prints usage and refuses" "usage: cs send"
rc_nonz "send/no-message-nonzero"
run "$CS" send notanid "hello"
has     "send/refuses-a-bad-id" "cs send refuses a malformed session id" "not a session id"
run "$CS" send last "hello"
has     "send/last-refuses-on-empty-ledger" "\`last\` with an empty ledger refuses with the fix" "no sessions recorded yet"
rc_nonz "send/last-empty-ledger-nonzero"

logs_reset
run "$CS" send session_01AbcXyz "just checking in"
readf "$FAKE_LOG"
has "send/uses-headless-form" "cs send uses the headless queue-and-exit form (-p, no TTY needed)" "$(argseq -p 'just checking in')"
has "send/passes-the-session-id" "the resolved id is passed with --cloud" "$(argseq --cloud session_01AbcXyz)"
has "send/requests-json" "cs send asks for machine-readable output" "$(argseq --output-format json)"

# --- ls ----------------------------------------------------------------------
rm -f "$LEDGER"
run "$CS" ls
has     "ls/absent-ledger-is-not-an-error" "cs ls with no ledger says so and points at the web list" "no dispatches recorded"
rc_zero "ls/absent-ledger-exit-zero"
has     "ls/names-the-authoritative-list" "cs ls never implies the local ledger is authoritative" "claude.ai/code"

# --- track -------------------------------------------------------------------
ledger_reset
run "$CS" track
has     "track/refuses-without-an-id" "cs track with no id is refused" "missing session id"
rc_nonz "track/no-id-nonzero"
run "$CS" track session_01Tracked
eq "track/defaults-the-title" "a tracked session with no title is recorded as 'untitled'" "$(ledger_field title)" "untitled"
eq "track/records-mode-tracked" "a tracked session is recorded with mode=tracked" "$(ledger_field mode)" "tracked"
cd "$WORK/notarepo"
run "$CS" track session_01Elsewhere "from the phone"
rc_zero "track/works-outside-a-repo" "cs track works outside a git repository"
eq "track/blank-repo-outside-a-checkout" "outside a repo the row records '-' rather than inventing one" \
   "$(ledger_field repo)" "-"
cd "$REPO_MAIN"

# --- rm ----------------------------------------------------------------------
run "$CS" rm
has     "rm/refuses-without-an-id" "cs rm with no id is refused" "missing session id"
run "$CS" rm notanid
has     "rm/refuses-a-bad-id" "cs rm refuses a malformed id" "not a session id"
run "$CS" rm session_01NotPresent
has     "rm/refuses-an-absent-id" "removing an id that is not in the ledger is an error, not a silent success" "no ledger entry"
rc_nonz "rm/absent-id-nonzero"
run "$CS" rm session_01Tracked
has "rm/removes-the-row" "cs rm reports what it removed" "removed 1 entry"
has "rm/says-it-does-not-stop" "cs rm says plainly that it does not stop the session" "does NOT stop or delete it"
run "$CS" ls
hasnt "rm/row-is-gone" "the removed row no longer appears in cs ls" "session_01Tracked"
has   "rm/other-rows-survive" "the other rows survive the rewrite" "session_01Elsewhere"

ledger_reset
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01KeepMe","title":"t","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"
printf 'a damaged line that is not json\n' >> "$LEDGER"
printf '%s\n' '{"ts":"2026-08-13T00:02:00-07:00","id":"session_01DropMe","title":"t","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"
run "$CS" rm session_01DropMe
readf "$LEDGER"
has   "rm/preserves-damaged-lines" "rewriting the ledger preserves unparseable lines rather than dropping them" "a damaged line that is not json"
has   "rm/keeps-untargeted-rows" "the untargeted row survives" "session_01KeepMe"
hasnt "rm/removed-row-is-gone" "the targeted row is gone" "session_01DropMe"
rm -f "$LEDGER"
run "$CS" rm session_01Whatever
has     "rm/empty-ledger-refused" "cs rm against a missing ledger refuses instead of reporting a removal" "ledger is empty"
rc_nonz "rm/empty-ledger-nonzero"

# --- open / web --------------------------------------------------------------
ledger_reset; logs_reset
run "$CS" open notanid
has     "open/refuses-a-bad-id" "cs open refuses a malformed id rather than opening a wrong URL" "not a session id"
rc_nonz "open/bad-id-nonzero"
run "$CS" open last
has     "open/last-refuses-on-empty-ledger" "cs open last with an empty ledger refuses" "no sessions recorded yet"
logs_reset
run "$CS" open session_01AbcXyz
readf "$FAKE_OPEN_LOG"
has "open/opens-the-session-url" "cs open opens the session's claude.ai/code URL" "https://claude.ai/code/session_01AbcXyz"
logs_reset
run "$CS" web
readf "$FAKE_OPEN_LOG"
has "web/opens-the-session-list" "cs web opens the full session list" "https://claude.ai/code"

# --- tp ----------------------------------------------------------------------
ledger_reset; logs_reset
run "$CS" tp notanid
has     "tp/refuses-a-bad-id" "cs tp refuses a malformed id" "not a session id"
run "$CS" tp last
has     "tp/last-refuses-on-empty-ledger" "cs tp last with an empty ledger refuses" "no sessions recorded yet"
cd "$WORK/notarepo"
run "$CS" tp session_01AbcXyz
has     "tp/refuses-outside-a-repo" "teleport outside a checkout of the session's repo is refused" "must run from a checkout"
rc_nonz "tp/outside-a-repo-nonzero"
cd "$REPO_MAIN"; touch "$REPO_MAIN/scratch-dirt.txt"
run "$CS" tp session_01AbcXyz
has     "tp/refuses-a-dirty-tree" "teleport into a dirty worktree is refused — it checks out the session's branch" "requires a clean working tree"
rc_nonz "tp/dirty-tree-nonzero"
readf "$FAKE_LOG"
hasnt "tp/dirty-tree-invoked-nothing" "the refusal happens before the CLI is invoked" "<arg>--teleport</arg>"
rm -f "$REPO_MAIN/scratch-dirt.txt"
logs_reset
run "$CS" tp session_01AbcXyz
readf "$FAKE_LOG"
has "tp/passes-teleport-and-id" "a clean tree teleports with --teleport <id>" "$(argseq --teleport session_01AbcXyz)"

# --- rc ----------------------------------------------------------------------
logs_reset
run "$CS" rc
readf "$FAKE_LOG"
has "rc/passes-remote-control" "cs rc invokes --remote-control" "$(argseq --remote-control)"
eq "rc/no-empty-arg-when-unnamed" "with no name, no empty argument is passed to the CLI" \
   "$(tr -d '\n' < "$FAKE_LOG")" "<arg>--remote-control</arg><end/>"
logs_reset
run "$CS" rc mybox
readf "$FAKE_LOG"
has "rc/passes-the-name" "a name is passed through to --remote-control" "$(argseq --remote-control mybox)"

# --- binary resolution -------------------------------------------------------
LASTCMD="CS_CLAUDE_BIN=/nonexistent cs send"
OUT="$(CS_CLAUDE_BIN=/nonexistent/claude PATH="/usr/bin:/bin" HOME="$WORK/emptyhome" "$CS" send session_01AbcXyz hi 2>&1)"; RC=$?
has     "binary/refuses-when-no-cli-found" "with no claude binary anywhere, cs says so and points at setup" "no \`claude\` binary found"
rc_nonz "binary/no-cli-nonzero"
LASTCMD="CS_CLAUDE_BIN=/nonexistent cs ls"
OUT="$(CS_CLAUDE_BIN=/nonexistent/claude PATH="/usr/bin:/bin" HOME="$WORK/emptyhome" "$CS" ls 2>&1)"; RC=$?
rc_zero "binary/ls-works-without-a-cli" "cs ls still reads its own ledger with no claude binary installed"

# =============================================================================
sec "4 — the git gate, every condition"
# =============================================================================

cd "$REPO_MAIN"; ledger_reset; logs_reset

# not a repo
cd "$WORK/notarepo"
trun "$CS" start "x"
has     "gate/not-a-repo" "dispatching outside a git repository is refused" "not inside a git repository"
rc_nonz "gate/not-a-repo-nonzero"
trun "$CS" start --bundle "x"
has "gate/not-a-repo-even-with-bundle" "--bundle does not relax the 'must be a repo' gate" "not inside a git repository"

# unborn branch
GATE_UNBORN="$WORK/repos/gate-unborn"; mkdir -p "$GATE_UNBORN"; git init -q -b main "$GATE_UNBORN" >/dev/null
cd "$GATE_UNBORN"
trun "$CS" start "x"
has     "gate/unborn-branch" "a branch with no commits is refused" "no commits yet"
rc_nonz "gate/unborn-branch-nonzero"
trun "$CS" start --bundle "x"
has "gate/unborn-even-with-bundle" "--bundle does not relax the 'needs a commit' gate" "no commits yet"

# detached HEAD
GATE_DET="$(new_repo gate-detached)"
git -C "$GATE_DET" commit -q --allow-empty -m second
git -C "$GATE_DET" checkout -q HEAD~1
cd "$GATE_DET"
trun "$CS" start "x"
has     "gate/detached-head" "a detached HEAD is refused" "detached HEAD"
rc_nonz "gate/detached-head-nonzero"
trun "$CS" start --bundle "x"
has "gate/detached-even-with-bundle" "--bundle does not relax the detached-HEAD gate" "detached HEAD"

# no origin remote
GATE_NOREMOTE="$(new_repo gate-no-remote)"
cd "$GATE_NOREMOTE"
trun "$CS" start "x"
has     "gate/no-origin" "no origin remote is refused, naming --bundle as the alternative" "no origin remote"
rc_nonz "gate/no-origin-nonzero"
logs_reset; ledger_reset
trun "$CS" start --bundle "x"
has "gate/no-origin-bundle-warns" "--bundle downgrades the missing remote to a warning" "will bundle the local repo"
eq  "gate/no-origin-bundle-dispatches" "--bundle then actually dispatches" "$(ledger_field mode)" "bundle"

# origin is not GitHub
GATE_NOTGH="$(new_repo gate-not-github)"
attach_origin "$GATE_NOTGH" "https://gitlab.com/example/x.git"
cd "$GATE_NOTGH"
trun "$CS" start "x"
has     "gate/non-github-origin" "a non-GitHub origin is refused" "origin is not GitHub"
rc_nonz "gate/non-github-origin-nonzero"
logs_reset; ledger_reset
trun "$CS" start --bundle "x"
eq "gate/non-github-bundle-dispatches" "--bundle allows a non-GitHub origin" "$(ledger_field mode)" "bundle"

# dirty tree
GATE_DIRTY="$(new_repo gate-dirty)"
attach_origin "$GATE_DIRTY" "https://github.com/example/x.git"
printf 'work in progress\n' > "$GATE_DIRTY/wip.txt"
cd "$GATE_DIRTY"
trun "$CS" start "x"
has     "gate/dirty-tree" "an uncommitted change is refused — the VM cannot see it" "uncommitted change(s)"
rc_nonz "gate/dirty-tree-nonzero"
logs_reset; ledger_reset
trun "$CS" start --bundle "x"
has "gate/dirty-bundle-warns-about-untracked" "--bundle warns that untracked files are NOT bundled" "untracked files are NOT"
eq  "gate/dirty-bundle-dispatches" "--bundle dispatches despite the dirty tree" "$(ledger_field mode)" "bundle"

# no upstream
GATE_NOUP="$(new_repo gate-no-upstream)"
git init -q --bare "$WORK/remotes/gate-no-upstream.git" >/dev/null
git -C "$GATE_NOUP" remote add origin "https://github.com/example/x.git"
cd "$GATE_NOUP"
trun "$CS" start "x"
has     "gate/no-upstream" "a branch with no upstream is refused, with the push command to fix it" "has no upstream"
has     "gate/no-upstream-names-the-fix" "the refusal names the exact command" "git push -u origin main"
rc_nonz "gate/no-upstream-nonzero"
logs_reset; ledger_reset
trun "$CS" start --bundle "x"
eq "gate/no-upstream-bundle-dispatches" "--bundle skips the upstream checks entirely" "$(ledger_field mode)" "bundle"

# unpushed commits
GATE_AHEAD="$(new_repo gate-unpushed)"
attach_origin "$GATE_AHEAD" "https://github.com/example/x.git"
git -C "$GATE_AHEAD" commit -q --allow-empty -m "not pushed yet"
cd "$GATE_AHEAD"
trun "$CS" start "x"
has     "gate/unpushed-commits" "an unpushed commit is refused" "unpushed commit(s) on 'main'"
rc_nonz "gate/unpushed-nonzero"
logs_reset; ledger_reset
trun "$CS" start --bundle "x"
eq "gate/unpushed-bundle-dispatches" "--bundle dispatches with unpushed commits" "$(ledger_field mode)" "bundle"

# upstream on a fork (also covered as D5; kept here so the gate list is complete)
cd "$REPO_FORK"
trun "$CS" start "x"
has "gate/fork-upstream" "an upstream on a fork is refused even when fully pushed" "not origin"

# the positive control — without it every gate check above could pass on a cs
# that refused unconditionally.
cd "$REPO_MAIN"; logs_reset; ledger_reset
trun "$CS" start "clean and pushed"
eq      "gate/clean-and-pushed-accepted" "a clean, pushed, GitHub-origin repo passes every gate" \
        "$(ledger_field id)" "session_01ViewLineWins"
rc_zero "gate/clean-and-pushed-exit-zero"

# =============================================================================
sec "5 — ledger robustness"
# =============================================================================

cd "$REPO_MAIN"

rm -f "$LEDGER"
run "$CS" ls;        has "ledger/absent-ls" "an absent ledger is reported, not a crash" "no dispatches recorded"
run "$CS" open last; has "ledger/absent-last" "\`last\` against an absent ledger refuses with the fix" "no sessions recorded yet"

: > "$LEDGER"
run "$CS" ls;        has "ledger/empty-ls" "an empty ledger is reported, not a crash" "no dispatches recorded"
run "$CS" open last; has "ledger/empty-last" "\`last\` against an empty ledger refuses" "no sessions recorded yet"

printf '   \n\n\t\n' > "$LEDGER"
run "$CS" ls
has     "ledger/whitespace-only-ls" "a whitespace-only ledger reports no readable records" "no readable records"
rc_nonz "ledger/whitespace-only-ls-nonzero"
run "$CS" open last
has "ledger/whitespace-only-last" "\`last\` against a whitespace-only ledger refuses" "no readable records"

printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01First","title":"a","repo":"-","branch":"b","mode":"clone","url":"u"}' > "$LEDGER"
printf '%s\n' 'GARBAGE {{{ not json' >> "$LEDGER"
printf '%s\n' '{"ts":"2026-08-13T00:02:00-07:00","id":"session_01Second","title":"b","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"
run "$CS" ls
has "ledger/malformed-line-counted" "a malformed line is counted and reported" "1 unreadable line(s) skipped"
has "ledger/malformed-line-others-listed" "the readable rows are still listed" "session_01Second"

printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01Whole","title":"a","repo":"-","branch":"b","mode":"clone","url":"u"}' > "$LEDGER"
printf '%s' '{"ts":"2026-08-13T00:01:00-07:00","id":"sessio' >> "$LEDGER"
run "$CS" ls;        has "ledger/truncated-final-line" "a truncated final line does not break cs ls" "session_01Whole"
run "$CS" open last; readf "$FAKE_OPEN_LOG"
has "ledger/truncated-final-line-last" "a truncated final line does not break \`last\`" "session_01Whole"

printf '%s' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01NoNewline","title":"a","repo":"-","branch":"b","mode":"clone","url":"u"}' > "$LEDGER"
run "$CS" ls
has "ledger/no-trailing-newline" "a final row with no trailing newline is still read" "session_01NoNewline"
eq  "ledger/no-trailing-newline-counted" "and is counted" "$(printf '%s' "$OUT" | grep -c 'recorded locally')" "1"
logs_reset; run "$CS" open last; readf "$FAKE_OPEN_LOG"
has "ledger/no-trailing-newline-last" "\`last\` reads a row with no trailing newline" "session_01NoNewline"

# A row that is valid JSON but missing the fields `record` always writes. Only
# reachable by hand-editing state/, which state/README.md forbids — but `cs ls`
# should still degrade to a message rather than a Python traceback.
printf '%s\n' '{"id":"session_01MissingFields"}' > "$LEDGER"
run "$CS" ls
hasnt "ledger/incomplete-row-ls-no-traceback" \
       "cs ls should degrade to a message on an incomplete-but-valid-JSON row, not raise KeyError" \
       "Traceback (most recent call last)"
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","title":"no id here"}' > "$LEDGER"
run "$CS" open last
hasnt "ledger/incomplete-row-last-no-traceback" \
       "\`last\` should refuse with a readable reason when the final row has no id, not raise KeyError" \
       "Traceback (most recent call last)"

# appending is what `record` does; make sure a long ledger still resolves `last`
ledger_reset
for i in 1 2 3 4 5; do
  printf '{"ts":"2026-08-13T00:0%s:00-07:00","id":"session_01Row%s","title":"t","repo":"-","branch":"b","mode":"clone","url":"u"}\n' "$i" "$i" >> "$LEDGER"
done
logs_reset; run "$CS" open last; readf "$FAKE_OPEN_LOG"
has "ledger/last-is-the-most-recent-row" "\`last\` resolves to the final row, not the first" "session_01Row5"

# --- the cross-repo guard itself (porting audit 2026-08-17: the tracked ledger travels
# with a port, and `last` resolved to a live session of the ORIGINAL repo, rc 0) ---------
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01Foreign","title":"t","repo":"some-other-repo","branch":"b","mode":"clone","url":"u"}' > "$LEDGER"
run "$CS" open last
has     "ledger/foreign-repo-row-refused" \
        "a last-row recorded in a different repo refuses instead of silently targeting the old repo's session" \
        "belongs to repo 'some-other-repo'"
rc_nonz "ledger/foreign-repo-row-nonzero" "that refusal exits non-zero"
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01NoRepoField","title":"t","branch":"b","mode":"clone","url":"u"}' > "$LEDGER"
logs_reset; run "$CS" open last; readf "$FAKE_OPEN_LOG"
has "ledger/missing-repo-field-still-allowed" \
    "an old row with no repo field keeps the pre-guard behaviour - only a POSITIVE mismatch refuses" \
    "session_01NoRepoField"

# =============================================================================
sec "6 — argument parsing"
# =============================================================================

cd "$REPO_MAIN"; ledger_reset; logs_reset

# `--` terminator
trun "$CS" start -- --bundle
readf "$FAKE_LOG"
has "args/double-dash-makes-a-literal-task" "after --, a flag-looking word is the task text" "$(argseq --cloud --bundle)"
eq  "args/double-dash-does-not-bundle" "and it is NOT interpreted as the --bundle flag" "$(ledger_field mode)" "clone"

logs_reset; ledger_reset
trun "$CS" start -- "-- a task starting with dashes"
readf "$FAKE_LOG"
has "args/task-may-start-with-dashes" "a task may start with dashes when passed after --" "$(argseq --cloud '-- a task starting with dashes')"

# multi-word task without quotes
logs_reset; ledger_reset
trun "$CS" start one two three
readf "$FAKE_LOG"
has "args/unquoted-task-is-joined" "an unquoted multi-word task becomes ONE argument, not three" "$(argseq --cloud 'one two three')"

# messages containing quotes, newlines and leading dashes
logs_reset
run "$CS" send session_01AbcXyz '--not-a-flag "quoted" '"'"'single'"'"' $(echo nope) `backtick`'
readf "$FAKE_LOG"
has "args/send-preserves-the-message-verbatim" \
    "quotes, a leading --, command substitution and backticks all survive as ONE literal argument" \
    "$(argseq '--not-a-flag "quoted" '"'"'single'"'"' $(echo nope) `backtick`')"

logs_reset
run "$CS" send session_01AbcXyz "$(printf 'line one\nline two')"
readf "$FAKE_LOG"
has "args/send-preserves-newlines" "a multi-line message survives as a single argument" "$(argseq "$(printf 'line one\nline two')")"

logs_reset
run "$CS" send session_01AbcXyz hello there world
readf "$FAKE_LOG"
has "args/send-joins-unquoted-words" "an unquoted multi-word message becomes ONE argument" "$(argseq 'hello there world')"

# flags placed AFTER the positional argument
logs_reset; ledger_reset
trun "$CS" start "a task" --bundle
readf "$FAKE_LOG"
hasnt "args/start-bundle-after-task" \
       "cs start must not silently swallow a --bundle placed after the task (cs handoff guards this for --plan; cs start does not)" \
       "$(argseq --cloud 'a task --bundle')"
# The consequence of the same defect, so it is on the record: the operator asked for
# --bundle and got a clone, with nothing printed. Fixed 2026-08-13; kept as a regression.
LASTCMD="ledger mode after: cs start \"a task\" --bundle"; RC=0; OUT="$(ledger_field mode)"
hasnt "args/start-bundle-after-task-recorded-as-clone" \
       "a swallowed --bundle must not end up recorded as a clone dispatch" "clone"
logs_reset; ledger_reset
trun "$CS" start "a task" --env ccpool_x
readf "$FAKE_LOG"
hasnt "args/start-env-after-task" \
       "cs start must not silently swallow an --env placed after the task" \
       "$(argseq --cloud 'a task --env ccpool_x')"

# handoff already guards this one — the positive control for the check above
run "$CS" handoff "a task" --plan x.md
has "args/handoff-plan-after-instruction-is-caught" "cs handoff does refuse a flag placed after its instruction" "--plan must come before"

# =============================================================================
sec "7 — cs new: the destination chooser, every combination"
# =============================================================================
# `cs new` resolves ONE thing — where the work runs — out of three inputs: an explicit
# flag, the CS_DEFAULT environment variable, and whether there is a terminal to ask in.
# Getting it wrong is asymmetric: resolving to cloud dispatches real autonomous work and
# consumes rate limits, resolving to here quietly ties the work to a laptop that is about
# to close. Section 3 covers its refusals one at a time; this is the full cross product,
# written as an explicit table so the expectation is a specification rather than a second
# copy of the same case statement.
#
# The `here` branch is exercised WITHOUT starting a session. CS_CLAUDE_BIN points at
# fixtures/argv-recorder, which records its argv and prints no `View:` line — so the argv
# says which branch resolved, and the ledger being empty at the end of this section is
# proof that not one of the 48 cases dispatched anything.

cd "$REPO_MAIN"; ledger_reset; logs_reset; : > "$ARGV_LOG"
NEW_STUB="$WORK/stub/argv-recorder"
NEW_TASK="matrix task"

new_case() { # flag(- for none) | CS_DEFAULT(UNSET or a value) | tty | outcome | why
  local flag="$1" def="$2" tty="$3" want="$4" why="$5"
  local fl="$flag"; [[ "$fl" == "-" ]] && fl=""
  local name="new-matrix/${flag}+CS_DEFAULT=${def}+tty=${tty}"
  local desc="flag=${flag}, CS_DEFAULT=${def}, tty=${tty} resolves to ${want}"
  [[ -n "$why" ]] && desc="$desc — $why"
  local -a cmd
  if [[ "$def" == UNSET ]]; then cmd=(env -u CS_DEFAULT); else cmd=(env "CS_DEFAULT=$def"); fi
  cmd=("${cmd[@]}" "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new)
  [[ -n "$fl" ]] && cmd=("${cmd[@]}" "$fl")
  cmd=("${cmd[@]}" "$NEW_TASK")
  : > "$ARGV_LOG"
  if [[ "$tty" == yes ]]; then trun "${cmd[@]}"; else run "${cmd[@]}"; fi
  case "$want" in
    here)  rc_zero "$name+exit" "$desc, and exits 0"
           eq "$name" "$desc" "$(argflat)" "<arg>$NEW_TASK</arg><end/>" ;;
    rc)    rc_zero "$name+exit" "$desc, and exits 0"
           eq "$name" "$desc" "$(argflat)" "<arg>--remote-control</arg><arg>$NEW_TASK</arg><end/>" ;;
    cloud) rc_zero "$name+exit" "$desc, and exits 0"
           eq "$name" "$desc" "$(head -1 "$ARGV_LOG")" "<arg>--cloud</arg>" ;;
    refuse:*)
           has     "$name" "$desc" "${want#refuse:}"
           rc_nonz "$name+exit" "$desc, and exits non-zero"
           # A refusal that has already invoked the CLI is not a refusal. This is the
           # check that keeps the cloud column of the matrix free.
           eq "$name+quiet" "$desc, and invokes nothing before refusing" "$(argflat)" "" ;;
    *)     _fail "$name" "$desc" "the table asked for an unknown outcome: $want" ;;
  esac
}

# flag | CS_DEFAULT | tty | outcome | why
#
# Two rules the table exists to prove: an explicit flag ALWAYS beats CS_DEFAULT (including
# a CS_DEFAULT that is not a valid value at all), and an unrecognised CS_DEFAULT refuses
# rather than falling back to a side. Note the two different refusals in the no-TTY column:
# "needs a terminal to ask in" is the chooser declining to guess, while "cs start needs an
# interactive terminal" means the destination DID resolve to cloud and the TTY gate that
# refused is `cs start`'s. Reading one for the other would hide a resolution bug.
while IFS='|' read -r flag def tty want why; do
  case "$flag" in ""|\#*) continue ;; esac
  new_case "$flag" "$def" "$tty" "$want" "$why"
done <<'ROWS'
-|UNSET|no|refuse:needs a terminal to ask in|nothing to go on and nowhere to ask: refuse, never pick a side
-|UNSET|yes|refuse:input closed before you answered|a terminal but stdin at EOF is an abandoned prompt, not an answer
-|ask|no|refuse:needs a terminal to ask in|ask means ask; with no TTY that is a refusal
-|ask|yes|refuse:input closed before you answered|
-|here|no|here|CS_DEFAULT answers the question when no flag does
-|here|yes|here|
-|cloud|no|refuse:cs start needs an interactive terminal|resolution DID reach cloud; the refusal is cs start's TTY gate
-|cloud|yes|cloud|
-|rc|no|rc|Remote Control runs locally, so it needs no TTY of its own
-|rc|yes|rc|
-|nonsense|no|refuse:expected here, cloud, rc, or ask|an unrecognised default refuses rather than defaulting to a side
-|nonsense|yes|refuse:expected here, cloud, rc, or ask|
--here|UNSET|no|here|
--here|UNSET|yes|here|
--here|ask|no|here|the flag wins over ask
--here|ask|yes|here|
--here|here|no|here|
--here|here|yes|here|
--here|cloud|no|here|THE RULE: an explicit flag beats CS_DEFAULT
--here|cloud|yes|here|THE RULE: an explicit flag beats CS_DEFAULT
--here|rc|no|here|
--here|rc|yes|here|
--here|nonsense|no|here|an invalid CS_DEFAULT is never even consulted once a flag decided
--here|nonsense|yes|here|
--local|UNSET|no|here|--local is an alias of --here
--local|UNSET|yes|here|
--local|ask|no|here|
--local|ask|yes|here|
--local|here|no|here|
--local|here|yes|here|
--local|cloud|no|here|
--local|cloud|yes|here|
--local|rc|no|here|
--local|rc|yes|here|
--local|nonsense|no|here|
--local|nonsense|yes|here|
--cloud|UNSET|no|refuse:cs start needs an interactive terminal|
--cloud|UNSET|yes|cloud|
--cloud|ask|no|refuse:cs start needs an interactive terminal|
--cloud|ask|yes|cloud|
--cloud|here|no|refuse:cs start needs an interactive terminal|THE RULE: an explicit flag beats CS_DEFAULT
--cloud|here|yes|cloud|THE RULE: an explicit flag beats CS_DEFAULT
--cloud|cloud|no|refuse:cs start needs an interactive terminal|
--cloud|cloud|yes|cloud|
--cloud|rc|no|refuse:cs start needs an interactive terminal|
--cloud|rc|yes|cloud|
--cloud|nonsense|no|refuse:cs start needs an interactive terminal|
--cloud|nonsense|yes|cloud|
ROWS

# The two documented aliases, kept out of the cross product so a failure names the alias.
: > "$ARGV_LOG"; run env CS_DEFAULT=local "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
eq "new-default/local-is-an-alias-of-here" "CS_DEFAULT=local resolves to here, like the --local flag" \
   "$(argflat)" "<arg>$NEW_TASK</arg><end/>"
: > "$ARGV_LOG"; run env CS_DEFAULT=phone "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
eq "new-default/phone-is-an-alias-of-rc" "CS_DEFAULT=phone resolves to Remote Control, like rc" \
   "$(argflat)" "<arg>--remote-control</arg><arg>$NEW_TASK</arg><end/>"

# A default that silently decides where autonomous work runs is the thing this folder's
# rule 4 is about, so it has to be visible in the output.
: > "$ARGV_LOG"; run env CS_DEFAULT=here "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
has "new-default/announces-that-the-default-decided" \
    "when CS_DEFAULT picks the destination, cs prints which one and why" "here (CS_DEFAULT)"
: > "$ARGV_LOG"; run env CS_DEFAULT=rc "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
has "new-default/announces-rc-too" "the same annotation appears for the rc default" "rc (CS_DEFAULT)"
: > "$ARGV_LOG"; run env CS_DEFAULT=here "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new --cloud "$NEW_TASK"
hasnt "new-default/no-annotation-when-a-flag-decided" \
      "with an explicit flag the CS_DEFAULT annotation is absent — it did not contribute" "(CS_DEFAULT)"

# --- the interactive chooser itself ------------------------------------------
# The only surface in `cs` that reads from a human. It needs a controlling terminal AND
# stdin that stays open AND an answer delivered after the prompt appears; fixtures/
# pty-drive.py is the only one of the three harness paths that provides all three.
run python3 "$FIXTURES/pty-drive.py" "" "" 10 -- /bin/echo pty-probe-ok
if [[ "$RC" -eq 0 && "$OUT" == *pty-probe-ok* ]]; then PTY_OK=yes; else PTY_OK=no; fi
NL=$'\n'

if [[ "$PTY_OK" != yes ]]; then
  for n in prompt-offers-three-choices answers-1-here answers-2-cloud answers-3-rc \
           answers-h-here answers-word-cloud answers-phone-rc rejects-a-bogus-answer \
           bogus-answer-dispatches-nothing; do
    _skip "new-chooser/$n" "no usable pty here: fixtures/pty-drive.py could not drive /bin/echo"
  done
else
  : > "$ARGV_LOG"
  ptyrun "[1/2/3]" "1$NL" 20 -- env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
  has "new-chooser/prompt-offers-three-choices" \
      "the prompt names all three destinations, including Remote Control" "Remote Control"
  eq  "new-chooser/answers-1-here" "answering 1 runs the task on this machine and dispatches nothing" \
      "$(argflat)" "<arg>$NEW_TASK</arg><end/>"

  : > "$ARGV_LOG"
  ptyrun "[1/2/3]" "h$NL" 20 -- env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
  eq "new-chooser/answers-h-here" "the letter form 'h' answers the same as 1" \
     "$(argflat)" "<arg>$NEW_TASK</arg><end/>"

  : > "$ARGV_LOG"
  ptyrun "[1/2/3]" "3$NL" 20 -- env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
  eq "new-chooser/answers-3-rc" "answering 3 starts Remote Control with the task as its name" \
     "$(argflat)" "<arg>--remote-control</arg><arg>$NEW_TASK</arg><end/>"

  : > "$ARGV_LOG"
  ptyrun "[1/2/3]" "phone$NL" 20 -- env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
  eq "new-chooser/answers-phone-rc" "the word 'phone' answers the same as 3" \
     "$(argflat)" "<arg>--remote-control</arg><arg>$NEW_TASK</arg><end/>"

  : > "$ARGV_LOG"
  ptyrun "[1/2/3]" "2$NL" 30 -- env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
  eq "new-chooser/answers-2-cloud" "answering 2 composes a handoff brief and dispatches it to the CLI" \
     "$(head -1 "$ARGV_LOG")" "<arg>--cloud</arg>"

  : > "$ARGV_LOG"
  ptyrun "[1/2/3]" "cloud$NL" 30 -- env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
  eq "new-chooser/answers-word-cloud" "the word 'cloud' answers the same as 2" \
     "$(head -1 "$ARGV_LOG")" "<arg>--cloud</arg>"

  : > "$ARGV_LOG"
  ptyrun "[1/2/3]" "4$NL" 20 -- env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new "$NEW_TASK"
  has "new-chooser/rejects-a-bogus-answer" "an answer that is not one of the three is refused, not rounded to one" \
      "not one of 1, 2 or 3"
  eq  "new-chooser/bogus-answer-dispatches-nothing" "and nothing at all is invoked" \
      "$(argflat)" ""
fi

# Closing controls for everything above. Without these the matrix could pass while
# resolving through the operator's real CLI, or while quietly recording dispatches.
readf "$FAKE_LOG"
eq "new-matrix/never-reached-the-dispatch-stub" \
   "no case in the matrix or the chooser reached fixtures/fake-claude, so CS_CLAUDE_BIN was honoured every time" \
   "$( [[ -s "$FAKE_LOG" ]] && echo reached || echo not-reached )" "not-reached"
LASTCMD="cat $LEDGER"; RC=0; OUT="$(cat "$LEDGER" 2>/dev/null)"
eq "new-matrix/recorded-no-dispatch" \
   "48 matrix cases and 8 answered prompts left the ledger empty — not one of them recorded a session" \
   "$( [[ -s "$LEDGER" ]] && echo nonempty || echo empty )" "empty"

# --- --plan against a destination that cannot use it -------------------------
# `--plan` only means anything to a cloud session: it names a committed file the VM will
# read. `cmd_new` (bin/cs:798-804, landed in a1b05ef, "refuse a dropped --plan") refuses the
# flag outright for any other destination rather than accepting and silently dropping it, as
# it once did — same family as reviewed defect 7 (a flag that silently did nothing).
#
# The positive control comes first: on the cloud path the flag genuinely reaches the brief,
# so the refusals below are proven to be about the destination, not about --plan being
# broken everywhere.
cd "$REPO_PLAN"; : > "$ARGV_LOG"; logs_reset; ledger_reset
trun "$CS" new --cloud --plan docs/plan.md "carry on"
has "new/plan-reaches-a-cloud-session" "the positive control: --plan does reach the brief on the cloud path" \
    'committed at `docs/plan.md`'

: > "$ARGV_LOG"
run env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new --here --plan docs/plan.md "carry on"
rc_nonz "new/plan-with-here-is-refused" \
    "a local destination cannot honour --plan, so cs new refuses instead of silently dropping it"
has "new/plan-with-here-names-the-destination" \
    "the refusal names --plan and the destination that would have ignored it" \
    "--plan applies only to a cloud session"
readf "$ARGV_LOG"
eq "new/plan-with-here-never-dispatched" \
   "the refusal happens before dispatch — claude is never invoked with a half-applied plan" \
   "$( [[ -s "$ARGV_LOG" ]] && echo invoked || echo not-invoked )" "not-invoked"

: > "$ARGV_LOG"
run env "CS_DEFAULT=rc" "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new --plan docs/plan.md "carry on"
rc_nonz "new/plan-with-rc-is-refused" \
    "the same refusal for a Remote Control destination chosen by CS_DEFAULT"
has "new/plan-with-rc-names-the-destination" \
    "the refusal names the destination for the CS_DEFAULT path too" \
    "--plan applies only to a cloud session"

: > "$ARGV_LOG"
run env "CS_CLAUDE_BIN=$NEW_STUB" "$CS" new --here --plan /nonexistent/plan.md "carry on"
rc_nonz "new/plan-with-here-refused-before-file-is-checked" \
    "the destination refusal fires first, so a plan file that does not even exist is still refused rather than silently accepted"
has "new/plan-with-here-refused-before-file-is-checked-names-it" \
    "same named refusal even though the file itself was never reached to be validated" \
    "--plan applies only to a cloud session"
cd "$REPO_MAIN"

# =============================================================================
sec "8 — cs shell-init: the emitted shell function as an artifact"
# =============================================================================
# `cs shell-init` prints a shell function for the operator to eval in their own startup
# file. That output is a shipped artifact: nothing re-checks it after it is pasted in, and
# a syntax error or a wrong resolution there breaks the `claude` command itself. Section 3
# asserts what the TEXT says; this asserts what the text DOES, by sourcing it in a subshell
# with fixtures/argv-recorder standing in for the binary.

SI="$WORK/shell-init.sh"
"$CS" shell-init > "$SI" 2>/dev/null
SI_LOG="$WORK/logs/argv-shellinit.log"; : > "$SI_LOG"
# A deliberately minimal PATH: the operator's real `claude` must be unreachable, so only
# the stub dir plus the system directories, with git's own directory added if it lives
# somewhere else (the wrapper calls `git rev-parse` to find the repo root).
SHPATH="$WORK/shellstub:/usr/bin:/bin"
GITDIR="$(dirname "$(command -v git)")"
case ":$SHPATH:" in *":$GITDIR:"*) ;; *) SHPATH="$SHPATH:$GITDIR" ;; esac
HAVE_ZSH=no; command -v zsh >/dev/null 2>&1 && HAVE_ZSH=yes

run bash -n "$SI"
rc_zero "shell-init/parses-under-bash" "the emitted function is syntactically valid bash"
if [[ "$HAVE_ZSH" == yes ]]; then
  run zsh -n "$SI"
  rc_zero "shell-init/parses-under-zsh" "the emitted function is syntactically valid zsh — the README tells the operator to paste it into ~/.zshrc"
else
  _skip "shell-init/parses-under-zsh" "zsh is not installed on this host"
fi

# Every argued form must reach the binary untouched. `--version`, `doctor` and `auth` are
# the ones an operator runs while diagnosing the wrapper itself; `-p` and `--resume` are
# the ones a script runs.
for sh in bash zsh; do
  if [[ "$sh" == zsh && "$HAVE_ZSH" != yes ]]; then
    _skip "shell-init/zsh/passes-through" "zsh is not installed on this host"; continue
  fi
  while IFS='|' read -r label form; do
    [[ -z "$label" ]] && continue
    : > "$SI_LOG"
    LASTCMD="$sh -c 'source shell-init.sh; claude $form'"
    OUT="$(PATH="$SHPATH" ARGV_LOG="$SI_LOG" HOME="$WORK/home" "$sh" -c "source '$SI'; claude $form" 2>&1)"; RC=$?
    rc_zero "shell-init/$sh/passes-through[$label]+exit" "\`claude $form\` under $sh exits with the binary's status"
    readf "$SI_LOG"
    has "shell-init/$sh/passes-through[$label]" \
        "\`claude $form\` reaches the binary with its arguments intact under $sh" "$(argseq $form)"
  done <<'FORMS'
--version|--version
doctor|doctor
auth|auth
-p|-p hello
--resume|--resume
FORMS
done

# The binary must be resolved with `whence -p`/`type -P`, never `command -v` — which
# returns the FUNCTION once the wrapper is defined, making the ~/.local/bin fallback dead
# code. Section 3 asserts the text; this is the behaviour, and it is the only way to tell
# the two apart: with claude ON PATH both spellings work, so the bug is invisible until
# the day it is not.
mkdir -p "$WORK/fallbackhome/.local/bin"
cp "$FIXTURES/argv-recorder" "$WORK/fallbackhome/.local/bin/claude"
chmod +x "$WORK/fallbackhome/.local/bin/claude"
mkdir -p "$WORK/nobinhome"
for sh in bash zsh; do
  if [[ "$sh" == zsh && "$HAVE_ZSH" != yes ]]; then
    _skip "shell-init/zsh/binary-resolution" "zsh is not installed on this host"; continue
  fi
  : > "$SI_LOG"
  LASTCMD="$sh: claude --version with claude only in ~/.local/bin"
  OUT="$(PATH="/usr/bin:/bin" HOME="$WORK/fallbackhome" ARGV_LOG="$SI_LOG" "$sh" -c "source '$SI'; claude --version" 2>&1)"; RC=$?
  readf "$SI_LOG"
  has "shell-init/$sh/falls-back-to-local-bin" \
      "with claude off PATH the wrapper still finds ~/.local/bin/claude — the fallback only runs if resolution ignored the function of the same name" \
      "$(argseq --version)"

  LASTCMD="$sh: claude --version with no binary anywhere"
  OUT="$(PATH="/usr/bin:/bin" HOME="$WORK/nobinhome" "$sh" -c "source '$SI'; claude --version" 2>&1)"; RC=$?
  has "shell-init/$sh/no-binary-says-so" "with no claude anywhere the wrapper says so rather than failing obscurely" \
      "no claude binary on PATH"
  eq  "shell-init/$sh/no-binary-returns-127" "and returns 127, the shell's own not-found status" "$RC" "127"
done

# --- interception, and the three ways it must NOT happen ---------------------
# These need a controlling terminal: the wrapper passes through whenever stdin or stdout
# is not a TTY, so without a pty every check below would pass for the wrong reason.
WRAP="$WORK/repos/wrapper-repo"
mkdir -p "$WRAP/cloud-sessions/bin"
cp "$CS_REAL" "$WRAP/cloud-sessions/bin/cs"; chmod +x "$WRAP/cloud-sessions/bin/cs"
git init -q -b main "$WRAP" >/dev/null
git -C "$WRAP" add -A >/dev/null 2>&1
git -C "$WRAP" commit -q -m "repo with a cs to route to"
NOCS_REPO="$(new_repo wrapper-without-cs)"

if [[ "$PTY_OK" != yes ]]; then
  for n in intercepts-a-bare-interactive-claude no-chooser-inside-a-session \
           passes-through-inside-a-session no-chooser-outside-a-repo no-chooser-without-cs; do
    _skip "shell-init/$n" "no usable pty here: fixtures/pty-drive.py could not drive /bin/echo"
  done
else
  # The positive control. Every "does not intercept" check below is vacuous if the wrapper
  # never intercepts anything at all.
  cd "$WRAP"; : > "$SI_LOG"
  ptyrun "[1/2/3]" "1$NL" 20 -- env "PATH=$SHPATH" "ARGV_LOG=$SI_LOG" "CS_CLAUDE_BIN=$NEW_STUB" \
         bash -c "source '$SI'; claude"
  has "shell-init/intercepts-a-bare-interactive-claude" \
      "inside the repo, a bare interactive \`claude\` reaches the chooser instead of starting a session" \
      "where should this run?"

  cd "$WRAP"; : > "$SI_LOG"
  ptyrun "" "" 20 -- env "PATH=$SHPATH" "ARGV_LOG=$SI_LOG" "CS_CLAUDE_BIN=$NEW_STUB" \
         "CLAUDE_CODE_SESSION_ID=session_01AlreadyInside" bash -c "source '$SI'; claude"
  hasnt "shell-init/no-chooser-inside-a-session" \
        "with CLAUDE_CODE_SESSION_ID set the wrapper does not ask — a session would be asking itself where to run" \
        "where should this run?"
  readf "$SI_LOG"
  eq "shell-init/passes-through-inside-a-session" \
     "and the bare claude still reaches the binary with no arguments added" "$(argflat "$SI_LOG")" "<end/>"

  cd "$WORK/notarepo"; : > "$SI_LOG"
  ptyrun "" "" 20 -- env "PATH=$SHPATH" "ARGV_LOG=$SI_LOG" "CS_CLAUDE_BIN=$NEW_STUB" \
         bash -c "source '$SI'; claude"
  hasnt "shell-init/no-chooser-outside-a-repo" \
        "outside a git repository there is no cs to route to, so it passes through" "where should this run?"

  cd "$NOCS_REPO"; : > "$SI_LOG"
  ptyrun "" "" 20 -- env "PATH=$SHPATH" "ARGV_LOG=$SI_LOG" "CS_CLAUDE_BIN=$NEW_STUB" \
         bash -c "source '$SI'; claude"
  hasnt "shell-init/no-chooser-without-cs" \
        "in a repo that has no cloud-sessions/bin/cs the wrapper passes through rather than erroring" \
        "where should this run?"
fi
cd "$REPO_MAIN"

# =============================================================================
sec "9 — git-gate states the suite did not reach"
# =============================================================================
# Section 4 walks the gate's own branches. These are repository SHAPES that reach the same
# code differently: an origin that is not an https URL, a checkout whose .git is not a
# directory, an upstream that was configured and then vanished, and a repo whose contents
# are partly another repo. Each check's description says whether the correct answer is a
# refusal or a dispatch, because for three of the four it is a dispatch and "it refused"
# would be just as wrong an outcome as "it dispatched" is for the fourth.

# --- an scp-style SSH origin: git@github.com:owner/repo.git ------------------
GATE_SSH="$(new_repo gate-ssh-origin)"
attach_origin "$GATE_SSH" "git@github.com:example/gate-ssh.git"
cd "$GATE_SSH"; logs_reset; ledger_reset
LASTCMD="git remote get-url origin"; RC=0; OUT="$(git remote get-url origin)"
# Some environments rewrite GitHub URLs for every git invocation. The Anthropic cloud VM
# does exactly this: its agent proxy injects GIT_CONFIG_COUNT / GIT_CONFIG_KEY_* with
# `url.https://github.com/.insteadOf git@github.com:`, so an scp-style origin reads back as
# https and this self-check fails through no fault of the gate. Measured on a cloud VM
# 2026-08-16; the three checks BELOW, which test the gate's actual behaviour, passed there.
# Skip loudly rather than assert a string the environment controls — and rather than delete
# the check, which is what stops the ones below being vacuous.
if [[ "$OUT" == "git@github.com:example/gate-ssh.git" ]]; then
  has "gate/ssh-origin-fixture-really-is-ssh" "the fixture's origin really is the scp-style SSH form (else the check below is vacuous)" \
      "git@github.com:example/gate-ssh.git"
else
  _skip "gate/ssh-origin-fixture-really-is-ssh" \
        "this environment rewrites GitHub URLs (got '$OUT'); the fixture cannot hold an scp-style origin here"
fi
trun "$CS" start "ssh origin"
eq      "gate/ssh-origin-proceeds" \
        "PROCEEDS: an scp-style SSH origin is recognised as GitHub and dispatches — the VM clones over the account's own grant, so the local URL form is not the deciding fact" \
        "$(ledger_field id)" "session_01ViewLineWins"
rc_zero "gate/ssh-origin-exit-zero" "and it exits 0"
hasnt   "gate/ssh-origin-not-mistaken-for-non-github" "the SSH form is never mistaken for a non-GitHub origin" "origin is not GitHub"

# --- a linked worktree, where .git is a FILE and not a directory -------------
GATE_WT="$(new_repo gate-worktree)"
attach_origin "$GATE_WT"
WTDIR="$WORK/repos/gate-worktree-linked"
git -C "$GATE_WT" worktree add -q -b wt-branch "$WTDIR" >/dev/null 2>&1
git -C "$WTDIR" push -q -u origin wt-branch >/dev/null 2>&1
git -C "$GATE_WT" remote set-url origin "https://github.com/example/gate-worktree.git"
LASTCMD="test -f $WTDIR/.git"; RC=0
OUT="$( [[ -f "$WTDIR/.git" ]] && echo "file" || echo "directory" )"
eq "gate/worktree-fixture-has-a-git-file" "the fixture is a real linked worktree: its .git is a FILE, not a directory" \
   "$OUT" "file"
cd "$WTDIR"; logs_reset; ledger_reset
trun "$CS" start "worktree dispatch"
eq      "gate/worktree-proceeds" \
        "PROCEEDS: a linked worktree passes every gate — rev-parse resolves through the .git file exactly as in a normal checkout" \
        "$(ledger_field id)" "session_01ViewLineWins"
rc_zero "gate/worktree-exit-zero" "and it exits 0"
eq "gate/worktree-records-its-own-branch" "the ledger records the worktree's branch, not the main checkout's" \
   "$(ledger_field branch)" "wt-branch"
eq "gate/worktree-records-the-worktree-dir-as-repo" \
   "the repo column is the worktree's directory name — cs names the checkout it ran in, which for a worktree is not the repository name" \
   "$(ledger_field repo)" "gate-worktree-linked"

# --- an upstream that was configured and then deleted ------------------------
# The branch still has branch.<name>.remote and .merge set, but refs/remotes/origin/<name>
# is gone — what a `git push --delete` on the remote plus a fetch --prune leaves behind.
GATE_GONE="$(new_repo gate-upstream-deleted)"
attach_origin "$GATE_GONE" "https://github.com/example/gone.git"
git -C "$GATE_GONE" update-ref -d refs/remotes/origin/main
LASTCMD="git config --get branch.main.remote"; RC=0; OUT="$(git -C "$GATE_GONE" config --get branch.main.remote)"
has "gate/deleted-upstream-fixture-still-configured" \
    "the fixture still has the upstream CONFIGURED — only the ref is gone, else this is just the no-upstream case again" "origin"
cd "$GATE_GONE"; logs_reset; ledger_reset
trun "$CS" start "x"
has     "gate/deleted-upstream-refused" \
        "REFUSES: an upstream whose ref has been deleted is reported as no upstream, with the push command to fix it — not as a git error" \
        "has no upstream"
rc_nonz "gate/deleted-upstream-nonzero" "and it exits non-zero"
readf "$FAKE_LOG"
eq "gate/deleted-upstream-dispatched-nothing" "the refusal happens before the CLI is invoked" \
   "$( [[ -s "$FAKE_LOG" ]] && echo invoked || echo not-invoked )" "not-invoked"
trun "$CS" doctor
has     "gate/deleted-upstream-doctor-blocks" "doctor predicts that same refusal rather than reporting the branch as fine" "no upstream for 'main'"
rc_nonz "gate/deleted-upstream-doctor-nonzero" "and doctor exits non-zero"
logs_reset; ledger_reset
trun "$CS" start --bundle "x"
eq "gate/deleted-upstream-bundle-dispatches" "--bundle skips the upstream checks, so it still dispatches" \
   "$(ledger_field mode)" "bundle"

# --- a repository with a submodule -------------------------------------------
SUB_BARE="$WORK/remotes/gate-sub.git"
git init -q --bare "$SUB_BARE" >/dev/null
git -C "$SUB_BARE" symbolic-ref HEAD refs/heads/main
SUBW="$WORK/repos/gate-sub-work"; mkdir -p "$SUBW"
git init -q -b main "$SUBW" >/dev/null
git -C "$SUBW" commit -q --allow-empty -m "submodule base"
git -C "$SUBW" remote add origin "$SUB_BARE"
git -C "$SUBW" push -q -u origin main
SUPER="$(new_repo gate-superproject)"
git -C "$SUPER" -c protocol.file.allow=always submodule add -q "$SUB_BARE" sub >/dev/null 2>&1
git -C "$SUPER" commit -q -m "add submodule"
attach_origin "$SUPER" "https://github.com/example/super.git"
LASTCMD="test -e $SUPER/sub/.git"; RC=0
OUT="$( [[ -e "$SUPER/sub/.git" ]] && echo present || echo missing )"
eq "gate/submodule-fixture-is-checked-out" "the fixture's submodule really is checked out (else the checks below measure an empty directory)" \
   "$OUT" "present"
cd "$SUPER"; logs_reset; ledger_reset
trun "$CS" start "superproject dispatch"
eq      "gate/submodule-clean-proceeds" \
        "PROCEEDS: a superproject whose submodule is at its recorded commit is clean and dispatches" \
        "$(ledger_field id)" "session_01ViewLineWins"
rc_zero "gate/submodule-clean-exit-zero" "and it exits 0"

printf 'scratch\n' > "$SUPER/sub/untracked-inside-submodule.txt"
logs_reset; ledger_reset
trun "$CS" start "dirty submodule"
has     "gate/submodule-dirty-refused" \
        "REFUSES: an untracked file INSIDE the submodule surfaces as a dirty superproject, and is refused like any other uncommitted change — the VM clones origin and would not see it" \
        "uncommitted change(s)"
rc_nonz "gate/submodule-dirty-nonzero" "and it exits non-zero"
rm -f "$SUPER/sub/untracked-inside-submodule.txt"

git -C "$SUPER/sub" remote set-url origin "https://github.com/example/sub.git"
cd "$SUPER/sub"; logs_reset; ledger_reset
trun "$CS" start "from inside the submodule"
eq      "gate/inside-a-submodule-proceeds" \
        "PROCEEDS: run from inside a submodule, cs gates the SUBMODULE — its origin, its branch, its cleanliness — and not the superproject that contains it" \
        "$(ledger_field id)" "session_01ViewLineWins"
eq "gate/inside-a-submodule-records-the-submodule" "the ledger row names the submodule, which is the repo the VM would clone" \
   "$(ledger_field repo)" "sub"

# =============================================================================
sec "10 — idempotency (the read-only commands must stay read-only)"
# =============================================================================
# `cs help`, `cs ls`, `cs env` and `cs doctor` are documented in AGENT.md as "the safe
# four" — the commands you can run to inspect state without touching anything. That is a
# claim about side effects, and until now nothing checked it. Each command below runs
# twice: the output must be identical, and every file under $HOME and the fixture root
# must be byte-identical afterwards.

cd "$REPO_MAIN"; logs_reset
ledger_reset
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01IdemA","title":"first","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"
printf '%s\n' '{"ts":"2026-08-13T00:01:00-07:00","id":"session_01IdemB","title":"second","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"

# A PRISTINE home, not the shared sandbox one. Earlier sections have already run every
# command in here many times, so a side effect that creates a file would already exist by
# now and comparing before/after would show nothing changing. Measured: a deliberately
# injected `: > "$HOME/.claude/doctor-was-here"` in cmd_doctor passed against the shared
# $HOME and is caught against this one.
IDEM_HOME="$WORK/idemhome"
fresh_idem_home() { rm -rf "$IDEM_HOME"; mkdir -p "$IDEM_HOME/.claude"; }
idem_tree() { manifest "$IDEM_HOME" "$WORK/csroot"; }

fresh_idem_home
BEFORE_TREE="$(idem_tree)"
run env "HOME=$IDEM_HOME" "$CS" ls; LS_ONE="$OUT"
run env "HOME=$IDEM_HOME" "$CS" ls; LS_TWO="$OUT"
eq "idem/ls-output-identical" "cs ls twice prints exactly the same listing" "$LS_ONE" "$LS_TWO"
eq "idem/ls-changes-nothing-on-disk" "cs ls writes nothing: every file under a pristine \$HOME and the fixture root is byte-identical afterwards" \
   "$(idem_tree)" "$BEFORE_TREE"

fresh_idem_home
BEFORE_TREE="$(idem_tree)"
run env "HOME=$IDEM_HOME" "$CS" env; ENV_ONE="$OUT"
run env "HOME=$IDEM_HOME" "$CS" env; ENV_TWO="$OUT"
eq "idem/env-show-output-identical" "cs env twice prints exactly the same report" "$ENV_ONE" "$ENV_TWO"
eq "idem/env-show-changes-nothing-on-disk" "cs env show writes nothing" \
   "$(idem_tree)" "$BEFORE_TREE"
LASTCMD="test -e $IDEM_HOME/.claude/settings.json"; RC=0
OUT="$( [[ -e "$IDEM_HOME/.claude/settings.json" ]] && echo created || echo absent )"
eq "idem/env-show-does-not-create-settings" "reading the environment does not create a settings file that was not there" \
   "$OUT" "absent"

run env "HOME=$IDEM_HOME" "$CS" env set env_IdempotencyProbe
SET_SHA="$(sha "$IDEM_HOME/.claude/settings.json")"
run env "HOME=$IDEM_HOME" "$CS" env set env_IdempotencyProbe
eq "idem/env-set-same-id-twice-is-byte-identical" "setting the same environment id twice leaves settings.json byte-identical" \
   "$(sha "$IDEM_HOME/.claude/settings.json")" "$SET_SHA"
BEFORE_TREE="$(idem_tree)"
run env "HOME=$IDEM_HOME" "$CS" env; ENV_ONE="$OUT"
run env "HOME=$IDEM_HOME" "$CS" env; ENV_TWO="$OUT"
eq "idem/env-show-with-a-pin-output-identical" "cs env with something pinned is also stable across runs" "$ENV_ONE" "$ENV_TWO"
eq "idem/env-show-with-a-pin-changes-nothing" "and still writes nothing" \
   "$(idem_tree)" "$BEFORE_TREE"
run env "HOME=$IDEM_HOME" "$CS" env clear
run env "HOME=$IDEM_HOME" "$CS" env clear
rc_zero "idem/env-clear-twice-succeeds" "clearing an already-cleared pin succeeds rather than reporting a failure"

fresh_idem_home
BEFORE_TREE="$(idem_tree)"
run env "HOME=$IDEM_HOME" "$CS" doctor; DOC_ONE="$OUT"; DOC_RC_ONE="$RC"
run env "HOME=$IDEM_HOME" "$CS" doctor; DOC_TWO="$OUT"; DOC_RC_TWO="$RC"
eq "idem/doctor-output-identical" "cs doctor twice prints exactly the same report — it measures, it does not mutate" \
   "$DOC_ONE" "$DOC_TWO"
eq "idem/doctor-exit-identical" "and reports the same verdict both times" "$DOC_RC_ONE" "$DOC_RC_TWO"
eq "idem/doctor-changes-nothing-on-disk" "cs doctor writes nothing — it is the command AGENT.md sends you to when you must not dispatch, so a side effect there is a side effect nobody expects" \
   "$(idem_tree)" "$BEFORE_TREE"

fresh_idem_home
BEFORE_TREE="$(idem_tree)"
run env "HOME=$IDEM_HOME" "$CS" help; HELP_ONE="$OUT"
run env "HOME=$IDEM_HOME" "$CS" help; HELP_TWO="$OUT"
eq "idem/help-output-identical" "cs help twice prints exactly the same text" "$HELP_ONE" "$HELP_TWO"
eq "idem/help-changes-nothing-on-disk" "cs help writes nothing" "$(idem_tree)" "$BEFORE_TREE"

# `cs rm` is the one command that rewrites the ledger. Running it twice on the same id is
# the realistic mistake — a second attempt after the first scrolled past — and it must
# refuse without touching the file, because the alternative is a truncated ledger.
ledger_reset
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01RmOnce","title":"go","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"
printf '%s\n' '{"ts":"2026-08-13T00:01:00-07:00","id":"session_01RmKeep","title":"stay","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"
run "$CS" rm session_01RmOnce
has     "idem/rm-first-removes" "the first rm removes the row" "removed 1 entry"
rc_zero "idem/rm-first-exit-zero" "and exits 0"
AFTER_RM_SHA="$(sha "$LEDGER")"
run "$CS" rm session_01RmOnce
has     "idem/rm-twice-refuses" "removing an id that is already gone refuses, naming the id" \
        "no ledger entry with id session_01RmOnce"
rc_nonz "idem/rm-twice-nonzero" "and exits non-zero"
eq "idem/rm-twice-leaves-the-ledger-byte-identical" \
   "the refused second rm did not rewrite, truncate or reorder the ledger" \
   "$(sha "$LEDGER")" "$AFTER_RM_SHA"
run "$CS" ls
has "idem/rm-twice-ledger-still-readable" "and the surviving row still lists normally afterwards" "session_01RmKeep"
rc_zero "idem/rm-twice-ls-exit-zero" "with cs ls still exiting 0"

# The check above cannot tell "refused and touched nothing" from "rewrote the file to
# identical bytes", because the first rm already rewrote it into canonical JSON. This one
# can: the ledger is hand-written with no spaces after the separators, which is not what
# `json.dumps` emits, so ANY rewrite changes the bytes.
ledger_reset
printf '%s\n' '{"ts":"2026-08-13T00:00:00-07:00","id":"session_01Untouched","title":"stay","repo":"-","branch":"b","mode":"clone","url":"u"}' >> "$LEDGER"
PRISTINE_SHA="$(sha "$LEDGER")"
run "$CS" rm session_01NeverRecorded
has     "idem/rm-absent-id-refuses" "removing an id that was never recorded refuses" "no ledger entry with id"
rc_nonz "idem/rm-absent-id-nonzero" "and exits non-zero"
eq "idem/rm-absent-id-does-not-rewrite-the-ledger" \
   "a refused rm does not rewrite the ledger at all — not even to re-serialise the rows it would have kept" \
   "$(sha "$LEDGER")" "$PRISTINE_SHA"

# =============================================================================
sec "12 — cs board: the master view (four sources, each degraded loudly)"
# =============================================================================
# `cs board` anchors on the repository its OWN copy is installed in (ROOT/..),
# so it is exercised from a cs INSTALLED in a fixture repo — the sandbox copy at
# $WORK/csroot would anchor on $WORK, which is not a repo. `cs sessions` (source
# b) shells out to `claude agents --json`; fixtures/fake-claude does not speak
# that verb, which is exactly what the degraded-sessions check uses. A second
# stub that does speak it drives the healthy path.

BR="$WORK/repos/board-fix"
mkdir -p "$BR/cloud-sessions/bin" "$BR/cloud-sessions/state" "$BR/atlas-os/heartbeat"
cp "$CS_REAL" "$BR/cloud-sessions/bin/cs"; chmod +x "$BR/cloud-sessions/bin/cs"
BCS="$BR/cloud-sessions/bin/cs"
if cmp -s "$CS_REAL" "$BCS"; then _pass "board/fixture-cs-is-identical" "the installed fixture cs is a byte-identical copy of bin/cs"
else LASTCMD="cmp $CS_REAL $BCS"; OUT=""; RC=1
     _fail "board/fixture-cs-is-identical" "the installed fixture cs is a byte-identical copy of bin/cs" "the copy differs"; fi

NOWTS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
# lane-seen: live and local. lane-unseen: on the board only. lane-old: ACTIVE but
# months stale, with a ledger row -> where=cloud. lane-done: ENDED, must vanish.
# The extra `flavour` column is the tolerant-parse probe: unknown columns are
# kept, never required, never fatal.
cat > "$BR/atlas-os/heartbeat/STATE.md" <<BOARDFIX
## Active Sessions

| session | goal | owns_paths | risk | status | last_update | flavour |
| --- | --- | --- | --- | --- | --- | --- |
| lane-seen | doing seen work | \`src/seen/**\` | low | ACTIVE | $NOWTS | vanilla |
| lane-unseen | elsewhere | \`src/unseen/**\` | low | ACTIVE | 2026-08-17T22:00:00Z | mint |
| lane-old | forgotten | \`src/old/**\` | low | ACTIVE | 2026-01-01T00:00:00Z | plain |
| lane-done | finished | \`src/done/**\` | low | ENDED | 2026-08-01T00:00:00Z | done |
BOARDFIX
printf '%s\n' '{"ts":"2026-08-15T00:00:00-07:00","id":"cse_01LaneOldMoved","title":"handoff: lane-old","repo":"board-fix","branch":"claude/lane-old","mode":"tracked","url":"u","lane":"lane-old"}' \
  > "$BR/cloud-sessions/state/sessions.jsonl"
git -C "$BR" init -q -b main >/dev/null; git -C "$BR" add -A >/dev/null 2>&1; git -C "$BR" commit -q -m base

cat > "$WORK/stub/agents-claude" <<AGENTSSTUB
#!/bin/bash
if [ "\$1" = "agents" ]; then
  printf '[{"sessionId":"lane-seen","cwd":"$BR","kind":"tui"},{"sessionId":"lane-unreg","cwd":"$BR","kind":"tui"}]\n'
  exit 0
fi
exit 1
AGENTSSTUB
chmod +x "$WORK/stub/agents-claude"
ACL="$WORK/stub/agents-claude"

BR_BOARD_SHA="$(sha "$BR/atlas-os/heartbeat/STATE.md")"
run env "CS_CLAUDE_BIN=$ACL" "$BCS" board
BOUT="$OUT"
rc_zero "board/renders-exit-zero" "with all four sources healthy, cs board exits 0"
has "board/source-board-ok"     "the board source reports its row counts"        "4 row(s), 3 live"
has "board/source-sessions-ok"  "the sessions source reports the local sessions" "2 local session(s)"
has "board/source-ledger-ok"    "the ledger source reports its rows"             "1 row(s)"
hasnt "board/no-degraded-when-healthy" "no source reads DEGRADED when all four answered" "DEGRADED"
hasnt "board/ended-row-released" "an ENDED board row does not render as live" "lane-done"
has "board/unknown-column-tolerated" \
    "a board with an unknown extra column still parses every row (3 live of 4 proves none was dropped)" \
    "lane-old"
has "board/unregistered-called-out" \
    "a live local session with no board row is called out as the gap auto-registration closes" \
    "UNREGISTERED"
has "board/unregistered-names-the-session" "and named" "lane-unreg"
has "board/unregistered-names-the-fix" "with the register command to close it" "._presence/<id>"
LASTCMD="grep ^lane-seen <board output>"; RC=0; OUT="$(printf '%s\n' "$BOUT" | grep '^lane-seen' || true)"
has "board/merges-triage-onto-board-row" "a board lane whose session id is live locally shows where=local" "local"
LASTCMD="grep ^lane-unseen <board output>"; RC=0; OUT="$(printf '%s\n' "$BOUT" | grep '^lane-unseen' || true)"
has "board/board-only-lane-is-unseen" "a board lane with no local process and no ledger row shows unseen" "unseen"
LASTCMD="grep ^lane-old <board output>"; RC=0; OUT="$(printf '%s\n' "$BOUT" | grep '^lane-old' || true)"
has "board/ledger-match-shows-cloud" "a board lane matched by a dispatch-ledger row shows where=cloud" "cloud"
eq "board/read-only" "rendering the board writes nothing to the board file" \
   "$(sha "$BR/atlas-os/heartbeat/STATE.md")" "$BR_BOARD_SHA"

run env "CS_CLAUDE_BIN=$ACL" "CLAUDE_CODE_SESSION_ID=lane-seen" "$BCS" board
has "board/self-is-starred" "the running session's own lane is marked with *" "lane-seen*"

run env "CS_CLAUDE_BIN=$ACL" "$BCS" board --json
rc_zero "board/json-exit-zero" "--json exits 0 while the board source is ok"
JOUT="$OUT"
LASTCMD="cs board --json | python3 json.load"
OUT="$(printf '%s' "$JOUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
rows = {r["lane"]: r for r in d["rows"] if r["kind"] == "board"}
print("VALID-JSON")
print("stale-old=%s" % rows["lane-old"]["stale"])
print("stale-seen=%s" % rows["lane-seen"]["stale"])
print("unseen-list=%s" % ",".join(d["board_unseen"]))
print("board-src=%s" % d["sources"]["board"]["status"])
print("unregistered=%s" % ",".join(r["session_id"] for r in d["unregistered"]))
' 2>&1)"; RC=$?
has "board/json-is-valid-json" "--json emits parseable JSON" "VALID-JSON"
has "board/json-stale-lease-marked" "an ACTIVE row older than the 30m lease carries stale=true — a claim, not proof" "stale-old=True"
has "board/json-fresh-row-not-stale" "a row inside the lease is not stale (else the flag is decoration)" "stale-seen=False"
has "board/json-unseen-list" "board rows unseen locally are listed for machines too" "unseen-list=lane-unseen"
has "board/json-unregistered-list" "the unregistered local session appears in the JSON" "unregistered=lane-unreg"

# --- each source degraded, loudly and independently --------------------------
mv "$BR/atlas-os/heartbeat/STATE.md" "$BR/atlas-os/heartbeat/STATE.md.aside"
run env "CS_CLAUDE_BIN=$ACL" "$BCS" board
has     "board/missing-board-is-degraded" "a missing board file renders as DEGRADED, never as an empty board" "DEGRADED"
has     "board/degraded-view-is-partial" "and the view says it is partial, not empty" "PARTIAL, not empty"
rc_nonz "board/missing-board-nonzero" "a degraded board source exits non-zero"
hasnt   "board/degraded-board-claims-no-coverage" \
        "with the board down, cs board never claims every session is on it" "every live local session is on the board"
run env "CS_CLAUDE_BIN=$ACL" "$BCS" board --json
rc_nonz "board/missing-board-json-nonzero" "--json exits non-zero too"
mv "$BR/atlas-os/heartbeat/STATE.md.aside" "$BR/atlas-os/heartbeat/STATE.md"

run env "CS_CLAUDE_BIN=$WORK/stub/claude" "$BCS" board
rc_zero "board/degraded-sessions-board-still-renders" "with only the sessions source down, the board source still answers and exit stays 0"
has "board/degraded-sessions-is-loud" "the sessions source is marked DEGRADED" "DEGRADED"
has "board/degraded-sessions-no-liveness-verdict" \
    "board rows are NOT called dead when liveness could not be determined — a skipped check is not a passed check" \
    "liveness of board rows NOT determined"
hasnt "board/degraded-sessions-no-dead-list" "the 'not seen on this machine' list is withheld, not emptied" "on the board but not seen"

run "$CS" help
has "help/lists-board"  "cs help lists the board command"  "cs board"
has "help/lists-exodus" "cs help lists the exodus command" "cs exodus"

# =============================================================================
sec "13 — cs exodus --dry-run: the partition IS the board's claims"
# =============================================================================
# A throwaway repo with a symlinked-root heartbeat fixture, per
# atlas-os/tests/heartbeat/README.md: heartbeat.py and worktree.py resolve their
# board relative to their own (symlinked, unresolved) path, so the REAL scripts
# run against a sandbox board and the real board never moves (section 17 proves
# it by hash). Three lanes registered through the real register (so the claims
# are exactly what the real gate admits), a dirty tree split across them, two
# unclaimed files, and — manufactured directly on the fixture board, because the
# real gate refuses to create one — a double claim.

HB_REAL="$REPO_REAL/atlas-os/bin/heartbeat.py"
WT_REAL="$REPO_REAL/atlas-os/bin/worktree.py"
BOARD_TMPL="$REPO_REAL/atlas-os/tests/heartbeat/lib/board.md"
EXODUS_OK=yes
for f in "$HB_REAL" "$WT_REAL" "$BOARD_TMPL"; do [[ -f "$f" ]] || EXODUS_OK=no; done

# `same` is the one comparator both the partition check and its negative control
# run through — so the control genuinely proves THE CHECK can go red, not some
# lookalike. Used by section 14's writes-nothing detector too.
same() { [ "$1" = "$2" ]; }
# file rows are the 5-space-indented lines INSIDE a unit block; goal lines are
# excluded by name, and the lane resets on any non-indented line so trailing
# hints (the "then /cloud all" line is indented too) can never be mistaken for
# files. Output: "<lane> <path>" sorted, one per file.
ex_partition() { printf '%s\n' "$1" | awk '/^unit /{lane=$2; next} !/^     /{lane=""; next} /^     goal:/{next} lane != ""{print lane, $NF}' | LC_ALL=C sort; }

if [[ "$EXODUS_OK" != yes ]]; then
  _skip "exodus/ALL" "atlas-os heartbeat.py / worktree.py / board template not found next to this checkout — the exodus fixture cannot be built (62 checks not run: sections 13-14 in full, see G24)"
  COLLAPSED=$((COLLAPSED+62))
else
  EXR="$WORK/repos/exodus-fix"
  mkdir -p "$EXR/atlas-os/bin" "$EXR/atlas-os/heartbeat" "$EXR/src/alpha" "$EXR/src/beta" "$EXR/docs" "$EXR/tools/gamma" "$EXR/notes"
  ln -s "$HB_REAL" "$EXR/atlas-os/bin/heartbeat.py"
  ln -s "$WT_REAL" "$EXR/atlas-os/bin/worktree.py"
  cp "$BOARD_TMPL" "$EXR/atlas-os/heartbeat/STATE.md"
  printf '__pycache__/\n' > "$EXR/.gitignore"   # worktree.py imports heartbeat.py by path; the bytecode cache must not read as dirt
  printf 'one\n' > "$EXR/src/alpha/one.txt"
  printf 'b1\n'  > "$EXR/src/beta/b1.txt"
  printf 'beta\n' > "$EXR/docs/beta.md"
  printf 'g\n'   > "$EXR/tools/gamma/g.sh"
  git -C "$EXR" init -q -b main >/dev/null
  git -C "$EXR" add -A >/dev/null 2>&1; git -C "$EXR" commit -q -m base
  git init -q --bare "$WORK/remotes/exodus-fix.git" >/dev/null
  git -C "$EXR" remote add origin "$WORK/remotes/exodus-fix.git"
  git -C "$EXR" push -q -u origin main

  cd "$EXR"
  run python3 atlas-os/bin/heartbeat.py register --session lane-alpha --goal "alpha work" --owns 'src/alpha/**' --risk low
  rc_zero "exodus/fixture-lane-alpha-registered" "lane-alpha registered through the REAL heartbeat.py against the sandbox board"
  run python3 atlas-os/bin/heartbeat.py register --session lane-beta --goal "beta work" --owns 'src/beta/** docs/beta.md' --risk low
  rc_zero "exodus/fixture-lane-beta-registered" "lane-beta registered (two claims, one a single file)"
  run python3 atlas-os/bin/heartbeat.py register --session lane-gamma --goal "gamma work" --owns 'tools/gamma/**' --risk low
  rc_zero "exodus/fixture-lane-gamma-registered" "lane-gamma registered"
  eq "exodus/fixture-board-is-the-sandbox" \
     "the registrations landed on the SANDBOX board — three ACTIVE rows there, and section 17 holds the real board's hash" \
     "$(grep -c '| ACTIVE |' "$EXR/atlas-os/heartbeat/STATE.md")" "3"
  git -C "$EXR" add -A >/dev/null 2>&1; git -C "$EXR" commit -q -m "board state"

  printf 'changed\n' >> "$EXR/src/alpha/one.txt"
  printf 'new\n'      > "$EXR/src/alpha/new.txt"
  printf 'changed\n' >> "$EXR/src/beta/b1.txt"
  printf 'changed\n' >> "$EXR/docs/beta.md"
  printf 'changed\n' >> "$EXR/tools/gamma/g.sh"
  printf 'o1\n' > "$EXR/notes/orphan1.md"
  printf 'o2\n' > "$EXR/orphan2.txt"
  eq "exodus/fixture-really-dirty" "the fixture tree really carries 7 dirty paths (an empty fixture passes forever while proving nothing)" \
     "$(git -C "$EXR" status --porcelain -uall | grep -c .)" "7"

  # what "wrote nothing" means for a dry run: the tree's porcelain status (with
  # -uall, so a file appearing inside an untracked directory cannot hide), the
  # default manifest path, and the board file, all byte-stable.
  ex_snap() {
    git -C "$EXR" status --porcelain -uall | LC_ALL=C sort
    [ -e "$WORK/csroot/state/exodus-manifest.json" ] && echo "MANIFEST-PRESENT"
    sha "$EXR/atlas-os/heartbeat/STATE.md"
  }
  SNAP_BEFORE="$(ex_snap)"

  run "$CS" exodus --dry-run
  rc_zero "exodus/dry-run-exit-zero" "a clean partition dry-runs with exit 0"
  EXOUT="$OUT"
  has "exodus/dry-run-says-plan-only" "the dry run says nothing was created, committed, or pushed" "plan only"
  has "exodus/dry-run-warns-no-self" \
      "with CLAUDE_CODE_SESSION_ID unset, exodus says NO lane was excluded rather than silently excluding none" \
      "NO lane was excluded"

  ACT="$(ex_partition "$EXOUT")"
  EXPECTED="$(printf 'lane-alpha src/alpha/new.txt\nlane-alpha src/alpha/one.txt\nlane-beta docs/beta.md\nlane-beta src/beta/b1.txt\nlane-gamma tools/gamma/g.sh\n' | LC_ALL=C sort)"
  eq "exodus/partition-is-nonempty" "the extracted partition has exactly 5 lane->file assignments (guards the checker against matching nothing)" \
     "$(printf '%s\n' "$ACT" | grep -c .)" "5"
  LASTCMD='same "$(ex_partition <dry-run output>)" "$EXPECTED"'; OUT="$ACT"
  run same "$ACT" "$EXPECTED"
  rc_zero "exodus/partition-exact-file-by-file" \
          "the partition assigns every dirty file to exactly the lane whose claim covers it — file by file, no more, no less"

  # NEGATIVE CONTROL: the same comparator, fed a deliberately wrong expected set
  # (gamma's file reassigned to alpha), must go red. A partition checker that
  # cannot fail is worth nothing.
  WRONG="$(printf 'lane-alpha src/alpha/new.txt\nlane-alpha src/alpha/one.txt\nlane-alpha tools/gamma/g.sh\nlane-beta docs/beta.md\nlane-beta src/beta/b1.txt\n' | LC_ALL=C sort)"
  run same "$ACT" "$WRONG"
  rc_nonz "nc/partition-check-can-go-red" \
          "negative control: the partition comparator rejects a wrong expected set — the green above is a measurement, not a formality"

  OUT="$EXOUT"; LASTCMD="cs exodus --dry-run"
  has "exodus/unclaimed-listed[orphan1]" "an unclaimed dirty file is listed by name" "?? notes/orphan1.md"
  has "exodus/unclaimed-listed[orphan2]" "both of them" "?? orphan2.txt"
  has "exodus/unclaimed-stay-behind" "and unclaimed files stay behind pending an explicit human claim-widening on the BOARD" "widen a claim on the board"
  hasnt "exodus/unclaimed-never-in-a-unit" "no unclaimed file was folded into any unit" "     ?? orphan2.txt"

  SNAP_AFTER="$(ex_snap)"
  LASTCMD='same "$SNAP_BEFORE" "$SNAP_AFTER"'; OUT="$SNAP_AFTER"
  run same "$SNAP_BEFORE" "$SNAP_AFTER"
  rc_zero "exodus/dry-run-writes-nothing" \
          "--dry-run writes NOTHING: porcelain status identical, no manifest at the default path, board hash unchanged"

  # NEGATIVE CONTROL: prove that detector can fail — a file created inside the
  # guarded window must flip it, and removing the file must calm it again.
  : > "$EXR/injected-by-negative-control.txt"
  run same "$SNAP_BEFORE" "$(ex_snap)"
  rc_nonz "nc/writes-nothing-detector-can-go-red" \
          "negative control: a file touched inside the guarded window is detected by the same comparison"
  rm -f "$EXR/injected-by-negative-control.txt"
  run same "$SNAP_BEFORE" "$(ex_snap)"
  rc_zero "nc/writes-nothing-detector-recovers" "and the detector reads clean again once the injection is removed"

  # a lane belonging to the session RUNNING exodus is excluded, loudly
  run env "CLAUDE_CODE_SESSION_ID=lane-alpha" "$CS" exodus --dry-run
  has "exodus/self-lane-excluded" "the running session's own lane is excluded and its files reported as held by THIS session" "held by THIS session"
  eq  "exodus/self-files-not-in-any-unit" "and its files appear in no unit" \
      "$(ex_partition "$OUT" | grep -c '^lane-alpha ' )" "0"

  # --- CONFLICT: a double claim is a hard refusal ------------------------------
  # The real gate refuses to create overlap, so the double claim is written onto
  # the FIXTURE board directly — which is the realistic shape too: a hand-edited
  # or merge-damaged board is exactly what this refusal exists to catch.
  awk '/^\| lane-gamma /{print; print "| lane-delta | overlap probe | `src/alpha/**` | low | ACTIVE | 2026-08-17T22:00:00Z |"; next}1' \
      "$EXR/atlas-os/heartbeat/STATE.md" > "$EXR/atlas-os/heartbeat/.conflict.tmp" \
    && mv "$EXR/atlas-os/heartbeat/.conflict.tmp" "$EXR/atlas-os/heartbeat/STATE.md"
  eq "exodus/conflict-fixture-really-overlaps" "the fixture board really carries the second claimant's row (else the refusal below is vacuous)" \
     "$(grep -c '^| lane-delta ' "$EXR/atlas-os/heartbeat/STATE.md")" "1"
  run "$CS" exodus --dry-run
  rc_nonz "exodus/conflict-is-a-hard-refusal" "a file claimed by two live sessions refuses the whole partition"
  has "exodus/conflict-banner" "the refusal is labeled CONFLICT" "CONFLICT"
  has "exodus/conflict-names-the-path" "and names the contended path" "src/alpha/one.txt"
  has "exodus/conflict-names-both-holders" "and both holders" "held by lane-delta"
  has "exodus/conflict-names-the-fix" "and sends the operator to the board's own validator, never to a conversation" "heartbeat.py validate"
  eq  "exodus/conflict-dispatched-no-units" "no unit block is printed on the way to the refusal being acted on — the manifest path stays empty" \
      "$( [[ -e "$WORK/csroot/state/exodus-manifest.json" ]] && echo present || echo absent )" "absent"
  git -C "$EXR" checkout -q -- atlas-os/heartbeat/STATE.md

  # --- the refusals around the partition --------------------------------------
  mv "$EXR/atlas-os/heartbeat/STATE.md" "$EXR/atlas-os/heartbeat/STATE.md.aside"
  run "$CS" exodus --dry-run
  has     "exodus/no-board-refused" "without the board there is no safe partition, and exodus says so" "board not found"
  rc_nonz "exodus/no-board-nonzero"
  mv "$EXR/atlas-os/heartbeat/STATE.md.aside" "$EXR/atlas-os/heartbeat/STATE.md"

  cd "$WORK/notarepo"
  run "$CS" exodus --dry-run
  has     "exodus/outside-a-repo-refused" "outside a git repository there is no dirty tree to partition" "not inside a git repository"
  rc_nonz "exodus/outside-a-repo-nonzero"
  run "$CS" exodus --bogus
  has     "exodus/unknown-flag-refused" "an unknown argument is refused with usage" "unknown argument"
  rc_nonz "exodus/unknown-flag-nonzero"
  cd "$EXR"

# =============================================================================
sec "14 — cs exodus, executed: branches, rollback, and the manifest"
# =============================================================================
# The execute run is safe here for the same reason the dry run is: the fixture's
# origin is a bare repo inside $WORK, worktrees land in $WORK/exwt, and the
# manifest is pointed into $WORK. lane-beta's worktree path is squatted BEFORE
# the run, so the middle unit of three fails — proving both the rollback and
# that the batch continues past a failure rather than aborting the units behind
# it.

  EXMAN="$WORK/exodus-manifest-test.json"
  mkdir -p "$WORK/exwt/lane-beta"; printf 'squatter\n' > "$WORK/exwt/lane-beta/junk.txt"
  EX_STATUS_BEFORE="$(git -C "$EXR" status --porcelain -uall | LC_ALL=C sort)"

  run env "ATLAS_WORKTREE_ROOT=$WORK/exwt" "CLAUDE_CODE_SESSION_ID=lane-src-tester" \
      "$CS" exodus --manifest "$EXMAN"
  rc_nonz "exodus/exec-nonzero-when-a-unit-fails" "a batch with a failed unit exits non-zero"
  has "exodus/exec-two-ready" "two units built, branches pushed" "2 unit(s) ready"
  has "exodus/exec-one-failed" "one unit failed" "1 failed"
  has "exodus/exec-failure-names-the-refusal" "the failed unit carries worktree.py's own refusal text" "worktree.py create refused"
  has "exodus/exec-shared-tree-promise" "and the run states the shared tree was not modified" "the shared tree was NOT modified"

  eq "exodus/alpha-branch-exact-files" \
     "claude/lane-alpha differs from base by exactly lane-alpha's claimed dirty files" \
     "$(git -C "$EXR" diff --name-only main claude/lane-alpha 2>/dev/null | LC_ALL=C sort | tr '\n' ' ')" \
     "src/alpha/new.txt src/alpha/one.txt "
  eq "exodus/gamma-branch-exact-files" \
     "claude/lane-gamma differs from base by exactly lane-gamma's one file — the batch CONTINUED past the failed middle unit" \
     "$(git -C "$EXR" diff --name-only main claude/lane-gamma 2>/dev/null | LC_ALL=C sort | tr '\n' ' ')" \
     "tools/gamma/g.sh "
  run git -C "$EXR" show-ref --verify --quiet refs/heads/claude/lane-beta
  rc_nonz "exodus/failed-unit-left-no-branch" "the failed unit's claude/ branch does not exist"
  run git -C "$EXR" show-ref --verify --quiet refs/heads/lane-beta
  rc_nonz "exodus/failed-unit-left-no-scaffold-branch" "nor worktree.py's scaffold branch"
  LASTCMD="git ls-remote --heads origin"; RC=0; OUT="$(git -C "$EXR" ls-remote --heads origin 2>&1)"
  has   "exodus/alpha-branch-pushed" "claude/lane-alpha reached origin" "refs/heads/claude/lane-alpha"
  has   "exodus/gamma-branch-pushed" "claude/lane-gamma reached origin" "refs/heads/claude/lane-gamma"
  hasnt "exodus/beta-branch-not-pushed" "nothing was pushed for the failed unit" "refs/heads/claude/lane-beta"
  LASTCMD="git log -1 --format=%B claude/lane-alpha"; RC=0; OUT="$(git -C "$EXR" log -1 --format=%B claude/lane-alpha 2>&1)"
  has "exodus/commit-names-the-source-session" "the commit names the session whose tree the work came from" "session lane-src-tester"
  has "exodus/commit-names-the-lane" "and the lane" "exodus(lane-alpha)"
  has "exodus/commit-names-the-claim" "and the claim that authorised the move" "src/alpha/**"

  EX_STATUS_AFTER="$(git -C "$EXR" status --porcelain -uall | LC_ALL=C sort)"
  run same "$EX_STATUS_BEFORE" "$EX_STATUS_AFTER"
  rc_zero "exodus/exec-shared-tree-untouched" \
          "the shared dirty tree is byte-for-byte as dirty as before — the lanes' files' future is decided by their PRs, not by exodus"
  LASTCMD="git status --porcelain (after exec)"; RC=0; OUT="$EX_STATUS_AFTER"
  has "exodus/unclaimed-still-dirty[orphan1]" "the unclaimed files remain dirty in the main tree" "?? notes/orphan1.md"
  has "exodus/unclaimed-still-dirty[orphan2]" "both of them" "?? orphan2.txt"
  eq "exodus/scratch-worktrees-removed" "the scratch worktrees are gone — only the main checkout remains registered" \
     "$(git -C "$EXR" worktree list --porcelain | grep -c '^worktree ')" "1"

  LASTCMD="validate $EXMAN"; RC=0
  OUT="$(EXMAN="$EXMAN" python3 - <<'PY' 2>&1
import json, os
m = json.load(open(os.environ["EXMAN"]))
ok = []
assert m["exodus_version"] == 1, "exodus_version != 1"
ok.append("VERSION-1")
lanes = [u["lane"] for u in m["units"]]
assert lanes == ["lane-alpha", "lane-gamma"], "unexpected unit lanes: %s" % lanes
ok.append("UNITS=alpha,gamma")
for u in m["units"]:
    for k in ("lane", "source_session", "goal", "branch", "base_commit", "brief",
              "owns", "files", "pushed", "routine", "track"):
        assert k in u, "unit %s lacks %s" % (u["lane"], k)
    r = u["routine"]
    assert r["mcp_connections"] == [], "mcp_connections not the explicit empty list"
    assert "disable" in r["name"], "routine name does not say disable"
    assert u["branch"] in r["message"], "routine message does not name the branch"
    t = u["track"]
    for k in ("lane", "owns", "from", "brief", "expect_branch"):
        assert k in t, "track lacks %s" % k
ok.append("UNIT-SHAPE-OK")
ok.append("MCP-EXPLICIT-EMPTY")
assert [f["lane"] for f in m["failed_units"]] == ["lane-beta"], "failed_units wrong"
ok.append("FAILED=beta")
assert sorted(x["path"] for x in m["unclaimed"]) == ["notes/orphan1.md", "orphan2.txt"], "unclaimed wrong"
ok.append("UNCLAIMED-LISTED")
assert m["source_session"] == "lane-src-tester", "source_session wrong"
ok.append("SOURCE-SESSION")
alpha = m["units"][0]
assert sorted(f["path"] for f in alpha["files"]) == ["src/alpha/new.txt", "src/alpha/one.txt"], "alpha files wrong"
ok.append("FILES-MATCH-BRANCH")
print(" ".join(ok))
PY
)"; RC=$?
has "manifest/validates-version"       "the manifest carries exodus_version 1"                        "VERSION-1"
has "manifest/units-are-the-ready-ones" "its units are exactly the two lanes whose branches pushed"    "UNITS=alpha,gamma"
has "manifest/unit-shape"              "every unit carries lane, branch, owns, files, routine, track"  "UNIT-SHAPE-OK"
has "manifest/mcp-connections-explicit" "the routine payload pins mcp_connections to the EXPLICIT empty list — routines inherit every connector otherwise" "MCP-EXPLICIT-EMPTY"
has "manifest/failed-unit-recorded"    "the failed unit is on the record, not silently dropped"        "FAILED=beta"
has "manifest/unclaimed-recorded"      "the unclaimed files are on the record for /cloud all to name"  "UNCLAIMED-LISTED"
has "manifest/source-session-recorded" "provenance: the dispatching session is recorded"               "SOURCE-SESSION"
has "manifest/files-match-the-branch"  "the manifest's file list agrees with what the branch carries"  "FILES-MATCH-BRANCH"

  run env "ATLAS_WORKTREE_ROOT=$WORK/exwt" "CLAUDE_CODE_SESSION_ID=lane-src-tester" \
      "$CS" exodus --manifest "$WORK/exodus-manifest-rerun.json"
  rc_nonz "exodus/rerun-refuses-existing-branches" "a second exodus does not silently re-commit onto branches a previous run pushed"
  has "exodus/rerun-names-the-branch-collision" "the per-unit refusal names the existing branch and demands a deliberate decision" "already exists"

  cd "$REPO_MAIN"
fi

# =============================================================================
sec "15 — the auto-register hook: observability must never break the observed"
# =============================================================================
# Same symlinked-throwaway-root technique: the hook walks up from its own
# BASH_SOURCE to find the repo, so a copy at <fixture>/cloud-sessions/bin/hooks/
# registers on the fixture's board and can never see the real one. Every path
# must exit 0 — a hook that fails holds the user's prompt hostage.

HOOKR="$WORK/repos/hook-fix"
if [[ "$EXODUS_OK" != yes || ! -f "$REAL_HOOK" ]]; then
  _skip "hook/ALL" "the hook or the atlas-os fixture inputs are missing — section 15 skipped in full (29 checks not run, see G24): $REAL_HOOK"
  COLLAPSED=$((COLLAPSED+29))
else
  mkdir -p "$HOOKR/cloud-sessions/bin/hooks" "$HOOKR/cloud-sessions/state" "$HOOKR/atlas-os/bin" "$HOOKR/atlas-os/heartbeat"
  cp "$REAL_HOOK" "$HOOKR/cloud-sessions/bin/hooks/session_register.sh"
  ln -s "$HB_REAL" "$HOOKR/atlas-os/bin/heartbeat.py"
  cp "$BOARD_TMPL" "$HOOKR/atlas-os/heartbeat/STATE.md"
  git -C "$HOOKR" init -q -b main >/dev/null; git -C "$HOOKR" add -A >/dev/null 2>&1; git -C "$HOOKR" commit -q -m base
  HOOK="$HOOKR/cloud-sessions/bin/hooks/session_register.sh"
  HBOARD="$HOOKR/atlas-os/heartbeat/STATE.md"
  HLOG="$HOOKR/cloud-sessions/state/hook-errors.log"

  if cmp -s "$REAL_HOOK" "$HOOK"; then _pass "hook/fixture-copy-is-identical" "the hook under test is a byte-identical copy of the staged one"
  else LASTCMD="cmp $REAL_HOOK $HOOK"; OUT=""; RC=1
       _fail "hook/fixture-copy-is-identical" "the hook under test is a byte-identical copy of the staged one" "the copy differs"; fi
  run bash -n "$REAL_HOOK"
  rc_zero "hook/parses-under-bash" "the staged hook is syntactically valid bash"

  # stdin is ALWAYS pinned: /dev/null for the empty case, a pipe for the garbage
  # case. Inheriting the suite's stdin would hand the hook's `head -c 8192`
  # whatever happens to be there.
  hook_null()    { ( cd "$HOOKR" && env "$@" bash "$HOOK" </dev/null ); }
  hook_garbage() { ( cd "$HOOKR" && printf '%%%% not json {{{' | env "$@" bash "$HOOK" ); }

  # The hook refuses to write as root, or under CLAUDE_CODE_REMOTE*, or when
  # $PWD is under /home/user* — session_register.sh:117-123, "cloud VM: skip,
  # harmless but useless". Those are exactly the conditions this suite itself
  # runs under on a cloud VM (root uid; CLAUDE_CODE_REMOTE* when the suite is
  # invoked from inside a real Claude Code Remote session, worked around above
  # by unsetting it; and this repo's own checkout path). VERIFIED 2026-08-18:
  # `id -u` alone is 0 here, so the hook can never write from this host no
  # matter what env is passed to it — every check below that reads the board
  # expecting a write would otherwise either FAIL (it does: 7 of them, spot
  # G24-adjacent) or, worse, vacuously PASS on a "no change" assertion that
  # cannot tell a correctly-guarded refusal from a hook that never even tried.
  # Probe once, honestly, rather than trust `id -u` alone — a future harness
  # might run unprivileged, or the hook's detection might change.
  HOOK_CAN_REGISTER=no
  ( cd "$HOOKR" && env CLAUDE_CODE_SESSION_ID=hook-probe-can-register bash "$HOOK" </dev/null ) >/dev/null 2>&1
  if grep -q '| hook-probe-can-register |' "$HBOARD" 2>/dev/null; then
    HOOK_CAN_REGISTER=yes
    cp "$BOARD_TMPL" "$HBOARD"   # undo the probe write; restore the pristine fixture board
  fi

  run hook_null CLAUDE_CODE_SESSION_ID=hooktest-fresh-01
  rc_zero "hook/fresh-register-exit-zero" "a fresh session registers and exits 0"
  readf "$HBOARD"
  if [[ "$HOOK_CAN_REGISTER" == yes ]]; then
    has "hook/fresh-register-row"        "the board gains a row keyed on the session id"          "| hooktest-fresh-01 |"
    has "hook/fresh-register-goal"       "its goal is marked as the hook's own"                   "auto-registered:"
    has "hook/fresh-register-sentinel"   "and its claim is the presence sentinel, never a real path" "._presence/hooktest-fresh-01"
    has "hook/fresh-register-worktree"   "the goal records which checkout the session is in"      "worktree=main"
  else
    for n in fresh-register-row fresh-register-goal fresh-register-sentinel fresh-register-worktree; do
      _skip "hook/$n" "this host is root (or otherwise cloud-VM-detected): session_register.sh:121 no-ops before it ever writes, so this check cannot measure real registration behaviour from here"
    done
  fi

  HSHA1="$(sha "$HBOARD")"
  run hook_null CLAUDE_CODE_SESSION_ID=hooktest-fresh-01
  rc_zero "hook/idempotent-exit-zero" "a second run for the same session exits 0"
  eq "hook/idempotent-board-unchanged" "and leaves the board byte-identical — the warm path is a read-only awk" \
     "$(sha "$HBOARD")" "$HSHA1"
  if [[ "$HOOK_CAN_REGISTER" == yes ]]; then
    eq "hook/idempotent-one-row" "exactly one row exists for the session" \
       "$(grep -c '^| hooktest-fresh-01 ' "$HBOARD")" "1"
  else
    _skip "hook/idempotent-one-row" "this host is root (or otherwise cloud-VM-detected): no row was ever written, so 'exactly one' cannot be measured from here"
  fi

  # warm-path latency: the hook fires on EVERY prompt once wired, so the
  # no-op path must stay under 300ms. Min of 3 so one scheduler hiccup cannot
  # flake the suite; if even the best of three misses, this machine is loaded
  # and the check SKIPS loudly with both numbers rather than lying either way.
  HMS="$(HOOKDIR="$HOOKR" HOOKSH="$HOOK" python3 - <<'PY' 2>/dev/null
import os, subprocess, time
best = None
env = dict(os.environ); env["CLAUDE_CODE_SESSION_ID"] = "hooktest-fresh-01"
for _ in range(3):
    t = time.time()
    subprocess.run(["bash", env["HOOKSH"]], stdin=subprocess.DEVNULL, cwd=env["HOOKDIR"],
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ms = int((time.time() - t) * 1000)
    best = ms if best is None or ms < best else best
print(best)
PY
)"
  LASTCMD="time bash session_register.sh (warm, min of 3)"; RC=0; OUT="min ${HMS:-unmeasured}ms"
  if [[ -n "$HMS" && "$HMS" -lt 300 ]]; then
    _pass "hook/warm-path-under-300ms" "the row-exists no-op path took ${HMS}ms (threshold 300ms, min of 3 runs)"
  else
    _skip "hook/warm-path-under-300ms" "threshold 300ms, measured min ${HMS:-unmeasured}ms over 3 runs — this machine is too loaded to measure honestly; a skipped check is not a passed check"
  fi

  run hook_garbage CLAUDE_CODE_SESSION_ID=hooktest-garbage-01
  rc_zero "hook/garbage-stdin-exit-zero" "garbage stdin never breaks the hook"
  readf "$HBOARD"
  if [[ "$HOOK_CAN_REGISTER" == yes ]]; then
    has "hook/garbage-stdin-still-registers" "with the id in the environment, garbage stdin still registers (the payload is a fallback, not a requirement)" \
        "| hooktest-garbage-01 |"
  else
    _skip "hook/garbage-stdin-still-registers" "this host is root (or otherwise cloud-VM-detected): session_register.sh:121 no-ops before it ever writes, so this check cannot measure real registration behaviour from here"
  fi
  HSHA2="$(sha "$HBOARD")"
  run hook_garbage -u CLAUDE_CODE_SESSION_ID
  rc_zero "hook/garbage-no-id-exit-zero" "garbage stdin with no id anywhere exits 0"
  eq "hook/garbage-no-id-board-unchanged" "and touches nothing — there is nothing to key a row on" \
     "$(sha "$HBOARD")" "$HSHA2"

  rm -f "$HLOG"
  run hook_null -u CLAUDE_CODE_SESSION_ID
  rc_zero "hook/missing-id-exit-zero" "no CLAUDE_CODE_SESSION_ID and an empty payload is a normal path, exit 0"
  eq "hook/missing-id-board-unchanged" "the board is untouched" "$(sha "$HBOARD")" "$HSHA2"
  eq "hook/missing-id-is-not-logged-as-failure" "and no error is logged — a missing id is normal, not a failure" \
     "$( [[ -e "$HLOG" ]] && echo present || echo absent )" "absent"

  run hook_null CLAUDE_CODE_SESSION_ID=hooktest-vm-01 CLAUDE_CODE_REMOTE=1
  rc_zero "hook/cloud-vm-exit-zero" "on a cloud VM (CLAUDE_CODE_REMOTE*) the hook is a silent no-op"
  eq "hook/cloud-vm-board-unchanged" "a row written on a VM dies with the clone, so none is written" \
     "$(sha "$HBOARD")" "$HSHA2"
  readf "$HBOARD"
  hasnt "hook/cloud-vm-no-row" "no row for the VM session appears" "hooktest-vm-01"

  # never-clobber: a deliberate registration with real claims is not the hook's
  # to touch, whatever its age. The row must be AGED past the 10-minute refresh
  # window first: against a fresh row the hook's refresh early-return keeps the
  # board unchanged even with the guard deleted, so this check passed against a
  # guard-stripped hook (found vacuous in review, 2026-08-17). Aged, the guard
  # is the only thing standing between the hook and an update write.
  ( cd "$HOOKR" && python3 atlas-os/bin/heartbeat.py register --session hooktest-real-01 \
      --goal "building real work" --owns 'src/real/**' --risk low ) >/dev/null 2>&1
  readf "$HBOARD"
  has "hook/clobber-fixture-row-exists" "the deliberate row really is on the fixture board (else never-clobber is vacuous)" "building real work"
  sed "s/^\(| hooktest-real-01 .*| ACTIVE | \)[0-9TZ:-]*\( |\)\$/\12026-01-01T00:00:00Z\2/" "$HBOARD" > "$HBOARD.tmp" \
    && mv "$HBOARD.tmp" "$HBOARD"
  LASTCMD="grep hooktest-real-01 (aged)"; RC=0; OUT="$(grep '^| hooktest-real-01 ' "$HBOARD")"
  has "hook/clobber-fixture-really-aged" "the deliberate row is aged past the refresh window (else the fresh-row early-return masks a missing guard)" "2026-01-01T00:00:00Z"
  HSHA3="$(sha "$HBOARD")"
  run hook_null CLAUDE_CODE_SESSION_ID=hooktest-real-01
  rc_zero "hook/never-clobber-exit-zero" "the hook exits 0 when it finds a deliberate row under its session id"
  eq "hook/never-clobber-board-unchanged" "and changes NOTHING — only rows whose goal starts 'auto-registered:' are the hook's to touch" \
     "$(sha "$HBOARD")" "$HSHA3"

  # stale refresh: an auto-registered row older than 10 minutes is refreshed,
  # preserving started=. Direct edit of the FIXTURE board manufactures the age.
  # This whole block depends on hooktest-fresh-01's row from fresh-register
  # actually existing, so it inherits the same HOOK_CAN_REGISTER gate.
  if [[ "$HOOK_CAN_REGISTER" == yes ]]; then
    HSTARTED="$(sed -n 's/.*started=\([^ ]*\).*/\1/p' "$HBOARD" | head -1)"
    sed "s/^\(| hooktest-fresh-01 .*| ACTIVE | \)[0-9TZ:-]*\( |\)\$/\12026-01-01T00:00:00Z\2/" "$HBOARD" > "$HBOARD.tmp" \
      && mv "$HBOARD.tmp" "$HBOARD"
    LASTCMD="grep hooktest-fresh-01 (aged)"; RC=0; OUT="$(grep '^| hooktest-fresh-01 ' "$HBOARD")"
    has "hook/stale-fixture-really-aged" "the fixture row really was aged (else the refresh below proves nothing)" "2026-01-01T00:00:00Z"
    run hook_null CLAUDE_CODE_SESSION_ID=hooktest-fresh-01
    rc_zero "hook/stale-refresh-exit-zero"
    LASTCMD="grep hooktest-fresh-01 (after refresh)"; RC=0; OUT="$(grep '^| hooktest-fresh-01 ' "$HBOARD")"
    hasnt "hook/stale-row-refreshed" "an auto-registered row past the 10-minute refresh window gets a fresh last_update" "2026-01-01T00:00:00Z"
    has   "hook/stale-refresh-preserves-started" "while started= keeps the ORIGINAL session start, not the refresh time" "started=$HSTARTED"
  else
    for n in stale-fixture-really-aged stale-refresh-exit-zero stale-row-refreshed stale-refresh-preserves-started; do
      _skip "hook/$n" "this host is root (or otherwise cloud-VM-detected): hooktest-fresh-01's row was never written by the hook, so there is nothing real to age or refresh"
    done
  fi
fi

# =============================================================================
sec "16 — the command surface: /cloud all, /cloud-workflow, and the TEMPLATE"
# =============================================================================
# These files are prompts, not programs — the only mechanical hold this suite
# can take is that the load-bearing sentences exist and the structures parse.
# What an agent DOES with them is outside what a $0 offline suite can measure,
# and README.md says so under deliberate blind spots.

CMD_CLOUD="$REPO_REAL/.claude/commands/cloud.md"
CMD_WF="$REPO_REAL/.claude/commands/cloud-workflow.md"
WF_TMPL="$REPO_REAL/docs/workflows/TEMPLATE.md"

readf "$CMD_CLOUD"
rc_zero "cmd/cloud-md-exists" ".claude/commands/cloud.md exists"
has "cmd/cloud-one-confirmation" "the all-mode asks ONE confirmation for the whole batch" "One confirmation, for the whole batch"
has "cmd/cloud-conflict-stops"   "a non-empty CONFLICT list is a stop"                    "Non-empty means stop"
has "cmd/cloud-conflicts-resolve-on-the-board" "conflicts resolve on the board, never in conversation" "never by assigning the file in conversation"
has "cmd/cloud-never-ends-the-lane" "the lane's board row is never ended — the claim guards the paths until the PR merges" 'Never `end` the lane'
has "cmd/cloud-unclaimed-move-via-board" "an unclaimed file moves only via a widened claim and a re-run" "widen that lane's claim"
has "cmd/cloud-uses-exodus" "the partition comes from cs exodus, not from a hand-built split" "cs exodus --dry-run"
has "cmd/cloud-routes-workflow" "a workflow argument routes to /cloud-workflow instead of the single-task flow" "cloud-workflow"
has "cmd/cloud-create-run-disable" "dispatch is create -> run -> disable as one sequence" "disable"
has "cmd/cloud-green-is-not-success" "a green routine run is named as start-evidence, not success" "the session started"

readf "$CMD_WF"
rc_zero "cmd/cloud-workflow-md-exists" ".claude/commands/cloud-workflow.md exists"
has "wf/five-sections-validated" "it validates all five required sections by name" "Mission Lanes Constraints Done-when Report-as"
has "wf/refuses-the-template"    "TEMPLATE-as-job is refused by name" "the template is a format, not a job"
has "wf/refuses-atlas-bin"       "a lane owning atlas-os/bin/** is refused — I10, and V-023 is a defect, not permission" "V-023"
has "wf/gates-lanes-on-the-board" "every lane is gated through overlap + register" "heartbeat.py overlap"
has "wf/mcp-connections-empty"   "the payload pins the explicit empty connector list" '"mcp_connections": []'
has "wf/mcp-empty-does-not-work" \
    "the file says plainly that an empty connector list does NOT prevent inheritance (V-041) - a reader who trusts the field instead of reading the routine back gets Gmail in a docs job" \
    "does not actually stop"
has "wf/never-end-these-rows"    "board rows are never ended after dispatch" 'Never `end` these rows'
# Superseded 2026-08-17: this check asserted the allowed_tools/Task claim stayed tagged
# UNVERIFIED. The first E2E dispatch exercised it - the session used ToolSearch, TaskCreate
# and TaskUpdate, none of them listed - so the claim is now VERIFIED and the old assertion
# guarded a belief we have measured. It now asserts the measured answer instead, which is
# the safety-relevant half: allowed_tools cannot be used to DENY a cloud session anything.
has "wf/allowed-tools-does-not-restrict" \
    "the file records the measured finding that allowed_tools is an auto-approval list, not a sandbox" \
    "does not restrict anything"
has "wf/no-test-dispatch"        "the file forbids dispatching to test it" "Do not dispatch anything to test this command"

readf "$WF_TMPL"
rc_zero "cmd/template-md-exists" "docs/workflows/TEMPLATE.md exists"
has "tmpl/example-marked-not-real" "the example block is marked as illustration, never a dispatchable job" "not a real workflow"
has "tmpl/owns-disjoint" "lanes' owns are required disjoint" "Disjoint across lanes"
has "tmpl/done-when-checkable" "done-when must be runnable, not admirable" "not a quality it can admire"

# --- every relative link in the three files resolves -------------------------
LASTCMD="linkcheck cloud.md cloud-workflow.md TEMPLATE.md"; RC=0
OUT="$(LC1="$CMD_CLOUD" LC2="$CMD_WF" LC3="$WF_TMPL" python3 - <<'PY' 2>&1
import os, re
bad = n = 0
for var in ("LC1", "LC2", "LC3"):
    path = os.environ[var]
    text = open(path).read()
    text = re.sub(r"```.*?```", "", text, flags=re.S)   # code fences quote paths that need not exist
    for m in re.finditer(r"\]\(([^)\s]+)\)", text):
        target = m.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#")[0]
        if not target:
            continue
        n += 1
        if not os.path.exists(os.path.join(os.path.dirname(path), target)):
            bad += 1
            print("BADLINK %s -> %s" % (os.path.basename(path), target))
print("LINKS-CHECKED=%d BAD=%d" % (n, bad))
PY
)"
has   "cmd/links-were-found"  "the link checker saw a real population of links (zero would be a vacuous pass)" "LINKS-CHECKED="
hasnt "cmd/links-none-broken" "every relative link in the three files resolves to an existing file" "BADLINK"
has   "cmd/links-bad-zero"    "and the checker's own count agrees" "BAD=0"

# --- the TEMPLATE's example parses as the format its own spec describes ------
# A tiny independent parser: extract the ```markdown fence under ## EXAMPLE and
# hold it to the five-section, owns-per-lane, done-when-per-lane contract. The
# same parser is then fed a mutilated copy as its negative control.
cat > "$WORK/wf-parse.py" <<'PY'
import re, sys
text = sys.stdin.read()
m = re.search(r"^## EXAMPLE.*?```markdown\n(.*?)\n```", text, re.S | re.M)
if not m:
    print("MISSING: no ```markdown example block under ## EXAMPLE"); sys.exit(1)
ex = m.group(1)
if not re.match(r"^# Workflow: \S+", ex):
    print("MISSING: '# Workflow: <name>' title"); sys.exit(1)
required = ["Mission", "Lanes", "Constraints", "Done-when", "Report-as"]
sections = re.findall(r"^## (.+)$", ex, re.M)
for s in required:
    if s not in sections:
        print("MISSING: ## %s" % s); sys.exit(1)
lanes = re.findall(r"^### (\S+)", ex, re.M)
owns = re.findall(r"^- owns:", ex, re.M)
donewhen = re.findall(r"^- done-when:", ex, re.M)
if not lanes:
    print("MISSING: no ### lanes"); sys.exit(1)
if not (len(lanes) == len(owns) == len(donewhen)):
    print("MISSING: %d lanes but %d owns / %d done-when lines" % (len(lanes), len(owns), len(donewhen))); sys.exit(1)
print("EXAMPLE-OK lanes=%d" % len(lanes))
PY
run python3 "$WORK/wf-parse.py" < "$WF_TMPL"
rc_zero "tmpl/example-parses" "the example block satisfies the format the file itself specifies"
has "tmpl/example-has-lanes" "with a real multi-lane shape, every lane carrying owns and done-when" "EXAMPLE-OK lanes=3"

# NEGATIVE CONTROL: the same parser over the template with its Done-when heading
# struck out must refuse — a format checker that accepts anything checks nothing.
LASTCMD="wf-parse.py < TEMPLATE.md (Done-when struck out)"
OUT="$(sed 's/^## Done-when$/## Done-later/' "$WF_TMPL" | python3 "$WORK/wf-parse.py" 2>&1)"; RC=$?
rc_nonz "nc/template-parser-can-go-red" "negative control: the parser rejects the example once a required section is renamed"
has     "nc/template-parser-names-the-miss" "and names what is missing" "MISSING"

# =============================================================================
sec "17 — tamper guards (proof the suite wrote nothing real)"
# =============================================================================

cd "$TESTS_DIR"
LASTCMD="shasum -a 256 $REAL_LEDGER"; RC=0; OUT="$(sha "$REAL_LEDGER")"
eq "tamper/real-ledger-untouched" "the repo's state/sessions.jsonl is byte-identical to before the run" \
   "$(sha "$REAL_LEDGER")" "$REAL_LEDGER_SHA"
LASTCMD="shasum -a 256 $REAL_SETTINGS"; RC=0; OUT="$(sha "$REAL_SETTINGS")"
eq "tamper/real-settings-untouched" "the operator's ~/.claude/settings.json is byte-identical to before the run" \
   "$(sha "$REAL_SETTINGS")" "$REAL_SETTINGS_SHA"
# The three files the new surface could reach. The board guard is what makes the
# symlinked-root fixtures of sections 13-15 trustworthy: registrations, hook
# runs and the manufactured conflict all landed somewhere, and this proves the
# somewhere was never the real board.
LASTCMD="shasum -a 256 $REAL_BOARD"; RC=0; OUT="$(sha "$REAL_BOARD")"
eq "tamper/real-board-untouched" "the real heartbeat board is byte-identical to before the run — every register, refresh and conflict landed on a sandbox board" \
   "$(sha "$REAL_BOARD")" "$REAL_BOARD_SHA"
LASTCMD="shasum -a 256 $REAL_MANIFEST"; RC=0; OUT="$(sha "$REAL_MANIFEST")"
eq "tamper/real-manifest-untouched" "the repo's default exodus-manifest path is exactly as it was (usually ABSENT) — every manifest this run wrote lives in the sandbox" \
   "$(sha "$REAL_MANIFEST")" "$REAL_MANIFEST_SHA"
LASTCMD="shasum -a 256 $REAL_HOOK"; RC=0; OUT="$(sha "$REAL_HOOK")"
eq "tamper/real-hook-untouched" "the staged hook script is byte-identical to before the run" \
   "$(sha "$REAL_HOOK")" "$REAL_HOOK_SHA"
# By content hash, not `git diff` vs HEAD: the guard's claim is that THIS RUN
# modified nothing, and diff-vs-HEAD also fires on someone's normal uncommitted
# in-flight work, which is not this suite's business.
LASTCMD="shasum -a 256 $CS_REAL"; RC=0; OUT="$(sha "$CS_REAL")"
eq "tamper/bin-cs-unmodified" "bin/cs is byte-identical to before the run" \
   "$(sha "$CS_REAL")" "$REAL_CS_SHA"

# =============================================================================
sec "18 — suite accounting: a collapsed section must declare how much it hid"
# =============================================================================
# G24: sections 13-14 and 15 collapse to a single loud `exodus/ALL` / `hook/ALL`
# skip when their fixture gate fails (EXODUS_OK / REAL_HOOK), and that one line
# used to just say "skipped" — SKIP went up by 1 while the real shortfall was
# 62 or 29 checks, so the printed tally stayed a confident-looking number while
# quietly measuring far less. The two skip reasons above now name the count
# directly. This section re-derives that count from the CURRENT script text
# (by section marker, not a hand-maintained figure that can drift unnoticed
# the next time someone adds a check to section 13, 14 or 15) and asserts it
# matches what was declared, with a negative control proving the parity check
# itself is capable of going red rather than trivially agreeing with anything.

count_checks_between() {  # start-marker end-marker -> count of check-registering lines in that range
  awk -v s="$1" -v e="$2" '
    $0 ~ s {inrange=1; next}
    $0 ~ e {inrange=0}
    inrange && /^[[:space:]]*(has |hasnt |xhas |xhasnt |rc_zero |rc_nonz |eq |_pass |_fail )/ {n++}
    END {print n+0}
  ' "$CS_DIR/tests/run.sh"
}

EXODUS_LIVE_COUNT="$(count_checks_between '^sec "13' '^sec "15')"
HOOK_LIVE_COUNT="$(count_checks_between '^sec "15' '^sec "16')"

eq "acct/exodus-shortfall-count-is-current" \
   "the '62 checks not run' declared in exodus/ALL's skip reason matches what sections 13-14 actually contain right now" \
   "$EXODUS_LIVE_COUNT" "62"
eq "acct/hook-shortfall-count-is-current" \
   "the '29 checks not run' declared in hook/ALL's skip reason matches what section 15 actually contains right now" \
   "$HOOK_LIVE_COUNT" "29"

# NEGATIVE CONTROL: count only section 13 (not 13+14) — a window that must NOT
# equal 62, since it deliberately leaves out section 14's checks. If
# count_checks_between agreed with 62 here too it would prove the two eq
# checks above pass no matter what, i.e. that they check nothing.
WRONG_COUNT="$(count_checks_between '^sec "13' '^sec "14')"
LASTCMD="count_checks_between(sec 13 .. sec 14) vs the declared 62"; RC=0; OUT="section 13 alone = $WRONG_COUNT"
if [[ "$WRONG_COUNT" != "62" ]]; then
  _pass "nc/acct-parity-can-go-red" "negative control: counting only section 13 gives $WRONG_COUNT, correctly NOT the declared 62 — the parity checks above have teeth"
else
  _fail "nc/acct-parity-can-go-red" "negative control: counting only section 13 gives $WRONG_COUNT" \
        "expected it to differ from 62 (a match here would mean the parity checks above are vacuous)"
fi


# ============================================================================
# 19 — cs claim: the presence -> movable upgrade
# ============================================================================
# The board is what `cs exodus` partitions by, so a session with no owns-claims
# cannot be moved. On 2026-08-18 a human had to read three transcripts and
# attribute 13 dirty files by inspecting each session's Edit/Write calls, by
# hand. `cs claim` does that derivation. Its two dangerous behaviours are
# OVER-claiming (collapsing to a directory that holds another lane's file, which
# hands that lane's work away) and writing anything on --dry-run. Both carry
# negative controls below.
#
# Same symlinked-throwaway-root technique as sections 13-15: the REAL
# heartbeat.py runs against a sandbox board, and section 17 proves by hash that
# the real board never moved.
log ""
log "## 19 — cs claim"
sec "19 — cs claim: presence -> movable"

CLAIM_OK=yes
for f in "$HB_REAL" "$BOARD_TMPL"; do [[ -f "$f" ]] || CLAIM_OK=no; done

if [[ "$CLAIM_OK" != yes ]]; then
  _skip "claim/ALL" "heartbeat.py / board template not found next to this checkout — the claim fixture cannot be built (16 checks not run: section 19 in full, see G24)"
  COLLAPSED=$((COLLAPSED+16))
else
  CLR="$WORK/repos/claim-fix"
  CLH="$WORK/home-claim"
  mkdir -p "$CLR/atlas-os/bin" "$CLR/atlas-os/heartbeat" "$CLR/lab/env" "$CLR/src" "$CLR/other"
  ln -s "$HB_REAL" "$CLR/atlas-os/bin/heartbeat.py"
  cp "$BOARD_TMPL" "$CLR/atlas-os/heartbeat/STATE.md"
  printf '__pycache__/\n' > "$CLR/.gitignore"
  printf 'a\n' > "$CLR/lab/env/one.txt"
  printf 'b\n' > "$CLR/lab/env/two.txt"
  printf 'c\n' > "$CLR/src/keep.txt"
  printf 'd\n' > "$CLR/other/theirs.txt"
  git -C "$CLR" init -q -b main >/dev/null
  git -C "$CLR" add -A >/dev/null 2>&1; git -C "$CLR" commit -q -m base
  # dirty the tree: this session's files plus one that belongs to nobody here
  printf 'a2\n' > "$CLR/lab/env/one.txt"
  printf 'b2\n' > "$CLR/lab/env/two.txt"
  printf 'c2\n' > "$CLR/src/keep.txt"

  # A synthetic transcript in the exact on-disk shape cs claim parses:
  # ~/.claude/projects/<cwd with / and . replaced by ->/<session-id>.jsonl
  CLSID="claimsess-0001"
  CLMANGLED="$(printf '%s' "$CLR" | tr './' '--')"
  mkdir -p "$CLH/.claude/projects/$CLMANGLED"
  CLTR="$CLH/.claude/projects/$CLMANGLED/$CLSID.jsonl"
  {
    # two Edits and a Write inside the repo
    printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"%s/lab/env/one.txt"}}]}}\n' "$CLR"
    printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"%s/lab/env/two.txt"}}]}}\n' "$CLR"
    printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"%s/src/keep.txt"}}]}}\n' "$CLR"
    # a path OUTSIDE the repo — must never be claimed
    printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"/etc/hosts"}}]}}\n'
    # a file written then deleted, and not in git status — must be dropped
    printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"%s/src/vanished.txt"}}]}}\n' "$CLR"
    # an ambiguous Bash target — must be SKIPPED, not guessed
    printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"echo x > $SOMEVAR/mystery.txt"}}]}}\n'
  } > "$CLTR"

  mkdir -p "$CLR/cloud-sessions/bin" "$WORK/home-empty"
  cp "$CS" "$CLR/cloud-sessions/bin/cs"
  CLCS="$CLR/cloud-sessions/bin/cs"
  cd "$CLR"

  # ---- derivation ---------------------------------------------------------
  run env HOME="$CLH" CLAUDE_CODE_SESSION_ID="$CLSID" "$CLCS" claim --dry-run
  CLOUT="$OUT"
  rc_zero "claim/dry-run-exits-zero" "a derivable claim exits 0"
  has "claim/derives-edited-files"   "files this session edited are derived from its own transcript" "lab"
  has "claim/derives-written-files"  "files this session wrote are derived too"                      "src/keep.txt"

  case "$CLOUT" in
    */etc/hosts*) _fail "claim/never-claims-outside-repo" "a path outside the repo is never claimed" "found /etc/hosts in the claim set" ;;
    *)            _pass "claim/never-claims-outside-repo" "a path outside the repo is never claimed" ;;
  esac
  # Only the CLAIMS block matters: the path may legitimately appear in the
  # explanatory prose above it. Extract just the claim list and check there.
  CLAIMED="$(printf '%s\n' "$CLOUT" | awk '/^claims \(/{f=1;next} /^$/{f=0} f')"
  case "$CLAIMED" in
    *vanished.txt*) _fail "claim/drops-deleted-files" "a file written then deleted, absent from git status, is not claimed" "vanished.txt is in the claim set" ;;
    *)              _pass "claim/drops-deleted-files" "a file written then deleted, absent from git status, is not claimed" ;;
  esac
  has "claim/skips-ambiguous-bash" "an ambiguous Bash redirect target is skipped and counted, never guessed" "SKIPPED"

  # ---- --dry-run writes NOTHING (with its negative control) ---------------
  CL_BOARD_SHA="$(sha "$CLR/atlas-os/heartbeat/STATE.md")"
  run env HOME="$CLH" CLAUDE_CODE_SESSION_ID="$CLSID" "$CLCS" claim --dry-run
  CL_BOARD_SHA2="$(sha "$CLR/atlas-os/heartbeat/STATE.md")"
  if same "$CL_BOARD_SHA" "$CL_BOARD_SHA2"; then
    _pass "claim/dry-run-writes-nothing" "--dry-run leaves the board byte-identical"
  else
    _fail "claim/dry-run-writes-nothing" "--dry-run leaves the board byte-identical" "board hash changed"
  fi
  # negative control: the same comparator must go red when the board really moves
  printf '\n<!-- tamper -->\n' >> "$CLR/atlas-os/heartbeat/STATE.md"
  CL_BOARD_SHA3="$(sha "$CLR/atlas-os/heartbeat/STATE.md")"
  if same "$CL_BOARD_SHA" "$CL_BOARD_SHA3"; then
    _fail "nc/claim-writes-nothing-can-go-red" "the writes-nothing detector can fail" "a genuinely modified board still compared equal"
  else
    _pass "nc/claim-writes-nothing-can-go-red" "the writes-nothing detector can fail when the board really changes"
  fi
  cp "$BOARD_TMPL" "$CLR/atlas-os/heartbeat/STATE.md"

  # ---- the overlap gate refuses rather than stealing ----------------------
  # Another lane takes lab/ first. cs claim must refuse, name it, and write nothing.
  run python3 atlas-os/bin/heartbeat.py register --session lane-other --goal "holds lab" --owns 'lab' --risk low
  CL_SHA_BEFORE="$(sha "$CLR/atlas-os/heartbeat/STATE.md")"
  run env HOME="$CLH" CLAUDE_CODE_SESSION_ID="$CLSID" "$CLCS" claim
  rc_nonz "claim/refuses-on-overlap" "a claim that would take a live lane's path refuses instead of widening into it"
  has     "claim/overlap-names-the-holder" "the refusal names the lane that already holds the path" "lane-other"
  CL_SHA_AFTER="$(sha "$CLR/atlas-os/heartbeat/STATE.md")"
  if same "$CL_SHA_BEFORE" "$CL_SHA_AFTER"; then
    _pass "claim/refusal-writes-nothing" "a refused claim leaves the board untouched"
  else
    _fail "claim/refusal-writes-nothing" "a refused claim leaves the board untouched" "board changed despite the refusal"
  fi
  # negative control: prove THAT refusal check can go red — with lab/ free, the
  # same command must succeed, so a always-refusing stub would fail here.
  run python3 atlas-os/bin/heartbeat.py end --session lane-other --evidence "test"
  run env HOME="$CLH" CLAUDE_CODE_SESSION_ID="$CLSID" "$CLCS" claim
  rc_zero "nc/claim-overlap-refusal-can-go-red" "with the path free the same claim SUCCEEDS — so the refusal above measured the gate, not a stub that always refuses"

  # ---- presence -> real claim upgrade ------------------------------------
  cp "$BOARD_TMPL" "$CLR/atlas-os/heartbeat/STATE.md"
  run python3 atlas-os/bin/heartbeat.py register --session "$CLSID" \
      --goal "auto-registered: cwd=$CLR" --owns "._presence/$CLSID" --risk low
  run env HOME="$CLH" CLAUDE_CODE_SESSION_ID="$CLSID" "$CLCS" claim
  rc_zero "claim/upgrades-presence-row" "a hook-created presence row is upgraded in place"
  BOARDTXT="$(cat "$CLR/atlas-os/heartbeat/STATE.md")"
  case "$BOARDTXT" in
    *"._presence/$CLSID"*) _fail "claim/sentinel-replaced" "the ._presence sentinel is replaced by the real claims" "sentinel still on the board" ;;
    *)                     _pass "claim/sentinel-replaced" "the ._presence sentinel is replaced by the real claims" ;;
  esac
  case "$BOARDTXT" in
    *"lab"*) _pass "claim/real-paths-on-board" "the derived paths are what the board now carries" ;;
    *)       _fail "claim/real-paths-on-board" "the derived paths are what the board now carries" "no derived path found on the board" ;;
  esac

  # ---- a deliberately-written goal is never clobbered --------------------
  cp "$BOARD_TMPL" "$CLR/atlas-os/heartbeat/STATE.md"
  run python3 atlas-os/bin/heartbeat.py register --session "$CLSID" \
      --goal "DELIBERATE: written by the session itself" --owns "src/keep.txt" --risk low
  run env HOME="$CLH" CLAUDE_CODE_SESSION_ID="$CLSID" "$CLCS" claim
  case "$(cat "$CLR/atlas-os/heartbeat/STATE.md")" in
    *"DELIBERATE: written by the session itself"*)
      _pass "claim/keeps-a-deliberate-goal" "a goal the session wrote itself survives a re-claim" ;;
    *)
      _fail "claim/keeps-a-deliberate-goal" "a goal the session wrote itself survives a re-claim" "the deliberate goal was overwritten" ;;
  esac

  # ---- loud refusals, never an empty success -----------------------------
  run env HOME="$CLH" CLAUDE_CODE_SESSION_ID="" "$CLCS" claim --dry-run
  rc_nonz "claim/refuses-without-a-session-id" "a claim keyed on nothing is refused, not invented"
  run env HOME="$WORK/home-empty" CLAUDE_CODE_SESSION_ID="nosuchsession" "$CLCS" claim --dry-run
  rc_nonz "claim/refuses-without-a-transcript" "no transcript is a named refusal, not an empty claim set"

  cd "$REPO_REAL"
fi


# ============================================================================
# 20 — the cloud fleet: heartbeat -> blocker -> visibility, and composition
# ============================================================================
# A cloud VM's telemetry dies with the VM and the heartbeat board does not
# travel, so git is the ONLY channel a cloud agent's liveness can survive on.
# bin/cloud-heartbeat.sh pushes beats to refs/heads/cloud-fleet/<session-id>
# (its own namespace, so telemetry never interleaves with the agent's work) and
# `cs fleet` reads them back.
#
# The defect this section exists to prevent, found 2026-08-19 by running the
# chain end to end: fleet scanned only each lane's OWN branch, so an agent that
# emitted a blocker correctly still rendered "never reported". The blocker was
# pushed, and invisible. Ground rule 4, in the one view built to surface it.
log ""
log "## 20 — cloud fleet: heartbeat, blockers, composition"
sec "20 — cloud fleet: heartbeat -> blocker -> visibility"

HB_SH="$CS_DIR/bin/cloud-heartbeat.sh"
UI_PY="$CS_DIR/bin/fleet_ui.py"
FLEET_OK=yes
for f in "$HB_SH" "$UI_PY"; do [[ -f "$f" ]] || FLEET_OK=no; done

if [[ "$FLEET_OK" != yes ]]; then
  _skip "fleet/ALL" "cloud-heartbeat.sh or fleet_ui.py not present — the fleet fixture cannot be built (19 checks not run: section 20 in full, see G24)"
  COLLAPSED=$((COLLAPSED+19))
else
  FLR="$WORK/repos/fleet-fix"
  mkdir -p "$FLR/cloud-sessions/bin" "$FLR/cloud-sessions/state"
  git init -q -b main "$FLR" >/dev/null
  git init -q --bare "$WORK/remotes/fleet-fix.git" >/dev/null
  cp "$HB_SH" "$FLR/cloud-sessions/bin/"
  cp "$UI_PY" "$FLR/cloud-sessions/bin/"
  cp "$CS"    "$FLR/cloud-sessions/bin/cs"
  FCS="$FLR/cloud-sessions/bin/cs"
  printf 'x\n' > "$FLR/work.txt"
  cd "$FLR"
  git remote add origin "$WORK/remotes/fleet-fix.git"
  git add -A >/dev/null 2>&1; git commit -q -m base; git push -q -u origin main
  printf '{"ts":"2026-08-18T00:00:00Z","id":"cse_FLEETA","title":"a","repo":"fleet-fix","branch":"claude/lane-a","mode":"tracked","url":"u","lane":"lane-a"}\n'  > cloud-sessions/state/sessions.jsonl
  printf '{"ts":"2026-08-18T00:00:00Z","id":"cse_FLEETB","title":"b","repo":"fleet-fix","branch":"claude/lane-b","mode":"tracked","url":"u","lane":"lane-b"}\n' >> cloud-sessions/state/sessions.jsonl

  # ---- the emitter -------------------------------------------------------
  run env CLAUDE_CODE_SESSION_ID=cse_FLEETA bash cloud-sessions/bin/cloud-heartbeat.sh start --lane lane-a --note "starting"
  rc_zero "fleet/emit-start-exits-zero" "the emitter exits 0 on a normal start — telemetry must never break the work it describes"
  HBF="$FLR/cloud-sessions/state/fleet/cse_FLEETA.json"
  if [[ -f "$HBF" ]] && python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$HBF" 2>/dev/null; then
    _pass "fleet/emit-writes-valid-json" "start writes a parseable heartbeat"
  else
    _fail "fleet/emit-writes-valid-json" "start writes a parseable heartbeat" "missing or unparseable: $HBF"
  fi
  run env CLAUDE_CODE_SESSION_ID=cse_FLEETA bash cloud-sessions/bin/cloud-heartbeat.sh blocked --kind needs-decision --detail "two options, cannot pick" --needs "a founder call"
  rc_zero "fleet/emit-blocked-exits-zero" "declaring a blocker exits 0 — an agent must be able to report being stuck without dying"
  has     "fleet/emit-blocked-announces" "the emitter says plainly that it recorded a blocker" "BLOCKED"

  # the beat must NOT be committed onto the agent's working branch
  BR_NOW="$(git -C "$FLR" rev-parse --abbrev-ref HEAD)"
  if git -C "$FLR" log --oneline "$BR_NOW" -- cloud-sessions/state/fleet 2>/dev/null | grep -q .; then
    _fail "fleet/beats-never-touch-the-work-branch" "heartbeats stay out of the agent's own branch history" "found beat commits on $BR_NOW"
  else
    _pass "fleet/beats-never-touch-the-work-branch" "heartbeats stay out of the agent's own branch history"
  fi

  # ---- fleet SEES the blocker (the 2026-08-19 defect) --------------------
  git -C "$FLR" fetch -q origin '+refs/heads/*:refs/remotes/origin/*' 2>/dev/null
  run env CS_CLAUDE_BIN=/usr/bin/true "$FCS" fleet
  FLOUT="$OUT"
  has "fleet/blocked-is-surfaced"     "a pushed blocker is rendered, not reported as never-having-reported" "BLOCKED"
  has "fleet/blocked-names-the-kind"  "the blocker's kind is shown"   "needs-decision"
  has "fleet/blocked-names-the-need"  "what it needs from a human is shown" "founder call"
  # Precise: look only at the lines that actually mention this agent, and assert
  # none of them describes it as silent. Matching "never reported" anywhere in the
  # output would also match the legend and the sibling agent's row.
  FLEETA_LINES="$(printf '%s\n' "$FLOUT" | grep "cse_FLEETA" || true)"
  case "$FLEETA_LINES" in
    *"never reported"*|*"no heartbeat"*)
      _fail "fleet/blocked-not-mistaken-for-silent" "an agent that DID report is never listed as never-reporting" "a cse_FLEETA line calls it silent: $FLEETA_LINES" ;;
    *)
      _pass "fleet/blocked-not-mistaken-for-silent" "an agent that DID report is never listed as never-reporting" ;;
  esac
  run env CS_CLAUDE_BIN=/usr/bin/true "$FCS" fleet
  if [[ "$RC" -eq 3 ]]; then
    _pass "fleet/blocked-exits-three" "any blocked agent makes cs fleet exit 3, so a watcher can act on it"
  else
    _fail "fleet/blocked-exits-three" "any blocked agent makes cs fleet exit 3, so a watcher can act on it" "exit was $RC"
  fi

  # NEGATIVE CONTROL: the exit code must track reality, not be a constant 3
  run env CLAUDE_CODE_SESSION_ID=cse_FLEETA bash cloud-sessions/bin/cloud-heartbeat.sh done --note "resolved"
  git -C "$FLR" fetch -q origin '+refs/heads/*:refs/remotes/origin/*' 2>/dev/null
  run env CS_CLAUDE_BIN=/usr/bin/true "$FCS" fleet
  if [[ "$RC" -eq 3 ]]; then
    _fail "nc/fleet-exit-can-go-green" "with the blocker cleared the SAME command must stop exiting 3 — otherwise the check above measured a constant" "still exiting 3 after done"
  else
    _pass "nc/fleet-exit-can-go-green" "with the blocker cleared the same command stops exiting 3 — the exit code tracks reality"
  fi

  # ---- degraded reads are loud, never empty -------------------------------
  run env CLAUDE_CODE_SESSION_ID=cse_FLEETB bash cloud-sessions/bin/cloud-heartbeat.sh start --lane lane-b
  printf 'not json at all\n' > "$FLR/cloud-sessions/state/fleet/cse_FLEETB.json"
  run env CS_CLAUDE_BIN=/usr/bin/true "$FCS" fleet
  case "$OUT" in
    *DEGRADED*|*degraded*|*unparseable*|*untrusted*)
      _pass "fleet/malformed-heartbeat-is-loud" "an unparseable heartbeat degrades loudly instead of vanishing" ;;
    *)
      _fail "fleet/malformed-heartbeat-is-loud" "an unparseable heartbeat degrades loudly instead of vanishing" "no degraded marker in output" ;;
  esac
  has "fleet/partial-is-not-empty" "the header says a degraded source makes the view PARTIAL, not empty" "PARTIAL"

  # ---- --json stays valid in the degraded state --------------------------
  run env CS_CLAUDE_BIN=/usr/bin/true "$FCS" fleet --json
  if printf '%s' "$OUT" | python3 -c "import json,sys;json.load(sys.stdin)" 2>/dev/null; then
    _pass "fleet/json-valid-when-degraded" "--json is parseable even with a corrupt heartbeat present"
  else
    _fail "fleet/json-valid-when-degraded" "--json is parseable even with a corrupt heartbeat present" "not valid JSON"
  fi

  # ---- the UI -------------------------------------------------------------
  rm -f "$FLR/cloud-sessions/state/fleet/cse_FLEETB.json"
  run env CS_CLAUDE_BIN=/usr/bin/true python3 cloud-sessions/bin/fleet_ui.py --out "$WORK/fleet-test.html"
  if [[ -s "$WORK/fleet-test.html" ]]; then
    _pass "fleet/ui-generates-a-page" "the UI writes a non-empty page"
  else
    _fail "fleet/ui-generates-a-page" "the UI writes a non-empty page" "no output file"
  fi
  UIEXT="$(grep -coE '<script[^>]+src="http|<link[^>]+href="http|<img[^>]+src="http|@import|fetch\(|XMLHttpRequest' "$WORK/fleet-test.html" 2>/dev/null || true)"
  if [[ "$UIEXT" -eq 0 ]]; then
    _pass "fleet/ui-is-self-contained" "the page fetches nothing external — it must render from file:// with no network"
  else
    _fail "fleet/ui-is-self-contained" "the page fetches nothing external" "$UIEXT external resource reference(s)"
  fi
  # NEGATIVE CONTROL: that detector must fire on a page that DOES fetch
  printf '<html><head><link rel="stylesheet" href="https://cdn.example.com/x.css"></head></html>\n' > "$WORK/fleet-bad.html"
  UIBAD="$(grep -coE '<script[^>]+src="http|<link[^>]+href="http|<img[^>]+src="http|@import|fetch\(|XMLHttpRequest' "$WORK/fleet-bad.html" || true)"
  if [[ "$UIBAD" -gt 0 ]]; then
    _pass "nc/ui-self-contained-can-go-red" "the self-containment detector fires on a page with a CDN stylesheet"
  else
    _fail "nc/ui-self-contained-can-go-red" "the self-containment detector fires on a page with a CDN stylesheet" "detector missed an obvious external fetch"
  fi

  # ---- COMPOSITION: features must not overwrite each other ---------------
  # Daniel's explicit requirement. cs fleet is a READER; running it must not
  # disturb the board, the ledger, or any claim that exodus partitions by.
  LEDG_SHA="$(sha "$FLR/cloud-sessions/state/sessions.jsonl")"
  run env CS_CLAUDE_BIN=/usr/bin/true "$FCS" fleet
  run env CS_CLAUDE_BIN=/usr/bin/true "$FCS" fleet --json
  LEDG_SHA2="$(sha "$FLR/cloud-sessions/state/sessions.jsonl")"
  if same "$LEDG_SHA" "$LEDG_SHA2"; then
    _pass "fleet/reader-never-mutates-the-ledger" "cs fleet leaves the dispatch ledger byte-identical"
  else
    _fail "fleet/reader-never-mutates-the-ledger" "cs fleet leaves the dispatch ledger byte-identical" "ledger hash changed"
  fi
  # and emitting heartbeats must not change what the working tree looks like to git
  printf 'changed\n' > "$FLR/work.txt"
  DIRTY_BEFORE="$(git -C "$FLR" status --porcelain -- work.txt)"
  run env CLAUDE_CODE_SESSION_ID=cse_FLEETA bash cloud-sessions/bin/cloud-heartbeat.sh beat --phase "still going"
  DIRTY_AFTER="$(git -C "$FLR" status --porcelain -- work.txt)"
  if same "$DIRTY_BEFORE" "$DIRTY_AFTER"; then
    _pass "fleet/emitter-does-not-disturb-the-work-tree" "emitting a beat leaves the agent's own dirty files exactly as they were"
  else
    _fail "fleet/emitter-does-not-disturb-the-work-tree" "emitting a beat leaves the agent's own dirty files exactly as they were" "work.txt status changed from '$DIRTY_BEFORE' to '$DIRTY_AFTER'"
  fi

  # ---- refusals are named, never silent ----------------------------------
  run env CLAUDE_CODE_SESSION_ID= bash cloud-sessions/bin/cloud-heartbeat.sh beat
  rc_nonz "fleet/emit-refuses-without-a-session-id" "a heartbeat keyed on nothing is refused, not invented"

  cd "$REPO_REAL"
fi

# =============================================================================
# summary
# =============================================================================
printf '\n%s%d passed  %d failed  %d xfail(known-open)  %d skipped%s\n' "$D" "$PASS" "$FAIL" "$XFAIL" "$SKIP" "$O"

log ""
log "## Summary"
log ""
log "| result | count |"
log "|---|---|"
log "| passed | $PASS |"
log "| failed | $FAIL |"
log "| xfail (known-open defect in \`cs\`) | $XFAIL |"
log "| skipped | $SKIP |"
log ""

if [[ "$XFAIL" -gt 0 ]]; then
  printf '%sknown-open defects in bin/cs (%d):%s\n' "$Y" "$XFAIL" "$O"
  log "**Known-open defects in \`bin/cs\` — an xfail is not a pass:**"
  log ""
  for n in "${OPEN_DEFECTS[@]}"; do printf '  - %s\n' "$n"; log "- \`$n\`"; done
  log ""
fi
if [[ "$SKIP" -gt 0 ]]; then
  printf '%s%d skipped check(s) — a skipped check is not a passed check.%s\n' "$Y" "$SKIP" "$O"
fi
if [[ "$COLLAPSED" -gt 0 ]]; then
  printf '%s%d additional check(s) never ran at all — collapsed into the skip lines above, not counted in "%d skipped." See G24.%s\n' \
    "$Y" "$COLLAPSED" "$SKIP" "$O"
  log "**$COLLAPSED additional check(s) never ran** — collapsed into an \`/ALL\` skip line above, not individually counted in \"$SKIP skipped.\" See G24."
fi
if [[ "$FAIL" -gt 0 ]]; then
  printf '%snot green.%s  failed: %s\n' "$R" "$O" "${FAILED_NAMES[*]}"
  log "**NOT GREEN.** Failed: ${FAILED_NAMES[*]}"
  exit 1
fi
printf '%sgreen.%s  No regression in the ten reviewed defects.\n' "$G" "$O"
log "**GREEN** — no regression in the ten reviewed defects."
exit 0
