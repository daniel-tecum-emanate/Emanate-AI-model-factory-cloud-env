# 10 — Running a fleet of agents on cloud VMs

> **Provenance note, 2026-08-18:** originally written against a different repo
> (`daniel0tgc/internal-company-tool`) — mechanism-level content stands (the fleet
> mechanism itself is genuinely usable here). Its `atlas-os/heartbeat/STATE.md`
> reference does not exist in this repo (`emanate-tecum-workflow`); this repo's own
> heartbeat board is [`../../coordination/heartbeat/STATE.md`](../../coordination/heartbeat/STATE.md),
> a different, unconnected system — the fleet mechanism (`cloud-fleet/*` refs, `cs fleet`)
> does not depend on either board.

Playbook [`08`](08-concurrent-sessions-and-exodus.md) is about many sessions on **this
machine** and how to move them. This one is about what happens after they leave: **many
agents running on cloud VMs at once**, and the two questions you actually have while they
run — *is it alive?* and *is it stuck?*

Read [`02-dispatch.md`](02-dispatch.md) and [`05-routines.md`](05-routines.md) first. This
playbook assumes you can already dispatch one agent and want twenty.

---

## The shape of it

Three moving parts, and they meet in git — never on a laptop that may be closed.

```
  YOU (laptop, may be shut)          ANTHROPIC VM × N                 GITHUB
  ─────────────────────────          ─────────────────                ──────
  routines API  ──create/run/──────► agent starts
                   disable           │
  cs track <id> ──► state/           │ cloud-heartbeat.sh start/beat/
                    sessions.jsonl   │ blocked/done/failed
                    (the ledger)     └──────────────────────────────► refs/heads/
                                     │                                cloud-fleet/<session-id>
                                     │ real work ───────────────────► claude/<lane>  → PR
  git fetch  ◄───────────────────────┴────────────────────────────────────┘
  cs fleet / fleet_ui.py
```

**Why git and nothing else.** A cloud session's own hook telemetry is written to the VM's
disk, is gitignored, and dies when the VM is reclaimed — deliberately, and do not "fix" it
([`../AGENT.md`](../AGENT.md), and the reverted fix in
[`../reference/verified-facts.md`](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md)). The heartbeat board at
`atlas-os/heartbeat/STATE.md` does not travel to a VM in any useful way either (`V-021`).
Anything that must outlive the VM has to be **pushed**. That is the whole design.

---

## 1 · Dispatch — create → run → **disable**, as one sequence

`claude --cloud` needs a TTY, so it cannot start a fleet from a script, a hook, or another
cloud session. The routines API is the only headless dispatch path
([`05-routines.md`](05-routines.md); `VERIFIED` — every probe in this folder was dispatched
that way). Per agent:

1. **create** the routine with the payload — task brief **plus the heartbeat block in §2**;
2. **read it back** and check `mcp_connections`;
3. **run** it;
4. **disable** it.

Four rules that have each cost something here:

- **Firing does not disarm.** `run` does not consume `run_once_at`, and a disabled routine
  still shows a future `next_run_at`. `enabled` is the deciding field. Nine probe routines
  were left armed on 2026-08-14 by the person who had just written the warning. Disable is
  step 4, not a follow-up task.
- **`"mcp_connections": []` does not mean "none".** An empty list reads as *unspecified*,
  and a routine created with one came back holding Gmail, Drive and Calendar
  (`V-041`, `VERIFIED 2026-08-17`). The only control proven to work is to `get` the routine
  after creating it and read the field back. If it holds connectors the work has no
  business with, disable rather than fire.
- **`allowed_tools` is an auto-approval list, not a sandbox** (`VERIFIED 2026-08-17`).
  Putting a short list there does not stop the agent using anything else. Do not dispatch
  credentialed work on the strength of it.
- **Record the dispatch or it is invisible.** The ledger is the only record that survives a
  dead VM, and `cs fleet` merges on it:

  ```bash
  cs track <session-id> "what it is doing" \
      --lane fine-tune-07 --expect-branch claude/fine-tune-07 \
      --brief briefs/fine-tune-07.md --trigger <routine-id>
  ```

  Without a row the agent can still appear (a heartbeat with no ledger row is rendered and
  labelled as such), but with no lane, no brief, and no branch to check.

### Subagents on the VM

An agent on a cloud VM can fan out. The `Agent` tool is present there, several subagents
run genuinely concurrently, they inherit the VM's git credentials, and their tool calls are
logged under distinct child session ids — `VERIFIED 2026-08-16`
([`../reference/subagents-in-cloud-2026-08-16.md`](../archive/foreign-repo-provenance-2026-08-18/subagents-in-cloud-2026-08-16.md)),
and again end-to-end on 2026-08-17 when one cloud session ran an entire workflow,
coordinator included, and opened one PR. So the unit of dispatch is not one agent — it is
one *coordinator* that spawns its own.

Two things to put in the payload when you do that:

- the coordinator beats for the whole unit; a subagent that beats too must pass
  `--session <distinct-id>`. Subagents can inherit the parent's `$CLAUDE_CODE_SESSION_ID`,
  and two writers on one id race for the same ref — the emitter retries four times, then
  warns and keeps the beat on the VM's disk only, where it dies.
- give each subagent a lane label, so the coordinator's `--note` can say which one is stuck.

---

## 2 · The heartbeat block every payload carries

Paste this into the routine payload, verbatim. It is the contract that makes a blocked
agent visible instead of a VM quietly burning. It is kept in
[`../bin/cloud-heartbeat.sh`](../bin/cloud-heartbeat.sh) so it cannot drift from the tool it
describes; extract it mechanically rather than retyping it:

````bash
sed -n '/^# ```text$/,/^# ```$/p' cloud-sessions/bin/cloud-heartbeat.sh \
  | sed -e '1d' -e '$d' -e 's/^# \{0,1\}//'
````

```text
## Fleet heartbeat — required, and it is how anyone knows you are alive

Run these from the repo root. They cost nothing, touch no branch of yours, and
exit 0 even when they fail — never let one stop your work.

  bash cloud-sessions/bin/cloud-heartbeat.sh start --lane <LANE> --note "<one line: what you are doing>"
  # then every few minutes, and at every milestone:
  bash cloud-sessions/bin/cloud-heartbeat.sh beat --phase "<what you are doing NOW>" --progress 2/7
  # exactly once at the end, whichever is true:
  bash cloud-sessions/bin/cloud-heartbeat.sh done   --note "<what landed: branch, PR, files>"
  bash cloud-sessions/bin/cloud-heartbeat.sh failed --detail "<what broke, concretely>"

## If you are stuck: DECLARE IT. Do not guess.

Do not invent a plausible answer, do not pick a default because none was given,
do not work around a missing credential, do not skip a check and call it passed.
Say so instead:

  bash cloud-sessions/bin/cloud-heartbeat.sh blocked \
    --kind <one of: needs-decision | needs-credential | needs-human-action |
                    dependency-missing | rate-limited | ambiguous-instruction> \
    --detail "<what is blocking you, concretely>" \
    --needs  "<the exact answer, artifact or action that unblocks you>"

Then KEEP BEATING while blocked — a blocked agent that goes silent is
indistinguishable from a dead one — and carry on with any part of the task that
is NOT blocked. When it clears:

  bash cloud-sessions/bin/cloud-heartbeat.sh beat --unblocked --phase "resumed: <what changed>"

Warnings from this script (e.g. a failed push) are not your problem. Keep working.
```

The six blocker kinds are a closed vocabulary — the emitter refuses an unknown one and
records nothing, so the blocker is not silently mistyped into invisibility:

| kind | use it when |
|---|---|
| `needs-decision` | a choice only a human can make |
| `needs-credential` | a token, key or login you do not have and must not invent |
| `needs-human-action` | something offline: an approval, a click, a merge |
| `dependency-missing` | a file, service, branch or tool that is not there |
| `rate-limited` | throttled or quota-exhausted; retrying is not progress |
| `ambiguous-instruction` | the task admits two readings and guessing is not safe |

What the emitter does with it, so nothing about it surprises you: it writes
`cloud-sessions/state/fleet/<session-id>.json`, then commits **that file only** with git
plumbing (`hash-object` → private `GIT_INDEX_FILE` → `write-tree` → `commit-tree`) and
pushes it to `refs/heads/cloud-fleet/<session-id>`. It never runs `add`, `commit`, or
`checkout`, never touches HEAD, the index or the working tree, and never puts telemetry in
the lane's PR diff. It exits **0** on every degraded path — offline, no credentials, lost
race — and **2** only on a refusal that means *nothing was recorded* (no session id, not a
git repo, no origin, bad usage). Details: [`../bin/README.md`](../bin/README.md).

---

## 3 · Watching the fleet

### Fetch first — the step that is easy to skip

Beats land on `refs/heads/cloud-fleet/*`, which a normal `git fetch` does not bring down,
and `cs fleet` reads heartbeat **files**. So the read path is fetch, then materialise:

```bash
git fetch origin '+refs/heads/cloud-fleet/*:refs/remotes/origin/cloud-fleet/*'
mkdir -p cloud-sessions/state/fleet
git for-each-ref --format='%(refname)' 'refs/remotes/origin/cloud-fleet/*' > /tmp/cs-fleet-refs
while read -r ref; do
  sid="${ref##*/}"
  git show "${ref}:cloud-sessions/state/fleet/${sid}.json" \
    > "cloud-sessions/state/fleet/${sid}.json"
done < /tmp/cs-fleet-refs
cs fleet
```

**`VERIFIED 2026-08-18`**, against a throwaway origin with a real emitter run on one side
and a real `cs fleet` on the other. Skipping the materialise step is not cosmetic — with
the refs fetched but not materialised, a genuinely BLOCKED agent renders as
`0 blocked … 1 never reported` and `cs fleet` **exits 0**. After the loop, the same agent:

```
!! 1 AGENT(S) BLOCKED — each needs a human. cs fleet exits 3.
  !! demo-two  demo-2  blocked, last report ? (STALE)
       kind   : needs-credential
       detail : no HF token on the VM
       needs  : a token, or drop the upload step
```

`cs fleet` does not perform that fetch itself today — see the limits in §5.

### `cs fleet`

```bash
cs fleet                    # one screen: every agent, blocked ones first
cs fleet --watch 60         # re-render every 60s; Ctrl-C exits 130
cs fleet --stale-after 30   # widen the freshness threshold (default 15 minutes)
cs fleet --no-remote        # skip `git ls-remote`; branch facts read "?", not "missing"
cs fleet --json             # the whole model, every fact carrying its source
cs fleet --prune --dry-run  # what it would delete, and why it refuses the rest
```

It merges three sources and tags each one in the header — the **ledger**, the
**heartbeats**, and the **branches** on origin — and if any of them is `DEGRADED`, `EMPTY`
or `SKIPPED` it says the view is **PARTIAL, not empty**. Behaviour worth relying on:

- a **BLOCKED** agent sorts first, is printed in full with kind / detail / needs / source,
  and sets the exit code;
- **STALE is not BLOCKED.** A heartbeat older than the threshold is a *claim*, not proof:
  the agent may be fine and silent, or dead. It never rewrites the reported status and
  never sets the exit code;
- a ledger row with **no heartbeat is rendered, never omitted**;
- a heartbeat file whose declared `session_id` disagrees with its filename is **UNTRUSTED**
  and is attributed to *neither* agent — unknown provenance refuses;
- a status the tool does not recognise is **UNKNOWN**, never folded into "working".

Exit codes, so a wrapper can act without parsing: **3** blocked · **4** failed · **1** a
source was degraded or a heartbeat untrusted · **130** `--watch` interrupted · **0**
nothing reported blocked — which is *not* the same as "every agent is fine", because an
agent that never reports cannot be seen at all.

```bash
cs fleet --no-remote >/dev/null || case $? in
  3) echo "someone is blocked — go look" ;;
  4) echo "someone failed" ;;
esac
```

### The page

```bash
python3 cloud-sessions/bin/fleet_ui.py --open
```

One self-contained HTML file at `cloud-sessions/state/fleet-ui.html` — no network, no
external resource, renders from `file://`. It prefers `cs fleet --json` and falls back to
reading `state/sessions.jsonl` and `state/fleet/*.json` directly, **saying on the page**
which it used. Exit **1** means the page was written and is partial. Leave it open on a
second screen while a fleet runs.

---

## 4 · When an agent reports blocked

1. **Read `needs` first.** It is the one field that says what would clear it. If it reads
   `NOT REPORTED`, the agent declared a blocker without saying what it wants — ask:
   `cs send <id> "what exactly are you blocked on?"`.
2. **Answer it, don't do it for it.** `cs send` needs no TTY and works with the laptop shut.
   Send the decision, the path, the corrected instruction.
3. **The agent clears its own blocker** with `beat --unblocked --phase "resumed: …"`. Watch
   for that, not for silence: if it stops beating while blocked you have lost the
   distinction between *stuck* and *dead*, and `cs fleet` will say so as STALE rather than
   guessing.
4. **`needs-credential` is a stop, not a puzzle.** Cloud environments have no secrets store
   — anyone with the environment can read anything you put in it. The correct answer is
   usually to drop that step from the cloud unit and do it locally, not to ship a token.
5. **There is no `cs stop`.** To wind an agent down: `cs send <id> "stop here and summarise
   what you did"`, then archive it from claude.ai/code. `cs rm` only forgets it locally.

A blocked agent is the system working. The failure mode this whole playbook exists to
prevent is the *other* one: an agent that guesses, keeps going, and produces confident
output nobody can trust.

---

## 5 · The honest limits

Everything here was measured on 2026-08-18 against the files on disk that day. Re-check it
after any lane touches `bin/cs`, `bin/cloud-heartbeat.sh` or `bin/fleet_ui.py`.

- **`cs fleet` does not read the `cloud-fleet/*` refs.** It reads this checkout's
  `state/fleet/*.json`, plus `state/fleet/` as carried on each ledger row's
  `expect_branch` — which is the lane's work branch, and the emitter deliberately never
  writes there. The fetch-and-materialise loop in §3 is the bridge, and until something
  closes the gap it is **required**, not optional. `VERIFIED 2026-08-18`; reported to the
  coordinator.
- **Schema drift between the emitter and `cs fleet`**, same measurement. The emitter writes
  `updated_at`; `cs fleet` reads `ts` — so every heartbeat shows age `?` and is flagged
  STALE regardless of how fresh it is. The emitter writes `ACTIVE`; `cs fleet`'s vocabulary
  is `starting/working/blocked/waiting/done/failed` — so a healthy agent renders as
  `UNKNOWN`, with the reason printed under the row. `BLOCKED`, `DONE` and `FAILED` do match,
  which is why the path that matters most works. [`fleet_ui.py`](../bin/fleet_ui.py) reads
  both spellings and is unaffected.
- **Nothing here is covered by the regression suite.** `grep -c fleet cloud-sessions/tests/run.sh`
  → `0` on 2026-08-18. `tests/run.sh` green says nothing about `cs fleet`, the emitter or
  the UI.
- **No fleet-wide stop, and no fleet-wide dispatch.** Each agent is created, run and
  disabled individually; `/cloud all` dispatches an exodus manifest, which is the
  *departure*, not the fleet.
- **Rate limits are shared.** Twenty agents draw on one subscription. Nothing here charges
  per VM, and nothing here should ever provision paid infrastructure —
  [`../reference/billing.md`](../archive/foreign-repo-provenance-2026-08-18/billing.md).
- **Exit 0 is not "all clear".** It means nothing *reported* blocked.

---

## Related

- [`05-routines.md`](05-routines.md) — the dispatch mechanics this playbook assumes
- [`08-concurrent-sessions-and-exodus.md`](08-concurrent-sessions-and-exodus.md) — the
  local half: presence, claims, `cs board`, `cs exodus`
- [`09-porting-to-another-repo.md`](09-porting-to-another-repo.md) — the emitter and
  `cs fleet` port with the folder; `cs board`, `cs claim` and `cs exodus` do not
- [`12-fleet-claims.md`](12-fleet-claims.md) — the sibling mechanism that answers *is this
  org+stream already being worked?*, keyed on `(org_slug, model_stream)` rather than
  session id, and dereferences the heartbeat this playbook describes for its own staleness
  check — read this playbook first, then `12`
- [`../bin/README.md`](../bin/README.md) — every executable, its cost, its side effects
- [`../state/README.md`](../state/README.md) — the ledger, the manifest, the fleet directory
- [`../reference/verified-facts.md`](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md) — the evidence record
