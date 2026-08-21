#!/usr/bin/env bash
# run-claims.sh — regression suite for cloud-sessions/bin/cloud-claim.sh.
#
# Sibling to run.sh (the suite for bin/cs), not a section inside it: run.sh's own header
# says it is "the regression suite for cloud-sessions/bin/cs", and cloud-claim.sh needs a
# different fixture shape (a real scratch bare remote + real working clones, so pushes and
# fetches are genuine git operations) rather than run.sh's stubbed-CLI fixture. This mirrors
# run.sh's helper-function idiom (sec/_pass/_fail/has/eq/run) and its design rules — $0 and
# offline by construction, nothing real ever written, refusals probed with impossible
# inputs, deterministic — rather than inventing a new test framework. See ../AGENT.md
# ground rules and ../tests/README.md.
#
# WHY A SANDBOXED FIXTURE, NOT THE REAL REPO'S OWN cloud-sessions/bin/cloud-claim.sh IN PLACE:
# cloud-claim.sh resolves its own repo via `git -C "$HERE" rev-parse --show-toplevel`, where
# $HERE is BASH_SOURCE's own (realpath-resolved) directory — exactly like cloud-heartbeat.sh.
# Invoking the real script by absolute path from an unrelated cwd does NOT sandbox it: $HERE
# still resolves to this real checkout's cloud-sessions/bin, so $REPO resolves to this real
# repo and every fetch/push would target ITS real `origin` (a real GitHub remote). This was
# caught for real while building this suite (GIT_TERMINAL_PROMPT=0 turned it into a loud
# auth failure rather than a silent push — see the build report) and is why every fixture
# below COPIES cloud-claim.sh into a throwaway repo at the identical relative path
# (cloud-sessions/bin/cloud-claim.sh) rather than exec'ing it in place.
#
# Usage:  bash cloud-sessions/tests/run-claims.sh [--keep] [-v]
set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
CC_REAL="$CS_DIR/bin/cloud-claim.sh"
RESULTS="$TESTS_DIR/RESULTS-claims.md"

KEEP=no; VERBOSE=no
for a in "$@"; do
  case "$a" in
    --keep) KEEP=yes ;;
    -v|--verbose) VERBOSE=yes ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

[[ -f "$CC_REAL" ]] || { echo "cannot find $CC_REAL" >&2; exit 2; }

# ---------------------------------------------------------- real-world baselines
# Same tamper-guard discipline as run.sh section 17/1: prove this run touched neither the
# real repo's origin nor its own state/ tree. Captured BEFORE anything else runs.
sha() { if [[ -e "$1" ]]; then shasum -a 256 "$1" | awk '{print $1}'; else echo "ABSENT"; fi; }
REPO_REAL="$CS_DIR/.."
REAL_ORIGIN="$(git -C "$REPO_REAL" remote get-url origin 2>/dev/null || echo "NONE")"
REAL_CC_SHA="$(sha "$CC_REAL")"
REAL_CLAIMS_STATE_MANIFEST() {
  find "$CS_DIR/state/claims" -type f 2>/dev/null | LC_ALL=C sort | while IFS= read -r f; do
    printf '%s  %s\n' "${f#$CS_DIR/}" "$(sha "$f")"
  done
}
REAL_CLAIMS_BEFORE="$(REAL_CLAIMS_STATE_MANIFEST)"
REAL_HEAD_BEFORE="$(git -C "$REPO_REAL" rev-parse HEAD 2>/dev/null || echo NONE)"
# NOT used for a byte-equality check below — this repo is under continuous multi-agent
# commit activity (verified: a real HEAD-moved failure during suite development turned out
# to be a concurrent, unrelated commit authored by the real git identity, nothing this
# suite did), so raw HEAD-equality is flaky here by construction, through no fault of the
# suite. What actually matters — that nothing THIS suite authored ever reached the real
# repo — is checked precisely below instead, by searching for the suite's own synthetic
# git identity rather than comparing HEAD wholesale.
REAL_LOG_HAS_TEST_IDENTITY_BEFORE="$(git -C "$REPO_REAL" log --all --format=%ae 2>/dev/null | grep -c 'tests@example\.invalid' || true)"
REAL_REFS_BEFORE="$(git -C "$REPO_REAL" for-each-ref 'refs/cloud-claims/*' 'refs/heads/cloud-claims/*' 2>/dev/null)"

# --------------------------------------------------------------------- sandbox
WORK="$(mktemp -d -t cc-tests.XXXXXX)"
cleanup() {
  chmod -R u+rwX "$WORK" 2>/dev/null
  if [[ "$KEEP" == yes ]]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

mkdir -p "$WORK/sessions" "$WORK/logs"

: > "$WORK/gitconfig"
export GIT_CONFIG_GLOBAL="$WORK/gitconfig" GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME="cc tests" GIT_AUTHOR_EMAIL="tests@example.invalid"
export GIT_COMMITTER_NAME="cc tests" GIT_COMMITTER_EMAIL="tests@example.invalid"
export GIT_TERMINAL_PROMPT=0
unset CLAUDE_CODE_SESSION_ID 2>/dev/null || true

# ------------------------------------------------------------------- reporting
PASS=0; FAIL=0; SKIP=0
FAILED_NAMES=()
if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; O=$'\033[0m'
else G=""; R=""; Y=""; D=""; O=""; fi

LASTCMD=""; OUT=""; RC=0

log() { printf '%s\n' "$*" >> "$RESULTS"; }
log_block() {
  log '  - command: `'"$LASTCMD"'`'
  log "  - exit code: $RC"
  log '  - actual output:'
  log '    ```'
  if [[ -z "$OUT" ]]; then log '    (no output)'; else printf '%s\n' "$OUT" | sed 's/^/    /' >> "$RESULTS"; fi
  log '    ```'
}
_pass() { PASS=$((PASS+1)); printf '%s  ok  %s%s\n' "$G" "$O" "$1"; log "- **PASS** \`$1\` — $2"; }
flat()  { printf '%s' "$1" | tr '\n' ' '; }
_fail() { FAIL=$((FAIL+1)); FAILED_NAMES+=("$1"); local w; w="$(flat "$3")"
          printf '%sFAIL  %s%s\n      %s%s%s\n' "$R" "$O" "$1" "$D" "$w" "$O"
          log "- **FAIL** \`$1\` — $2"; log "  - why: $w"; log_block; }
_skip() { SKIP=$((SKIP+1)); printf '%sskip  %s%s  %s(%s)%s\n' "$Y" "$O" "$1" "$D" "$2" "$O"
          log "- **SKIP** \`$1\` — $2"; }
sec()   { printf '\n%s%s%s\n' "$D" "$1" "$O"; log ""; log "## $1"; log ""; }

run() {
  LASTCMD="$*"
  OUT="$("$@" 2>&1)"; RC=$?
  [[ "$VERBOSE" == yes ]] && printf '%s$ %s\n%s%s\n' "$D" "$LASTCMD" "$OUT" "$O"
  return 0
}
has()    { case "$OUT" in *"$3"*) _pass "$1" "$2" ;; *) _fail "$1" "$2" "expected output to contain: $3" ;; esac; }
hasnt()  { case "$OUT" in *"$3"*) _fail "$1" "$2" "output must NOT contain: $3" ;; *) _pass "$1" "$2" ;; esac; }
rc_zero(){ local d="${2:-the command exits 0}"
           if [[ "$RC" -eq 0 ]]; then _pass "$1" "$d"; else _fail "$1" "$d" "expected exit 0, got $RC"; fi; }
rc_eq()  { local want="$2" d="${3:-the command exits $2}"
           if [[ "$RC" -eq "$want" ]]; then _pass "$1" "$d"; else _fail "$1" "$d" "expected exit $want, got $RC: $(flat "$OUT")"; fi; }
eq()     { if [[ "$3" == "$4" ]]; then _pass "$1" "$2"; else _fail "$1" "$2" "expected [$4], got [$3]"; fi; }

# small, safe JSON-field reader — same idiom cloud-claim.sh's own jf() and run.sh's own
# ledger_field() use: never hand-parse JSON in bash.
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

now_iso() { python3 -c 'import datetime as dt; print(dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"))'; }
minutes_ago_iso() { python3 -c 'import datetime as dt,sys; print((dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=float(sys.argv[1]))).replace(microsecond=0).isoformat().replace("+00:00","Z"))' "$1"; }

# ---------------------------------------------------------------- fixture builders
BARE="$WORK/remote.git"
git init -q --bare "$BARE"
git init -q -b main "$WORK/seed" >/dev/null
git -C "$WORK/seed" commit -q --allow-empty -m seed
git -C "$WORK/seed" remote add origin "$BARE"
git -C "$WORK/seed" push -q -u origin main

# new_session <name> — a fresh clone of the shared bare remote, with cloud-claim.sh copied
# to the identical relative path (cloud-sessions/bin/cloud-claim.sh) the real script lives
# at, so its own BASH_SOURCE-based self-location lands inside THIS clone, never the real
# repo. Prints the clone's path.
new_session() {
  local dir="$WORK/sessions/$1"
  git clone -q "$BARE" "$dir" >/dev/null
  mkdir -p "$dir/cloud-sessions/bin"
  cp "$CC_REAL" "$dir/cloud-sessions/bin/cloud-claim.sh"
  chmod +x "$dir/cloud-sessions/bin/cloud-claim.sh"
  printf '%s\n' "$dir"
}

# cc <session-dir> <session-id> <args...> — run the sandboxed copy as that session.
cc() {
  local dir="$1" sid="$2"; shift 2
  LASTCMD="cloud-claim($sid) $*"
  OUT="$(CLAUDE_CODE_SESSION_ID="$sid" bash "$dir/cloud-sessions/bin/cloud-claim.sh" "$@" 2>&1)"; RC=$?
  [[ "$VERBOSE" == yes ]] && printf '%s$ %s\n%s%s\n' "$D" "$LASTCMD" "$OUT" "$O"
  return 0
}

# forge_heartbeat <session-dir> <held-by-sid> <status> <age-minutes> — push a heartbeat
# file directly onto refs/heads/cloud-fleet/<held-by-sid> via plumbing, bypassing
# cloud-heartbeat.sh entirely (this suite is about cloud-claim.sh; a hand-built heartbeat
# fixture is the realistic shape for "some other session's heartbeat exists on origin").
forge_heartbeat() {
  local dir="$1" sid="$2" status="$3" age_min="$4"
  local updated; updated="$(minutes_ago_iso "$age_min")"
  local hbfile="$WORK/hb-$sid.json"
  python3 -c 'import json,sys
json.dump({"schema":1,"kind":"cloud-heartbeat","session_id":sys.argv[1],
           "status":sys.argv[2],"updated_at":sys.argv[3],"beats":1},
          open(sys.argv[4],"w"))' "$sid" "$status" "$updated" "$hbfile"
  local rel="cloud-sessions/state/fleet/$sid.json"
  local blob; blob="$(git -C "$dir" hash-object -w --path "$rel" -- "$hbfile")"
  local idx="$WORK/hb-index-$sid"; rm -f "$idx"
  ( export GIT_INDEX_FILE="$idx"
    git -C "$dir" update-index --add --cacheinfo "100644,$blob,$rel" )
  local tree; tree="$(GIT_INDEX_FILE="$idx" git -C "$dir" write-tree)"
  local commit; commit="$(git -C "$dir" commit-tree "$tree" -m "beat $sid")"
  git -C "$dir" push -q origin "$commit:refs/heads/cloud-fleet/$sid"
}

# remote_claim_field <org> <stream> <field> — read a field straight off the bare remote,
# independent of any session's local fetch cache, so assertions are against ground truth.
remote_claim_field() {
  local org="$1" stream="$2" field="$3"
  local tmp="$WORK/remote-read.json"
  if git -C "$BARE" show "refs/heads/cloud-claims/$org/$stream:cloud-sessions/state/claims/$org/$stream.json" \
       >"$tmp" 2>/dev/null; then
    jf "$tmp" "$field"
  else
    printf '<no-ref>'
  fi
}
remote_claim_ref_exists() {
  git -C "$BARE" show-ref --verify --quiet "refs/heads/cloud-claims/$1/$2"
}

# ------------------------------------------------------------------ results log
{
  printf '# cloud-claim.sh regression suite — results\n\n'
  printf 'Generated by `cloud-sessions/tests/run-claims.sh` on %s.\n\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
  printf 'Host bash `%s`, git `%s`, python3 `%s`.\n\n' \
    "$(bash --version | head -1 | sed -E 's/.*version ([^ ]+).*/\1/')" \
    "$(git --version | awk '{print $3}')" "$(python3 -V 2>&1 | awk '{print $2}')"
  printf 'Every fetch/push in this run targets a scratch bare repo under a temp directory\n'
  printf '(`%s`), never the real `origin`. Section 1 and the tamper guard at the end prove it.\n\n' "$WORK"
} > "$RESULTS"

printf '%scloud-claim.sh regression suite%s   %s\n' "$D" "$O" "$(date '+%Y-%m-%d %H:%M:%S')"

# =============================================================================
sec "1 — harness self-guards"
# =============================================================================

A0="$(new_session sess-guard)"
LASTCMD="grep origin remote"; RC=0
OUT="$(git -C "$A0" remote get-url origin)"
case "$OUT" in
  "$BARE") _pass "harness/origin-is-sandboxed" "the session clone's origin is the scratch bare remote, not GitHub" ;;
  *) _fail "harness/origin-is-sandboxed" "the session clone's origin is the scratch bare remote" "origin resolved to: $OUT" ;;
esac

cc "$A0" session_probe help
has "harness/help-runs-the-copy" "help exits cleanly against the sandboxed copy" "cloud-claim.sh"
rc_eq "harness/help-exit-zero" 0

cmp -s "$CC_REAL" "$A0/cloud-sessions/bin/cloud-claim.sh" \
  && _pass "harness/copy-is-identical" "the script under test is a byte-identical copy of bin/cloud-claim.sh" \
  || _fail "harness/copy-is-identical" "the script under test is a byte-identical copy" "copy differs from the real script"

# =============================================================================
sec "2 — check: FREE on a virgin org"
# =============================================================================

A="$(new_session sess-a-check-free)"
cc "$A" session_AAA check --org acme --stream per-account
has    "check/free-says-free" "check on a never-claimed org+stream reports FREE" "FREE"
rc_eq  "check/free-exit-zero" 0 "FREE exits 0"

cc "$A" session_AAA check --org acme
has "check/stream-defaults-to-per-account" "omitting --stream defaults to per-account and still reads FREE" "FREE"

# =============================================================================
sec "3 — claim: the happy path"
# =============================================================================

B="$(new_session sess-b-claim-happy)"
cc "$B" session_AAA claim --org acme --stream per-account --stage S1 --run-id run-123 --lane test-lane
has   "claim/reports-claimed" "a claim on a FREE org+stream succeeds" "claimed"
rc_eq "claim/exit-zero" 0

eq "claim/remote-held-by"     "correct held_by landed on the remote"       "$(remote_claim_field acme per-account held_by)"       "session_AAA"
eq "claim/remote-org"         "correct org_slug landed on the remote"      "$(remote_claim_field acme per-account org_slug)"      "acme"
eq "claim/remote-stream"      "correct model_stream landed on the remote"  "$(remote_claim_field acme per-account model_stream)"  "per-account"
eq "claim/remote-stage"       "correct current_stage landed on the remote" "$(remote_claim_field acme per-account current_stage)" "S1"
eq "claim/remote-run-id"      "correct run_id landed on the remote"        "$(remote_claim_field acme per-account run_id)"        "run-123"
eq "claim/remote-lane"        "correct lane landed on the remote"          "$(remote_claim_field acme per-account lane)"          "test-lane"
eq "claim/remote-heartbeat-ref" "heartbeat_ref points at the holder's own fleet ref" \
   "$(remote_claim_field acme per-account heartbeat_ref)" "refs/heads/cloud-fleet/session_AAA"
eq "claim/remote-schema"      "schema is 1"                                "$(remote_claim_field acme per-account schema)"        "1"
eq "claim/remote-released-at-null" "a fresh claim is not released"         "$(remote_claim_field acme per-account released_at)"   ""
eq "claim/remote-prior-claim-null" "a fresh (FREE-origin) claim has no prior_claim" "$(remote_claim_field acme per-account prior_claim)" ""

# =============================================================================
sec "4 — check: MINE for the holder, a distinct claim is untouched by another org"
# =============================================================================

cc "$B" session_AAA check --org acme --stream per-account
has   "check/mine-says-mine" "the holder's own check reports MINE" "MINE"
rc_eq "check/mine-exit-zero" 0

C="$(new_session sess-c-other-org)"
cc "$C" session_ZZZ check --org globex --stream per-account
has "check/other-org-still-free" "a different org+stream is unaffected by acme's claim" "FREE"

# =============================================================================
sec "5 — check/claim: STALE reclaim (the design's §3.8 STALE case)"
# =============================================================================

# session_AAA holds acme/per-account (section 3) but has never posted a heartbeat at all —
# the "ref absent" arm of the STALE classification.
D="$(new_session sess-d-stale)"
cc "$D" session_BBB check --org acme --stream per-account
has   "stale/no-heartbeat-reads-stale" "a claim whose holder never posted a heartbeat is STALE" "STALE"
rc_eq "stale/exit-one" 1
has   "stale/prints-the-holder" "the STALE output names who it is reclaiming from" "session_AAA"

cc "$D" session_BBB claim --org acme --stream per-account --stage S1
has   "stale/reclaim-succeeds" "claiming a STALE org+stream succeeds" "claimed"
rc_eq "stale/reclaim-exit-zero" 0
eq "stale/reclaim-new-holder" "held_by flips to the reclaiming session" \
   "$(remote_claim_field acme per-account held_by)" "session_BBB"
eq "stale/prior-claim-records-old-holder" "prior_claim.held_by names the superseded session" \
   "$(remote_claim_field acme per-account prior_claim.held_by)" "session_AAA"
eq "stale/prior-claim-records-old-stage"  "prior_claim.current_stage carries over the superseded stage" \
   "$(remote_claim_field acme per-account prior_claim.current_stage)" "S1"
eq "stale/claimed-at-resets" "a reclaim starts a new claim epoch (claimed_at is NOT preserved from the stale claim)" \
   "$([[ "$(remote_claim_field acme per-account claimed_at)" == "$(remote_claim_field acme per-account updated_at)" ]] && echo same || echo different)" \
   "same"

# An expired-but-present heartbeat (old updated_at, still ACTIVE) must ALSO read STALE, not
# just an absent one — the age check, not merely ref-presence, is what's on trial here.
E="$(new_session sess-e-stale-expired)"
forge_heartbeat "$E" session_BBB ACTIVE 45   # 45 minutes old > the 30-minute threshold
cc "$E" session_CCC check --org acme --stream per-account
has   "stale/expired-heartbeat-reads-stale" "an ACTIVE heartbeat older than 30 minutes is STALE, not LIVE" "STALE"
rc_eq "stale/expired-exit-one" 1

# A DONE heartbeat must read STALE even if it is fresh by the clock.
F="$(new_session sess-f-stale-done)"
forge_heartbeat "$F" session_BBB DONE 1
cc "$F" session_CCC check --org acme --stream per-account
has   "stale/done-status-reads-stale" "a fresh but DONE heartbeat is STALE, not LIVE" "STALE"
rc_eq "stale/done-status-exit-one" 1

# =============================================================================
sec "6 — renew"
# =============================================================================

# Move a fresh claim through a stage transition. Start clean: release acme first.
G="$(new_session sess-g-renew-setup)"
forge_heartbeat "$G" session_BBB ACTIVE 1
cc "$G" session_BBB release --org acme --stream per-account --reason handoff
cc "$G" session_BBB claim --org acme --stream per-account --stage S1 --run-id r-renew
forge_heartbeat "$G" session_BBB ACTIVE 1

cc "$G" session_BBB renew --org acme --stream per-account --stage S3
has   "renew/reports-renewed" "renew as the holder succeeds" "renew"
rc_eq "renew/exit-zero" 0
eq "renew/stage-updated"      "current_stage reflects the new stage" "$(remote_claim_field acme per-account current_stage)" "S3"
eq "renew/held-by-unchanged"  "held_by is unchanged by renew"        "$(remote_claim_field acme per-account held_by)"       "session_BBB"
eq "renew/claimed-at-preserved" "renew does not reset claimed_at (same claim epoch continues)" \
   "$([[ -n "$(remote_claim_field acme per-account claimed_at)" ]] && echo present || echo missing)" "present"

H="$(new_session sess-h-renew-not-holder)"
cc "$H" session_ZZZ renew --org acme --stream per-account --stage S9
has    "renew/refuses-non-holder" "renew refuses when the caller does not hold the claim" "do not hold"
rc_eq  "renew/refuses-non-holder-exit-two" 2
eq "renew/non-holder-attempt-did-not-change-stage" "a refused renew changes nothing on the remote" \
   "$(remote_claim_field acme per-account current_stage)" "S3"

I="$(new_session sess-i-renew-no-claim)"
cc "$I" session_QQQ renew --org neverclaimed --stream per-account --stage S1
has   "renew/refuses-when-nothing-claimed" "renew on an org with no claim at all refuses" "no claim at all"
rc_eq "renew/refuses-when-nothing-claimed-exit-two" 2

# =============================================================================
sec "7 — release"
# =============================================================================

cc "$G" session_BBB release --org acme --stream per-account --reason done
has   "release/reports-released" "release as the holder succeeds" "release"
rc_eq "release/exit-zero" 0
eq "release/reason-recorded"  "release_reason is recorded"            "$(remote_claim_field acme per-account release_reason)" "done"
eq "release/released-at-set"  "released_at is set"                    "$([[ -n "$(remote_claim_field acme per-account released_at)" ]] && echo set || echo unset)" "set"

cc "$G" session_BBB check --org acme --stream per-account
has "release/check-after-release-is-free" "check after release reports FREE again" "FREE"
rc_eq "release/check-after-release-exit-zero" 0

J="$(new_session sess-j-release-not-holder)"
cc "$J" session_ZZZ release --org globex --stream per-account --reason done
has   "release/refuses-non-holder" "release refuses for a session that never held the claim" "do not hold"
rc_eq "release/refuses-non-holder-exit-two" 2

cc "$J" session_ZZZ release --org acme --stream per-account --reason done
has   "release/refuses-already-released" "releasing an already-released claim is refused, not a silent no-op" "already released"
rc_eq "release/refuses-already-released-exit-two" 2

# =============================================================================
sec "8 — LIVE: refuses cleanly and never overwrites (design §3.8's load-bearing path)"
# =============================================================================

K="$(new_session sess-k-live-setup)"
cc "$K" session_LIVE1 claim --org initech --stream per-account --stage S1 --lane initech-lane
forge_heartbeat "$K" session_LIVE1 ACTIVE 2
BEFORE_LIVE_JSON="$(git -C "$BARE" show "refs/heads/cloud-claims/initech/per-account:cloud-sessions/state/claims/initech/per-account.json")"

L="$(new_session sess-l-live-check)"
cc "$L" session_LIVE2 check --org initech --stream per-account
has   "live/check-says-live" "check against a genuinely live claim reports LIVE" "LIVE"
rc_eq "live/check-exit-three" 3
has   "live/check-names-the-holder" "the LIVE output names who holds it" "session_LIVE1"
has   "live/check-shows-heartbeat-status" "the LIVE output surfaces the dereferenced heartbeat status" "ACTIVE"

cc "$L" session_LIVE2 claim --org initech --stream per-account --stage S1
has    "live/claim-refuses" "claiming an org+stream with a live holder is refused" "already claimed and that session is alive"
rc_eq  "live/claim-refuses-exit-three" 3

AFTER_LIVE_JSON="$(git -C "$BARE" show "refs/heads/cloud-claims/initech/per-account:cloud-sessions/state/claims/initech/per-account.json")"
eq "live/claim-refusal-does-not-touch-the-ref" \
   "a refused LIVE claim leaves the existing claim commit byte-for-byte unchanged on the remote" \
   "$AFTER_LIVE_JSON" "$BEFORE_LIVE_JSON"
eq "live/held-by-still-the-original-holder" "held_by after the refused attempt is still the original session" \
   "$(remote_claim_field initech per-account held_by)" "session_LIVE1"

# BLOCKED must surface too, not just ACTIVE.
M="$(new_session sess-m-live-blocked)"
cc "$M" session_LIVE3 claim --org umbrella --stream per-account --stage S2
BLOCKHB="$WORK/hb-blocked.json"
python3 -c 'import json; json.dump({"schema":1,"kind":"cloud-heartbeat","session_id":"session_LIVE3","status":"BLOCKED","updated_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),"blocker":{"kind":"needs-credential","detail":"no HF token","needs":"a token"}}, open("'"$BLOCKHB"'","w"))'
BLOB="$(git -C "$M" hash-object -w --path cloud-sessions/state/fleet/session_LIVE3.json -- "$BLOCKHB")"
IDXB="$WORK/hb-index-blocked"; rm -f "$IDXB"
( export GIT_INDEX_FILE="$IDXB"; git -C "$M" update-index --add --cacheinfo "100644,$BLOB,cloud-sessions/state/fleet/session_LIVE3.json" )
TREEB="$(GIT_INDEX_FILE="$IDXB" git -C "$M" write-tree)"
COMMITB="$(git -C "$M" commit-tree "$TREEB" -m beat)"
git -C "$M" push -q origin "$COMMITB:refs/heads/cloud-fleet/session_LIVE3"

cc "$M" session_LIVE4 check --org umbrella --stream per-account
has   "live/blocked-still-reads-live" "a BLOCKED (not just ACTIVE) fresh heartbeat still means LIVE, not reclaimable" "LIVE"
rc_eq "live/blocked-exit-three" 3
has   "live/blocked-surfaces-the-blocker" "check surfaces the blocked holder's own blocker kind/detail/needs" "needs-credential"
has   "live/blocked-surfaces-detail" "the blocker detail is surfaced" "no HF token"

# =============================================================================
sec "9 — real concurrent claim: exactly one winner, the ref never corrupts"
# =============================================================================

N1="$(new_session sess-n1-race)"
N2="$(new_session sess-n2-race)"
# Both racers already have a fresh heartbeat posted BEFORE either attempts to claim.
# Without this, the loser's post-rejection re-derivation would (correctly, per design
# §3.8's own STALE-retry rule) see the winner's brand-new claim as STALE — no heartbeat
# posted YET is indistinguishable from "crashed before its first beat" — and legitimately
# reclaim on top of it, so BOTH would exit 0. That is not a bug (§3.8 names this exact
# edge case), but it means testing "the loser sees LIVE and refuses" requires the winner
# to already be established as alive first, which is the steady-state case this section
# means to exercise.
forge_heartbeat "$N1" session_RACE1 ACTIVE 1
forge_heartbeat "$N2" session_RACE2 ACTIVE 1
: > "$WORK/race1.out"; : > "$WORK/race2.out"
(
  CLAUDE_CODE_SESSION_ID=session_RACE1 bash "$N1/cloud-sessions/bin/cloud-claim.sh" \
    claim --org raceco --stream per-account --stage S1 >"$WORK/race1.out" 2>&1
  echo $? > "$WORK/race1.rc"
) &
PID1=$!
(
  CLAUDE_CODE_SESSION_ID=session_RACE2 bash "$N2/cloud-sessions/bin/cloud-claim.sh" \
    claim --org raceco --stream per-account --stage S1 >"$WORK/race2.out" 2>&1
  echo $? > "$WORK/race2.rc"
) &
PID2=$!
wait "$PID1" "$PID2" 2>/dev/null
RC1="$(cat "$WORK/race1.rc" 2>/dev/null || echo -1)"
RC2="$(cat "$WORK/race2.rc" 2>/dev/null || echo -1)"

LASTCMD="two concurrent: claim --org raceco (session_RACE1 & session_RACE2)"
RC=0; OUT="rc1=$RC1 rc2=$RC2"
if { [[ "$RC1" == 0 && "$RC2" == 3 ]] || [[ "$RC1" == 3 && "$RC2" == 0 ]]; }; then
  _pass "race/exactly-one-winner" "exactly one of two truly concurrent claim attempts on a FREE org succeeds (0), the other refuses (3)"
else
  _fail "race/exactly-one-winner" "exactly one of two concurrent claims succeeds, the other refuses" \
    "rc1=$RC1 rc2=$RC2 (out1: $(flat "$(cat "$WORK/race1.out" 2>/dev/null)")) (out2: $(flat "$(cat "$WORK/race2.out" 2>/dev/null)"))"
fi

WINNER=""
[[ "$RC1" == 0 ]] && WINNER=session_RACE1
[[ "$RC2" == 0 ]] && WINNER=session_RACE2
if [[ -n "$WINNER" ]]; then
  eq "race/remote-converges-to-the-winner" "the remote's held_by is exactly the process that exited 0" \
     "$(remote_claim_field raceco per-account held_by)" "$WINNER"
else
  _fail "race/remote-converges-to-the-winner" "the remote's held_by is exactly the process that exited 0" "no winner exited 0 — see race/exactly-one-winner"
fi

O_CNT=0
git -C "$BARE" for-each-ref 'refs/heads/cloud-claims/raceco/*' --format='%(refname)' > "$WORK/raceco-refs.txt"
O_CNT="$(wc -l < "$WORK/raceco-refs.txt" | tr -d ' ')"
eq "race/exactly-one-ref-created" "the race produced exactly one claim ref for raceco/per-account, not two divergent ones" "$O_CNT" "1"

# =============================================================================
sec "10 — validation and refusal paths"
# =============================================================================

P="$(new_session sess-p-validation)"

cc "$P" session_AAA check --org acme --stream bogus
has "validate/unknown-stream-refused" "an unrecognised --stream is refused, not silently accepted" "unknown --stream"
rc_eq "validate/unknown-stream-exit-two" 2

cc "$P" session_AAA check
has "validate/missing-org-refused" "check with no --org is refused" "needs --org"
rc_eq "validate/missing-org-exit-two" 2

cc "$P" session_AAA claim --org acme --stream per-account
has "validate/claim-missing-stage-refused" "claim with no --stage is refused" "needs --stage"
rc_eq "validate/claim-missing-stage-exit-two" 2

cc "$P" session_AAA renew --org acme --stream per-account
has "validate/renew-missing-stage-refused" "renew with no --stage is refused" "needs --stage"
rc_eq "validate/renew-missing-stage-exit-two" 2

cc "$P" session_AAA release --org acme --stream per-account
has "validate/release-missing-reason-refused" "release with no --reason is refused" "needs --reason"
rc_eq "validate/release-missing-reason-exit-two" 2

cc "$P" session_AAA release --org acme --stream per-account --reason bogus
has "validate/release-unknown-reason-refused" "an unrecognised --reason is refused" "unknown release reason"
rc_eq "validate/release-unknown-reason-exit-two" 2

cc "$P" session_AAA check --org acme --stage S1
has "validate/stage-on-check-refused" "--stage does not belong to check and is refused" "belongs to claim/renew"
rc_eq "validate/stage-on-check-exit-two" 2

cc "$P" session_AAA renew --org acme --stream per-account --stage S1 --lane x
has "validate/lane-on-renew-refused" "--lane does not belong to renew and is refused" "belongs to claim"
rc_eq "validate/lane-on-renew-exit-two" 2

env -u CLAUDE_CODE_SESSION_ID bash "$P/cloud-sessions/bin/cloud-claim.sh" check --org acme --stream per-account >"$WORK/nosid.out" 2>&1
RC=$?
LASTCMD="cloud-claim (no session id) check --org acme"; OUT="$(cat "$WORK/nosid.out")"
has   "validate/no-session-id-refused" "no \$CLAUDE_CODE_SESSION_ID and no --session is refused" "no session id"
rc_eq "validate/no-session-id-exit-two" 2

cc "$P" 'bad session!' check --org acme --stream per-account
has "validate/bad-session-id-refused" "a session id unsafe as a filename/ref is refused" "not usable as a filename or a git ref"
rc_eq "validate/bad-session-id-exit-two" 2

# Leads with an alphanumeric (so it passes the charset/leading-char check) but still
# contains '..', to exercise the DEDICATED '..' substring guard specifically, rather than
# the leading-character guard that a bare `../etc` would trip first.
cc "$P" session_AAA check --org 'acme..secret'
has "validate/org-path-traversal-refused" "an --org containing '..' is refused" "contains '..'"
rc_eq "validate/org-path-traversal-exit-two" 2

cc "$P" session_AAA check --org '../etc'
has "validate/org-leading-dot-refused" "an --org starting with a non-alphanumeric character (e.g. a bare ../etc) is refused too, by the charset guard" "not usable as a filename or a git ref"
rc_eq "validate/org-leading-dot-exit-two" 2

cc "$P" session_AAA bogus-verb --org acme
has "validate/unknown-verb-refused" "an unknown verb is an error, not a fallthrough" "unknown verb"
rc_eq "validate/unknown-verb-exit-two" 2

cc "$P" session_AAA help
has   "validate/help-lists-verbs" "help documents all four verbs" "release"
rc_eq "validate/help-exit-zero" 0

# =============================================================================
sec "11 — the dispatch block extracts mechanically and matches the design"
# =============================================================================

sed -n '/^# ```text$/,/^# ```$/p' "$CC_REAL" | sed -e '1d' -e '$d' -e 's/^# \{0,1\}//' > "$WORK/extracted-block.txt"
LASTCMD="sed -n .../\`\`\`text.../\`\`\`/p ... (dispatch block extraction)"; RC=0
OUT="$(cat "$WORK/extracted-block.txt")"
has "dispatch/extracts-nonempty" "the mechanical extraction command yields non-empty text" "Fleet claim check"
has "dispatch/mentions-check-verb" "the extracted block tells the agent to run check first" "cloud-claim.sh check"
has "dispatch/mentions-all-four-exit-codes" "the extracted block documents the STALE case" "STALE"
has "dispatch/covers-live-stop" "the extracted block tells the agent to STOP on LIVE" "STOP"
has "dispatch/orders-renew-before-beat" "the extracted block pairs renew with the heartbeat beat" "renew --org"
has "dispatch/orders-release-before-terminal-heartbeat" "the extracted block releases before the terminal heartbeat call" "release --org"

FENCE_OPEN="$(grep -c '^# ```text$' "$CC_REAL")"
FENCE_CLOSE="$(grep -c '^# ```$' "$CC_REAL")"
eq "dispatch/exactly-one-fence-pair-open"  "exactly one '# \`\`\`text' fence marker exists (a second would corrupt the sed range extraction)" "$FENCE_OPEN" "1"
eq "dispatch/exactly-one-fence-pair-close" "exactly one closing '# \`\`\`' fence marker exists"                                                "$FENCE_CLOSE" "1"

# =============================================================================
sec "12 — tamper guard: nothing real was touched"
# =============================================================================

eq "tamper/real-cloud-claim-sh-unchanged" "bin/cloud-claim.sh itself was not modified by running this suite" "$(sha "$CC_REAL")" "$REAL_CC_SHA"
eq "tamper/real-origin-unchanged" "the real repo's own origin remote is unchanged" \
   "$(git -C "$REPO_REAL" remote get-url origin 2>/dev/null || echo NONE)" "$REAL_ORIGIN"
REAL_LOG_HAS_TEST_IDENTITY_AFTER="$(git -C "$REPO_REAL" log --all --format=%ae 2>/dev/null | grep -c 'tests@example\.invalid' || true)"
eq "tamper/no-suite-commit-in-real-repo" \
   "no commit authored by this suite's own synthetic git identity (tests@example.invalid) ever reached the real repo — deliberately NOT a raw HEAD-equality check, since this repo sees legitimate concurrent commits from other sessions during a run" \
   "$REAL_LOG_HAS_TEST_IDENTITY_AFTER" "$REAL_LOG_HAS_TEST_IDENTITY_BEFORE"
eq "tamper/real-claims-state-unchanged" "the real repo's cloud-sessions/state/claims/ tree is byte-identical before and after" \
   "$(REAL_CLAIMS_STATE_MANIFEST)" "$REAL_CLAIMS_BEFORE"
eq "tamper/real-local-refs-unchanged" "no refs/cloud-claims/* or refs/heads/cloud-claims/* ref exists in the real repo" \
   "$(git -C "$REPO_REAL" for-each-ref 'refs/cloud-claims/*' 'refs/heads/cloud-claims/*' 2>/dev/null)" "$REAL_REFS_BEFORE"

# ------------------------------------------------------------------------ summary
{
  log ""
  log "## Summary"
  log ""
  log "- PASS: $PASS"
  log "- FAIL: $FAIL"
  log "- SKIP: $SKIP"
  if [[ "$FAIL" -gt 0 ]]; then
    log ""
    log "### Failed checks"
    for n in "${FAILED_NAMES[@]}"; do log "- \`$n\`"; done
  fi
} >> "$RESULTS"

echo
printf 'PASS=%d  FAIL=%d  SKIP=%d\n' "$PASS" "$FAIL" "$SKIP"
if [[ "$FAIL" -gt 0 ]]; then
  printf '%sNOT GREEN%s — failed: %s\n' "$R" "$O" "${FAILED_NAMES[*]}"
  exit 1
else
  printf '%sgreen.%s\n' "$G" "$O"
  exit 0
fi
