# 08 — Concurrent sessions and exodus

> **Provenance note, 2026-08-18:** originally written against a different repo
> (`daniel0tgc/internal-company-tool`) — mechanism-level content stands. Its
> `atlas-os/heartbeat/` board references do not exist in this repo
> (`emanate-tecum-workflow`); `cs board`/`cs exodus` therefore refuse loudly here by design
> (see [`09-porting-to-another-repo.md`](09-porting-to-another-repo.md)).

How several local sessions stop being invisible to each other, and how everything movable
goes to the cloud in one motion. Written for the operator who has forgotten the
architecture; the design intent is one sentence: **no session should ever need to be
prompted to participate.**

Component status matters here more than in any other playbook: the surfaces below were
built in the 2026-08-17 wave, and this file was written alongside them, not after
measuring them. The physics underneath is `VERIFIED` and linked; each surface's own tag is
in [the status table](#status-honestly) at the bottom. When the tests lane's results land,
they supersede that table.

---

## The model, one screen

Local sessions **auto-register** on the heartbeat board through a
SessionStart/UserPromptSubmit hook — presence is physics, not protocol, keyed on
`$CLAUDE_CODE_SESSION_ID` because the liveness oracle
matches ids, not lane names.
A session that then **claims real paths** (`--owns`) becomes partitionable, because the
board [refuses overlapping claims](../../atlas-os/heartbeat/README.md#claim-syntax).
`cs board` shows all of it on one screen. `cs exodus` splits the shared dirty tree **by
those claims** into per-lane branches, and writes a dispatch MANIFEST — it
does not dispatch, because the routines API is reachable only from inside a session
(dispatch is TTY-gated;
routines are the headless path).
`/cloud all` consumes the manifest: per unit it writes the lane's brief and commits it to
the unit's branch (exodus ships only a placeholder path — the manifest says so itself),
then dispatches the unit as a one-off routine, create → run → **disable**. `/cloud workflow <file>` is the deeper move: one cloud
coordinator reads a workflow file and fans out with subagents *in the cloud* —
cloud subagents run concurrently and are ledger-logged under their own session ids, `VERIFIED 2026-08-16`
— so the orchestrator itself leaves the laptop too.

```
  laptop                                                    cloud
┌───────────────────────────┐
│ session A ─┐              │
│ session B ─┼─ hook ───────┼─▶  heartbeat board
│ session C ─┘  (automatic) │    presence rows + owns-claims
└───────────────────────────┘         │
                                      ▼
                  cs board ── one screen: board rows, session
                      │       triage, worktrees, dispatch ledger
                      ▼
                  cs exodus ── partition the shared dirty tree
                      │        BY OWNS-CLAIMS; per-lane branch;
                      │        UNCLAIMED files → ask
                      ▼
                  MANIFEST  (nothing dispatched yet)
                      │
   inside a session:  ▼
                  /cloud all ── per unit: write+commit the brief,
                      │         routine create→run→DISABLE, then
                      │         cs track --lane --owns --from
                      │                --brief --trigger --expect-branch
                      ▼
              ┌─────────────────────────────┐
              │ one VM per lane             │──▶ claude/<lane> branch → PR
              └─────────────────────────────┘
              ┌─────────────────────────────┐
  variant:    │ /cloud workflow <file>      │──▶ ONE coordinator VM reads
              │                             │    the workflow file, fans out
              └─────────────────────────────┘    with subagents in the cloud
```

## Presence, claims, and what actually makes a session movable

Three states, and only the third can be moved:

| State | Set by | `cs board` shows | `cs exodus` can move it? |
|---|---|---|---|
| **Invisible** | nothing | `UNREGISTERED` | no — its files land in UNCLAIMED |
| **Present** | the `SessionStart` hook, automatically | a row with a `._presence/<id>` sentinel | **no** — a sentinel claims no real path |
| **Movable** | **`cs claim`**, run inside that session | a row carrying real owns-paths | yes |

The hook makes a session *visible without being asked*. It does **not** make it movable —
a presence sentinel is a placeholder, not an ownership claim. `cs claim` is the upgrade.

**What `cs claim` does.** It reads that session's own transcript, collects every path it
wrote through `Write`/`Edit`/`MultiEdit` and every Bash redirect whose target is plainly a
literal path, drops anything outside the repo or long since deleted, collapses to the
narrowest safe set (a directory only when *every* dirty file under it belongs to this
session), runs the claim through `heartbeat.py overlap`, and only then writes. If the gate
refuses, it prints the refusal, names the lane already holding the path, and **writes
nothing** — widening into another lane's territory is the one failure that would hand that
lane's work away.

**Why it derives rather than asks.** On 2026-08-18, moving three live sessions required a
human to read three transcripts and attribute thirteen dirty files by inspecting each
session's `Edit`/`Write` calls. That inference is mechanical, so it belongs in the tool.

**What it cannot do, stated plainly:**

- **It infers from writes.** A session that only *read* files claims nothing — correctly,
  since it has no work in flight, but do not mistake an empty claim for a failure.
- **It can under-claim when the work is in a subagent.** A subagent's tool calls live in
  its own transcript, so files only a subagent wrote may not appear. Use `--add <path>`.
- **It skips ambiguous Bash targets rather than guessing** — `> $SOMEVAR/x` is never
  claimed, and the count of skipped targets is printed. Over-claiming is the worse error.
- **`--dry-run` writes nothing**, verified by board checksum with a negative control
  proving that check can go red.

## Why the partition is safe

The measurement that forced this design, 2026-08-15: **three live sessions, one working
tree, every one of them `DIRTY`, zero mechanically movable** — recorded in
[the `/cloud` command](../../.claude/commands/cloud.md#steps), which named the blocker
precisely: the VM clones origin, and work living only in a shared dirty tree cannot be
separated per session without committing someone else's mid-flight edits. (The working
tree held roughly 42 dirty files that day; the per-file count is `UNVERIFIED` — no repo
record carries it, only the three-sessions/one-tree/zero-movable finding is written down.)

The insight `cs exodus` is built on: **the board's owns-claims are already the partition.**
A claim is a declared write-set, the board
[refuses any two live rows whose claims overlap](../../atlas-os/heartbeat/README.md#claim-syntax),
and every claim is a subtree claim — so at any moment, each dirty file falls under at most
one live lane. Committing lane A's claimed files to lane A's branch therefore cannot
capture lane B's half-finished edits: the disjointness that made concurrent *writing* safe
is exactly the disjointness that makes splitting the *tree* safe. Exodus invents nothing;
it reads the same rows [`DISPATCH.md`](../../atlas-os/playbooks/DISPATCH.md) already makes
lanes declare.

Which is also why **unclaimed dirty files are never touched without an explicit yes.** A
file no row claims has no declared owner, so no branch has a warrant for it — and
[a missing ownership record is not permission](../AGENT.md) (ground rule 4). Exodus lists
unclaimed files by name and stops. "Probably fine to bundle with lane A" is how someone
else's mid-edit state gets committed under the wrong flag; the answer is a human's, every
time.

## The honest limits

Each one has a workaround; none has a fix. Do not let the tooling's fluency imply
otherwise.

| Limit | Workaround |
|---|---|
| **A presence row makes a session visible, not movable.** The hook registers with the sentinel claim `owns="._presence/<session-id>"` — a nonexistent path, disjoint per session — because [a live row that owns nothing is refused](../../atlas-os/heartbeat/README.md#what-validate-refuses-to-let-stand). A sentinel claims no real files, so exodus can partition nothing for it | Upgrade the claim: `heartbeat.py update` (or re-register) with the real paths the session is writing, checked with `overlap` first — [commands](../../atlas-os/heartbeat/README.md#commands) |
| **Movable is not worth moving.** A session waiting on a human decision is mechanically movable and pointless to move — the VM would sit waiting on the same human, with worse latency. Exodus moves *work*, not *judgment*; the COULD/SHOULD distinction is [the `/cloud` command's step 1](../../.claude/commands/cloud.md#steps) | Answer the question locally first, or leave that lane off the manifest. Read the transcript, not the verdict column |
| **Conversations never migrate.** No export, no import, no attach — only work moves, restated as a brief ([`/cloud`](../../.claude/commands/cloud.md#steps), and [02 on writing briefs](02-dispatch.md#write-the-prompt-like-the-author-is-leaving)). The cloud session starts with no memory of what the local one knew | The brief carries the intent — `/cloud all` writes one per lane, from the board row's goal, its claims, and the manifest's file list — and what the brief omits, the cloud session cannot know. The *reverse* direction is better: teleport pulls a cloud session home **with** its full history — [03 § Bringing it home](03-steer-and-return.md#bringing-it-home) |
| **Semantic conflicts across disjoint files survive every gate.** Lane A renames a thing, lane B links to the old name; the claims never overlapped, both branches merge clean, the result is broken. The board, exodus, and git are all blind to it — [named in `/cloud` step 3b](../../.claude/commands/cloud.md#steps) | Re-run the suites after **each** merge, not once at the end — a green merge is not a green system |
| **The hook is staged until Daniel wires it.** [`.claude/settings.json`](../../.claude/settings.json) is deny-listed for agent writes (its own header says so), so no agent can apply hook wiring — the snippet is staged for a human at [`bin/hooks/WIRING.md`](../bin/hooks/WIRING.md). Until it is applied, sessions do **not** auto-register and the board shows only lanes that registered by hand | Wire it (Daniel, once), or fall back to manual `heartbeat.py register` per [`DISPATCH.md`](../../atlas-os/playbooks/DISPATCH.md) — the rest of the pipeline works the same either way |

## Runbook — the close-the-lid motion

Five steps, in order. Steps 1–3 are safe to run any time; step 4 dispatches real work.

```bash
cs board                    # 1. see everything: board rows, triage, worktrees, ledger
cs claim                    # 1b. IN EACH SESSION you want moved — derives what that
                            #     session has been editing and claims it on the board.
                            #     Without this a session is visible but NOT movable.
cs exodus --dry-run         # 2. preview the partition: units, CONFLICTs, UNCLAIMED
                            # 3. resolve what the dry run surfaced — on the board
/cloud all                  # 4. in a session — one confirmation, then it runs the
                            #    execute (branches + MANIFEST), writes each brief,
                            #    dispatches create→run→disable
cs board                    # 5. confirm the ledger rows landed; close the lid
```

- **Step 3 is the human step and cannot be skipped.** The dry run stops on CONFLICTs and
  lists UNCLAIMED files by design; resolution happens on the board (narrow or widen a
  claim, then re-run the dry run), never by editing the manifest. `/cloud all` then asks
  one confirmation covering the whole batch — "all of them, yes" is an acceptable answer;
  silence is not.
- **Step 4 runs inside a Claude Code session** (`/cloud all`), never from a bare shell —
  the routines API is the only headless dispatch path and it is reached in-session. Do
  **not** run the execute (`cs exodus` without `--dry-run`) by hand first: `/cloud all`
  runs the execute itself, exodus refuses a pre-existing `claude/<lane>` branch, and the
  re-run overwrites the manifest with the all-failed result. Each
  unit is create → run → **disable** before the next starts, and each gets a
  `cs track` row with full provenance (`--lane --owns --from --brief --trigger
  --expect-branch`) because
  the VM's own telemetry dies with the VM — the dispatch ledger is the record.
- **Whole workflow, orchestrator included:** `/cloud workflow <file>` dispatches one
  coordinator session that reads the workflow file (convention: `docs/workflows/`) and
  fans out in the cloud —
  subagent fan-out in a VM is `VERIFIED 2026-08-16`.
  Use it when the units are one workflow's stages rather than independent lanes.
- Results come back as `claude/<lane>` branches and PRs. Steer with `cs send`, retrieve
  with `cs tp` ([03](03-steer-and-return.md)), and re-run the suites after each merge.

### When something refuses

| Symptom | Move |
|---|---|
| Exodus reports `CONFLICT` — two rows' claims overlap a dirty path | Treat it as a stop, like [`/cloud` step 3b](../../.claude/commands/cloud.md#steps) says: narrow one lane's claim (`heartbeat.py overlap` to test, then `update`), or ship one lane and hold the other until it merges |
| Exodus reports `UNCLAIMED` files | Assign them to a lane's claim, or leave them local. Never wave them through by default — that is the one gate with a human in it on purpose |
| A routine is still armed after dispatch (`enabled: true`) | Run `/routines-audit`. Firing does not consume `run_once_at` and a disabled routine still shows a future `next_run_at` — **`enabled` is the deciding field** ([`../AGENT.md`](../AGENT.md)). Nine were once left armed in a single day |
| Board shows `STALE` for a session that is visibly working | The row's name does not match what the oracle sees — ids, not names, `V-021`. The hook keys rows on `$CLAUDE_CODE_SESSION_ID` for exactly this reason; a hand-registered lane name goes stale by construction |
| Triage says `0 mechanically movable` | Read the verdicts before concluding anything — the 2026-08-15 shared-tree case above. The durable fix is per-lane worktrees ([`worktree.py`](../../atlas-os/bin/worktree.py)) *before* work starts, plus real claims; not a forced dispatch now |
| `cs tp` refuses to bring a session home | Dirty local tree — it will not stash behind your back. Commit or clean, then retry ([03](03-steer-and-return.md#bringing-it-home)) |

Standing safety, unchanged by any of this: pass an explicit `mcp_connections` list on
every routine —
they inherit every connected connector otherwise
— and never put a repo directory on PATH, for
the reason measured 2026-08-15.

## Status, honestly

As of this file's writing (2026-08-17). A row here is a claim about *that day*; a later
run of [`../tests/run.sh`](../tests/README.md) supersedes it.

| Piece | Status |
|---|---|
| Routine dispatch chain (create → run → disable), headless | **`VERIFIED`** — 4b, and every probe was dispatched this way |
| Subagent fan-out inside a cloud VM, concurrent, ledger-logged | **`VERIFIED 2026-08-16`** — evidence |
| Heartbeat board refuses overlapping claims; liveness keys on session id | **`VERIFIED`** — [board contract](../../atlas-os/heartbeat/README.md), V-021 |
| The shared-tree blocker the partition answers | **`VERIFIED 2026-08-15`** — [recorded in `/cloud`](../../.claude/commands/cloud.md#steps) |
| Auto-register hook (behaviour, and its timing targets) | **staged for a human** — wiring in [`bin/hooks/WIRING.md`](../bin/hooks/WIRING.md), not applied. Offline suite green 2026-08-17, warm path ~60ms sandboxed (measured in a run against a different repo, so treat the timing as indicative only); behaviour under a real wired hook `UNVERIFIED` until Daniel applies it |
| `cs board` | **`VERIFIED` 2026-08-17** — run on this repo, all four sources `ok`, exit 0; suite §12 covers the degraded modes |
| `cs exodus` (partition correctness, branch/manifest output) | **dry-run `VERIFIED` 2026-08-17** on this repo (partition listed, exit 0, nothing written); execute exercised only in the sandboxed suite (§14) — a real-origin execute is `UNVERIFIED` |
| `/cloud all` manifest consumption | **`UNVERIFIED`** — built this wave; the dispatch chain under it is `VERIFIED` |
| `/cloud workflow <file>` end to end | **`UNVERIFIED`** — built this wave; the fan-out it relies on is `VERIFIED` |
| `._presence/<session-id>` sentinel-claim convention | Convention, chosen this wave — satisfies the board's owns-requirement by design; collision behaviour at scale `UNVERIFIED` |

## Next

→ back to [the playbook index](README.md) — 08 is the last one. The reading order that got
you here started at [01](01-setup.md); the daily motion is the runbook above.
