# AGENT.md — operating rules for this folder

> **Provenance note, updated 2026-08-24:** the reference-record files this document used to
> cite (`verified-facts.md`, `billing.md`, `this-repo-in-the-cloud.md`,
> `porting-audit-2026-08-17.md`) were verified against a **different repository**
> (`daniel0tgc/internal-company-tool`), not this one. They were archived in 2026-08-18 and
> have now been removed from this repo entirely, which is scoped to the fine-tuning harness.
> Where their findings still matter they are stated inline below rather than cited; the
> files themselves remain in git history. The operating rules below (ground rules, the safe
> four, the git gate, the deny-listed paths) are mechanism-level and stand.

Read this before doing anything in `cloud-sessions/`. It is the local authority; the
human-facing guide is [`README.md`](README.md).

**What this folder is:** the knowledge and the tooling for running Claude Code sessions on
cloud infrastructure so work survives the laptop being closed or shut down.

**Self-containment — superseded 2026-08-17.** This file used to say "nothing here depends
on another repository". That was true until `cs board`, `cs exodus` and the
auto-registration hook were built: all three depend on `atlas-os/bin/heartbeat.py` (and
exodus on `atlas-os/bin/worktree.py`). The dependency is deliberate — the heartbeat board
IS the master registry — and it **degrades loudly**: exodus refuses naming the missing
file, board exits 1 DEGRADED (verified in a bare transplant, 2026-08-17). `cs claim` (2026-08-18) joins them: it writes claims THROUGH `heartbeat.py` and refuses
by name without it. The core
(`new/start/handoff/send/ls/track/rm/env/doctor/sessions/shell-init`), plus the whole fleet
half — `cs fleet`, `bin/cloud-heartbeat.sh`, `bin/cloud-claim.sh`, `bin/fleet_ui.py` —
remains portable with bash + python3 + git + the `claude` CLI. `bin/cloud-claim.sh`
(2026-08-20) is the claim-on-`(org_slug, model_stream)` sibling to `cloud-heartbeat.sh` —
same "no" as the rest of the fleet half: it depends on nothing under `atlas-os/`. (The
"Depends on `atlas-os/`" table itself lives in
[`bin/README.md`](bin/README.md#dependencies-honestly), not here — this paragraph is this
file's own record of the same fact.) Porting: see
[`playbooks/09-porting-to-another-repo.md`](playbooks/09-porting-to-another-repo.md), which
carries the conclusions of the 2026-08-17 porting audit.
Several surfaces *outside* the folder also depend on it — see the note under the layout
table.

---

## Layout — do not add files to the root

Exactly **two files** live at the root: this one and `README.md`. Everything else goes in
a subfolder. If you need a new document, it belongs in `playbooks/` (how to do a thing) or
`reference/` (what is true about a thing).

| Path | Contains | Rule |
|---|---|---|
| `bin/` | `cs` (the operator entrypoint), `cloud-heartbeat.sh` (the cloud-side emitter), `fleet_ui.py` (the page), `hooks/` | keep them dependency-free: bash + python3 + git + the `claude` CLI. Per-file cost and side effects: [`bin/README.md`](bin/README.md) |
| `playbooks/` | task-shaped instructions, numbered in reading order | one workflow per file |
| `reference/` | claims, limits, matrices, error tables, competitor architecture | every claim carries a status tag and states its evidence inline — the date it was measured and how |
| `tests/` | `run.sh`, the regression suite for `bin/cs`, and its `RESULTS.md` log | $0 and offline by construction — it stubs `claude`, `gh` and `open`, and never dispatches. Run it after any change to `cs` |
| `state/` | `sessions.jsonl` (the dispatch ledger, **tracked on purpose** since 2026-08-14), `exodus-manifest.json`, and `fleet/` — the cloud agents' own heartbeat files | runtime data, append-only, **never hand-edited**. `cs rm` removes a ledger row; `cs fleet --prune` is the only thing that deletes a heartbeat. See [`state/README.md`](state/README.md) |

One surface depends on this folder but **does not exist in this repo**: the `/cloud` slash
command (`.claude/commands/cloud.md`), which walks a session through preparing a handoff. It
lives in `emanate-tecum-workflow` and was not part of the fine-tuning-harness import, so
there is nothing here to open. It names `cs doctor`, `cs new --cloud --plan` and this
folder's playbooks, so a change to `cs`'s interface can leave it stale — fix it over there,
not here.

## Ground rules

These exist because this workstream has repeatedly produced confident documentation that
turned out to be wrong — including corrections to claims made hours earlier, within the
same session that made them.

1. **Tag every claim.** `VERIFIED` (run here, output quoted) · `DOCS` (Anthropic's
   documentation, not exercised here) · `UNVERIFIED` · `DISPROVEN`. "It should work" is not
   a status. Record a new claim next to the thing it is about — in the playbook or
   `reference/` file that relies on it — with the command and its output, and the date. There
   is no separate evidence register in this repo; the claim and its proof live together.

2. **Verify a claim the way the claim says to verify it.** In July, `claude --cloud` was
   declared non-existent because it was absent from `--help` — when the research being
   checked had already said the flags were hidden from `--help`. An hour was lost on the
   most valuable capability in the folder.

3. **Run a negative control.** `claude --cloud --version` exits 0 — and so does
   `claude --definitelynotaflag --version`, because `--version` short-circuits option
   validation. A probe that a bogus control also passes is not a probe.

4. **Never let a degraded condition read as a meaningful value.** A rate limit is not an
   empty list. A missing ownership record is not permission. A missing TTY is not "run it
   locally instead". A blocked host is not a hang. When provenance is unknown, refuse. The
   tool written to enforce this rule
   then broke it ten ways of its own, and on 2026-08-14 a probe found the *test suite*
   asserting twelve refusals against an exit code that was always 0 — a green run measuring
   nothing.

5. **Supersede, never delete.** A wrong claim gets marked and corrected in place, with what
   corrected it. The record of how a wrong belief formed is the useful part. This is why the
   telemetry fix that shipped and was reverted an hour later is written up with **both**
   halves rather than tidied into whichever one is current. (Scope note, 2026-08-24: whole
   *documents* belonging to other initiatives have been removed from this repo, which is
   about narrowing scope, not about erasing corrections. Git history keeps them. This rule
   still governs every claim in every file that remains.)

6. **Distinguish "not permitted" from "not possible".** GitHub Actions works fine
   technically and is forbidden by policy. Those need different handling and different
   wording.

7. **A skipped check is not a passed check**, and a green run is not a successful task.
   Routines report green when the session merely started and exited cleanly.

## Constraints that will bite you

- **`claude --cloud` requires a TTY.** You cannot dispatch a cloud session from the Bash
  tool, a hook, or a cron job — the CLI refuses, and warns that a non-interactive
  invocation "would silently ignore `--cloud`" and run the work locally. That includes
  *from inside another cloud session*: probe 6 confirmed `cs new` there refuses with
  "needs a terminal to ask in", and the `/cloud` command says so itself. For scheduled
  cloud work use a [routine](playbooks/05-routines.md) — that is how all seven probes were
  dispatched. For steering an existing session, `cs send` works headlessly.
- **A first interactive run in an untrusted directory blocks on a trust prompt.** It will
  stall silently if nothing is typing.
- **The cloud VM clones the GitHub remote, not the disk.** Uncommitted or unpushed work is
  invisible, and the resulting error reads like a git problem. `cs start` gates on this.
- **`cs start`, `cs handoff` and `cs new --cloud` dispatch real work and consume the
  account's rate limits.** Do not dispatch a session to test the tooling — use `cs doctor`,
  which touches nothing, and `tests/run.sh`, which stubs the CLI. `cs help`, `cs ls`,
  `cs env` and `cs doctor` are the safe four.
- **All LLM work goes through Claude Code — never another API.** Standing instruction from
  Daniel, 2026-08-14. Do not add a dependency that calls OpenAI, Gemini, Deepgram, Hugging
  Face or any hosted model API, and do not wire up `ANTHROPIC_API_KEY`: an API key bills per
  token *and* disables `--cloud`, `--teleport`, Remote Control and routines, which all
  require subscription auth. It costs money and removes capability at the same time.
- **Firing a routine does not disarm it.** `run` does not consume `run_once_at`, and a
  disabled routine still displays a future `next_run_at` — `enabled` is the deciding field.
  Create → run → **disable**, as one sequence. Nine probe routines were left armed on
  2026-08-14 by the same person who wrote the warning.
- **Never provision paid infrastructure.** Recurring spend is Daniel's decision. Cost the
  options, write them down, stop.
- **Cloud environments have no secrets store.** Never write an API key into an environment
  variable or setup script; anyone using that environment can read it. Codex removes
  secrets before the agent phase and Cursor scopes build secrets to the build step; we
  have neither, so keep credentialed work out of cloud sessions entirely.
- **`.claude/settings.json`, `.mcp.json` and `atlas-os/` are tracked as of 2026-08-13**, so
  a cloud session now *does* get this repo's hooks, permission deny-rules, MCP config and
  control plane. This reverses what this file said until 2026-08-14 ("a cloud session gets
  none of…"), and the reversal cuts both ways: do not tell a cloud session a guardrail is
  absent when it is enforced, and do not assume enforcement is complete — probe 7 wrote
  into `atlas-os/bin/**` from a cloud VM via `python3 -c open()`, because the guard matches
  tool names and Bash idioms, not filesystem writes (`V-023`).
- **A cloud session's own hook telemetry does not survive the VM, and that is deliberate.**
  Hooks fire; `atlas-os/telemetry/raw/` is gitignored and dies with the machine. Do not
  "fix" this by having a session commit its own trace — that was shipped on 2026-08-14, and
  reverted the same hour, because it turns an involuntary record into a cooperative one and
  truncates it at the last commit. The channel that *is* meant to survive the VM is
  [`bin/cloud-heartbeat.sh`](bin/cloud-heartbeat.sh)'s pushed git ref — use that, not a
  committed trace.
- **Routines silently inherit every connected claude.ai connector**, including Gmail and
  Drive, and run with no approval prompts. **An explicit `mcp_connections: []` does not
  stop this** — verified 2026-08-17, a routine created with an empty list came back holding
  Gmail, Drive and Calendar (`V-041`). An empty list reads as *unspecified*, not *none*.
  The only control proven to work is to `get` the routine after creating it and read
  `mcp_connections` back; disable rather than fire if it holds connectors the work has no
  business with.

## When you change something here

- New capability or workflow → a numbered file in `playbooks/`, plus a row in
  [`reference/surfaces.md`](reference/surfaces.md).
- New claim → stated inline in the playbook or `reference/` file that depends on it, with
  the status tag and the actual command output.
- New failure mode → [`reference/troubleshooting.md`](reference/troubleshooting.md), as
  *error text → what it actually means → fix*.
- Changed `bin/cs` → run `bash cloud-sessions/tests/run.sh` (it costs nothing and
  dispatches nothing), then `cs doctor` on the real machine, because the suite stubs
  `claude`, `gh` and `open` and cannot tell you what the real ones answer. Update
  [`bin/README.md`](bin/README.md) and the command block in [`README.md`](README.md) if the
  interface moved. (The `/cloud` slash command also names that interface, but it lives in
  `emanate-tecum-workflow` and is not in this repo — see the layout section above.)
- New probe report → a row in [`reference/README.md`](reference/README.md), and fold its
  findings into the `reference/` or playbook file the finding is actually about. A report is
  not a status; leaving it as the only home for a finding means the next reader has to diff
  seven files to learn the current answer.
- Anything superseded → correct it in place in the `reference/` or playbook file that
  carries it, and say what changed and when. There is no `archive/` in this repo — the
  2026-08-24 harness trim removed it (it held foreign-repo provenance and the superseded
  keep-the-laptop-awake research record). Superseded material goes to git history, not to a
  folder.

## Current state, one line

Cloud dispatch, headless steering, teleport, and routines are **verified working**
(2026-08-12); a cloud session can push a branch and, since 2026-08-14, **open a PR**. Seven
probes have now measured this from inside real VMs: `cs` is green on Linux and macOS after
four BSD/GNU bugs, this repo's hooks and deny rules **do** reach a cloud session, and that
session's own telemetry still dies with the VM by design. Remote Control **connects**, with
the phone half untested; interactive terminal attach is **gated off** for this account.
GitHub is connected to the Claude account — before that, dispatch was silently bundling
instead of cloning, which is still the correction worth reading first. Dates and the
per-mechanism detail are carried by the playbook that uses each mechanism.
