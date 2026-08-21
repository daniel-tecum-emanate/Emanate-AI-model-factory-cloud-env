#!/usr/bin/env bash
# cloud-claim.sh — a mutual-exclusion claim on one (org_slug, model_stream), so two
# cloud sessions (a retry, a stale-looking survivor, a second operator) never both
# advance the same fine-tune pipeline run at once.
#
# Design: PRs/model-factory-cloud-environment-v1/04-multi-session-coordination-design.md
# (§3.6-3.11). This script mirrors ../bin/cloud-heartbeat.sh's own plumbing exactly —
# same private-index/hash-object/write-tree/commit-tree commit construction, same JSON
# schema-versioning convention, same mechanically-extractable dispatch-block header —
# but answers a different question. A heartbeat ref answers "is this session alive?",
# keyed on session id. A claim ref answers "is this org+stream already being worked, by
# whom, at what stage, and is that claim still good?", keyed on (org_slug, model_stream).
# A claim never carries its own liveness clock — it points at the holding session's own
# heartbeat ref and *that* is dereferenced for staleness, exactly once, in exactly one
# place (the `resolve` step below), so the two mechanisms can never quietly disagree
# about whether someone is still alive.
#
#   cloud-claim.sh check   --org <slug> [--stream per-account|intelligence]
#   cloud-claim.sh claim   --org <slug> [--stream ...] --stage <stage> [--run-id <uuid>] [--lane <name>]
#   cloud-claim.sh renew   --org <slug> [--stream ...] --stage <stage> [--run-id <uuid>]
#   cloud-claim.sh release --org <slug> [--stream ...] --reason done|failed|handoff
#   cloud-claim.sh help
#
# `--org`/`--stream` are required on EVERY verb, including renew/release — unlike
# cloud-heartbeat.sh, where only `start` needs `--lane` because that ref is already keyed
# on session id for every later verb. Here the ref is keyed on the resource, not the
# session, so which resource is never implicit.
#
# Session id comes from $CLAUDE_CODE_SESSION_ID, or --session <id> on any verb.
#
# ---------------------------------------------------------------------------
# THE DISPATCH BLOCK — paste this into a routine payload / cloud prompt, immediately
# BEFORE the existing cloud-heartbeat.sh block (this must run first). Extract it
# mechanically, the same way cloud-heartbeat.sh's own block is extracted:
#   sed -n '/^# ```text$/,/^# ```$/p' cloud-claim.sh | sed -e '1d' -e '$d' -e 's/^# \{0,1\}//'
#
# ```text
# ## Fleet claim check — required, and it runs BEFORE anything else
#
#   git fetch origin \
#     '+refs/heads/cloud-claims/*:refs/remotes/origin/cloud-claims/*' \
#     '+refs/heads/cloud-fleet/*:refs/remotes/origin/cloud-fleet/*'
#
#   bash cloud-sessions/bin/cloud-claim.sh check --org <ORG> --stream <STREAM>
#
# Read the exit code before doing anything else:
#
#   0  FREE or MINE  -> claim it, then start your heartbeat, then begin real work:
#        bash cloud-sessions/bin/cloud-claim.sh claim \
#          --org <ORG> --stream <STREAM> --stage <FIRST_STAGE> --run-id <RUN_ID> --lane <LANE>
#        bash cloud-sessions/bin/cloud-heartbeat.sh start --lane <LANE> --note "claimed <ORG>/<STREAM>"
#
#   1  STALE   -> the previous holder looks dead (details printed). Claim it anyway --
#        cloud-claim.sh claim records exactly what you're superseding -- then proceed
#        as above. Do not silently skip this step because it "seems fine."
#
#   3  LIVE    -> STOP. Someone else is already working this org+stream. Do not run
#        factory.py, do not run run_pipeline.py, do not touch this org's data. Say so:
#          bash cloud-sessions/bin/cloud-heartbeat.sh blocked \
#            --kind dependency-missing \
#            --detail "org=<ORG> stream=<STREAM> already claimed (see cloud-claim.sh check output)" \
#            --needs "wait for release, or a human decides to force-clear it"
#        Then stop. This is not a failure -- it is the system working.
#
#   2  refused -> a usage/precondition problem (bad org/stream name, no origin, not a
#        git repo). Fix the dispatch, do not retry blindly.
#
# On every later stage transition, renew the claim in the same breath as your heartbeat
# beat:
#
#   bash cloud-sessions/bin/cloud-claim.sh renew --org <ORG> --stream <STREAM> --stage <STAGE> --run-id <RUN_ID>
#   bash cloud-sessions/bin/cloud-heartbeat.sh beat --phase "<STAGE>" --progress <N>/<TOTAL>
#
# At the end, whichever is true, release before your terminal heartbeat call:
#
#   bash cloud-sessions/bin/cloud-claim.sh release --org <ORG> --stream <STREAM> --reason done
#   bash cloud-sessions/bin/cloud-heartbeat.sh done --note "..."
#
#   bash cloud-sessions/bin/cloud-claim.sh release --org <ORG> --stream <STREAM> --reason failed
#   bash cloud-sessions/bin/cloud-heartbeat.sh failed --detail "..."
# ```
# ---------------------------------------------------------------------------
#
# WHY A DEDICATED REF NAMESPACE, SIBLING TO cloud-fleet/* NOT NESTED IN IT
#
# refs/heads/cloud-claims/<org_slug>/<model_stream> — always exactly two path segments
# after the namespace, never zero, never one, never three. This is a hard git rule, not
# style: a ref cannot be a strict prefix of another (refs/heads/cloud-claims/acme and
# refs/heads/cloud-claims/acme/per-account cannot coexist), so model_stream is always
# written explicitly, even for the default `per-account`, to avoid ever creating a bare
# cloud-claims/<org> leaf that a later stream-qualified push for the same org would
# collide with. org_slug and model_stream are each validated with the exact regex
# cloud-heartbeat.sh already uses for session ids — each becomes both a ref path segment
# and a filename, so each is validated, not trusted.
#
# Session-id sharding for heartbeats exists so "one writer per ref" makes collisions rare
# by construction. Org+stream sharding for claims exists for the OPPOSITE reason: the
# ref's identity has to equal the resource's identity, or mutual exclusion means nothing —
# a claim ref is deliberately the one thing multiple sessions, over time, contend for.
#
# The write path never touches this agent's HEAD, index, or working tree, exactly like
# cloud-heartbeat.sh: hash-object -w the JSON blob, build a tree via a private
# GIT_INDEX_FILE (read-tree the parent if one exists, update-index --add --cacheinfo the
# one blob, write-tree), commit-tree, push by sha. History is never force-pushed away —
# `git log refs/heads/cloud-claims/<org>/<stream>` stays a complete, tamper-evident record
# of every session that ever held this org+stream.
#
# THE ONE PIECE THAT IS GENUINELY LOAD-BEARING: NOT FORCE-PUSHED, AND WHY THAT MATTERS
# MECHANICALLY, NOT JUST STYLISTICALLY
#
# A plain (non-`+`) `git push <sha>:<ref>` asserts an expected old value for the ref — for
# a session that read the ref as absent, that assertion is "this ref does not yet exist"
# (the all-zero old-sha git sends for a new ref); for a session that read it as present,
# the assertion is "the ref is still at the commit I just read." If another session's
# `claim` landed in the meantime, the remote's actual value no longer matches that
# assertion, and the push is rejected with the same [rejected]/"fetch first"/non-fast-
# forward family of errors cloud-heartbeat.sh already parses for its own ref-reuse case.
# This is the SAME git guarantee that already makes cloud-heartbeat.sh's collision
# handling work; nothing new is assumed about git here.
#
# On rejection, `claim` (unlike cloud-heartbeat.sh) does NOT reparent onto the winner and
# retry — a rejection on a claim ref means someone else's claim landed, and reparenting
# onto it would silently steal it. Instead it re-fetches, reads the winner's claim, and
# re-derives FREE/MINE/STALE/LIVE against it: if the winner is live and not mine, this
# session lost the race fairly and refuses (exit 3) without pushing anything; if the
# winner is itself already stale or released, this session retries its own claim parented
# on the winner's commit, bounded at the same PUSH_ATTEMPTS as cloud-heartbeat.sh, then
# gives up loudly rather than spin. `renew`/`release`, by contrast, only ever write a ref
# THEY already verified they hold, so a rejection there means a benign double-write of
# their own history (a network retry, a second writer sharing a session id) — exactly
# cloud-heartbeat.sh's own case — and DOES reparent-and-retry.
#
# STALENESS: THE CLOUD-NATIVE EQUIVALENT OF THE 30-MINUTE LOCAL LEASE
#
# A claim is only valid if the held_by session's own cloud-fleet/<held_by> heartbeat ref
# shows `updated_at` within STALE_MIN minutes and its last `status` is not DONE or FAILED.
# 30 minutes, matching coordination/heartbeat/STATE.md's local lease exactly — not
# cs fleet's own --stale-after default of 15 — because a claim gates potentially-costly
# real work (the train stage, dispatch_node's real spend path), so the more conservative
# of the two existing numbers is the right default: a false STALE (reclaiming a session
# that's actually fine) risks a real collision; a false LIVE (waiting an extra few minutes
# on a session that's actually dead) only costs time.
#
# EXIT CODES — deliberately NOT the same "always 0 unless refused" shape as
# cloud-heartbeat.sh, because 0 here is a specific, actionable answer (FREE or MINE — go
# ahead) that a calling agent branches on, not mere delivery-confirmation of telemetry. A
# degraded condition must never read as that answer (the same rule the rest of this repo
# calls ground rule 4), so an internal bug in this script is treated as a refusal (exit 2),
# not silently swallowed to 0 the way cloud-heartbeat.sh swallows its own bugs — swallowing
# to 0 here would tell a caller "FREE, proceed" on a script crash, which is exactly the
# failure mode this whole mechanism exists to prevent.
#
#   check:           0 FREE/MINE · 1 STALE · 2 refusal (nothing checked) · 3 LIVE
#   claim:            0 claimed  ·   —     · 2 refusal (nothing recorded) · 3 refused (LIVE)
#   renew / release:  0 success, incl. degrade-and-warn on a failed push (telemetry about
#                       an already-held claim must never fail the agent's real work, same
#                       rule as cloud-heartbeat.sh) · 2 refusal, incl. "you don't hold this
#                       claim" (nothing recorded)
#
# `check` never writes anything, ever — it is the read-only probe a newly dispatched agent
# (or a human) runs to decide what to do next.

set -euo pipefail

EMITTER_VERSION=1
SCHEMA_VERSION=1
CLAIMS_NS="cloud-claims"        # ref namespace: refs/heads/cloud-claims/<org>/<stream>
FLEET_NS="cloud-fleet"          # dereferenced for staleness only; never written here
PUSH_ATTEMPTS=4                 # bounded: 1 try + 3 reparent-and-retry, same as cloud-heartbeat.sh
STALE_MIN=30                    # minutes; matches heartbeat_validate.sh's local lease, not
                                 # cs fleet's own --stale-after default of 15 (see header)
VALID_STREAMS="per-account intelligence"
VALID_REASONS="done failed handoff"

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -P "$HERE/.." && pwd)"          # the cloud-sessions/ folder, wherever it was ported to
CLAIMS_DIR="$ROOT/state/claims"
FLEET_DIR="$ROOT/state/fleet"

INTENTIONAL=no
FINAL_RC=0
TMPD=""

# Belt and braces, but the OPPOSITE belt from cloud-heartbeat.sh's: an unhandled failure
# here must not read as "FREE, go ahead" (exit 0) or as any other meaningful answer, so it
# is treated as a refusal (exit 2) rather than suppressed to 0. See EXIT CODES above.
finish() {
  local rc=$?
  if [[ -n "$TMPD" ]]; then rm -rf "$TMPD" 2>/dev/null || true; fi
  if [[ "$INTENTIONAL" == "yes" ]]; then exit "$FINAL_RC"; fi
  if [[ $rc -ne 0 ]]; then
    printf 'cloud-claim: internal error (exit %d) — treated as a refusal so it cannot be\n' "$rc" >&2
    printf 'cloud-claim: mistaken for a real answer. Nothing was checked or recorded.\n' >&2
    exit 2
  fi
  exit 0
}
trap finish EXIT

warn() { printf 'cloud-claim: %s\n' "$*" >&2; }
info() { printf 'cloud-claim: %s\n' "$*"; }

ok_exit()    { INTENTIONAL=yes; FINAL_RC=0; exit 0; }   # FREE / MINE / claimed / renewed / released
stale_exit() { INTENTIONAL=yes; FINAL_RC=1; exit 1; }   # check only
live_exit()  { INTENTIONAL=yes; FINAL_RC=3; exit 3; }   # check: LIVE · claim: refused, lost the race

# Refusals are loud and non-zero on purpose: they mean nothing was checked or recorded.
refuse() {
  local headline="$1"; shift
  printf 'cloud-claim: REFUSED — %s\n' "$headline" >&2
  local line
  for line in "$@"; do printf '  %s\n' "$line" >&2; done
  INTENTIONAL=yes; FINAL_RC=2; exit 2
}

usage() {
  cat <<'USAGE'
cloud-claim.sh — a mutual-exclusion claim on one (org_slug, model_stream), so two cloud
                 sessions never both advance the same fine-tune pipeline run at once.
                 Sibling to cloud-heartbeat.sh: same plumbing, a different question.

  cloud-claim.sh check   --org <slug> [--stream per-account|intelligence]
  cloud-claim.sh claim   --org <slug> [--stream ...] --stage <stage> [--run-id <uuid>] [--lane <name>]
  cloud-claim.sh renew   --org <slug> [--stream ...] --stage <stage> [--run-id <uuid>]
  cloud-claim.sh release --org <slug> [--stream ...] --reason done|failed|handoff
  cloud-claim.sh help

  --session <id>   override $CLAUDE_CODE_SESSION_ID (valid on every verb)
  --stream          one of: per-account | intelligence (default: per-account)

Exit codes:
  check    0 FREE or MINE (go ahead)  ·  1 STALE (reclaimable)  ·  3 LIVE (someone else)
           2 refusal — a usage/precondition problem, nothing was checked
  claim    0 claimed (including a reclaim from STALE)  ·  3 refused, a live claim exists
           2 refusal — nothing was recorded
  renew    0 renewed, or a failed push degraded to a warning (the claim itself is
             unaffected — staleness is judged by the heartbeat ref, not this ref's age)
           2 refusal, including "you do not hold this claim" — nothing was recorded
  release  same as renew

`check` never writes anything. See this script's own header comment for the exact
dispatch-block text to paste into a routine payload, and for why the push on `claim` is
never forced.
USAGE
}

# --------------------------------------------------------------- argument parse
[[ $# -ge 1 ]] || { usage >&2; INTENTIONAL=yes; FINAL_RC=2; exit 2; }

VERB="$1"; shift
case "$VERB" in
  help|-h|--help) usage; ok_exit ;;
  check|claim|renew|release) ;;
  *) printf 'cloud-claim: unknown verb: %s\n\n' "$VERB" >&2; usage >&2
     INTENTIONAL=yes; FINAL_RC=2; exit 2 ;;
esac

SID=""; ORG=""; STREAM="per-account"; STAGE=""; RUN_ID=""; LANE=""; REASON=""
HAVE_ORG=no; HAVE_STREAM=no; HAVE_STAGE=no; HAVE_RUN_ID=no; HAVE_LANE=no; HAVE_REASON=no

need_arg() {
  # $1 flag, $2 remaining count
  [[ "$2" -ge 2 ]] || refuse "$1 needs a value" "usage: cloud-claim.sh $VERB ... $1 <value>"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)  need_arg "$1" $#; SID="$2";              shift 2 ;;
    --org)      need_arg "$1" $#; ORG="$2";     HAVE_ORG=yes;    shift 2 ;;
    --stream)   need_arg "$1" $#; STREAM="$2";  HAVE_STREAM=yes; shift 2 ;;
    --stage)    need_arg "$1" $#; STAGE="$2";   HAVE_STAGE=yes;  shift 2 ;;
    --run-id)   need_arg "$1" $#; RUN_ID="$2";  HAVE_RUN_ID=yes; shift 2 ;;
    --lane)     need_arg "$1" $#; LANE="$2";    HAVE_LANE=yes;   shift 2 ;;
    --reason)   need_arg "$1" $#; REASON="$2";  HAVE_REASON=yes; shift 2 ;;
    --) shift; break ;;
    *) refuse "unexpected argument: $1" "run: cloud-claim.sh help" ;;
  esac
done
[[ $# -eq 0 ]] || refuse "unexpected argument: $1" "run: cloud-claim.sh help"

# Per-verb flag rules — strict, same philosophy as cloud-heartbeat.sh: a flag that does
# not belong to this verb is refused rather than silently accepted or silently ignored.
[[ "$HAVE_ORG" == yes && -n "$ORG" ]] || refuse "$VERB needs --org" \
  "every verb needs --org: which resource is never implicit for a claim (unlike a heartbeat, which is keyed on session id already)"

case "$VERB" in
  check)
    [[ "$HAVE_STAGE" == no ]]   || refuse "--stage belongs to claim/renew"   "check is read-only"
    [[ "$HAVE_RUN_ID" == no ]]  || refuse "--run-id belongs to claim/renew"  "check is read-only"
    [[ "$HAVE_LANE" == no ]]    || refuse "--lane belongs to claim"         "check is read-only"
    [[ "$HAVE_REASON" == no ]]  || refuse "--reason belongs to release"     "check is read-only"
    ;;
  claim)
    [[ "$HAVE_STAGE" == yes && -n "$STAGE" ]] || refuse "claim needs --stage" \
      "the stage you are about to start at: cloud-claim.sh claim --org <slug> --stage <stage>"
    [[ "$HAVE_REASON" == no ]] || refuse "--reason belongs to release" "for claim, there is nothing to give a reason for yet"
    ;;
  renew)
    [[ "$HAVE_STAGE" == yes && -n "$STAGE" ]] || refuse "renew needs --stage" \
      "the stage you just transitioned to: cloud-claim.sh renew --org <slug> --stage <stage>"
    [[ "$HAVE_LANE" == no ]]   || refuse "--lane belongs to claim" "lane is set once, at claim time, and cannot change on renew"
    [[ "$HAVE_REASON" == no ]] || refuse "--reason belongs to release" "renew is not a terminal call"
    ;;
  release)
    [[ "$HAVE_REASON" == yes && -n "$REASON" ]] || refuse "release needs --reason" \
      "one of: $VALID_REASONS — a release with an unstated reason is indistinguishable from a crash"
    _known=no; for r in $VALID_REASONS; do [[ "$REASON" == "$r" ]] && _known=yes; done
    [[ "$_known" == yes ]] || refuse "unknown release reason: $REASON" "use one of: $VALID_REASONS"
    [[ "$HAVE_STAGE" == no ]]  || refuse "--stage belongs to claim/renew" "release does not change the stage"
    [[ "$HAVE_RUN_ID" == no ]] || refuse "--run-id belongs to claim/renew"
    [[ "$HAVE_LANE" == no ]]   || refuse "--lane belongs to claim"
    ;;
esac

# model_stream is one of exactly per-account | intelligence (design doc §3.3) — a closed
# vocabulary, refused on typo rather than silently opening a third, uncollided namespace.
_known_stream=no; for s in $VALID_STREAMS; do [[ "$STREAM" == "$s" ]] && _known_stream=yes; done
[[ "$_known_stream" == yes ]] || refuse "unknown --stream: $STREAM" "use one of: $VALID_STREAMS"

# ------------------------------------------------------------------ session id
SID="${SID:-${CLAUDE_CODE_SESSION_ID:-}}"
[[ -n "$SID" ]] || refuse "no session id" \
  "\$CLAUDE_CODE_SESSION_ID is unset and --session was not passed." \
  "A claim held by nobody identifiable is worse than no claim at all." \
  "Run this from inside the session, or pass:  cloud-claim.sh $VERB --session <id> ..."

# org_slug, model_stream and the session id all become both a ref path segment and a
# filename, so all three are validated, not trusted — the exact rule and regex
# cloud-heartbeat.sh already uses for session ids.
validate_slug() {
  local val="$1" label="$2"
  [[ "$val" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || refuse "$label is not usable as a filename or a git ref: $val" \
    "allowed: letters, digits, dot, dash, underscore; must start alphanumeric."
  [[ "$val" != *".."* ]] || refuse "$label contains '..': $val"
  [[ "${#val}" -le 128 ]] || refuse "$label longer than 128 characters"
}
validate_slug "$SID" "session id"
validate_slug "$ORG" "--org"
validate_slug "$STREAM" "--stream"

# ----------------------------------------------------------------- preconditions
command -v git >/dev/null 2>&1 || refuse "no \`git\` on PATH" \
  "git push is the only channel by which a claim survives this VM."
command -v python3 >/dev/null 2>&1 || refuse "no \`python3\` on PATH" \
  "python3 encodes, parses and merges the claim file; hand-rolled JSON quoting in bash is" \
  "a corruption waiting to happen. Install python3 or drop the claim check from this dispatch."

REPO=""
if REPO_RAW="$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null)"; then
  REPO="$(cd -P "$REPO_RAW" && pwd)"
fi
[[ -n "$REPO" ]] || refuse "not inside a git repository" \
  "$HERE is not in a git working tree, and a claim that cannot be committed cannot be pushed." \
  "Run this from the clone the agent is working in."

case "$CLAIMS_DIR/" in
  "$REPO"/*) ;;
  *) refuse "the state directory is outside the repository" \
       "claims dir: $CLAIMS_DIR" "repo: $REPO" \
       "The claim is pushed as a repo-relative path; it must live inside the repo." ;;
esac

git -C "$REPO" remote get-url origin >/dev/null 2>&1 || refuse "no \`origin\` remote" \
  "A claim that cannot reach origin cannot be seen by any other session, so it would" \
  "provide no protection at all — the exact failure this script exists to prevent." \
  "Add an origin, or dispatch without the claim check."

mkdir -p "$CLAIMS_DIR" 2>/dev/null || refuse "cannot create $CLAIMS_DIR" \
  "check permissions on $ROOT/state"
[[ -w "$CLAIMS_DIR" ]] || refuse "$CLAIMS_DIR is not writable"

# Mirrors cloud-heartbeat.sh's own fleet/.gitignore drop: claim files are staged here and
# pushed to refs/heads/cloud-claims/<org>/<stream>, then fetched back as a read cache —
# deliberately NOT tracked on a work branch, so a stray `git add -A` cannot sweep a claim
# snapshot into the agent's PR diff.
if [[ ! -f "$CLAIMS_DIR/.gitignore" ]]; then
  cat >"$CLAIMS_DIR/.gitignore" <<'GITIGNORE' 2>/dev/null || true
# Claim files are staged here and pushed to
# refs/heads/cloud-claims/<org>/<stream> by ../../bin/cloud-claim.sh, and fetched back
# here as a read cache. They are deliberately NOT tracked on a work branch: a claim
# snapshot must not enter a PR diff any more than a heartbeat beat does.
*.json
*.json.corrupt-*
GITIGNORE
fi

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/cloud-claim.XXXXXX")" || refuse "cannot create a temp dir"

CLAIM_REL="cloud-sessions/state/claims/$ORG/$STREAM.json"
CLAIM_FILE="$CLAIMS_DIR/$ORG/$STREAM.json"
mkdir -p "$(dirname "$CLAIM_FILE")" 2>/dev/null || refuse "cannot create $(dirname "$CLAIM_FILE")"

CLAIM_REMOTE_REF="refs/remotes/origin/$CLAIMS_NS/$ORG/$STREAM"
CLAIM_PUSH_REF="refs/heads/$CLAIMS_NS/$ORG/$STREAM"
CLAIM_LOCAL_REF="refs/$CLAIMS_NS/$ORG/$STREAM"     # this script's own cache of "last known good", never shown by `git branch`

# Telemetry/coordination commits are machine-authored and say so, exactly as
# cloud-heartbeat.sh's own commits do. `.invalid` is the reserved never-resolvable TLD
# (RFC 2606).
export GIT_AUTHOR_NAME="cloud-claim" GIT_AUTHOR_EMAIL="cloud-claim@invalid"
export GIT_COMMITTER_NAME="cloud-claim" GIT_COMMITTER_EMAIL="cloud-claim@invalid"

redact() { sed -E 's#//[^/@[:space:]]+@#//<redacted>@#g'; }   # a remote URL can carry a token

now_iso() { python3 -c 'import datetime as dt; print(dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"))'; }

# small, safe JSON-field readers — same idiom tests/run.sh already uses (ledger_field()):
# never hand-parse JSON in bash.
jf() { python3 -c 'import json,sys
try:
    v=json.load(open(sys.argv[1]))
    for k in sys.argv[2].split("."):
        v = v[k] if isinstance(v, dict) and k in v else None
        if v is None: break
    print(v if v is not None else "")
except Exception:
    print("")
' "$1" "$2"; }

# ---------------------------------------------------------------------- git fetch
# Both namespaces, one fetch, run BY THIS SCRIPT rather than left as a manual pre-step —
# the one deliberate departure from `cs fleet`'s own pattern (see header comment). check
# and claim need the fleet namespace too (to dereference a held claim's heartbeat);
# renew/release only ever touch their own already-held claim ref, so they fetch claims
# only. A fetch failure is refused (2) for every verb: this script would otherwise have
# to guess at a resource-identity question with stale or absent information, and ground
# rule 4 (never let a degraded condition read as a meaningful value) forbids that guess.
fetch_namespaces() {
  local want_fleet="$1"
  local -a refspecs=("+refs/heads/$CLAIMS_NS/*:refs/remotes/origin/$CLAIMS_NS/*")
  if [[ "$want_fleet" == yes ]]; then
    refspecs+=("+refs/heads/$FLEET_NS/*:refs/remotes/origin/$FLEET_NS/*")
  fi
  local out rc=0
  out="$(git -C "$REPO" fetch --quiet origin "${refspecs[@]}" 2>&1)" || rc=$?
  if [[ $rc -ne 0 ]]; then
    refuse "could not fetch $CLAIMS_NS/$FLEET_NS from origin" \
      "$(printf '%s' "$out" | redact | tail -3)" \
      "Without a fresh read this script cannot tell FREE from LIVE, and guessing wrong" \
      "in either direction is unsafe — retry once connectivity is back."
  fi
}

# ----------------------------------------------------------- resolve (check's steps 1-6)
# Prints exactly one line to stdout: RESULT=FREE|MINE|STALE|LIVE, followed by
# `key=value` lines (bash-sourceable) carrying whatever that result needs downstream.
# This is the ONE place staleness is ever decided — see header comment.
resolve() {
  fetch_namespaces yes

  if ! git -C "$REPO" rev-parse -q --verify "$CLAIM_REMOTE_REF" >/dev/null 2>&1; then
    printf 'RESULT=FREE\nREASON=no-prior-claim\n'
    return 0
  fi

  local claim_json="$TMPD/existing-claim.json"
  if ! git -C "$REPO" show "$CLAIM_REMOTE_REF:$CLAIM_REL" >"$claim_json" 2>"$TMPD/show.err"; then
    refuse "the claims ref exists but its content could not be read" \
      "ref: $CLAIM_REMOTE_REF" "path: $CLAIM_REL" \
      "$(cat "$TMPD/show.err" 2>/dev/null | redact | tail -3)" \
      "Refusing rather than guessing FREE or LIVE against unknown content."
  fi
  if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$claim_json" >/dev/null 2>"$TMPD/parse.err"; then
    refuse "the existing claim file is not valid JSON" \
      "ref: $CLAIM_REMOTE_REF" "path: $CLAIM_REL" \
      "$(cat "$TMPD/parse.err" 2>/dev/null | tail -3)" \
      "A corrupt claim is left exactly as found — supersede it by hand, not by this script guessing."
  fi

  local released_at held_by
  released_at="$(jf "$claim_json" released_at)"
  held_by="$(jf "$claim_json" held_by)"

  if [[ -n "$released_at" ]]; then
    printf 'RESULT=FREE\nREASON=released\nPREV_HELD_BY=%s\n' "$held_by"
    return 0
  fi
  if [[ "$held_by" == "$SID" ]]; then
    printf 'RESULT=MINE\nCLAIM_FILE=%s\n' "$claim_json"
    return 0
  fi

  # Not free, not mine: dereference the holder's OWN heartbeat ref, exactly once, here.
  local hb_ref="refs/remotes/origin/$FLEET_NS/$held_by"
  local hb_json="$TMPD/heartbeat.json"
  local hb_status="" hb_updated="" fresh=no
  if git -C "$REPO" rev-parse -q --verify "$hb_ref" >/dev/null 2>&1 \
     && git -C "$REPO" show "$hb_ref:cloud-sessions/state/fleet/$held_by.json" >"$hb_json" 2>/dev/null \
     && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$hb_json" >/dev/null 2>&1; then
    hb_status="$(jf "$hb_json" status)"
    hb_updated="$(jf "$hb_json" updated_at)"
    fresh="$(CC_NOW="$(now_iso)" CC_UPDATED="$hb_updated" CC_STALE_MIN="$STALE_MIN" python3 -c '
import datetime as dt, os, sys
upd = os.environ["CC_UPDATED"]
now = dt.datetime.fromisoformat(os.environ["CC_NOW"].replace("Z","+00:00"))
try:
    t = dt.datetime.fromisoformat(upd.replace("Z","+00:00"))
except Exception:
    print("no"); sys.exit(0)
age_min = (now - t).total_seconds() / 60.0
print("yes" if age_min <= float(os.environ["CC_STALE_MIN"]) else "no")
')"
  else
    hb_json=""
  fi

  # Only ACTIVE or BLOCKED (fresh) is LIVE — matching the design doc's own wording
  # (§3.7) exactly, and `cs fleet`'s own established convention (playbook 10): a status
  # this script does not recognise is never folded into "alive". DONE/FAILED are the
  # named terminal statuses; anything else unrecognised falls through to STALE too,
  # rather than being treated as a fresh third thing that might mean "still working".
  if [[ -n "$hb_json" && "$fresh" == yes && ( "$hb_status" == "ACTIVE" || "$hb_status" == "BLOCKED" ) ]]; then
    printf 'RESULT=LIVE\nCLAIM_FILE=%s\nHEARTBEAT_FILE=%s\nHELD_BY=%s\n' "$claim_json" "$hb_json" "$held_by"
    return 0
  fi

  # Absent, unreadable, corrupt, expired, terminal (DONE/FAILED), or any status this
  # script does not recognise — all collapse to STALE, the safe direction: STALE only
  # means "reclaimable", never "confirmed dead", so misreading an unrecognised or
  # unreadable heartbeat this way asserts nothing false.
  printf 'RESULT=STALE\nCLAIM_FILE=%s\nHEARTBEAT_FILE=%s\nHELD_BY=%s\n' "$claim_json" "${hb_json:-}" "$held_by"
}

# Runs resolve(), sourcing its stdout as shell variables (RESULT, CLAIM_FILE,
# HEARTBEAT_FILE, HELD_BY, REASON, PREV_HELD_BY) into the caller's scope.
#
# Deliberately NOT `done < <(resolve)`: process substitution forks resolve() into a
# subshell, and refuse()'s `exit 2` inside fetch_namespaces() would then only kill that
# subshell — the main script would sail on with an empty $RESULT, silently falling
# through every case statement that switches on it. `resolve > file` is a plain
# redirection on a simple command, which bash does NOT subshell, so a refuse() inside it
# still unwinds through the real EXIT trap. Reading the file back with `< file` (not a
# pipe) keeps the while-loop itself out of a subshell too.
run_resolve() {
  RESULT=""; CLAIM_FILE_OUT=""; HEARTBEAT_FILE_OUT=""; HELD_BY_OUT=""; REASON_OUT=""; PREV_HELD_BY_OUT=""
  : > "$TMPD/resolve.out"
  resolve > "$TMPD/resolve.out"
  local k v
  while IFS='=' read -r k v; do
    case "$k" in
      RESULT) RESULT="$v" ;;
      CLAIM_FILE) CLAIM_FILE_OUT="$v" ;;
      HEARTBEAT_FILE) HEARTBEAT_FILE_OUT="$v" ;;
      HELD_BY) HELD_BY_OUT="$v" ;;
      REASON) REASON_OUT="$v" ;;
      PREV_HELD_BY) PREV_HELD_BY_OUT="$v" ;;
    esac
  done < "$TMPD/resolve.out"
}

# Prints a human-readable summary of a resolved LIVE/STALE claim, for `check`'s output
# and for `claim`'s refusal-on-LIVE output.
describe_claim() {
  local claim_file="$1" hb_file="$2"
  [[ -z "$claim_file" ]] && return 0
  printf '  org/stream : %s/%s\n' "$(jf "$claim_file" org_slug)" "$(jf "$claim_file" model_stream)"
  printf '  held_by    : %s\n'   "$(jf "$claim_file" held_by)"
  printf '  lane       : %s\n'   "$(jf "$claim_file" lane)"
  printf '  stage      : %s\n'   "$(jf "$claim_file" current_stage)"
  printf '  run_id     : %s\n'   "$(jf "$claim_file" run_id)"
  printf '  claimed_at : %s\n'   "$(jf "$claim_file" claimed_at)"
  printf '  updated_at : %s\n'   "$(jf "$claim_file" updated_at)"
  if [[ -n "$hb_file" && -s "$hb_file" ]]; then
    printf '  heartbeat  : status=%s updated_at=%s\n' "$(jf "$hb_file" status)" "$(jf "$hb_file" updated_at)"
    local bk; bk="$(jf "$hb_file" blocker.kind)"
    if [[ -n "$bk" ]]; then
      printf '  blocker    : kind=%s detail=%s needs=%s\n' \
        "$bk" "$(jf "$hb_file" blocker.detail)" "$(jf "$hb_file" blocker.needs)"
    fi
  else
    printf '  heartbeat  : not found / unreadable — that is why this reads STALE, not LIVE\n'
  fi
}

# ------------------------------------------------------------------- the build step
# Constructs the next claim JSON (claim / renew / release) exactly the way
# cloud-heartbeat.sh builds its own state: python3 owns all JSON logic, one atomic
# os.replace write. $1 = output path. Reads env vars CC_*, set by each verb below.
build_claim_json() {
  local out="$1"
  CC_OUT="$out" python3 - <<'PY'
import json, os, sys

out = os.environ["CC_OUT"]

def env(k, d=""): return os.environ.get(k, d)
def has(k): return os.environ.get(k) == "yes"

mode   = env("CC_MODE")
now    = env("CC_NOW")
existing_path = env("CC_EXISTING_PATH")

base = None
if existing_path and os.path.exists(existing_path):
    try:
        with open(existing_path) as fh:
            base = json.load(fh)
        if not isinstance(base, dict):
            base = None
    except (ValueError, OSError):
        base = None

state = dict(base) if base else {}
state["schema"] = int(env("CC_SCHEMA"))
state["kind"] = "cloud-claim"
state["org_slug"] = env("CC_ORG")
state["model_stream"] = env("CC_STREAM")
state["held_by"] = env("CC_HELD_BY")
state["heartbeat_ref"] = env("CC_HEARTBEAT_REF")
state.setdefault("history", [])
state.setdefault("claimed_at", now)
state.setdefault("run_id", None)
state.setdefault("lane", None)
state.setdefault("current_stage", None)
state.setdefault("released_at", None)
state.setdefault("release_reason", None)
state.setdefault("prior_claim", None)

if mode == "claim":
    fresh = env("CC_FRESH_EPOCH") == "yes"   # FREE or STALE: a new claim period starts
    if fresh:
        state["claimed_at"] = now
        state["history"] = []
        state["released_at"] = None
        state["release_reason"] = None
    prior_json = env("CC_PRIOR_CLAIM_JSON")
    state["prior_claim"] = json.loads(prior_json) if prior_json else None
    if has("CC_HAVE_LANE"):
        state["lane"] = env("CC_LANE")[:200]
    state["current_stage"] = env("CC_STAGE")[:200]
    if has("CC_HAVE_RUN_ID"):
        state["run_id"] = env("CC_RUN_ID")[:200]
    state["history"].append({"ts": now, "event": "claimed", "stage": state["current_stage"], "by": state["held_by"]})

elif mode == "renew":
    state["current_stage"] = env("CC_STAGE")[:200]
    if has("CC_HAVE_RUN_ID"):
        state["run_id"] = env("CC_RUN_ID")[:200]
    state["history"].append({"ts": now, "event": "renewed", "stage": state["current_stage"], "by": state["held_by"]})

elif mode == "release":
    state["released_at"] = now
    state["release_reason"] = env("CC_REASON")
    state["history"].append({"ts": now, "event": "released", "reason": state["release_reason"], "by": state["held_by"]})

state["history"] = state["history"][-10:]     # bounded, per design doc §3.5
state["updated_at"] = now
state["emitter"] = {"name": "cloud-claim.sh", "version": int(env("CC_EMITTER"))}

tmp = out + ".tmp.%d" % os.getpid()
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(tmp, "w") as fh:
    json.dump(state, fh, indent=2, sort_keys=True)
    fh.write("\n")
os.replace(tmp, out)

prog = state.get("current_stage") or "-"
print("cloud-claim: %s org=%s stream=%s stage=%s held_by=%s" %
      (mode, state["org_slug"], state["model_stream"], prog, state["held_by"]))
PY
}

build_tree() {
  # $1 blob sha, $2 parent commit (may be empty). Reads the parent's tree first so
  # anything else already on that ref survives — a supersede, not a replace. Identical
  # to cloud-heartbeat.sh's own build_tree().
  local blob="$1" parent="$2" idx="$TMPD/index"
  rm -f "$idx"
  (
    export GIT_INDEX_FILE="$idx"
    if [[ -n "$parent" ]]; then
      git -C "$REPO" read-tree "$parent" 2>/dev/null || exit 1
    fi
    git -C "$REPO" update-index --add --cacheinfo "100644,$blob,$CLAIM_REL" 2>/dev/null || exit 1
    git -C "$REPO" write-tree 2>/dev/null || exit 1
  )
}

commit_and_push_or_refuse() {
  # $1 parent (may be empty), $2 commit message. Non-force push; on ANY failure this
  # refuses (exit 2) — unlike renew/release's degrade-and-warn, a claim that was never
  # confirmed pushed provides no protection at all, so `claim` must not proceed as if
  # it succeeded. Returns 0 with COMMIT set on success (the caller still has to check
  # rejection vs. other failure via the global PUSH_REJECTED flag).
  local parent="$1" msg="$2"
  local blob tree
  blob="$(git -C "$REPO" hash-object -w --path "$CLAIM_REL" -- "$CLAIM_FILE" 2>&1)" \
    || refuse "could not store the claim as a git object" "$(printf '%s' "$blob" | redact | head -1)"
  tree="$(build_tree "$blob" "$parent")" || refuse "could not build the claim tree"
  [[ -n "$tree" ]] || refuse "could not build the claim tree" "write-tree produced no output"

  if [[ -n "$parent" ]]; then
    COMMIT="$(git -C "$REPO" commit-tree "$tree" -p "$parent" -m "$msg" 2>/dev/null || true)"
  else
    COMMIT="$(git -C "$REPO" commit-tree "$tree" -m "$msg" 2>/dev/null || true)"
  fi
  [[ -n "$COMMIT" ]] || refuse "could not create the claim commit"

  PUSH_OUT=""; PUSH_RC=0
  PUSH_OUT="$(git -C "$REPO" push --quiet origin "$COMMIT:$CLAIM_PUSH_REF" 2>&1)" || PUSH_RC=$?
  return $PUSH_RC
}

is_rejection() {
  # The "reference already exists" / "[remote rejected]" shape is DISTINCT from the
  # "[rejected] ... non-fast-forward" shape cloud-heartbeat.sh parses, and both are real:
  # a plain push of `sha:new-ref` where the LOCAL side has no prior knowledge of that ref
  # (the FREE case — nothing was ever fetched, because nothing had ever been pushed yet)
  # asserts "this must not currently exist" with no client-side history to compare against,
  # so when another session's claim landed first, the SERVER rejects it with "reference
  # already exists", not a client-detected non-fast-forward. Caught by this suite's own
  # concurrent FREE-vs-FREE race test (section 9) — without this arm, a lost race on a
  # never-before-claimed org+stream misreported as a generic push failure (refuse, exit 2)
  # instead of being re-derived as LIVE/STALE like every other collision.
  case "$1" in
    *"[rejected]"*|*non-fast-forward*|*"fetch first"*|*"cannot lock ref"*|*"Updates were rejected"*\
    |*"[remote rejected]"*|*"reference already exists"*|*"already exists"*|*"stale info"*) return 0 ;;
    *) return 1 ;;
  esac
}

# =============================================================================
# check
# =============================================================================
do_check() {
  run_resolve
  case "$RESULT" in
    FREE)
      info "FREE — $ORG/$STREAM has no live claim (${REASON_OUT:-no-prior-claim})."
      ok_exit
      ;;
    MINE)
      info "MINE — $ORG/$STREAM is already claimed by this session ($SID)."
      describe_claim "$CLAIM_FILE_OUT" ""
      ok_exit
      ;;
    STALE)
      info "STALE — $ORG/$STREAM was held by $HELD_BY_OUT, which looks dead. Reclaimable."
      describe_claim "$CLAIM_FILE_OUT" "$HEARTBEAT_FILE_OUT"
      stale_exit
      ;;
    LIVE)
      info "LIVE — $ORG/$STREAM is held by $HELD_BY_OUT and that session is alive. Do not proceed."
      describe_claim "$CLAIM_FILE_OUT" "$HEARTBEAT_FILE_OUT"
      live_exit
      ;;
  esac
}

# =============================================================================
# claim
# =============================================================================
do_claim() {
  run_resolve
  local parent="" fresh_epoch=no prior_json="" msg=""

  case "$RESULT" in
    LIVE)
      warn "REFUSED — $ORG/$STREAM is already claimed and that session is alive."
      describe_claim "$CLAIM_FILE_OUT" "$HEARTBEAT_FILE_OUT" >&2
      live_exit
      ;;
    FREE)
      parent=""
      fresh_epoch=yes
      ;;
    MINE)
      parent="$(git -C "$REPO" rev-parse -q --verify "$CLAIM_REMOTE_REF" 2>/dev/null || true)"
      fresh_epoch=no
      ;;
    STALE)
      parent="$(git -C "$REPO" rev-parse -q --verify "$CLAIM_REMOTE_REF" 2>/dev/null || true)"
      fresh_epoch=yes
      prior_json="$(CC_CF="$CLAIM_FILE_OUT" CC_HB="$HEARTBEAT_FILE_OUT" CC_NOW="$(now_iso)" python3 -c '
import json, os
cf = json.load(open(os.environ["CC_CF"]))
hb_path = os.environ.get("CC_HB", "")
hb = None
if hb_path and os.path.exists(hb_path):
    try:
        hb = json.load(open(hb_path))
    except Exception:
        hb = None
print(json.dumps({
    "held_by": cf.get("held_by"),
    "current_stage": cf.get("current_stage"),
    "claimed_at": cf.get("claimed_at"),
    "last_heartbeat_at": hb.get("updated_at") if hb else None,
    "last_heartbeat_status": hb.get("status") if hb else None,
    "reclaimed_at": os.environ["CC_NOW"],
}))
')"
      ;;
  esac

  local existing_path=""
  [[ "$RESULT" == MINE ]] && existing_path="$CLAIM_FILE_OUT"

  CC_MODE=claim CC_OUT="$CLAIM_FILE" CC_SCHEMA="$SCHEMA_VERSION" CC_EMITTER="$EMITTER_VERSION" \
  CC_ORG="$ORG" CC_STREAM="$STREAM" CC_HELD_BY="$SID" \
  CC_HEARTBEAT_REF="refs/heads/$FLEET_NS/$SID" \
  CC_STAGE="$STAGE" CC_HAVE_RUN_ID="$HAVE_RUN_ID" CC_RUN_ID="$RUN_ID" \
  CC_HAVE_LANE="$HAVE_LANE" CC_LANE="$LANE" \
  CC_FRESH_EPOCH="$fresh_epoch" CC_PRIOR_CLAIM_JSON="$prior_json" \
  CC_EXISTING_PATH="$existing_path" CC_NOW="$(now_iso)" \
  build_claim_json "$CLAIM_FILE" || refuse "could not build the claim file"

  msg="claim $ORG/$STREAM ($SID)"
  local attempt=1
  while :; do
    if commit_and_push_or_refuse "$parent" "$msg"; then
      git -C "$REPO" update-ref "$CLAIM_LOCAL_REF" "$COMMIT" 2>/dev/null || true
      info "claimed $ORG/$STREAM -> ${COMMIT:0:8} (${RESULT})"
      ok_exit
    fi

    if is_rejection "$PUSH_OUT"; then
      if [[ $attempt -ge $PUSH_ATTEMPTS ]]; then
        warn "gave up after $PUSH_ATTEMPTS attempts: $ORG/$STREAM kept changing hands."
        warn "  last git message: $(printf '%s' "$PUSH_OUT" | redact | tail -1)"
        warn "  if you are still seeing this, heartbeat 'blocked --kind ambiguous-instruction'"
        refuse "could not establish a claim after $PUSH_ATTEMPTS attempts" \
          "$ORG/$STREAM's claim ref kept changing hands between contested reclaims." \
          "Nothing was recorded. Investigate rather than retrying blindly."
      fi
      # Re-fetch, read the winner, and re-derive FREE/MINE/STALE/LIVE against IT —
      # never reparent onto a winner's claim blindly (that would silently steal it).
      run_resolve
      case "$RESULT" in
        MINE)
          info "claim already recorded by this session ($SID) — treating as success."
          ok_exit
          ;;
        LIVE)
          warn "lost the race — $ORG/$STREAM is now held by $HELD_BY_OUT and that session is alive."
          describe_claim "$CLAIM_FILE_OUT" "$HEARTBEAT_FILE_OUT" >&2
          live_exit
          ;;
        FREE|STALE)
          # The winner is itself already gone/stale — retry parented on their commit.
          parent="$(git -C "$REPO" rev-parse -q --verify "$CLAIM_REMOTE_REF" 2>/dev/null || true)"
          fresh_epoch=yes
          if [[ "$RESULT" == STALE ]]; then
            prior_json="$(CC_CF="$CLAIM_FILE_OUT" CC_HB="$HEARTBEAT_FILE_OUT" CC_NOW="$(now_iso)" python3 -c '
import json, os
cf = json.load(open(os.environ["CC_CF"]))
hb_path = os.environ.get("CC_HB", "")
hb = None
if hb_path and os.path.exists(hb_path):
    try:
        hb = json.load(open(hb_path))
    except Exception:
        hb = None
print(json.dumps({
    "held_by": cf.get("held_by"),
    "current_stage": cf.get("current_stage"),
    "claimed_at": cf.get("claimed_at"),
    "last_heartbeat_at": hb.get("updated_at") if hb else None,
    "last_heartbeat_status": hb.get("status") if hb else None,
    "reclaimed_at": os.environ["CC_NOW"],
}))
')"
          else
            prior_json=""
          fi
          CC_MODE=claim CC_OUT="$CLAIM_FILE" CC_SCHEMA="$SCHEMA_VERSION" CC_EMITTER="$EMITTER_VERSION" \
          CC_ORG="$ORG" CC_STREAM="$STREAM" CC_HELD_BY="$SID" \
          CC_HEARTBEAT_REF="refs/heads/$FLEET_NS/$SID" \
          CC_STAGE="$STAGE" CC_HAVE_RUN_ID="$HAVE_RUN_ID" CC_RUN_ID="$RUN_ID" \
          CC_HAVE_LANE="$HAVE_LANE" CC_LANE="$LANE" \
          CC_FRESH_EPOCH="$fresh_epoch" CC_PRIOR_CLAIM_JSON="$prior_json" \
          CC_EXISTING_PATH="" CC_NOW="$(now_iso)" \
          build_claim_json "$CLAIM_FILE" || refuse "could not build the claim file"
          attempt=$((attempt + 1))
          ;;
      esac
    else
      refuse "could not push the claim to origin" \
        "$(printf '%s' "$PUSH_OUT" | redact | grep -v '^$' | tail -3)" \
        "A claim that was not confirmed pushed protects nobody — it was NOT recorded."
    fi
  done
}

# ------------------------------------------------------- shared precondition for renew/release
# Fetches the claims namespace and refuses unless the remote claim's held_by is this
# session — the "you don't hold this claim" refusal from the design doc's exit table.
require_mine() {
  fetch_namespaces no
  if ! git -C "$REPO" rev-parse -q --verify "$CLAIM_REMOTE_REF" >/dev/null 2>&1; then
    refuse "you do not hold a claim on $ORG/$STREAM" "there is no claim at all — nothing to $VERB"
  fi
  local existing="$TMPD/mine-check.json"
  if ! git -C "$REPO" show "$CLAIM_REMOTE_REF:$CLAIM_REL" >"$existing" 2>/dev/null \
     || ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$existing" >/dev/null 2>&1; then
    refuse "the existing claim on $ORG/$STREAM could not be read" "refusing rather than guessing"
  fi
  local held_by released_at
  held_by="$(jf "$existing" held_by)"
  released_at="$(jf "$existing" released_at)"
  if [[ -n "$released_at" ]]; then
    refuse "you do not hold this claim — $ORG/$STREAM was already released" "release_reason=$(jf "$existing" release_reason)"
  fi
  [[ "$held_by" == "$SID" ]] || refuse "you do not hold this claim" \
    "$ORG/$STREAM is held by $held_by, not this session ($SID)"
  MINE_EXISTING="$existing"
}

# Push with cloud-heartbeat.sh's OWN collision philosophy: a rejection here means a
# benign double-write of MY OWN chain (a network retry, a second writer sharing a
# session id), so reparent onto the new tip and retry — never a resource race, because
# require_mine() already established this ref is one only I should be writing.
# On exhausted retries or a non-rejection push failure: degrade-and-warn, exit 0 — the
# claim itself is unaffected either way (staleness is judged off the heartbeat ref, not
# this write), so this is telemetry-shaped, unlike `claim`'s own push.
push_degrade_on_failure() {
  local mode="$1"     # renew | release, for messaging only
  local parent attempt=1
  parent="$(git -C "$REPO" rev-parse -q --verify "$CLAIM_REMOTE_REF" 2>/dev/null || true)"
  while :; do
    if commit_and_push_or_refuse "$parent" "$2"; then
      git -C "$REPO" update-ref "$CLAIM_LOCAL_REF" "$COMMIT" 2>/dev/null || true
      info "$mode $ORG/$STREAM -> ${COMMIT:0:8}"
      ok_exit
    fi
    if is_rejection "$PUSH_OUT"; then
      if [[ $attempt -ge $PUSH_ATTEMPTS ]]; then
        warn "gave up after $PUSH_ATTEMPTS attempts reparenting onto $ORG/$STREAM's own chain."
        warn "  the claim itself is unaffected — your heartbeat, not this ref, is what proves"
        warn "  you are still alive. Your work is unaffected."
        ok_exit
      fi
      git -C "$REPO" fetch --quiet origin "+$CLAIM_PUSH_REF:$CLAIM_REMOTE_REF" 2>/dev/null || true
      parent="$(git -C "$REPO" rev-parse -q --verify "$CLAIM_REMOTE_REF" 2>/dev/null || true)"
      attempt=$((attempt + 1))
    else
      warn "push failed (git exit $PUSH_RC) — the $mode is on disk at $CLAIM_FILE but did"
      warn "  not reach origin. The claim itself is unaffected; your heartbeat is what other"
      warn "  sessions actually check for staleness. Your work is unaffected."
      warn "  git said: $(printf '%s' "$PUSH_OUT" | redact | grep -v '^$' | tail -2 | tr '\n' ' ')"
      ok_exit
    fi
  done
}

# =============================================================================
# renew
# =============================================================================
do_renew() {
  require_mine
  CC_MODE=renew CC_OUT="$CLAIM_FILE" CC_SCHEMA="$SCHEMA_VERSION" CC_EMITTER="$EMITTER_VERSION" \
  CC_ORG="$ORG" CC_STREAM="$STREAM" CC_HELD_BY="$SID" \
  CC_HEARTBEAT_REF="refs/heads/$FLEET_NS/$SID" \
  CC_STAGE="$STAGE" CC_HAVE_RUN_ID="$HAVE_RUN_ID" CC_RUN_ID="$RUN_ID" \
  CC_HAVE_LANE=no CC_LANE="" \
  CC_EXISTING_PATH="$MINE_EXISTING" CC_NOW="$(now_iso)" \
  build_claim_json "$CLAIM_FILE" || { warn "could not build the renewed claim file — your work is unaffected"; ok_exit; }
  push_degrade_on_failure renew "renew $ORG/$STREAM stage=$STAGE ($SID)"
}

# =============================================================================
# release
# =============================================================================
do_release() {
  require_mine
  CC_MODE=release CC_OUT="$CLAIM_FILE" CC_SCHEMA="$SCHEMA_VERSION" CC_EMITTER="$EMITTER_VERSION" \
  CC_ORG="$ORG" CC_STREAM="$STREAM" CC_HELD_BY="$SID" \
  CC_HEARTBEAT_REF="refs/heads/$FLEET_NS/$SID" \
  CC_REASON="$REASON" \
  CC_HAVE_RUN_ID=no CC_RUN_ID="" CC_HAVE_LANE=no CC_LANE="" \
  CC_EXISTING_PATH="$MINE_EXISTING" CC_NOW="$(now_iso)" \
  build_claim_json "$CLAIM_FILE" || { warn "could not build the released claim file — your work is unaffected"; ok_exit; }
  push_degrade_on_failure release "release $ORG/$STREAM reason=$REASON ($SID)"
}

# --------------------------------------------------------------------- dispatch
case "$VERB" in
  check)   do_check ;;
  claim)   do_claim ;;
  renew)   do_renew ;;
  release) do_release ;;
esac
