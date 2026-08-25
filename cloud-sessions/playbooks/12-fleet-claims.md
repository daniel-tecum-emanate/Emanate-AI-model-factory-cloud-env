# 12 — Fleet claims: one org+stream, one live worker

> **Numbering note:** the design doc that specifies this mechanism
> (`PRs/model-factory-cloud-environment-v1/04-multi-session-coordination-design.md`, §5)
> suggested the filename `11-fleet-claims.md`. By the time this was built, `11` had already
> been taken by [`11-finetune-pipeline-agent.md`](11-finetune-pipeline-agent.md), so this
> is `12`. Read that file's own build report
> (`PRs/model-factory-cloud-environment-v1/08-cloud-claim-build-report.md`) for the rest of
> what changed between the design and the build.

Playbook [`10`](10-fleet.md) answers *is this session alive, and doing what?* for every
agent on a cloud VM. This one answers a different question about the same fleet: **is this
`(org_slug, model_stream)` already being worked, by whom, at what stage, and is that claim
still good?** Read `10` first — this mechanism is a sibling to the heartbeat, not a
replacement, and reuses its plumbing on purpose.

Why a separate file rather than a new section in `10`: a claim has its own ref namespace,
its own JSON schema, its own algorithm for three verbs, and its own collision-handling rule
that is the *opposite* of the heartbeat's — folding it into `10` would bury a mechanism this
size inside a file about a different one. `08` and `10` are already kept separate for the
same reason despite being tightly related; `11` is kept separate from `10` despite *using*
its mechanism directly. A claim is not "a concrete application of 10's mechanism" the way
`11` is — it is a new mechanism that happens to depend on reading `10`'s heartbeats for one
fact (staleness). That earns it its own file too.

---

## Why this exists

`cloud-heartbeat.sh --lane <name>` is a string a human types. Two sessions can pass the
same lane, or two *different* lanes that are actually the same org+stream, and nothing
compares them. Nothing lets a newly dispatched agent ask, before touching `factory.py` or
any org data: *is my org+stream already claimed?* This is not hypothetical — three real
cloud fires against `org=qa-dryrun-synth` produced zero confirmed heartbeat signal
(`PRs/model-factory-cloud-dispatch-v1/RUN-LOG.md`), and nobody could tell whether any of the
three sessions was still silently alive. A fourth dispatch, with no claim check, starts with
the same blindness the first three had.

`cloud-claim.sh` closes that gap with a second, small, git-ref-based mechanism that mirrors
`cloud-heartbeat.sh`'s own plumbing — JSON file, dedicated ref namespace, hash-object /
write-tree / commit-tree, silent-degrade-except-on-real-refusal exit codes — but answers the
claim question instead of the liveness one. Full design:
`PRs/model-factory-cloud-environment-v1/04-multi-session-coordination-design.md`
(in `emanate-tecum-workflow`, not this repo).

## The shape of it

```
  YOU (laptop, may be shut)          ANTHROPIC VM × N                 GITHUB
  ─────────────────────────          ─────────────────                ──────
                                     agent starts
                                     │
                                     │ cloud-claim.sh check  ─────────► refs/heads/
                                     │   (before ANYTHING else)         cloud-claims/<org>/
                                     │                                  <stream>
                                     │ 0 FREE/MINE -> claim, then
                                     │   cloud-heartbeat.sh start ────► refs/heads/
                                     │ 1 STALE -> claim anyway           cloud-fleet/<sid>
                                     │ 3 LIVE  -> STOP, heartbeat
                                     │   blocked, do not touch the org
                                     │
                                     │ every stage transition:
                                     │   cloud-claim.sh renew  ───────► (same claim ref,
                                     │   cloud-heartbeat.sh beat ─────►  same heartbeat ref)
                                     │
                                     │ terminal:
                                     │   cloud-claim.sh release ──────► claim ref: released_at set
                                     │   cloud-heartbeat.sh done/failed
```

A claim file **never carries its own liveness clock.** It points at the holding session's
own heartbeat ref (`heartbeat_ref`), and *that* is dereferenced for staleness — once, in one
place, inside `cloud-claim.sh` itself. This is the direct fix for the class of bug behind
`heartbeat_validate.sh`'s own `adhoc-` prefix incident (`04-...md` §1.1): two records of the
same fact that can drift out of sync silently. Here there is only ever one record of
liveness (the heartbeat), and the claim only ever points at it.

## Claim identity: `(org_slug, model_stream)`, not `(org_slug, stage)`

It is tempting to key a claim on stage, since the task that motivated this is usually
framed as "org/stage." That would be wrong: stage is sequential within one run (a run
cannot be at S1 and S7 at once), so per-stage claims for the same run would never actually
contend with each other. The real contention is two *different* attempts — a retry, a
stale-looking survivor, a second operator — both trying to be **the** live run for one
`(org_slug, model_stream)`, which is exactly the resource identity
`fine-tuning/factory-product/data-model/factory_runs.md`'s own live-uniqueness invariant
already uses. Stage is not discarded — it is **claim content** (the `current_stage` field,
updated on every `renew`), not claim identity.

`model_stream` is one of exactly `per-account` | `intelligence`, defaulting to
`per-account` — mirroring `factory.py`'s own default. Every real dispatch fired so far
omits stream entirely, so in practice, today, org alone *is* the effective key; this only
starts to matter once an Intelligence-stream run is dispatched for real. `cloud-claim.sh`
enforces this as a closed vocabulary (refuses an unrecognised `--stream`) rather than
accepting any string, so a typo cannot silently open a third, uncollided namespace.

## Ref naming

```
refs/heads/cloud-claims/<org_slug>/<model_stream>
```

Always exactly two path segments after the namespace — never zero, never one, never three.
This is a hard git rule, not style: a ref cannot be a strict prefix of another
(`refs/heads/cloud-claims/acme` and `refs/heads/cloud-claims/acme/per-account` cannot
coexist), so `model_stream` is always written explicitly, even for the default
`per-account`. `org_slug` and `model_stream` are each validated with the exact regex
`cloud-heartbeat.sh` already uses for session ids (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, no `..`,
≤128 chars) — each becomes both a ref path segment and a filename, so each is validated, not
trusted.

Sibling to `cloud-fleet/*`, not nested in it. Session-id sharding for heartbeats exists so
"one writer per ref" makes collisions rare by construction. Org+stream sharding for claims
exists for the *opposite* reason: the ref's identity has to equal the resource's identity,
or mutual exclusion means nothing — a claim ref is deliberately the one thing multiple
sessions, over time, contend for.

## The claim file

```json
{
  "schema": 1,
  "kind": "cloud-claim",
  "org_slug": "qa-dryrun-synth",
  "model_stream": "per-account",
  "run_id": "eeab961b-bba3-4e6d-b560-35fe022fd79a",
  "held_by": "cse_013C1GmAkECnXRRJQtfS4qyo",
  "lane": "qa-dryrun-synth-per-account",
  "heartbeat_ref": "refs/heads/cloud-fleet/cse_013C1GmAkECnXRRJQtfS4qyo",
  "current_stage": "S3",
  "claimed_at": "2026-08-20T15:56:03Z",
  "updated_at": "2026-08-20T16:10:11Z",
  "released_at": null,
  "release_reason": null,
  "prior_claim": null,
  "history": [
    {"ts": "2026-08-20T15:56:03Z", "event": "claimed", "stage": "S1", "by": "cse_013C1GmAkECnXRRJQtfS4qyo"},
    {"ts": "2026-08-20T16:10:11Z", "event": "renewed", "stage": "S3", "by": "cse_013C1GmAkECnXRRJQtfS4qyo"}
  ]
}
```

- **`held_by`** — named after `coordination/heartbeat/STATE.md`'s File Locks column
  (`file | held_by | task | status`), on purpose: same concept, same vocabulary.
- **`heartbeat_ref`** — the one pointer that makes liveness dereferencable without guessing
  a naming scheme.
- **`updated_at`** — informational only, for a human reading `git log`. **Not authoritative
  for staleness** — see below.
- **`released_at` / `release_reason`** (`done` | `failed` | `handoff`) — non-null means
  free, checked first, before anything about heartbeats.
- **`prior_claim`** — populated only when this claim superseded a stale one:
  `{held_by, current_stage, claimed_at, last_heartbeat_at, last_heartbeat_status,
  reclaimed_at}`. Never populated for a graceful (`released_at`-set) handoff — nothing was
  "left behind" worth recording there.
- **`history`** — bounded to the last 10 entries.

## The four verbs

```bash
cloud-claim.sh check   --org <slug> [--stream per-account|intelligence]
cloud-claim.sh claim   --org <slug> [--stream ...] --stage <stage> [--run-id <uuid>] [--lane <name>]
cloud-claim.sh renew   --org <slug> [--stream ...] --stage <stage> [--run-id <uuid>]
cloud-claim.sh release --org <slug> [--stream ...] --reason done|failed|handoff
```

`--org`/`--stream` are required on **every** verb, including `renew`/`release` — unlike
`cloud-heartbeat.sh`, where only `start` needs `--lane`. Here the ref is keyed on the
resource, not the session, so which resource is never implicit. Session id comes from
`$CLAUDE_CODE_SESSION_ID`, or `--session <id>`, exactly as in `cloud-heartbeat.sh`.

**`check`** fetches both `cloud-claims/*` and `cloud-fleet/*` itself (a newly dispatched
agent has no operator standing by to remember a two-step fetch-then-materialise
incantation, unlike `cs fleet`'s own pattern — see [`10-fleet.md`](10-fleet.md)'s honest
limits), then classifies:

| Result | Meaning | Exit |
|---|---|---|
| **FREE** | never claimed, or the claim was released | 0 |
| **MINE** | `held_by` is this session — a resumed session re-checking its own claim | 0 |
| **STALE** | claimed, but the holder's heartbeat is absent, unreadable, older than 30 minutes, or terminal (`DONE`/`FAILED`) — reclaimable | 1 |
| **LIVE** | claimed, and the holder's heartbeat is fresh and `ACTIVE`/`BLOCKED` | 3 |

`check` never writes anything — it is the read-only probe a newly dispatched agent runs
before doing anything else.

**`claim`** runs `check`'s classification internally first, so a bare `claim` is always
safe to call without a preceding `check`. **LIVE refuses (exit 3), nothing pushed** — this
is not a degrade-and-continue case; proceeding would mean two sessions doing real work,
possibly real spend, against the same run. FREE/MINE/STALE all build a new claim and push
it with a **plain, non-forced** `git push <sha>:refs/heads/cloud-claims/<org>/<stream>`.

**`renew`** refuses (exit 2, nothing recorded) unless the currently-fetched remote claim's
`held_by` is this session. Otherwise it updates `current_stage` (and `run_id`, if it became
known after `claim`) and pushes. Call it on every stage transition, in the same breath as
the heartbeat beat — not on a timer, which keeps claim-ref push volume at roughly once per
`STAGES` entry rather than once every few minutes.

**`release`** sets `released_at`/`release_reason` (`done`|`failed`|`handoff`) and pushes.
Call it **before** the terminal heartbeat call (`done`/`failed`) — release is never inferred
from the heartbeat's own terminal status, on purpose: "I am voluntarily done" is a fact only
the claim itself should assert, so a session that forgets to call `release` is
indistinguishable from one that crashed, and both correctly fall through to STALE rather
than a silent FREE.

## The one piece that is genuinely load-bearing: the push on `claim` is never forced

A plain (non-`+`) push asserts the ref's expected prior value. For a session that read the
ref as absent, that assertion is "this ref does not yet exist" (the all-zero old-sha git
sends for a new ref); if another session's `claim` landed in the meantime, the push is
rejected with the same `[rejected]`/"fetch first"/non-fast-forward family
`cloud-heartbeat.sh` already parses for its own ref-reuse case. **On rejection, `claim` does
not reparent onto the winner and retry, unlike `cloud-heartbeat.sh`.** Reparenting would
silently steal a claim that is not this session's to take. Instead it re-fetches, reads the
winner's claim, and re-derives FREE/MINE/STALE/LIVE against *it*:

- winner is live and not mine → **I lost the race fairly.** Exit 3, refuse, nothing pushed.
  The dispatch prompt (below) then calls `cloud-heartbeat.sh blocked` with the winner's
  identity — `cloud-claim.sh` itself never calls the heartbeat script; that call belongs to
  the agent following the dispatch block, so the integration point stays a prompt-level
  decision, not a hard-coded one.
- winner is *also* already stale (a narrow edge case: they crashed between their own push
  and their first heartbeat beat) → retry the claim, parented on their commit, bounded at
  the same `PUSH_ATTEMPTS = 4` as `cloud-heartbeat.sh`, then refuse loudly rather than spin.

`renew`/`release`, by contrast, only ever write a ref they already verified they hold, so a
rejection there **is** treated as a benign double-write of their own history — the same
reparent-and-retry `cloud-heartbeat.sh` already does — because that ref should have exactly
one legitimate writer at a time while a claim is live.

## Staleness: one dereference, matching the 30-minute local lease

A claim is only valid if the `held_by` session's own `cloud-fleet/<held_by>` heartbeat ref
shows `updated_at` within **30 minutes** and its last `status` is not `DONE`/`FAILED`. 30
minutes, matching `coordination/heartbeat/STATE.md`'s local lease exactly — **not**
`cs fleet`'s own `--stale-after` default of 15 (see [`10-fleet.md`](10-fleet.md) §3) — a
deliberate choice: a claim gates potentially-costly real work (the `train` stage,
`dispatch_node`'s real spend path), so the more conservative of the two existing numbers is
the right default. A false STALE risks a real collision; a false LIVE only costs time.

**The long-pole case:** `train` can legitimately run for hours waiting on an external job.
Rather than lengthen the global threshold (which would make every *other* stage's
staleness detection slower), the fix belongs in the dispatch prompt for long-running
stages: require a fixed beat cadence (every 5-10 minutes) regardless of milestones. Claim
correctness now depends on that cadence in a way it didn't before this mechanism existed.

## The dispatch block every payload carries

Paste this **immediately before** the existing `cloud-heartbeat.sh` block (it must run
first) — kept in [`../bin/cloud-claim.sh`](../bin/cloud-claim.sh)'s own header so it cannot
drift from the tool it describes:

````bash
sed -n '/^# ```text$/,/^# ```$/p' cloud-sessions/bin/cloud-claim.sh \
  | sed -e '1d' -e '$d' -e 's/^# \{0,1\}//'
````

```text
## Fleet claim check — required, and it runs BEFORE anything else

  git fetch origin \
    '+refs/heads/cloud-claims/*:refs/remotes/origin/cloud-claims/*' \
    '+refs/heads/cloud-fleet/*:refs/remotes/origin/cloud-fleet/*'

  bash cloud-sessions/bin/cloud-claim.sh check --org <ORG> --stream <STREAM>

Read the exit code before doing anything else:

  0  FREE or MINE  -> claim it, then start your heartbeat, then begin real work:
       bash cloud-sessions/bin/cloud-claim.sh claim \
         --org <ORG> --stream <STREAM> --stage <FIRST_STAGE> --run-id <RUN_ID> --lane <LANE>
       bash cloud-sessions/bin/cloud-heartbeat.sh start --lane <LANE> --note "claimed <ORG>/<STREAM>"

  1  STALE   -> the previous holder looks dead (details printed). Claim it anyway --
       cloud-claim.sh claim records exactly what you're superseding -- then proceed
       as above. Do not silently skip this step because it "seems fine."

  3  LIVE    -> STOP. Someone else is already working this org+stream. Do not run
       factory.py, do not run run_pipeline.py, do not touch this org's data. Say so:
         bash cloud-sessions/bin/cloud-heartbeat.sh blocked \
           --kind dependency-missing \
           --detail "org=<ORG> stream=<STREAM> already claimed (see cloud-claim.sh check output)" \
           --needs "wait for release, or a human decides to force-clear it"
       Then stop. This is not a failure -- it is the system working.

  2  refused -> a usage/precondition problem (bad org/stream name, no origin, not a
       git repo). Fix the dispatch, do not retry blindly.

On every later stage transition, renew the claim in the same breath as your heartbeat
beat:

  bash cloud-sessions/bin/cloud-claim.sh renew --org <ORG> --stream <STREAM> --stage <STAGE> --run-id <RUN_ID>
  bash cloud-sessions/bin/cloud-heartbeat.sh beat --phase "<STAGE>" --progress <N>/<TOTAL>

At the end, whichever is true, release before your terminal heartbeat call:

  bash cloud-sessions/bin/cloud-claim.sh release --org <ORG> --stream <STREAM> --reason done
  bash cloud-sessions/bin/cloud-heartbeat.sh done --note "..."

  bash cloud-sessions/bin/cloud-claim.sh release --org <ORG> --stream <STREAM> --reason failed
  bash cloud-sessions/bin/cloud-heartbeat.sh failed --detail "..."
```

This is not yet pasted into `model-factory-finetune-orchestrator-v1`'s real routine
prompt — that integration, and whether `dispatch_node()` itself should hard-gate on a live
claim, are open questions for Daniel (design doc §6), not resolved by building the
standalone script.

## What this does not solve (unchanged from the design doc)

- **It cannot see a session that never got far enough to call it.** If the dispatched
  session's self-clone/checkout step fails before reaching the claim-check step, neither
  this mechanism nor the heartbeat ever produces a signal.
- **It is advisory, not enforced.** Nothing forces a cloud agent to call `check` before
  `factory.py` — no local hook fires inside a cloud sandbox. The dispatch block above is the
  only enforcement surface, exactly as it already is for the heartbeat contract.
- **A local `run_pipeline.py` invocation does not claim anything under this design.** Local
  and cloud stay separate systems, mirroring `coordination/heartbeat/STATE.md` vs.
  `cloud-fleet/*`. If a local run and a cloud run could ever target the same org+stream,
  this design's claim ref has no visibility into the local one at all — named, not solved,
  in the design doc's §3.15 and §6.
- **This does not add fleet-wide visibility.** `check` answers for the one org+stream it
  names. A "show me every claimed org+stream right now" listing verb is a natural,
  small follow-on, not built here.

## Related

- [`10-fleet.md`](10-fleet.md) — the heartbeat this mechanism dereferences for staleness,
  and the fetch-then-materialise pattern this mechanism deliberately does NOT copy (it
  fetches internally instead)
- [`../bin/README.md`](../bin/README.md) — `cloud-claim.sh`'s own cost/side-effects entry
- [`../tests/run-claims.sh`](../tests/run-claims.sh) — the regression suite: happy path,
  the LIVE refusal, the STALE reclaim, all against a scratch bare remote
- `PRs/model-factory-cloud-environment-v1/04-multi-session-coordination-design.md` — the
  full design this playbook summarises
- `PRs/model-factory-cloud-environment-v1/08-cloud-claim-build-report.md` — what was built,
  the real test output, and the judgment calls made while implementing
