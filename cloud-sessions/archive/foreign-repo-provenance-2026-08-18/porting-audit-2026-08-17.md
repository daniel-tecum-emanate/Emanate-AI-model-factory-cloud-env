# Porting audit — what breaks when `cloud-sessions/` is copied into another repo

**Writer:** lane `lane-porting` · **Date:** 2026-08-17 · **Status:** dated probe report,
not current state. Method: a real transplant, not a grep — see "Method" below.

**Question answered:** Daniel wants to copy this folder into other repos and have it
work. What actually happens when he does?

**One-line verdict:** the *pre-exodus* command set genuinely is self-contained — the
467-check regression suite runs green inside a bare transplant with no `atlas-os`, no
`.claude/`, and a foreign origin. But the folder's headline claim is no longer true as
written: `cs board`, `cs exodus`, and `bin/hooks/session_register.sh` depend on
`atlas-os/**`, which does not travel with the folder; four outside surfaces depend on the
folder, not one; and a naive port ships **three silent breaks**, the worst of which makes
`cs send last` / `cs tp last` in the new repo target a live session of *this* repo.

Folder rule kept honestly: this report is the only file this lane may write. The rules in
[`AGENT.md`](../AGENT.md) say a new probe report also gets a row in
[`README.md`](README.md) and its findings folded into
[`verified-facts.md`](verified-facts.md) — both are owned by other live lanes right now,
so those two edits are **deliberately left to the folder's owner** rather than done here.

---

## Method

A throwaway repo (`other-repo`) was built under `/private/tmp`, `git init -b main`, with
`cloud-sessions/` copied in wholesale (the naive port — nothing else: no `atlas-os/`, no
`.claude/`), one commit, and a fake GitHub origin
`https://github.com/example/other-repo.git`. All commands ran with `$HOME` redirected to
a scratch home, `CS_CLAUDE_BIN` pointed at a stub that answers only `--version`,
`auth status`, and `agents --json` (and refuses anything else with exit 97), and stub
`gh` / `open` on PATH — the same isolation discipline as `tests/run.sh`.

**Never run, per this folder's own rules:** `cs start`, `cs handoff`, `cs new`,
`cs send`, `cs tp`, `cs rc`, `claude --cloud`, bare `claude`, any routine call. Everything
below about *real dispatch* in a ported repo is therefore code-reading plus the existing
`VERIFIED` record, tagged accordingly — the transplant proves the preflight and refusal
behavior, not a live cloud round-trip from a second repo (nobody has ever done one;
that is itself a gap this report names).

A second fixture variant (`nested-repo`, folder at `tools/cloud-sessions/`) probed the
placement assumption. Both fixtures are disposable.

## What each safe command did in the transplant — `VERIFIED 2026-08-17`

| Command | Result in the naive port | Class |
|---|---|---|
| `bash -n` all 8 scripts | clean | a |
| `cs help` | rc 0, full usage | a |
| `cs ls` | rc 0 — lists the **12 inherited rows**, all `internal-company-tool@main` | a (mechanics) / **c** (contents — see silent break 1) |
| `cs open last` | rc 0 — opened `https://claude.ai/code/cse_01PNiwHhwDuByaDuJenYU5Kg`, a real session of the *old* repo | **c** |
| `cs env` / `set` / `clear` | rc 0, correct, writes only `$HOME/.claude/settings.json` | a (from repo root; see break 4 for subdirs) |
| `cs doctor` | rc 1, `not dispatchable from here.` (`no upstream`, `no TTY`) — every line correct; counts the inherited ledger as `ok  ledger  12 recorded dispatch(es)` | a, with one c-flavored line |
| `cs sessions` | rc 0, `no local sessions found.` (stub returned `[]`) | a |
| `cs board` | rc 1 — `board  DEGRADED … No such file or directory`, plus `a DEGRADED source makes this view PARTIAL, not empty` | **b** |
| `cs exodus --dry-run` (clean *and* dirty tree) | rc 1 — `error: atlas-os/bin/heartbeat.py not found — exodus partitions by the board's claims and refuses to run without the machinery that maintains them` | **b** |
| `session_register.sh` (fake session id) | rc 0, silent to the user; one line appended to `state/hook-errors.log`: `no board at …/atlas-os/heartbeat/STATE.md; skipped session=…` | **b**, borderline — loud only if someone reads the log; [`WIRING.md`](../bin/hooks/WIRING.md) §"How to verify" is the step that catches it |
| `bash cloud-sessions/tests/run.sh` (inside the transplant) | **`467 passed  0 failed  0 xfail  0 skipped` — green**, tamper guards green | a — but see break 7 for what green does *not* mean |
| `tests/doc-drift.sh` | rc 0, all 8 guards green | a |

## The three worst silent breaks

### 1. The tracked ledger travels, and `last` then targets a foreign live session — `VERIFIED`

`state/sessions.jsonl` is tracked on purpose (`git ls-files` confirms; decision recorded
in [`../state/README.md`](../state/README.md)). Correct for *this* repo — it is the only
durable record of a dispatched cloud agent. But it means **every clone or copy of the
folder ships this repo's 12 dispatch rows**, and `cs` resolves `last` as "most recent
ledger row" with no repo check. Measured in the transplant:

```
$ cs open last          # inside other-repo, rc 0
fake-open received: https://claude.ai/code/cse_01PNiwHhwDuByaDuJenYU5Kg
```

That is `lane-gapclose`, a real session of `internal-company-tool`. The same resolution
feeds `cs send last "…"` — which would **headlessly steer a live session of the old
repo from inside the new one** — and `cs tp last`, which would try to teleport it into
the new repo's checkout: `cmd_tp` ([`bin/cs:1723-1729`](../bin/cs)) gates only on
"inside a work tree" and "clean", never on the row's `repo` field matching the current
repo. No command refuses, warns, or exits non-zero anywhere on this path. This is the
exact shape ground rule 4 forbids: inherited provenance read as a meaningful value.

**Porting consequence:** truncate `state/sessions.jsonl` as step one of any port, and
verify with `cs ls` → `no dispatches recorded`. A worthwhile hardening for the `bin/`
owner (proposal, not a change): `send`/`tp`/`open` could warn when the resolved row's
`repo` ≠ current repo basename.

### 2. The documented install (`ln -sf … ~/.local/bin/cs`) mis-roots the tool — `VERIFIED`, on the real machine

All three install docs give the same command
([`README.md:25`](../README.md), [`bin/README.md:11`](../bin/README.md),
[`playbooks/01-setup.md:73`](../playbooks/01-setup.md)):

```bash
mkdir -p ~/.local/bin && ln -sf "$PWD/cloud-sessions/bin/cs" ~/.local/bin/cs
```

`cs` resolves itself from `BASH_SOURCE` without resolving symlinks
([`bin/cs:14-21`](../bin/cs)): invoked through the symlink, `HERE=~/.local/bin`,
`ROOT=~/.local`, so the ledger becomes `~/.local/state/sessions.jsonl` and `cs board`'s
repo becomes `$HOME`. Measured on the machine that wrote the docs, today:

```
$ ~/.local/bin/cs ls
no dispatches recorded. Authoritative list: https://claude.ai/code
$ echo $?          # 0
$ wc -l < cloud-sessions/state/sessions.jsonl
      12
```

Empty-but-fine over a 12-row ledger, exit 0 — a textbook ground-rule-4 violation, live
right now, in the shipping repo. Reproduced against the transplant too: the symlinked
`cs ls` created a stray `state/` directory next to the symlink's parent. It has gone
unnoticed here only because in-repo sessions invoke `cloud-sessions/bin/cs` by path.

**Porting consequence:** a porting guide that copies the install step verbatim gives
every new repo a `cs` whose ledger silently lives in `~/.local/state` — and with N
repos, one global `cs` name can point at only one of them anyway. Until the `bin/` owner
fixes resolution (a `readlink`/`pwd -P` loop over `BASH_SOURCE`), the honest install for
a port is a two-line wrapper script (`exec /abs/path/<repo>/cloud-sessions/bin/cs "$@"`)
or invoking by path. Do not ship the symlink step in the guide as-is.

### 3. `/cloud` and `/cloud-workflow` hardcode this repo's clone URL inside the dispatch payload — silent wrong-repo dispatch

The routine-create payloads that both commands instruct an agent to send verbatim carry:

- [`.claude/commands/cloud.md:370`](../../.claude/commands/cloud.md) —
  `"sources": [{"git_repository": {"url": "https://github.com/daniel0tgc/internal-company-tool"}}]`
- [`.claude/commands/cloud-workflow.md:130`](../../.claude/commands/cloud-workflow.md) — same URL

Port the folder plus these commands into `other-repo`, run `/cloud` there, and the
routine is created against **`internal-company-tool`**: create returns HTTP 200, run
returns a `cse_` id, the run reports green — and the session executes the new repo's
brief against the old repo's code, or fails hunting for a brief that was never committed
there. Nothing on the path refuses. (`UNVERIFIED` by live dispatch — this audit dispatches
nothing — but it follows mechanically from the payload text, and the 2026-08-12 record
shows exactly this class of failure presenting as plausible success.) Same file, same
class: the hardcoded transcript path `~/.claude/projects/-Users-danieltecum-internal-company-tool/…`
([`cloud.md:231`](../../.claude/commands/cloud.md)) makes step 1's transcript-reading
silently find nothing in any other repo.

The environment ids are the account-level cousin: `env_01Xmz1Nv8o7vdKZagtFCKaq7`
(`cloud.md:335,367`, `cloud-workflow.md:127`, and **baked into `cs` itself** at
[`bin/cs:1317`](../bin/cs) as exodus's `DEFAULT_ENV_ID`) remain valid for any repo on
*this* account — and are silently wrong for any other account. The bridge-env warning
names `MacBook-Pro:internal-company-tool:7fcb` (`cloud.md:337`), which is per-machine,
per-repo.

## The rest of the silent-break list

4. **`cs env` reads the project layer from `cwd`, not the repo root** —
   `settings_env_id()` ([`bin/cs:495-511`](../bin/cs)) joins `.claude/settings.json`
   onto `os.getcwd()`. `VERIFIED` in the transplant: with a project pin at the root,
   `cs env show` from the root says `pinned  env_ProjectPin (from project settings)`;
   from a subdirectory it says `pinned  none` — an `ok`-tagged wrong answer, and
   `cs env set` from a subdirectory would miss the `NOT IN EFFECT` warning for the same
   reason. Not porting-specific, but a porting checklist that says "run `cs env set`"
   must say "from the repo root".

5. **Placement is load-bearing and nothing says so.** The folder must land at
   `<repo-root>/cloud-sessions` exactly. In the nested fixture (`tools/cloud-sessions/`):
   `cs board` answers for the wrong root (`cs board — tools`, board sought at
   `tools/atlas-os/…` — [`bin/cs:1005,1012`](../bin/cs) resolve the repo as `$ROOT/..`,
   not via git), and `session_register.sh` hardcodes `<root>/cloud-sessions` for its log
   path ([`session_register.sh:129,151`](../bin/hooks/session_register.sh)) — `VERIFIED`:
   the nested hook exited 0 and **created a stray `cloud-sessions/state/hook-errors.log`
   at the repo root** of a repo that had deliberately put the folder elsewhere.
   (`cs exodus` alone survives nesting — it resolves through `git rev-parse
   --show-toplevel`… and then looks for `atlas-os/` at that toplevel.)

6. **`state/README.md` contradicts itself about the one file that must be scrubbed.**
   Line 6: "**`sessions.jsonl` is tracked on purpose**." Line 18: "coverage: the ledger
   is ignored… the only untracked file present is `sessions.jsonl` itself." Git says
   *tracked* (`git ls-files cloud-sessions/state/` lists it; `git check-ignore` exits 1).
   A porter reading the stale paragraph concludes a clone won't carry the rows — it will.
   Doc fix owed to the folder owner.

7. **A green suite in the port measures only the portable subset.** `tests/run.sh` has
   **zero** checks touching `cs sessions`, `cs board`, or `cs exodus` (grep count 0 —
   its section 3 tests "every command in `bin/README.md`", and
   [`bin/README.md`](../bin/README.md)'s command table omits all three). So the
   transplant's `467 passed / 0 failed` is real *and* says nothing about the three
   commands that couple to `atlas-os`. Ground rule 7 applies to the suite itself: green
   is not "ported". Related staleness in the same file: [`bin/README.md:80`](../bin/README.md)
   still claims "Dependencies and side effects: Bash, `python3`, and the `claude` CLI.
   **Nothing outside this folder.**" — false since exodus/board landed.

## Does AGENT.md's self-containment claim survive?

**No — not as written.** [`AGENT.md:6-10`](../AGENT.md) claims: "It is self-contained:
`bin/cs` needs bash, python3 and the `claude` CLI, and nothing here depends on another
repository. One thing *outside* the folder depends on it."

- **Inward, false in practice.** `cs board` reads `atlas-os/heartbeat/STATE.md`
  ([`bin/cs:1012`](../bin/cs)); `cs exodus` refuses to run at all without
  `atlas-os/bin/heartbeat.py`, the board, and (execute mode) `atlas-os/bin/worktree.py`
  ([`bin/cs:1289-1296`](../bin/cs)); `session_register.sh` needs both board and
  heartbeat ([`session_register.sh:152-153`](../bin/hooks/session_register.sh)); and
  `cs board`'s own remediation output tells the user to run
  `python3 atlas-os/bin/heartbeat.py register …` ([`bin/cs:1228,1243`](../bin/cs)) — a
  command that does not exist in a ported repo. "Another repository" is technically the
  same repo, so a lawyerly reading survives; the operative promise — *copy the folder
  and it works* — holds only for the pre-exodus command set. To the claim's credit, the
  degradation is **loud** (hard refusals, `DEGRADED`, exit 1), which is why these land
  in class (b) and not (c).
- **Outward, undercounted.** Not one dependent surface but at least four, plus two
  conventions: [`.claude/commands/cloud.md`](../../.claude/commands/cloud.md) (named),
  [`.claude/commands/cloud-workflow.md`](../../.claude/commands/cloud-workflow.md),
  [`.claude/commands/routines-audit.md`](../../.claude/commands/routines-audit.md)
  (links `billing.md` twice), `lab/bin/dispatch.py` (names `cs start` as a dispatch
  mechanism), plus the `docs/workflows/TEMPLATE.md` format and the `docs/handoff-*.md`
  brief convention that `cs exodus` bakes into its manifests
  ([`bin/cs:1638`](../bin/cs)).

The sentence should be superseded in place by the folder owner, per ground rule 5 — this
report is the correcting evidence, not the correction.

## Coupling inventory

### atlas-os dependencies (do not travel with the folder)

| Where | What | Severity for a port |
|---|---|---|
| `bin/cs:1012` (`cs board` source a) | reads `atlas-os/heartbeat/STATE.md` | board permanently DEGRADED, exit 1 — loud |
| `bin/cs:1289-1296` (`cs exodus`) | requires `heartbeat.py`, the board, `worktree.py` | exodus impossible — loud refusal |
| `bin/cs:1228,1243,1515,1634` | output tells users to run `atlas-os/bin/*.py` | dead advice in a port |
| `bin/cs:683-698` (handoff brief) | standing instruction "Do NOT promote or commit `atlas-os/telemetry/`" | harmless voodoo in a repo without it; needs substitution (decision 2) |
| `bin/hooks/session_register.sh:152-153` | board + heartbeat at `<main-root>/atlas-os/` | hook is a per-session no-op; logs once per session to `state/hook-errors.log` |
| `/cloud` steps 3b, `/cloud all` | `heartbeat.py overlap/register/update`, the board partition | the whole `/cloud all` mode and claim-gating steps are inoperable |
| `/cloud-workflow` step 2 | same | lane gating inoperable |
| docs' telemetry-ledger claims (`verified-facts.md` §telemetry, `this-repo-in-the-cloud.md`) | describe `atlas-os/telemetry/` behavior | history about this repo; must not be read as claims about the target |

### Repo/account-specific literals

| Literal | Where | Kind |
|---|---|---|
| `https://github.com/daniel0tgc/internal-company-tool` | `cloud.md:370`, `cloud-workflow.md:130`, `playbooks/01-setup.md:49`, `playbooks/07:17,64,65,248` (bookmark URLs) | **must substitute** — break 3 |
| `-Users-danieltecum-internal-company-tool` transcript path | `cloud.md:231` | must substitute |
| `env_01Xmz1Nv8o7vdKZagtFCKaq7` (Default env) | `cloud.md:335,367`, `cloud-workflow.md:127`, **`bin/cs:1317`** | account-level: fine for any repo on this account, silently wrong elsewhere |
| `env_01Muitz2g8DLxNUs1K4njJRp` (bridge, `MacBook-Pro:internal-company-tool:7fcb`) | `cloud.md:337` | per-machine+repo; the *warning* generalizes, the id does not |
| `claude-sonnet-5`, `docs/handoff-<lane>.md` | `bin/cs:1656,1638` (exodus manifest) | convention — decision 3 |
| 12 ledger rows naming this repo's sessions | `state/sessions.jsonl` (tracked) | **must truncate** — break 1 |
| `daniel0tgc`, `dtecum001@gmail.com`, this repo's doctor/RC transcripts | `playbooks/01,02,04`, `verified-facts.md`, probe reports | history; fine to carry if clearly labeled as this repo's record |
| `tecum-knowledge-framework` recurring routines | `routines-audit.md:51,66` | account-level and still true on this account; example text elsewhere |

### Outside-the-folder pieces a port must ALSO copy (the folder alone is not the product)

1. `.claude/commands/cloud.md`, `cloud-workflow.md`, `routines-audit.md` — the actual
   dispatch brains (`cs` itself never calls the routines API).
2. `docs/workflows/TEMPLATE.md` — `/cloud-workflow` refuses without the format.
3. The `settings.json` hooks snippet staged in
   [`bin/hooks/WIRING.md`](../bin/hooks/WIRING.md) — per-repo, human-applied, and only
   meaningful if the heartbeat decision (below) is yes.
4. An install step for `cs` on PATH — but **not** the documented symlink (break 2).
5. If board/exodus/auto-registration are wanted: `atlas-os/bin/heartbeat.py`,
   `atlas-os/bin/worktree.py`, `atlas-os/heartbeat/STATE.md` — none of which are in this
   folder, and which bring their own AGENT.md/invariants baggage.

### Account-level vs repo-level (what carries for free)

| Carries automatically (account) | Per-repo (must redo) | Per-machine |
|---|---|---|
| claude.ai Max auth | first-run workspace **trust prompt** (stalls a silent dispatch) | `~/.local/bin` install |
| GitHub↔Claude connection (`/web-setup`, done once) — **but** whether the GitHub app can see the *new* repo depends on its install scope; `NOT CHECKABLE` from the CLI (`cs doctor` says so); the tell is routine-create `403 You don't have access to a repository this routine uses` | `origin` remote, upstream, pushed state | local `gh` auth |
| cloud environments incl. the Default env id | committed `.claude/settings.json` deny rules/hooks (a cloud session gets only what the *new* repo commits) | `~/.claude/settings.json` env pin |
| routines API + existing routines + connector inheritance (and its risks) | heartbeat board, worktree layout, brief conventions | `claude` binary resolution |

## Classification and counts

- **(a) works as-is anywhere — 9:** `cs help`; ledger mechanics (`ls`/`track`/`rm`);
  `cs doctor`; `cs env` (from repo root); `cs sessions`; `cs open`/`web`; the whole
  git-gate/handoff-composition/chooser/shell-init refusal surface (suite-proven);
  `tests/run.sh` + fixtures (467 green in the transplant); `tests/doc-drift.sh`.
- **(b) degrades loudly and correctly without atlas-os — 3:** `cs exodus` (hard refusal
  naming the missing machinery); `cs board` (`DEGRADED … PARTIAL, not empty`, exit 1);
  `session_register.sh` (exit-0 by explicit contract, one log line per skip — loud only
  through WIRING.md's verify step, which the porting checklist must make mandatory).
- **(c) breaks silently — must be fixed before porting is honest — 7:** inherited
  ledger → foreign `last` (breaks 1); symlink install mis-roots `cs` (break 2 — live on
  this machine today); hardcoded clone URL/transcript path in `/cloud` +
  `/cloud-workflow` (break 3); hardcoded account env ids incl. `bin/cs:1317`
  (cross-account); `cs env` cwd-not-root project-layer read (break 4); nested-placement
  strays (break 5); doc rot that flatters the port — `state/README.md` self-contradiction,
  `bin/README.md:80` "Nothing outside this folder", and the suite's zero coverage of
  board/exodus/sessions (breaks 6-7).
- **(d) repo-specific by design, needs a documented substitution — 7:** exodus manifest
  defaults (env id, model, `docs/handoff-<lane>.md`); the handoff brief's
  `atlas-os/telemetry` clause; the heartbeat dependency itself (port the machinery or
  drop the three commands); the three command files' repo examples; playbooks'
  transcripts and bookmark URLs; `reference/` probe reports (dated evidence about this
  repo/account, to carry as clearly-labeled history); the WIRING.md settings snippet.

## Porting kit — sketch only (build it elsewhere)

**Minimal copy list.** `cloud-sessions/` wholesale to `<new-root>/cloud-sessions`
(placement exact — break 5), then immediately: truncate `state/sessions.jsonl`;
optionally drop `archive/` and the probe reports or add a one-line "ported from
internal-company-tool; the dated evidence below is about that repo" header to
`reference/README.md`. Plus, from outside the folder: the three `.claude/commands`
files (with substitutions), `docs/workflows/TEMPLATE.md`, and — only if decision 1 is
yes — the three `atlas-os` heartbeat files and the WIRING.md snippet.

**Per-repo checklist, in order:**

1. Make the three decisions below, first — they change what gets copied.
2. Copy the folder to `<root>/cloud-sessions`; truncate `state/sessions.jsonl`; verify
   `cs ls` → `no dispatches recorded`.
3. Copy the command files; substitute every literal:
   `grep -rn "daniel0tgc\|internal-company-tool\|env_01Xmz\|env_01Mui\|-Users-danieltecum" cloud-sessions .claude/commands`
   must return only clearly-labeled history before the port is done.
4. `bash cloud-sessions/tests/run.sh` — must be green, knowing green covers only the
   portable subset (finding 7).
5. Install `cs` by wrapper or by path — not the symlink command in the docs (break 2).
6. `cs doctor` from the repo root in a real terminal; drive it to `ready.` (origin on
   GitHub, upstream, pushed). Open the repo once interactively to clear the trust prompt.
7. Web side: confirm the Claude GitHub app covers the new repo (routine-create 403 is
   the only tell); pick or create a cloud environment, `cs env set <id>` **from the repo
   root**; never a bridge env.
8. If heartbeat=yes: port the machinery, apply the WIRING.md snippet by hand, and run
   WIRING.md §"How to verify" — the hook-errors.log check is the only thing that catches
   a dead hook.
9. First dispatch: one read-only probe (playbook 02 shape), `cs track` it with full
   provenance, and read the transcript — a green run is a started session, not a
   succeeded task. This closes the gap this audit could not: no port has ever done a
   live round-trip.

**The three decisions a new repo forces:**

1. **Does it get a heartbeat?** No → `cs board`, `cs exodus`, auto-registration, and
   `/cloud all` are dead (loudly); single-session `/cloud` steps 1-7 still work; delete
   the WIRING.md snippet from the port so a wired-but-boardless hook doesn't log a skip
   per session forever. Yes → three `atlas-os` files come along and are now a second
   surface to keep in sync.
2. **What replaces the deny-list / guarded paths?** The handoff brief's telemetry
   clause (`bin/cs:683`), `/cloud-workflow`'s "never write `atlas-os/bin/**`", and the
   cannot-move list's repo-specific rows (Twenty, Supabase MCP, meeting-recorder) all
   name *this* repo's protected things. The new repo must either name its own
   equivalents or delete the clauses — shipping instructions about directories that
   don't exist teaches sessions to ignore instructions.
3. **Where do briefs live?** `docs/handoff-<lane>.md` and `docs/workflows/*.md` are
   baked into `cs exodus` manifests (`bin/cs:1638`), `/cloud` step 4, and
   `/cloud-workflow` step 1. Adopt `docs/` in the new repo, or change all three surfaces
   together — a partial change strands briefs where the routine prompt won't look.

## Fix-before-porting list (for the owning lanes — proposals, not changes made here)

1. `bin/cs`: resolve `BASH_SOURCE` through symlinks (break 2) — this one is a live
   defect in *this* repo, not just a porting hazard.
2. `bin/cs`: `send`/`tp`/`open` warn when the resolved row's `repo` ≠ current repo
   (break 1).
3. `bin/cs`: `settings_env_id()` should anchor on the git toplevel, not `cwd` (break 4).
4. `.claude/commands/cloud.md` + `cloud-workflow.md`: derive the `git_repository` URL
   from `git remote get-url origin` instead of hardcoding it (break 3) — correct even in
   this repo.
5. Docs: supersede `AGENT.md`'s self-contained sentence and "one surface outside";
   fix `bin/README.md:80` and its command table (add `sessions`/`board`/`exodus`);
   fix the `state/README.md` coverage paragraph (finding 6).
6. Tests: any coverage at all for `board`/`exodus`/`sessions`, so a green suite stops
   being silent about exactly the commands that don't port.
