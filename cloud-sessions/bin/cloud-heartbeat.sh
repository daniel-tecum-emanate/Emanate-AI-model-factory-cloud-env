#!/usr/bin/env bash
# cloud-heartbeat.sh — the cloud-side half of fleet tracking.
#
# Provenance note, 2026-08-18: originally written against a different repo
# (daniel0tgc/internal-company-tool); this script itself is mechanism-level and
# repo-agnostic. atlas-os/heartbeat/STATE.md, mentioned below, does not exist in
# this repo (emanate-tecum-workflow) — this repo's board is
# ../../coordination/heartbeat/STATE.md, a different, unconnected system.
#
# A cloud VM's local telemetry DIES WITH THE VM (deliberately — no telemetry
# persistence in cloud VMs, confirmed 2026-08-14), and the heartbeat board at
# atlas-os/heartbeat/STATE.md does not travel to a cloud session in any useful way
# (V-021). So the only channel by which a cloud agent's liveness can reach a laptop
# that may be closed is **git push**. This script is that channel: it writes one
# small JSON file per session and pushes it to a dedicated ref.
#
#   cloud-heartbeat.sh start   --lane <name> [--note "..."]
#   cloud-heartbeat.sh beat    [--phase "..."] [--note "..."] [--progress done/total] [--unblocked]
#   cloud-heartbeat.sh blocked --kind <kind> --detail "..." [--needs "..."]
#   cloud-heartbeat.sh done    [--note "..."]
#   cloud-heartbeat.sh failed  --detail "..."
#   cloud-heartbeat.sh help
#
# Session id comes from $CLAUDE_CODE_SESSION_ID, or --session <id> on any verb.
#
# ---------------------------------------------------------------------------
# THE DISPATCH BLOCK — paste this into a routine payload / cloud prompt.
# Extract it mechanically:
#   sed -n '/^# ```text$/,/^# ```$/p' cloud-heartbeat.sh | sed -e '1d' -e '$d' -e 's/^# \{0,1\}//'
#
# ```text
# ## Fleet heartbeat — required, and it is how anyone knows you are alive
#
# Run these from the repo root. They cost nothing, touch no branch of yours, and
# exit 0 even when they fail — never let one stop your work.
#
#   bash cloud-sessions/bin/cloud-heartbeat.sh start --lane <LANE> --note "<one line: what you are doing>"
#   # then every few minutes, and at every milestone:
#   bash cloud-sessions/bin/cloud-heartbeat.sh beat --phase "<what you are doing NOW>" --progress 2/7
#   # exactly once at the end, whichever is true:
#   bash cloud-sessions/bin/cloud-heartbeat.sh done   --note "<what landed: branch, PR, files>"
#   bash cloud-sessions/bin/cloud-heartbeat.sh failed --detail "<what broke, concretely>"
#
# ## If you are stuck: DECLARE IT. Do not guess.
#
# Do not invent a plausible answer, do not pick a default because none was given,
# do not work around a missing credential, do not skip a check and call it passed.
# Say so instead:
#
#   bash cloud-sessions/bin/cloud-heartbeat.sh blocked \
#     --kind <one of: needs-decision | needs-credential | needs-human-action |
#                     dependency-missing | rate-limited | ambiguous-instruction> \
#     --detail "<what is blocking you, concretely>" \
#     --needs  "<the exact answer, artifact or action that unblocks you>"
#
# Then KEEP BEATING while blocked — a blocked agent that goes silent is
# indistinguishable from a dead one — and carry on with any part of the task that
# is NOT blocked. When it clears:
#
#   bash cloud-sessions/bin/cloud-heartbeat.sh beat --unblocked --phase "resumed: <what changed>"
#
# Warnings from this script (e.g. a failed push) are not your problem. Keep working.
# ```
# ---------------------------------------------------------------------------
#
# WHY A DEDICATED REF, AND WHY ONE PER SESSION
#
# Heartbeats must not land on the lane branch: telemetry commits would interleave
# with real work, pollute the PR diff, and make `git log` on the branch useless.
# So beats go to their own namespace, `cloud-fleet/<session-id>`, one branch per
# session, and NOTHING here ever touches the agent's HEAD, index or working tree —
# the commit is assembled with plumbing (hash-object / a private GIT_INDEX_FILE /
# write-tree / commit-tree) and pushed by sha.
#
# One branch per session rather than one shared `cloud-fleet` branch, because:
#   1. Each ref then has exactly ONE writer, so the common case is collision-free.
#      On a shared branch with twenty agents beating every few minutes, contention
#      is the normal case and every beat pays a fetch+reparent.
#   2. Failure stays isolated. On a shared branch one wedged emitter retrying in a
#      loop delays everyone else's telemetry; here it can only lose its own.
#   3. The reader loses nothing. One fetch still gets the whole fleet:
#        git fetch origin '+refs/heads/cloud-fleet/*:refs/remotes/origin/cloud-fleet/*'
#      and `git ls-remote origin 'refs/heads/cloud-fleet/*'` enumerates live lanes
#      without transferring a single object.
#   4. Cleanup is per-session and the namespace keeps these refs out of the
#      top-level branch list.
# The collision path is implemented anyway (fetch → reparent → retry, bounded),
# because a session id CAN legitimately be written twice: a resumed/teleported
# session on a second VM, two worktrees, or a subagent that inherited the parent's
# $CLAUDE_CODE_SESSION_ID.
#
# History is preserved rather than force-pushed flat: `git log cloud-fleet/<id>`
# is then a free, tamper-evident timeline of the run.
#
# EXIT CODES
#   0  the beat was recorded (and, unless a warning says otherwise, pushed).
#      Also 0 for every degraded condition that is not the agent's fault — a
#      failed push, an offline remote, a lost race. The work matters more than the
#      telemetry about the work.
#   2  refusal: bad usage, or a precondition that makes tracking impossible (no
#      session id, not a git repo, no origin, unwritable state dir). Nothing was
#      recorded and the operator must fix the dispatch. A heartbeat that silently
#      goes nowhere is worse than none: it reads as "tracked" when it is not.
#   Anything else is a bug in this script and is suppressed to 0 by the EXIT trap.

set -euo pipefail

EMITTER_VERSION=1
SCHEMA_VERSION=1
FLEET_NS="cloud-fleet"          # ref namespace: refs/heads/cloud-fleet/<session-id>
PUSH_ATTEMPTS=4                 # bounded: 1 try + 3 reparent-and-retry
BLOCKER_KINDS="needs-decision needs-credential needs-human-action dependency-missing rate-limited ambiguous-instruction"

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -P "$HERE/.." && pwd)"          # the cloud-sessions/ folder, wherever it was ported to
FLEET_DIR="$ROOT/state/fleet"

INTENTIONAL=no
FINAL_RC=0
TMPD=""

# Belt and braces on "never break the agent's real work": any unhandled failure —
# including a bug in this script — leaves through here as exit 0, loudly.
finish() {
  local rc=$?
  if [[ -n "$TMPD" ]]; then rm -rf "$TMPD" 2>/dev/null || true; fi
  if [[ "$INTENTIONAL" == "yes" ]]; then exit "$FINAL_RC"; fi
  if [[ $rc -ne 0 ]]; then
    printf 'cloud-heartbeat: internal error (exit %d) — suppressed so it cannot fail your work.\n' "$rc" >&2
    printf 'cloud-heartbeat: this beat was NOT recorded. Your task is unaffected; keep going.\n' >&2
  fi
  exit 0
}
trap finish EXIT

warn() { printf 'cloud-heartbeat: %s\n' "$*" >&2; }

ok_exit() { INTENTIONAL=yes; FINAL_RC=0; exit 0; }

# Refusals are loud and non-zero on purpose: they mean nothing was recorded.
refuse() {
  local headline="$1"; shift
  printf 'cloud-heartbeat: REFUSED — %s\n' "$headline" >&2
  local line
  for line in "$@"; do printf '  %s\n' "$line" >&2; done
  INTENTIONAL=yes; FINAL_RC=2; exit 2
}

usage() {
  cat <<'USAGE'
cloud-heartbeat.sh — make a cloud agent's liveness, progress and blockers visible
                     on a laptop that may be closed. Writes JSON, pushes it to a
                     dedicated git ref. Never touches your branch or working tree.

  cloud-heartbeat.sh start   --lane <name> [--note "..."] [--phase "..."] [--progress d/t]
  cloud-heartbeat.sh beat    [--phase "..."] [--note "..."] [--progress d/t] [--unblocked]
  cloud-heartbeat.sh blocked --kind <kind> --detail "..." [--needs "..."] [--phase "..."]
  cloud-heartbeat.sh done    [--note "..."] [--phase "..."]
  cloud-heartbeat.sh failed  --detail "..." [--note "..."]
  cloud-heartbeat.sh help

  --session <id>   override $CLAUDE_CODE_SESSION_ID (valid on every verb)
  --lane <name>    lane/goal label (valid on every verb; required on start)

  <kind> is one of:
    needs-decision        a choice only a human can make
    needs-credential      a token/key/login you do not have and must not invent
    needs-human-action    something offline: an approval, a click, a merge
    dependency-missing    a file, service, branch or tool that is not there
    rate-limited          throttled or quota-exhausted, retry is not progress
    ambiguous-instruction the task admits two readings and guessing is not safe

Exit 0 always, except usage/precondition refusals, which exit 2.
USAGE
}

# --------------------------------------------------------------- argument parse
[[ $# -ge 1 ]] || { usage >&2; INTENTIONAL=yes; FINAL_RC=2; exit 2; }

VERB="$1"; shift
case "$VERB" in
  help|-h|--help) usage; ok_exit ;;
  start|beat|blocked|done|failed) ;;
  *) printf 'cloud-heartbeat: unknown verb: %s\n\n' "$VERB" >&2; usage >&2
     INTENTIONAL=yes; FINAL_RC=2; exit 2 ;;
esac

SID=""; LANE=""; NOTE=""; PHASE=""; PROGRESS=""; KIND=""; DETAIL=""; NEEDS=""; UNBLOCK=no
HAVE_LANE=no; HAVE_NOTE=no; HAVE_PHASE=no; HAVE_PROGRESS=no
HAVE_KIND=no; HAVE_DETAIL=no; HAVE_NEEDS=no

need_arg() {
  # $1 flag, $2 remaining count
  [[ "$2" -ge 2 ]] || refuse "$1 needs a value" "usage: cloud-heartbeat.sh $VERB ... $1 <value>"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)   need_arg "$1" $#; SID="$2";      shift 2 ;;
    --lane)      need_arg "$1" $#; LANE="$2";     HAVE_LANE=yes;     shift 2 ;;
    --note)      need_arg "$1" $#; NOTE="$2";     HAVE_NOTE=yes;     shift 2 ;;
    --phase)     need_arg "$1" $#; PHASE="$2";    HAVE_PHASE=yes;    shift 2 ;;
    --progress)  need_arg "$1" $#; PROGRESS="$2"; HAVE_PROGRESS=yes; shift 2 ;;
    --kind)      need_arg "$1" $#; KIND="$2";     HAVE_KIND=yes;     shift 2 ;;
    --detail)    need_arg "$1" $#; DETAIL="$2";   HAVE_DETAIL=yes;   shift 2 ;;
    --needs)     need_arg "$1" $#; NEEDS="$2";    HAVE_NEEDS=yes;    shift 2 ;;
    --unblocked) UNBLOCK=yes; shift ;;
    --) shift; break ;;
    *) refuse "unexpected argument: $1" "run: cloud-heartbeat.sh help" ;;
  esac
done
[[ $# -eq 0 ]] || refuse "unexpected argument: $1" "run: cloud-heartbeat.sh help"

# Per-verb flag rules. Permissive where a flag is unambiguous (--note/--phase/
# --progress mean the same thing on every verb), strict where accepting it would
# record something misleading.
case "$VERB" in
  start)
    [[ "$HAVE_LANE" == yes && -n "$LANE" ]] || refuse "start needs --lane" \
      "the lane is how a row on the board is named: cloud-heartbeat.sh start --lane <name>"
    [[ "$HAVE_KIND" == no && "$HAVE_NEEDS" == no && "$HAVE_DETAIL" == no ]] || \
      refuse "--kind/--needs/--detail belong to \`blocked\`" "for start, use --note"
    [[ "$UNBLOCK" == no ]] || refuse "--unblocked belongs to \`beat\`" "start already clears any blocker"
    ;;
  beat)
    [[ "$HAVE_KIND" == no && "$HAVE_NEEDS" == no && "$HAVE_DETAIL" == no ]] || \
      refuse "--kind/--needs/--detail belong to \`blocked\`" \
             "if you are blocked, say so: cloud-heartbeat.sh blocked --kind <kind> --detail \"...\"" \
             "if you are not, use --note/--phase"
    ;;
  blocked)
    [[ "$HAVE_KIND" == yes && -n "$KIND" ]] || refuse "blocked needs --kind" "one of: $BLOCKER_KINDS"
    [[ "$HAVE_DETAIL" == yes && -n "$DETAIL" ]] || refuse "blocked needs --detail" \
      "say concretely what is blocking you — a blocker with no detail cannot be acted on"
    _known=no
    for k in $BLOCKER_KINDS; do [[ "$KIND" == "$k" ]] && _known=yes; done
    [[ "$_known" == yes ]] || refuse "unknown blocker kind: $KIND" \
      "use one of: $BLOCKER_KINDS" \
      "nothing was recorded — re-run with a valid kind so the blocker is not lost"
    [[ "$UNBLOCK" == no ]] || refuse "--unblocked contradicts \`blocked\`" "use: beat --unblocked"
    [[ "$HAVE_NEEDS" == yes ]] || warn "no --needs given: recording a blocker without saying what would clear it. Add --needs \"...\" next beat."
    ;;
  done)
    [[ "$HAVE_DETAIL" == no ]] || refuse "\`done\` takes --note, not --detail" "--detail is for blocked/failed"
    [[ "$HAVE_KIND" == no && "$HAVE_NEEDS" == no ]] || refuse "--kind/--needs belong to \`blocked\`"
    [[ "$UNBLOCK" == no ]] || refuse "--unblocked belongs to \`beat\`"
    ;;
  failed)
    [[ "$HAVE_DETAIL" == yes && -n "$DETAIL" ]] || refuse "failed needs --detail" \
      "say what broke — \"failed\" with no detail is indistinguishable from a crash"
    [[ "$HAVE_KIND" == no && "$HAVE_NEEDS" == no ]] || refuse "--kind/--needs belong to \`blocked\`"
    [[ "$UNBLOCK" == no ]] || refuse "--unblocked belongs to \`beat\`"
    ;;
esac

if [[ "$HAVE_PROGRESS" == yes ]]; then
  [[ "$PROGRESS" =~ ^[0-9]+/[0-9]+$ ]] || refuse "--progress must look like done/total, e.g. 3/8" \
    "got: $PROGRESS"
  PROG_DONE="${PROGRESS%%/*}"; PROG_TOTAL="${PROGRESS##*/}"
  [[ "$PROG_TOTAL" != "0" ]] || refuse "--progress total cannot be 0" "got: $PROGRESS"
else
  PROG_DONE=""; PROG_TOTAL=""
fi

# ------------------------------------------------------------------ session id
SID="${SID:-${CLAUDE_CODE_SESSION_ID:-}}"
[[ -n "$SID" ]] || refuse "no session id" \
  "\$CLAUDE_CODE_SESSION_ID is unset and --session was not passed." \
  "A beat keyed on nothing cannot be matched to a session, so the operator would see" \
  "an anonymous row that no dispatch record can explain — worse than no row at all." \
  "Run this from inside the session, or pass:  cloud-heartbeat.sh $VERB --session <id> ..."

# The id becomes a filename AND a git ref, so it is validated, not trusted.
[[ "$SID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || refuse "session id is not usable as a filename or a git ref: $SID" \
  "allowed: letters, digits, dot, dash, underscore; must start alphanumeric."
[[ "$SID" != *".."* ]] || refuse "session id contains '..': $SID"
[[ "${#SID}" -le 128 ]] || refuse "session id longer than 128 characters"

# ----------------------------------------------------------------- preconditions
command -v git >/dev/null 2>&1 || refuse "no \`git\` on PATH" \
  "git push is the only channel by which a beat survives this VM."
command -v python3 >/dev/null 2>&1 || refuse "no \`python3\` on PATH" \
  "python3 encodes and merges the state file; hand-rolled JSON quoting in bash is a" \
  "corruption waiting to happen. Install python3 or drop the emitter from this dispatch."

REPO=""
if REPO_RAW="$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null)"; then
  REPO="$(cd -P "$REPO_RAW" && pwd)"
fi
[[ -n "$REPO" ]] || refuse "not inside a git repository" \
  "$HERE is not in a git working tree, and a beat that cannot be committed cannot be pushed." \
  "Run the emitter from the clone the agent is working in."

case "$FLEET_DIR/" in
  "$REPO"/*) ;;
  *) refuse "the state directory is outside the repository" \
       "fleet dir: $FLEET_DIR" "repo: $REPO" \
       "The beat is pushed as a repo-relative path; it must live inside the repo." ;;
esac
REL_DIR="${FLEET_DIR#$REPO/}"
REL_FILE="$REL_DIR/$SID.json"
STATE_FILE="$FLEET_DIR/$SID.json"

git -C "$REPO" remote get-url origin >/dev/null 2>&1 || refuse "no \`origin\` remote" \
  "This VM's telemetry dies with the VM. Without a remote there is nowhere to push a" \
  "beat to, so the agent would look tracked while being invisible — the exact failure" \
  "this script exists to prevent. Add an origin, or dispatch without the emitter."

mkdir -p "$FLEET_DIR" 2>/dev/null || refuse "cannot create $FLEET_DIR" \
  "check permissions on $ROOT/state"
[[ -w "$FLEET_DIR" ]] || refuse "$FLEET_DIR is not writable"

# The fleet dir is local staging on every machine: on the VM it is what gets pushed,
# on the laptop it is a read cache of what was fetched. Ignoring *.json here keeps a
# `git add -A` in the agent's real work from sweeping telemetry into its PR — the
# push path uses plumbing and is unaffected by ignore rules.
if [[ ! -f "$FLEET_DIR/.gitignore" ]]; then
  cat >"$FLEET_DIR/.gitignore" <<'GITIGNORE' 2>/dev/null || true
# Beat files are staged here and pushed to refs/heads/cloud-fleet/<session-id> by
# ../../bin/cloud-heartbeat.sh, and fetched back here as a read cache. They are
# deliberately NOT tracked on a work branch: telemetry must not enter a PR diff.
*.json
*.json.corrupt-*
.locks/
GITIGNORE
fi

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/cloud-heartbeat.XXXXXX")" || refuse "cannot create a temp dir"

# ------------------------------------------------------------ facts about the VM
# Deliberately NOT recorded: the origin URL. A remote can carry credentials in its
# userinfo, and this file is pushed to git. Branch and short sha are enough.
HOSTNAME_="$(uname -n 2>/dev/null || echo unknown)"
OS_="$(uname -sr 2>/dev/null || echo unknown) $(uname -m 2>/dev/null || echo '')"
USER_="$(id -un 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
GIT_HEAD="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# ---------------------------------------------------------------- the state file
# One writer per session is the normal case, but a subagent can inherit the parent's
# $CLAUDE_CODE_SESSION_ID. mkdir is atomic everywhere; flock is not on macOS. If the
# lock cannot be taken we proceed anyway — a slightly raced counter beats a lost beat.
LOCK="$FLEET_DIR/.locks/$SID"
mkdir -p "$FLEET_DIR/.locks" 2>/dev/null || true
LOCKED=no
_i=0
while [[ $_i -lt 6 ]]; do
  if mkdir "$LOCK" 2>/dev/null; then LOCKED=yes; break; fi
  sleep 0.3
  _i=$((_i + 1))
done
[[ "$LOCKED" == yes ]] || warn "another emitter holds the lock for $SID; writing anyway"

release_lock() { [[ "$LOCKED" == yes ]] && rmdir "$LOCK" 2>/dev/null; return 0; }

WRITE_RC=0
CH_FILE="$STATE_FILE" CH_VERB="$VERB" CH_SID="$SID" \
CH_SCHEMA="$SCHEMA_VERSION" CH_EMITTER="$EMITTER_VERSION" \
CH_LANE="$LANE" CH_HAVE_LANE="$HAVE_LANE" \
CH_NOTE="$NOTE" CH_HAVE_NOTE="$HAVE_NOTE" \
CH_PHASE="$PHASE" CH_HAVE_PHASE="$HAVE_PHASE" \
CH_DONE="$PROG_DONE" CH_TOTAL="$PROG_TOTAL" CH_HAVE_PROGRESS="$HAVE_PROGRESS" \
CH_KIND="$KIND" CH_DETAIL="$DETAIL" CH_NEEDS="$NEEDS" CH_HAVE_NEEDS="$HAVE_NEEDS" \
CH_UNBLOCK="$UNBLOCK" \
CH_HOSTNAME="$HOSTNAME_" CH_OS="$OS_" CH_USER="$USER_" \
CH_BRANCH="$GIT_BRANCH" CH_HEAD="$GIT_HEAD" \
python3 - <<'PY' || WRITE_RC=$?
import datetime as dt, json, os, sys

path   = os.environ["CH_FILE"]
verb   = os.environ["CH_VERB"]
sid    = os.environ["CH_SID"]
now    = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def env(k):  return os.environ.get(k, "")
def has(k):  return os.environ.get(k) == "yes"

# --- load prior state; a corrupt file is superseded, never silently discarded ---
state = None
if os.path.exists(path):
    try:
        with open(path) as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            raise ValueError("top level is not an object")
    except (ValueError, OSError) as exc:
        keep = path + ".corrupt-" + now.replace(":", "").replace("-", "")
        try:
            os.rename(path, keep)
            sys.stderr.write("cloud-heartbeat: prior state unreadable (%s); kept it at %s\n" % (exc, keep))
        except OSError:
            sys.stderr.write("cloud-heartbeat: prior state unreadable (%s); overwriting\n" % exc)
        state = None

fresh = state is None
if fresh:
    if verb != "start":
        sys.stderr.write("cloud-heartbeat: no prior `start` for this session; opening the record at `%s`\n" % verb)
    state = {
        "schema": int(env("CH_SCHEMA")),
        "kind": "cloud-heartbeat",
        "session_id": sid,
        "lane": sid,
        "status": "ACTIVE",
        "phase": "",
        "note": "",
        "progress": None,
        "blocker": None,
        "failure": None,
        "started_at": now,
        "updated_at": now,
        "ended_at": None,
        "beats": 0,
        "last_verb": verb,
        "history": [],
    }

# schema/identity are re-asserted every write: an older emitter must not leave a
# file that claims a schema it did not write.
state["schema"] = int(env("CH_SCHEMA"))
state["kind"] = "cloud-heartbeat"
state["session_id"] = sid
state.setdefault("history", [])
state.setdefault("beats", 0)
state.setdefault("started_at", now)
state.setdefault("blocker", None)
state.setdefault("failure", None)
state.setdefault("progress", None)
state.setdefault("ended_at", None)

if has("CH_HAVE_LANE") and env("CH_LANE"):
    state["lane"] = env("CH_LANE")[:200]
if has("CH_HAVE_NOTE"):
    state["note"] = env("CH_NOTE")[:2000]
if has("CH_HAVE_PHASE"):
    state["phase"] = env("CH_PHASE")[:2000]
if has("CH_HAVE_PROGRESS"):
    state["progress"] = {"done": int(env("CH_DONE")), "total": int(env("CH_TOTAL"))}

detail = env("CH_DETAIL")[:4000]

if verb == "start":
    state["started_at"] = now
    state["ended_at"] = None
    state["failure"] = None
    state["blocker"] = None
    state["status"] = "ACTIVE"

elif verb == "beat":
    if state.get("status") in ("DONE", "FAILED"):
        # A beat after an end is not an error — a wrapper may still be looping — but
        # it must not resurrect a finished run as ACTIVE without saying so.
        sys.stderr.write("cloud-heartbeat: this session was already %s; reopening as ACTIVE\n" % state["status"])
        state["ended_at"] = None
    if env("CH_UNBLOCK") == "yes":
        if state.get("blocker"):
            b = dict(state["blocker"])
            b["cleared_at"] = now
            state.setdefault("cleared_blockers", []).append(b)
            state["cleared_blockers"] = state["cleared_blockers"][-10:]
        state["blocker"] = None
        state["status"] = "ACTIVE"
    elif state.get("blocker"):
        state["status"] = "BLOCKED"      # blocked-and-alive: still beating, still stuck
    else:
        state["status"] = "ACTIVE"

elif verb == "blocked":
    prior = state.get("blocker") or {}
    same = prior.get("kind") == env("CH_KIND") and prior.get("detail") == detail
    state["blocker"] = {
        "kind": env("CH_KIND"),
        "detail": detail,
        "needs": env("CH_NEEDS")[:2000] if has("CH_HAVE_NEEDS") else (prior.get("needs", "") if same else ""),
        "since": prior.get("since", now) if same else now,
        "restated_at": now,
    }
    state["status"] = "BLOCKED"
    state["ended_at"] = None

elif verb == "done":
    state["status"] = "DONE"
    state["ended_at"] = now

elif verb == "failed":
    state["status"] = "FAILED"
    state["ended_at"] = now
    state["failure"] = {"detail": detail, "at": now}

state["updated_at"] = now
state["beats"] = int(state.get("beats", 0)) + 1
state["last_verb"] = verb
state["host"] = {"hostname": env("CH_HOSTNAME"), "os": env("CH_OS").strip(), "user": env("CH_USER")}
state["git"] = {"branch": env("CH_BRANCH"), "head": env("CH_HEAD")}
state["emitter"] = {"name": "cloud-heartbeat.sh", "version": int(env("CH_EMITTER"))}

entry = {"ts": now, "verb": verb, "status": state["status"]}
if state.get("phase"):
    entry["phase"] = state["phase"][:200]
if verb == "blocked":
    entry["kind"] = env("CH_KIND")
    entry["detail"] = detail[:200]
if verb == "failed":
    entry["detail"] = detail[:200]
state["history"].append(entry)
state["history"] = state["history"][-40:]      # bounded: the file stays small and cheap

tmp = path + ".tmp.%d" % os.getpid()
with open(tmp, "w") as fh:
    json.dump(state, fh, indent=2, sort_keys=True)
    fh.write("\n")
os.replace(tmp, path)                          # atomic: a reader never sees a half file

prog = ""
if state.get("progress"):
    prog = " %d/%d" % (state["progress"]["done"], state["progress"]["total"])
blk = ""
if state.get("blocker"):
    blk = " [%s: %s]" % (state["blocker"]["kind"], state["blocker"]["detail"][:60])
print("cloud-heartbeat: %s lane=%s beat=%d%s%s" %
      (state["status"], state.get("lane", "?"), state["beats"], prog, blk))
PY

release_lock

if [[ $WRITE_RC -ne 0 ]]; then
  warn "could not write $STATE_FILE (python3 exit $WRITE_RC) — nothing pushed. Your work is unaffected."
  ok_exit
fi

# ------------------------------------------------------------------- the push
# Everything below is plumbing on purpose. No `git add`, no `git commit`, no
# checkout, no stash: the agent's index, HEAD and working tree are never touched.
# The only mutation of the agent's repo is loose objects in .git (gc-able) and one
# local ref under refs/cloud-fleet/, which is invisible to `git branch`.

REMOTE_REF="refs/heads/$FLEET_NS/$SID"
LOCAL_REF="refs/$FLEET_NS/$SID"

# Telemetry commits are machine-authored and say so. `.invalid` is the reserved
# never-resolvable TLD (RFC 2606), so this can never be mistaken for a real address
# and never inherits whatever identity the VM happens to have configured.
export GIT_AUTHOR_NAME="cloud-heartbeat" GIT_AUTHOR_EMAIL="cloud-heartbeat@invalid"
export GIT_COMMITTER_NAME="cloud-heartbeat" GIT_COMMITTER_EMAIL="cloud-heartbeat@invalid"

redact() { sed -E 's#//[^/@[:space:]]+@#//<redacted>@#g'; }   # a remote URL can carry a token

build_tree() {
  # $1 blob sha, $2 parent commit (may be empty). Reads the parent's tree first so
  # anything else already on that ref survives — this is a supersede, not a replace.
  local blob="$1" parent="$2" idx="$TMPD/index"
  rm -f "$idx"
  (
    export GIT_INDEX_FILE="$idx"
    if [[ -n "$parent" ]]; then
      git -C "$REPO" read-tree "$parent" 2>/dev/null || exit 1
    fi
    git -C "$REPO" update-index --add --cacheinfo "100644,$blob,$REL_FILE" 2>/dev/null || exit 1
    git -C "$REPO" write-tree 2>/dev/null || exit 1
  )
}

BLOB=""
if ! BLOB="$(git -C "$REPO" hash-object -w --path "$REL_FILE" -- "$STATE_FILE" 2>&1)"; then
  warn "could not store the beat as a git object: $(printf '%s' "$BLOB" | redact | head -1)"
  warn "the beat is on disk at $STATE_FILE but will die with this VM."
  ok_exit
fi

PARENT="$(git -C "$REPO" rev-parse -q --verify "$LOCAL_REF" 2>/dev/null || true)"
MSG="$VERB $SID"
if [[ -n "$LANE" ]]; then MSG="$VERB $LANE ($SID)"; fi

attempt=1
while :; do
  TREE=""
  if ! TREE="$(build_tree "$BLOB" "$PARENT")" || [[ -z "$TREE" ]]; then
    if [[ -n "$PARENT" ]]; then
      # A local ref pointing at an object this clone no longer has: start a new root
      # rather than give up. The remote keeps its history either way.
      warn "cannot read the previous beat commit; starting a fresh chain for this session"
      PARENT=""; continue
    fi
    warn "could not build the beat tree — nothing pushed. Your work is unaffected."
    ok_exit
  fi

  COMMIT=""
  if [[ -n "$PARENT" ]]; then
    COMMIT="$(git -C "$REPO" commit-tree "$TREE" -p "$PARENT" -m "$MSG" 2>/dev/null || true)"
  else
    COMMIT="$(git -C "$REPO" commit-tree "$TREE" -m "$MSG" 2>/dev/null || true)"
  fi
  if [[ -z "$COMMIT" ]]; then
    warn "could not create the beat commit — nothing pushed. Your work is unaffected."
    ok_exit
  fi

  PUSH_OUT=""; PUSH_RC=0
  PUSH_OUT="$(git -C "$REPO" push --quiet origin "$COMMIT:$REMOTE_REF" 2>&1)" || PUSH_RC=$?

  if [[ $PUSH_RC -eq 0 ]]; then
    git -C "$REPO" update-ref "$LOCAL_REF" "$COMMIT" 2>/dev/null || true
    printf 'cloud-heartbeat: pushed %s -> %s/%s\n' "${COMMIT:0:8}" "$FLEET_NS" "$SID"
    ok_exit
  fi

  CLEAN="$(printf '%s' "$PUSH_OUT" | redact)"
  case "$PUSH_OUT" in
    *"[rejected]"*|*non-fast-forward*|*"fetch first"*|*"cannot lock ref"*|*"Updates were rejected"*)
      # Somebody else wrote this ref: a resumed session on another VM, a second
      # worktree, or a subagent sharing the id. Reparent onto their commit and retry.
      if [[ $attempt -ge $PUSH_ATTEMPTS ]]; then
        warn "gave up after $PUSH_ATTEMPTS attempts: $FLEET_NS/$SID is being written by someone else"
        warn "  and each retry lost the race. This beat is on disk at $STATE_FILE but is NOT"
        warn "  visible to the operator. Two writers on one session id usually means two VMs"
        warn "  share \$CLAUDE_CODE_SESSION_ID — pass a distinct --session to one of them."
        warn "  last git message: $(printf '%s' "$CLEAN" | tail -1)"
        ok_exit
      fi
      git -C "$REPO" fetch --quiet --force origin "+$REMOTE_REF:$LOCAL_REF" 2>/dev/null || true
      PARENT="$(git -C "$REPO" rev-parse -q --verify "$LOCAL_REF" 2>/dev/null || true)"
      sleep "0.$(( (RANDOM % 6) + 2 ))"
      attempt=$((attempt + 1))
      ;;
    *)
      # Offline, no credentials, remote gone: retrying is not progress, and this is
      # never fatal to the agent's work.
      warn "push failed (git exit $PUSH_RC) — the beat is on disk at $STATE_FILE but"
      warn "  cannot reach the operator; it will die with this VM. Keep working."
      warn "  git said: $(printf '%s' "$CLEAN" | grep -v '^$' | tail -2 | tr '\n' ' ')"
      ok_exit
      ;;
  esac
done
