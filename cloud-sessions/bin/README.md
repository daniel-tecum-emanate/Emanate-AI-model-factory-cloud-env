# bin/

> **Provenance note, 2026-08-18:** originally written against a different repo
> (`daniel0tgc/internal-company-tool`) — mechanism-level content stands; its `atlas-os/`
> mentions describe a dependency that does not exist in this repo
> (`emanate-tecum-workflow`), so `cs board`/`cs exodus` refuse loudly here by design (see
> [`../playbooks/09-porting-to-another-repo.md`](../playbooks/09-porting-to-another-repo.md)).

The executables this folder ships. **Superseded 2026-08-18 — this file used to open "One
file: `cs`".** There are now three programs and a hooks directory, and the "nothing outside
this folder" claim it also made was already wrong. Both are corrected below.

| File | What it is | Cost | Side effects |
|---|---|---|---|
| [`cs`](cs) | the single operator entrypoint — every command in the table below | free except `start`/`handoff`/`send`/`new --cloud`, which consume rate limits | appends `../state/sessions.jsonl`; `env set/clear` writes `~/.claude/settings.json`; `exodus` creates branches; `fleet --prune` deletes heartbeat files |
| [`cloud-heartbeat.sh`](cloud-heartbeat.sh) | the **cloud-side** emitter. An agent on a VM calls it to say it is alive, making progress, blocked, done, or failed | free, no model call | writes `../state/fleet/<id>.json`; pushes one commit to `refs/heads/cloud-fleet/<id>`; creates a local ref under `refs/cloud-fleet/` |
| [`cloud-claim.sh`](cloud-claim.sh) | a mutual-exclusion claim on one `(org_slug, model_stream)`, so two cloud sessions never both advance the same fine-tune pipeline run. `check`/`claim`/`renew`/`release`. Sibling to `cloud-heartbeat.sh`, not a flag on it — same plumbing, a different question | free, no model call | writes `../state/claims/<org>/<stream>.json`; pushes one commit to `refs/heads/cloud-claims/<org>/<stream>`; creates a local ref under `refs/cloud-claims/`; `check` writes nothing |
| [`fleet_ui.py`](fleet_ui.py) | renders the fleet as one self-contained HTML page | free, no network at all | writes `../state/fleet-ui.html` (or `--out`) and nothing else |
| [`hooks/`](hooks/README.md) | `session_register.sh` + its staged wiring — auto-registration on the heartbeat board | free | board rows via `heartbeat.py`; `../state/hook-errors.log` |

Nothing else belongs here — see [`../AGENT.md`](../AGENT.md) for the layout rule.

## Put `cs` on PATH — a wrapper, **not** a symlink

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/cs <<EOF
#!/bin/bash
exec "$PWD/cloud-sessions/bin/cs" "\$@"
EOF
chmod +x ~/.local/bin/cs
```

**Superseded 2026-08-17 — this file used to print `ln -sf … ~/.local/bin/cs`, and that
install is broken.** `cs` derives its root from `BASH_SOURCE` without resolving symlinks
([`cs:14-15`](cs)), so invoked *through* a symlink it computes `ROOT=~/.local`, looks for a
ledger at `~/.local/state/sessions.jsonl`, and prints `no dispatches recorded` with **exit
0** over a real ledger. An empty-but-fine answer where a populated one exists is exactly
what ground rule 4 forbids. Measured 2026-08-17, in a porting audit that reproduced this as
one of its concrete breaks. The wrapper `exec`s the real path, so `BASH_SOURCE` stays truthful. The
underlying resolution in `cs` is still unfixed — the wrapper is the workaround, not the fix.

**And do not put `cloud-sessions/bin` on PATH at all.** It is tracked, agent-writable, and
files here keep their exec bit through a clone — so a file committed here named `git` (or
`ls`, or `python3`) would shadow the real binary the next time you type it. A cloud session
has Bash and is told to commit and push, so such a file reaches your Mac through a merged
PR, a branch checkout, or `cs tp`. Naming one known file closes the path without giving up
the command. Found by the safety audit, 2026-08-15.

**Do not edit `cs` as documentation work.** If you change it, run
`bash cloud-sessions/tests/run.sh` (free, offline, dispatches nothing), then `cs doctor` and
the refusal paths (dirty tree, no upstream, no remote, no TTY) on the real machine before
claiming it works.

---

## `cs` — the commands

The full list accepted by the dispatcher at the bottom of `cs`, checked against `cs help` on
2026-08-18. `last` resolves to the most recent dispatch in the ledger; an ID argument may
also be a full `claude.ai/code` URL, which is stripped down to the bare ID.

| Command | What it does |
|---|---|
| `cs doctor` | Preflight only — touches nothing and dispatches nothing. Checks the resolved `claude` binary and version, that auth is a claude.ai subscription rather than an API key, that no `ANTHROPIC_*`/Bedrock/Vertex env var is overriding that auth, local `gh` authentication, the repo's branch, remote, worktree cleanliness and upstream state, whether stdout is a TTY, and the ledger count. Exits non-zero either when your account or install is broken (`not ready.`) **or** when those are fine but a dispatch from this directory would be refused (`not dispatchable from here.`). It does not print `ready.` unless `cs start` would actually work. |
| `cs new ["<task>"]` | The front door, and the only interactive command. Asks whether the work should run **here** (a local session, no git gate), in the **cloud** (the full git gate, then `cs handoff`), or here-but-phone-drivable (`cs rc`). Refuses to guess: with no TTY it exits rather than picking a side, `--cloud` without a task is refused because a cloud session runs unattended, and `--plan` with `here`/`rc` is refused rather than silently dropped. `--here`/`--cloud` skip the question. |
| `CS_DEFAULT=cloud\|here\|rc\|ask` | Answers `cs new` once in the environment instead of every time. An explicit flag still wins; an unrecognised value refuses rather than falling back to a side. |
| `cs start "<task>"` | Dispatch to a cloud VM and return. Refuses without a TTY, then applies the git gate. Runs `claude --cloud` under `script` so the session ID can be captured and recorded. |
| `cs start --bundle …` | Upload the local repo instead of cloning from GitHub. Relaxes the git gate to warnings and sets `CCR_FORCE_BUNDLE=1`. Tracked edits are bundled; untracked files are not. |
| `cs start --env <id> …` | Pass `--environment` through to the CLI, which documents it for **self-hosted `ccpool_…` environments only** (Team/Enterprise). For an Anthropic-managed environment use `cs env set`; passing an `env_…` id here is `UNVERIFIED` and `cs` warns. |
| `cs handoff "<what to continue>"` | Composes a self-contained brief from the current repo state — branch, last five commits, instructions to finish autonomously, commit to a `claude/` branch, open a PR, and stop rather than guess — then hands it to `cs start`. The git gate runs *before* the brief is composed, because the brief asserts the branch is clean and pushed. |
| `cs handoff --plan <file> …` | Point the session at a plan you already wrote. The file must exist and be committed; an uncommitted plan is refused, because the VM clones origin and would not see it. |
| `cs sessions [--json]` | Live **local** Claude sessions on this machine, and whether each one *could* move to the cloud. Shells out to `claude agents --json`. Also accepts `--idle-after <minutes>` (default 45, or `CS_IDLE_MIN`) — **not shown in `cs help`**. |
| `cs board [--json]` | The master **local** view: heartbeat lanes + local sessions + worktrees + cloud dispatches, merged on session id. Reads `atlas-os/heartbeat/STATE.md`; without it, exits 1 `DEGRADED` and says the view is PARTIAL, not empty. Anchors on the repo `cs` is installed in, so it answers for that repo from any cwd. |
| `cs fleet [--json] [--stale-after N] [--watch N] [--no-remote] [--prune [--dry-run]]` | The **cloud** counterpart of `board`: every agent on a cloud VM, merged from the ledger, the agents' own heartbeat files, and origin's branches, each fact tagged with its source. A BLOCKED agent sorts first, is printed in full, and sets **exit 3**; failed sets 4; a degraded source or an untrusted heartbeat sets 1; `--watch` interrupted is 130. `--watch` refuses to combine with `--json` or `--prune`. Reads only, except `--prune`. See [`../playbooks/10-fleet.md`](../playbooks/10-fleet.md). |
| `cs claim [--dry-run] [--goal "<text>"] [--add <path>]… [--session <id>]` | Declare what **this** session is working on. Derives owns-claims from the session's own transcript (`Write`/`Edit`/`MultiEdit` calls), checks them through the board's `overlap` gate, then `register`/`update`s them — every write goes **through** `heartbeat.py`, never by hand-editing the board. It infers from *writes*, so a read-only session claims nothing, a session whose work happened in a subagent under-claims, and an ambiguous `Bash` redirect target is counted but never claimed; all three are reported out loud, and `--add` is the escape hatch. Requires `atlas-os/bin/heartbeat.py` and the board. |
| `cs exodus [--dry-run] [--manifest <path>]` | Partition the dirty tree by the board's owns-claims into per-lane `claude/<lane>` branches plus a dispatch manifest. Prepares the aggregate move and **dispatches nothing** — `/cloud all` does that. Requires `heartbeat.py` and (to execute) `worktree.py`, and refuses by name without them. |
| `cs env` / `cs env show` | Show the pinned environment (read from project-local, project, then user settings) and print the network-access tiers and the web-only steps to change them. Warns when the pinned ID is a self-hosted `ccpool_*` environment, and that a bridge environment routes work back to this Mac. |
| `cs env set <id>` | Write `remote.defaultEnvironmentId` into `~/.claude/settings.json`, atomically. Warns when a higher-precedence layer (project or project-local) already pins something else, because the user layer is the lowest and the write would otherwise report success while changing nothing. |
| `cs env clear` | Remove that key — both the nested and flat forms — so dispatches use the account default environment. Warns if a higher-precedence layer still pins one. |
| `cs shell-init` | Prints a shell function that routes a bare `claude` in this repo through the chooser. **Prints only — never edits a shell file.** Passes every argued form (`--version`, `doctor`, `auth`, `-p`, `--resume`) straight through, and no-ops inside an existing session. Resolves the real binary with `whence -p`/`type -P`, never `command -v`, which would return the function itself. |
| `cs send <id\|last> "<msg>"` | Steer a running session via `claude -p … --cloud <id> --output-format json`. The one steering path that needs no TTY — it works from a script, a cron job, or a phone-tethered shell with the laptop shut. |
| `cs ls [--json]` | Print the dispatches recorded by this machine. Says explicitly that claude.ai/code remains the authoritative list, since sessions started on the web or phone are not in the ledger unless tracked. |
| `cs track <id> ["title"]` `[--lane L --owns P --from S --brief F --trigger T --expect-branch B]` | Record a session started elsewhere (web, phone, a routine) into the ledger with mode `tracked`, and with provenance: which lane, which paths it owns, the source session, the committed brief, the routine id, and the branch its output should appear on. `--lane` and `--expect-branch` are what make the row useful to `cs fleet`. |
| `cs rm <id>` | Forget a session locally by removing its ledger row. Rewrites the ledger atomically and preserves any unparseable lines rather than dropping them. **Does not stop, archive, or delete the session** — there is no way to stop one. |
| `cs open [id\|last]` | Open that session at claude.ai/code. Defaults to `last`. |
| `cs web` | Open the full session list at claude.ai/code. |
| `cs tp [id\|last]` | Teleport the session back into this terminal (`claude --teleport`). Must run from a checkout of the session's repository with a clean working tree. Aliased as `cs teleport`. |
| `cs rc ["name"]` | Remote Control (`claude --remote-control`): keep the session **local** and drive it from the phone or browser. Aliased as `cs remote-control`. |
| `cs help` | Usage. Also printed for no arguments, `-h`, or `--help`. An unknown command is an error, not a fallthrough. |

There is **no `cs stop`**: Anthropic exposes no call that stops a running cloud session.
Wind one down with `cs send <id> "stop here and summarise what you did"`, then archive it
from the web.

Capability status per command — what has actually been run versus what is only documented —
is carried by each command's own row in this file and by the status tags in
[`../playbooks/`](../playbooks/README.md). `claude remote-control` (server mode) **connects**;
the `--remote-control` flag form that `cs rc` uses is `UNVERIFIED`, and the phone half is
untested. Interactive terminal attach (`claude --cloud <id>`) is **not enabled for this
account** and `cs` deliberately exposes no command for it.

### How `cs` resolves `claude`

In order: `$CS_CLAUDE_BIN` if set and executable, then `claude` on PATH, then
`~/.local/bin/claude`, then the highest-versioned binary inside the VS Code extension
bundle. If none exists it exits with a pointer to
[`../playbooks/01-setup.md`](../playbooks/01-setup.md). `cs doctor` prints which one won and
warns if it is the VS Code extension's private copy, because the version difference changes
which features exist.

### The git gate

Applied by `cs start` and `cs handoff`. The cloud VM clones your **GitHub remote**, not your
disk, so it refuses on: not being inside a git repository, no `origin` remote, an `origin`
that is not GitHub, any uncommitted change, a branch with no upstream, or unpushed commits —
each with the exact command to fix it. `--bundle` downgrades the remote and dirty-tree
conditions to warnings and skips the upstream checks.

---

## `cloud-heartbeat.sh` — the cloud-side emitter

Runs **on the VM**, called by the agent itself. Nothing on the laptop invokes it.

```
cloud-heartbeat.sh start   --lane <name> [--note "..."] [--phase "..."] [--progress d/t]
cloud-heartbeat.sh beat    [--phase "..."] [--note "..."] [--progress d/t] [--unblocked]
cloud-heartbeat.sh blocked --kind <kind> --detail "..." [--needs "..."] [--phase "..."]
cloud-heartbeat.sh done    [--note "..."] [--phase "..."]
cloud-heartbeat.sh failed  --detail "..." [--note "..."]
cloud-heartbeat.sh help
```

Session id comes from `$CLAUDE_CODE_SESSION_ID`, or `--session <id>` on any verb. `<kind>`
is one of `needs-decision`, `needs-credential`, `needs-human-action`, `dependency-missing`,
`rate-limited`, `ambiguous-instruction` — a closed set; an unknown one is refused so a
blocker cannot be mistyped into invisibility. The dispatch block to paste into a routine
payload lives in the script's own header and is quoted in
[`../playbooks/10-fleet.md`](../playbooks/10-fleet.md).

**Side effects, exactly.** It writes `../state/fleet/<session-id>.json` (atomically, via
`os.replace`), drops a `.gitignore` in that directory so a stray `git add -A` cannot sweep
telemetry into the agent's PR, and pushes **one commit containing that one file** to
`refs/heads/cloud-fleet/<session-id>`. The commit is assembled with plumbing —
`hash-object`, a private `GIT_INDEX_FILE`, `write-tree`, `commit-tree` — so **HEAD, the
index and the working tree are never touched**. There is no `add`, `commit`, `checkout` or
`stash` anywhere in it. The only other mutation is a local ref under `refs/cloud-fleet/`,
which `git branch` does not show. Commits are authored as
`cloud-heartbeat@invalid` (RFC 2606 reserved TLD) so machine telemetry can never be mistaken
for a person, and the origin URL is deliberately **not** recorded — a remote can carry a
token in its userinfo.

**Exit codes.** `0` on every path that is not the agent's fault, including a failed push, an
offline remote, or a lost race — the work matters more than the telemetry about the work,
and an `EXIT` trap suppresses even an internal bug to 0 with a loud message. `2` only for a
refusal, which means **nothing was recorded**: bad usage, no session id, not a git repo, no
`origin`, unwritable state directory. A heartbeat that silently goes nowhere is worse than
none, because it reads as "tracked" when it is not.

**One writer per session is the normal case.** A `mkdir` lock guards against a subagent that
inherited the parent's `$CLAUDE_CODE_SESSION_ID`; a rejected push is retried by reparenting
onto the other writer's commit, four attempts, then it warns that two VMs are sharing an id.
A corrupt prior state file is renamed to `.corrupt-<timestamp>` and superseded, never
silently discarded.

## `cloud-claim.sh` — the mutual-exclusion claim

Runs **on the VM**, called by the agent itself, immediately before its first
`cloud-heartbeat.sh start`. Answers a different question than the heartbeat: not "is this
session alive?" but "is this `(org_slug, model_stream)` already being worked, by whom, at
what stage, and is that claim still good?" Design:
[`PRs/model-factory-cloud-environment-v1/04-multi-session-coordination-design.md`](../../PRs/model-factory-cloud-environment-v1/04-multi-session-coordination-design.md).

```
cloud-claim.sh check   --org <slug> [--stream per-account|intelligence]
cloud-claim.sh claim   --org <slug> [--stream ...] --stage <stage> [--run-id <uuid>] [--lane <name>]
cloud-claim.sh renew   --org <slug> [--stream ...] --stage <stage> [--run-id <uuid>]
cloud-claim.sh release --org <slug> [--stream ...] --reason done|failed|handoff
cloud-claim.sh help
```

`--org`/`--stream` are required on **every** verb — unlike `cloud-heartbeat.sh`, where only
`start` needs `--lane` because that ref is already keyed on session id for every later
verb. Here the ref is keyed on the resource, not the session, so which resource is never
implicit. Session id comes from `$CLAUDE_CODE_SESSION_ID`, or `--session <id>` on any verb,
exactly as in `cloud-heartbeat.sh`. `--stream` is a closed vocabulary (`per-account` |
`intelligence`, default `per-account`) — refused on a typo rather than silently opening a
third, uncollided namespace. The dispatch block to paste into a routine payload —
**immediately before** the existing heartbeat block, since it must run first — lives in the
script's own header and is quoted in
[`../playbooks/12-fleet-claims.md`](../playbooks/12-fleet-claims.md).

**Side effects, exactly.** It writes `../state/claims/<org>/<stream>.json` (atomically, via
`os.replace`), drops a `.gitignore` in `../state/claims/` so a stray `git add -A` cannot
sweep a claim snapshot into the agent's PR, and pushes **one commit containing that one
file** to `refs/heads/cloud-claims/<org>/<stream>`. The commit is assembled with the same
plumbing as `cloud-heartbeat.sh` — `hash-object`, a private `GIT_INDEX_FILE`, `write-tree`,
`commit-tree` — so HEAD, the index and the working tree are never touched. `check` writes
nothing at all; it is the read-only probe a newly dispatched agent runs to decide what to
do next.

**Exit codes are not the same shape as `cloud-heartbeat.sh`'s.** A heartbeat's `0` just
means "delivery attempted, and even a failed delivery is fine — the work matters more than
the telemetry." A claim's `0` means something a caller branches on (`FREE`/`MINE` — go
ahead), so a degraded condition must never read as that answer: `check`/`claim` are `0`
`FREE`/`MINE` · `1` `STALE` (reclaimable) · `2` refusal, nothing checked or recorded · `3`
`LIVE` (someone else, alive — do not proceed). `renew`/`release` are `0` on success
*including* a degraded/failed push (the claim itself is unaffected by a renew's content not
landing, because staleness is judged off the **heartbeat** ref, never off this ref's own
age) · `2` refusal, including "you do not hold this claim." An internal bug in this script
is treated as a refusal (exit 2), not swallowed to 0 the way `cloud-heartbeat.sh` swallows
its own — swallowing to 0 here would tell a caller "FREE, proceed" on a crash, which is
exactly the failure this mechanism exists to prevent.

**The push on `claim` is never forced, and that is the one piece of this script that is
genuinely load-bearing for correctness.** A plain push asserts the ref's expected prior
value; if another session's claim landed first, the push is rejected with the same
`[rejected]`/non-fast-forward family `cloud-heartbeat.sh` already parses. Unlike
`cloud-heartbeat.sh`, `claim` does **not** reparent onto the rejection and retry — that
would silently steal someone else's claim. It re-fetches, reads the winner's claim, and
re-derives FREE/MINE/STALE/LIVE against it: live-and-not-mine refuses (exit 3, nothing
pushed); stale-or-free-again retries parented on the winner's commit, bounded at
`PUSH_ATTEMPTS = 4`. `renew`/`release` only ever write a ref they already verified they
hold, so a rejection there **is** treated as a benign double-write of their own history —
reparent-and-retry, exactly `cloud-heartbeat.sh`'s own logic — because that ref should have
exactly one legitimate writer at a time.

**Staleness is judged in exactly one place.** A claim never carries its own liveness clock:
`check`/`claim` dereference the `held_by` session's own `cloud-fleet/<held_by>` heartbeat
ref and look at *its* `updated_at`/`status`, once, rather than trusting anything the claim
file itself asserts about freshness. Threshold is 30 minutes — matching
`coordination/heartbeat/STATE.md`'s local lease, not `cs fleet`'s own 15-minute
`--stale-after` default — because a claim gates potentially-costly real work.

## `fleet_ui.py` — the page

```bash
python3 cloud-sessions/bin/fleet_ui.py [--out PATH] [--open] [--json PATH|-]
                                       [--root DIR] [--stale-after MIN] [--no-cmd]
```

Standard library only, Python 3.8+, macOS and Linux. Prefers `cs fleet --json`; with
`--no-cmd`, or if that command is unavailable, it reads `state/sessions.jsonl` and
`state/fleet/*.json` directly — and **says on the page** which source it used. The emitted
page loads no external resource and has no `fetch`/XHR/WebSocket: it renders correctly from
a `file://` URL on a machine with no network. Writes the page and nothing else. Exit `0` =
written, every source ok · `1` = written but PARTIAL, at least one source degraded or absent
· `2` = could not be written.

## `hooks/`

See [`hooks/README.md`](hooks/README.md). Staged, not wired; a human applies
[`hooks/WIRING.md`](hooks/WIRING.md) because `.claude/settings.json` is the trust boundary.

---

## Dependencies, honestly

Bash 3.2, `python3`, `git`, and the `claude` CLI. **"Nothing outside this folder" was true
until 2026-08-17** and is now wrong:

| Depends on `atlas-os/` | Behaviour without it |
|---|---|
| `cs board` | exits 1 `DEGRADED`, says the view is PARTIAL — **loud** |
| `cs claim` | refuses by name (`heartbeat.py` / board not found) — **loud** |
| `cs exodus` | refuses by name (`heartbeat.py`, and `worktree.py` to execute) — **loud** |
| `hooks/session_register.sh` | logs and exits 0; sessions simply never appear — **silent** |

Everything else — `new/start/handoff/send/ls/track/rm/open/web/tp/rc/env/doctor/sessions/shell-init`,
`cs fleet`, `cloud-heartbeat.sh`, `cloud-claim.sh` and `fleet_ui.py` — needs nothing outside
this folder. Porting: [`../playbooks/09-porting-to-another-repo.md`](../playbooks/09-porting-to-another-repo.md).

`cs start`, `cs handoff`, `cs send` and `cs new --cloud` consume the account's rate limits.
Do not dispatch a session to test the tooling: use `cs doctor`, which touches nothing, and
`bash cloud-sessions/tests/run.sh`, which stubs `claude`, `gh` and `open`. Note that
`run.sh` covers **none** of `cs fleet`, `cloud-heartbeat.sh` or `fleet_ui.py` as of
2026-08-18 (`grep -c fleet cloud-sessions/tests/run.sh` → `0`) — a green `run.sh` says
nothing about them. `cloud-claim.sh` does **not** inherit that blind spot: it has its own
sandboxed suite, [`tests/run-claims.sh`](../tests/run-claims.sh) (a scratch bare remote +
working clone, never the real origin), covering the check/claim/renew/release happy path,
the push-rejection-on-live-claim refusal, and the reclaim-from-stale path — run it after any
change to `cloud-claim.sh`.
