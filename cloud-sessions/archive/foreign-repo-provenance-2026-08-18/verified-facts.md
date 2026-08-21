# Verified facts

Every load-bearing claim in this folder, with **how** it was checked. The folder rule is
that a claim with no verification method is a guess, and gets tagged as one.

Status values: `VERIFIED` (run here, output quoted) · `DOCS` (stated by Anthropic's
documentation, not exercised here) · `UNVERIFIED` (believed, untested) · `DISPROVEN`.

## Passes

There is no single "last full pass" any more — the record was built in four rounds on two
different machines, and saying otherwise would date the whole file to whichever round ran
last. Each section below carries its own date; this is the index of them.

| Pass | Where | What it established |
|---|---|---|
| **2026-08-14 — reconciliation** | this Mac · `claude` 2.1.231 · macOS 26.5.1 arm64 · bash 3.2.57 | reconciled every claim in this folder against `bin/cs` at commit `f44fbf9` and against the six probe reports. Corrected the telemetry conclusion below, which recorded a fix that was **reverted an hour after it shipped** |
| 2026-08-14 — probes 1–6 | an Anthropic cloud VM · Ubuntu 24.04.4 x86_64 · `claude` 2.1.231 · **root** | the first measurements taken *inside* a cloud session: four BSD/GNU portability bugs, three disproven claims, and the cloud-telemetry finding |
| 2026-08-13 | this Mac | the adversarial review of `bin/cs`, and the regression suite that followed it |
| 2026-08-12 | this Mac · `claude` 2.1.229 | the original end-to-end chain: dispatch, headless steer, teleport, routines, and the GitHub connection that turned out to be missing |

Account throughout: `dtecum001@gmail.com` (Max, claude.ai auth).

`bin/cs` and `tests/` are under active change. Where a number here can drift — the
regression suite's check count is the only one — this file pins each measurement to the
commit it was taken at and points at [`../tests/RESULTS.md`](../tests/RESULTS.md) for the
living count, rather than freezing a number that will be wrong next week.

---

## The chain that matters

These five are the whole product. All were run end-to-end on 2026-08-12 against
`daniel0tgc/internal-company-tool`, producing cloud session
`session_01B3GFCqymJeAJMyyn1QH1PT`.

### 1. Dispatch works — `VERIFIED`

`claude --cloud "<task>"` creates a cloud session and **exits immediately**. It does not
block, stream, or hold the terminal.

```
✔ Created cloud session: Run system verification probe
View: https://claude.ai/code/session_01B3GFCqymJeAJMyyn1QH1PT?from=cli&m=0
Resume with: claude --teleport session_01B3GFCqymJeAJMyyn1QH1PT
```

That output shape is what `cs start` parses to record the session ID.

### 2. Dispatch requires a TTY — `VERIFIED`, and it matters

Run non-interactively, `--cloud` refuses:

```
Error: --cloud requires an interactive terminal.
Non-interactive invocations (piped stdout, --init-only, --sdk-url) run locally and
would silently ignore --cloud. Drop --cloud, or run from a TTY.
```

Read the second sentence carefully: without that guard, a piped invocation would have
run the work **on your Mac** while you believed it was in the cloud. `cs start` refuses
before doing anything for the same reason, and wraps the call in `script` to supply a
TTY while still capturing the session ID.

Consequence: **you cannot dispatch a cloud session from a cron job, a hook, or another
agent's Bash tool.** Only steering (fact 3) works headlessly.

### 3. Headless steering works — `VERIFIED`

`claude -p "<message>" --cloud <session-id>` queues a message into a running session and
exits. No TTY, no local session state, works from any machine logged into the account.

```
$ claude -p "Also report the current working directory and the git branch you are on." \
    --cloud session_01B3GFCqymJeAJMyyn1QH1PT --output-format json
{"ok":true,"session_id":"session_01B3GFCqymJeAJMyyn1QH1PT","url":"https://claude.ai/code/session_01B3GFCqymJeAJMyyn1QH1PT?from=cli&m=0"}
```

This is the scriptable half of the loop. `cs send` wraps it.

### 4. Teleport works — `VERIFIED`

`claude --teleport <session-id>` pulled the session back into a local terminal. Observed
stages, in order: `Validating session` → `Fetching session logs` → `Getting branch info`
→ `Checking out branch` → session resumed.

The restored transcript contained **both** the original dispatch prompt and the
follow-up sent headlessly in fact 3, plus the cloud agent's tool calls. Working tree was
clean and still on `main` afterwards (the probe created no branch).

Teleport is **one-way**: the terminal copy is independent afterwards, and further local
work does not flow back to the cloud session.

### 4a. …but it was a **bundle**, not a GitHub clone — `VERIFIED`, correction

Facts 1–4 above are all true, and one belief underneath them was wrong. **GitHub was never
connected to the Claude account**, so the dispatch did not clone from the remote. Claude
Code silently fell back to bundling the local repository and uploading it — a documented
fallback that "activates automatically when GitHub access isn't available".

Three pieces of evidence, found only because routines were tested:

```
# creating a routine against the same repo that cs start had just worked on
HTTP 403 {"error":{"message":"You don't have access to a repository this routine uses.",
          "type":"permission_error"}}

# /web-setup in a session, which would be a no-op if GitHub were connected
Connect Claude on the web to GitHub?
Claude on the web requires connecting to your GitHub account to clone and push code
on your behalf.
```

**Why this matters, and it is not cosmetic:**

- **Bundled sessions cannot push back to the remote.** No branch, no PR. The only way to
  retrieve work is `--teleport`. The teleport in fact 4 succeeded because the probe
  created nothing to push.
- **Untracked files are excluded** from a bundle, and the whole thing is capped at 100 MB.
- **Routines cannot run at all** — there is no local machine to bundle from, which is
  exactly what the 403 is saying.
- It is silent. Nothing in the dispatch output distinguishes a clone from a bundle.

**Fixed the same day.** `/web-setup` → *Continue* returned `Connected as daniel0tgc.`
Routine creation against the same repo then returned **HTTP 200** where it had returned
403 minutes earlier. Since a routine has no local machine to bundle from, a routine that
runs at all is proof the clone path works.

Recorded rather than quietly corrected because it is a clean example of the folder's
fourth ground rule: a missing capability degraded into a *plausible success* instead of an
error, and it survived a verification pass that only tested the happy path.

### 4b. Routines fire and clone — `VERIFIED`

Created via the routines API and fired immediately, after GitHub was connected:

```
create  → HTTP 200  trig_01Bxk1rjkAZMB2P1pVS2L9st
run     → HTTP 200  {"session_id":"cse_01U63391Ug1GFZTvEgJUUNKe",
                     "last_fired_at":"2026-08-13T01:02:26Z"}
```

Two details worth carrying:

- **Routine sessions use the `cse_` prefix**, not `session_`. Both are valid session IDs
  for `cs send` / `--teleport`; `cs` matches either.
- **A `Claude_Code_Remote` MCP connection is attached automatically** to routines created
  through the API, even when none was requested. Worth knowing before assuming a routine
  has exactly the tools you gave it.

### 4c. A cloud session can push a branch — `VERIFIED`

The last claim left open by the bundling correction. A one-off routine
(`trig_01PztBgHXxgvNUzopSZHEg2U` → `cse_01QpoK8yTuohcmgrfvTbppH6`) was asked to branch,
commit and push. It landed:

```
$ git ls-remote --heads origin
refs/heads/claude/push-probe-20260812
refs/heads/main

$ git log --oneline -1 FETCH_HEAD
7f11481 Push-capability probe (safe to delete)
$ git log -1 --format='%an <%ae>' FETCH_HEAD
Claude <noreply@anthropic.com>
```

Probe branch deleted afterwards; `origin` is back to `main` only.

**Note the commit author:** `Claude <noreply@anthropic.com>`, not your GitHub identity.
The *push* is authenticated as you — which is what the docs mean by "commits and PRs carry
your GitHub user" — but the commit metadata attributes the agent. Both facts matter if you
audit history by author.

### 4d. Every connected claude.ai connector is attached to a routine by default — `VERIFIED`

Creating a routine through the API with **no** `mcp_connections` field returned a routine
carrying four:

```
Google_Calendar   https://calendarmcp.googleapis.com/mcp/v1
Claude_Code_Remote https://api.anthropic.com/v1/code/mcp/meta
Gmail             https://gmailmcp.googleapis.com/mcp/v1
Google_Drive      https://drivemcp.googleapis.com/mcp/v1
```

A routine whose entire job was to touch a git repository was handed **Gmail and Drive
access**, unasked. Routines run autonomously with no approval prompts, and Claude may use
any tool from an attached connector, **including writes**.

An earlier routine created minutes before carried only `Claude_Code_Remote`, so the set
tracks whatever is connected to the account at creation time — it is not a fixed default.

**Practical rule:** pass an explicit `mcp_connections` list, or review every routine at
[claude.ai/code/routines](https://claude.ai/code/routines) after creating it. Do not assume
a routine has only the tools you named.

### 5. Interactive terminal attach is NOT available — `VERIFIED`

`claude --cloud <session-id>` without `-p` is documented as attaching your terminal to a
running session. On this account it is gated:

```
Error: Attaching to an existing cloud session is not enabled for your account.
```

Anthropic's docs describe this as a gradual rollout and say to contact your account team.
**The `-p` queue-and-exit form is unaffected** and is what `cs send` uses.

Practical effect: the interactive attach/detach loop happens at
[claude.ai/code](https://claude.ai/code) or in the mobile app, not in a terminal. That is
sufficient for steering from a phone, which was the original requirement.

---

## Environment facts found on this machine

### The CLI was not installed — `VERIFIED`, now fixed

Before 2026-08-12 there was **no `claude` on PATH**. `which claude` failed, and no global
npm package existed. The only copy was the VS Code extension's private binary at
`~/.vscode/extensions/anthropic.claude-code-2.1.229-darwin-arm64/resources/native-binary/claude`.

Every command in the July archive that begins `claude …` was therefore unrunnable as
written. Fixed by installing the native build and putting it on PATH:

```bash
~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude install latest
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

Result: `~/.local/bin/claude` → 2.1.229 (**2.1.231** as of 2026-08-14 — same build the
cloud VMs run). `cs doctor` checks this and warns if you are running the extension's
private copy, because its version can differ from the CLI's.

### First run in a directory shows a trust prompt — `VERIFIED`

A freshly installed native CLI has no trust record, so the first interactive run in any
directory blocks on *"Quick safety check: Is this a project you created or one you
trust?"*. This silently stalls any automation that supplies no keystrokes. Run `claude`
once by hand in each repo you intend to dispatch from.

### The July implementation no longer exists — `VERIFIED`

The archived record documents a `longrun` system in the `emanate-tecum-workflow` repo.
None of it is on disk any more:

| Referenced by the archive | Present 2026-08-12 |
|---|---|
| `scripts/longrun.sh` | no |
| `scripts/longrun/` (watchdog, spawner, Cursor clients) | no |
| `LONG-RUNNING-SESSIONS.md` | no |
| `PRs/longrun-remote/RESEARCH-FINDINGS.md` | no |
| `PRs/longrun-remote/FACT-CHECK-2026-07-29.md` | no |
| `scripts/verify_system.sh` | no |

So every `./scripts/longrun.sh …` command in the archive is a dead instruction. This is
why `bin/cs` is self-contained inside this folder and depends on nothing outside it
except the `claude` CLI itself.

### Auth and access — `VERIFIED`

```
$ claude auth status
{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty",
 "email": "dtecum001@gmail.com", "subscriptionType": "max"}

$ gh auth status
✓ Logged in to github.com account daniel0tgc (keyring)
  Token scopes: 'gist', 'read:org', 'repo'
```

`claude.ai` auth (not an API key) is what makes `--cloud` and `--teleport` legal. The
`repo` scope on `gh` is what `/web-setup` syncs for repository cloning.

**`gh` being authenticated locally is not the same as GitHub being connected to your
Claude account.** They are separate grants, and `cs doctor` can only see the first. See
[4a](#4a-but-it-was-a-bundle-not-a-github-clone--verified-correction) — this distinction
cost a wrong conclusion today.

### Remote Control server mode registers a bridge environment — `VERIFIED`

Starting `claude remote-control` created an environment that then appeared in the
routine/cloud environment picker as `MacBook-Pro:internal-company-tool:7fcb`, `kind:
bridge`, alongside `Default` (`kind: anthropic_cloud`).

Worth knowing before you pick one: a **bridge** environment routes the work back to *your
Mac*. Selecting it for a routine or a cloud session gives you none of the "survives a
closed laptop" property that is the entire point. Use `Default` /
`kind: anthropic_cloud` for anything that must outlive the machine.

### `.claude/`, `.mcp.json` and `atlas-os/` are now tracked — `VERIFIED 2026-08-14`, correction

Until `23b98f0` (2026-08-13) they were untracked, and this folder said so in several
places as a standing constraint: *"a cloud session gets none of this repo's hooks,
permission deny-rules, MCP servers, or control plane."* That was true when written and is
**no longer true**. Measured on this machine:

```
$ git ls-files .claude .mcp.json | head
.claude/commands/cloud.md
.claude/settings.json
.mcp.json

$ git ls-files atlas-os | wc -l      # 150
$ git ls-files engagements | wc -l   # 41
```

Consequences, each confirmed by a probe rather than assumed:

- **Hooks fire in cloud sessions** — probe 6, below. This was the *prediction* made in
  [`this-repo-in-the-cloud.md`](this-repo-in-the-cloud.md) when the directories were still
  untracked, and it held.
- **The permission deny rules reach the cloud** — probe 7 confirmed `permissions.deny`
  loads there and refuses five of six write attempts against `atlas-os/bin/**`. The sixth
  walks through, which is a property of the matcher, not of the cloud; see the I10 section.
- **`.mcp.json` reaches the cloud and fails closed** — it references
  `${SUPABASE_MCP_PAT}`/`${SUPABASE_PROJECT_REF}` and neither is set on a VM, so probe 7's
  `claude doctor` there reported exactly that and nothing loaded. No secret travels.
- **The dispatch ledger is tracked too**, as of `1ad5228` — see the telemetry correction
  below for why that is the record that survives a VM and the hook trace is not.

The old "deliberately dumber cloud sessions" framing was a real fork in the road and it was
taken in the *other* direction. Recorded here because guardrail claims must not be stale in
either direction: assuming a protection exists when it does not is the obvious danger, and
assuming it does not exist when it does is how a session gets told to work around a rule
that is actually enforced.

---

## The tooling was reviewed and was not sound — 2026-08-13

An adversarial review of `bin/cs` found **ten defects, all reproduced**, five of which
printed green success while doing the wrong thing. That is this folder's own ground rule 4
violated by the tool written to enforce it. Recording it rather than quietly fixing it,
because the pattern is the useful part: *every one of them was a degraded condition that
read as a meaningful value.*

All ten are fixed. They were first re-verified by a throwaway script in a scratch
directory — which is exactly the kind of proof that evaporates, and it did. The permanent
replacement is [`../tests/run.sh`](../tests/run.sh), where each of the ten is pinned by
name (`D1`–`D10`); [`../tests/README.md`](../tests/README.md) maps defect → check. The four
worth remembering:

| Defect | Why it mattered |
|---|---|
| **Wrong session ID recorded** | The ID was grepped from the *whole* transcript, but `claude` echoes the task title above the `View:` URL. Any task mentioning another session id won. `cs handoff` made it near-certain by embedding `git log -5` in the brief — and this repo's commit messages name session ids. `cs send last` would then steer **a different, running session**. Now parses only the `View:` line |
| **`cs doctor` said `ready.` when dispatch would refuse** | Git state and TTY were warn-only, so doctor's verdict contradicted the tool's own gates. Now prints `not dispatchable from here.` and exits non-zero |
| **`cs env set` could destroy `~/.claude/settings.json`** | `json.dump(open(p,"w"))` truncates before writing; a SIGINT mid-write left an unparseable file where a 13 MB valid one had been, reproduced 1-in-9. Now writes to a temp file and `os.replace`s |
| **`cs env set`/`clear` reported success while doing nothing** | Both always wrote the *user* layer — the lowest precedence — so a project-layer pin silently won, and `clear` popped only the nested key form. Now warns when the write is not what dispatch will use |

Also fixed: a branch tracking a **fork** passed the "pushed" gate and dispatched against
stale origin; one malformed ledger byte broke `last` for every command; `--env`/`--plan`
with no value exited silently under `set -e`; `--plan` was not repo-root-relative, so a
session started at the root could not find its own plan; every handoff row in `cs ls` was
the same truncated brief; and `resolve_id` mangled malformed input instead of rejecting it.

Two things the review changed beyond bug fixes:

- **`cs rm <id>`** now exists. A wrong ledger row was previously unfixable — `state/` is
  "never hand-edit" and there was no remove. Needed within the hour: a verification script
  written for this very review wrote six junk rows into the real ledger.
- **There is no stop.** Nothing in the CLI or API stops a running cloud session. You can
  `cs send` it an instruction to wind down and then archive it from the web. Documented in
  [`03-steer-and-return.md`](../playbooks/03-steer-and-return.md#there-is-no-stop) rather
  than left as an unanswered question.

### The suite then found three more — `VERIFIED 2026-08-13`

Ten is the number the review found, and it is the number
[`../tests/README.md`](../tests/README.md) is organised around — but it is not the number
of defects fixed. Writing the suite found **three more of the same class** (commit
`74bc197`), so the running total is **thirteen**:

| Defect | Why it mattered |
|---|---|
| `cs start "task" --bundle` silently swallowed the flag | The flag landed inside `"$*"`, so it dispatched a **clone** and recorded `mode=clone`. You asked for one thing, got the opposite, and nothing was printed. `cs handoff` already guarded this for `--plan`. `--` is now the escape hatch for a task whose text really contains a flag word |
| `cs ls` raised `KeyError` on a row that is valid JSON but missing fields | The malformed-line defect one layer in — the ledger survives the damage it was hardened against, then dies on a subtler version of it |
| `last_id` did the same, breaking `last` for every command | Same cause, worse blast radius: `last` is the argument every other command takes |

All five checks that had been written as `xfail` against these flipped to `XPASS` under the
fixes and were promoted to normal checks — which is the mechanism working: a suite that
cannot report "your known-broken thing started working" lets a fix sit mislabelled forever.

### `--env` is self-hosted only — `VERIFIED`, correction

`cs start --env <id>` was advertised as a general per-dispatch environment override. It is
not. The CLI is explicit:

```
--environment <environment_id>   Create a new cloud session that runs on
                                 the given self-hosted environment (ccpool_...).
```

For an Anthropic-managed environment the supported path is `remote.defaultEnvironmentId`,
which `cs env set` writes. Passing an `env_…` id to `--env` is `UNVERIFIED` and `cs` now
warns rather than implying an override that may silently do nothing.

### What `cs` gained after the review, and what is actually proven about each — 2026-08-14

The tool grew four commands and an environment variable in two days. Their statuses are not
uniform, and the difference matters: a regression check proves `cs` behaves as written, a
probe proves it behaves that way on Linux, and neither proves a real cloud session was
dispatched by it.

| Surface | Landed | Status |
|---|---|---|
| `cs new` — the local-vs-cloud chooser | `d63ac00` | `VERIFIED` on Linux (probe 6: all five refusal paths, exact messages, non-zero exits) and covered by the suite. The **interactive** branch is exercised only against scripted stdin |
| `cs env` (`show`/`set`/`clear`) | review round | `VERIFIED` by regression checks `D3*`/`D4*` — including that a write to the user layer *warns* when a higher-precedence layer outranks it. `D3a`/`D3b` **cannot run as root**, so on any cloud VM they are skipped, not passed |
| `cs handoff` | review round | The composed brief is asserted line-by-line by `handoff/brief-*` checks. **No session in the ledger was dispatched by it** — see below |
| `cs rm` | review round | `VERIFIED` by regression checks, including that it preserves unparseable ledger lines rather than dropping them |
| `cs shell-init` / `CS_DEFAULT` | `f44fbf9` | Eleven regression checks, including that the emitted function is valid shell and that `shell-init` **writes nothing**. `UNVERIFIED` in a real shell startup — nobody here has yet `eval`'d it into a `~/.zshrc` and lived with it |

**The gap worth naming.** All nine rows in `state/sessions.jsonl` have `mode: tracked`:

```
$ python3 -c "…" cloud-sessions/state/sessions.jsonl
2026-08-12 … tracked  session_01B3GFCqymJeAJMyyn1QH1PT  Read-only VM verification probe
2026-08-14 … tracked  cse_01FMQZBzegc91HwwxWjSGncc      Probe 7: does I10 hold in cloud sessions?
  (…9 rows, every one `tracked`)
```

Every cloud session in this workstream was started by hand or by a routine and recorded
*afterwards* with `cs track`. `claude --cloud` is `VERIFIED` (fact 1) and the dispatch
*gates* are covered by the suite — but `cs start`'s own dispatch path (`script` pty →
parse the `View:` line → append a `clone`/`bundle` row) **has never produced a row against
the real CLI**. Every proof of it is against `fixtures/fake-claude`. That is the same blind
spot as the `View:`-line row in the not-verified table, seen from the ledger side, and it
is why the README's capability table now says what was actually run.

**This gap was closed on 2026-08-14** — see "`cs start`'s own record path" further down —
and the ledger above is shown as it stood *before* that proof. **Corrected 2026-08-16
(G09):** the closing dispatch produced exactly one non-`tracked` row (`mode: bundle`, repo
`csreal@master`), and that row was then removed with `cs rm`, "since a scratch repo is not
worth remembering." That was a deliberate choice, not an oversight, but it means the ledger
looks identical to the state that motivated this section, and a reader who stops here — or
re-runs `python3 -c "…" cloud-sessions/state/sessions.jsonl` today, which still prints all
`tracked` rows — cannot tell from the ledger alone that the gap was ever closed. The quoted
terminal output in the later section (`✔ Created cloud session…`, `recorded
session_015p9NFN42raZjT3qwGBH8BD`) is the whole surviving record of that proof.

## Measured inside a real cloud VM — 2026-08-14

**Seven** probes ran in actual cloud sessions on 2026-08-14, each pushing its findings back
as a branch. All seven were dispatched as routine runs rather than by `cs start` — every
one carries a `cse_` session id (the routine prefix, fact 4b) and five name their
`trig_…` id in the ledger row. Their reports are indexed in
[`README.md`](README.md#the-probe-reports--primary-evidence-not-current-state); the
sections below are what survived from them, which is the part you should rely on. A probe
report is a dated snapshot of one commit, and the first four describe a test suite that was
red then and is green now.

The first probe alone disproved three things this folder had written down, and two of them
were affecting behaviour. Full output:
[`cloud-vm-probe-2026-08-14.md`](cloud-vm-probe-2026-08-14.md).

### `x-deny-reason: host_not_allowed` does not exist — `DISPROVEN`

The blocked-host signal is a **plain-text 403 body**, not a header:

```
HTTP/1.1 403 Forbidden
Content-Type: text/plain; charset=utf-8

request blocked: no rule or allowlist entry allows host "example.com"
```

Worse, **a plain `curl` swallows that body** on a CONNECT tunnel failure — the probe only
retrieved it by opening a raw socket and issuing `CONNECT` by hand. So a blocked host
presents as a hang or a generic failure, not as a labelled refusal.

This mattered: the `cs handoff` brief was instructing every dispatched session to look for
a header that does not exist. Corrected in `bin/cs`, `limits.md`,
`06-environments.md` and `how-others-build-this.md`.

Also measured: package registries are reached **directly, outside the proxy** —
`no_proxy` pre-allows `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `jsr.io`,
`index.crates.io`, `proxy.golang.org` and `*.anthropic.com`. `api.github.com` is proxied
and allowed; `example.com` is refused.

### `--break-system-packages` is not required — `DISPROVEN` (keep the flag anyway)

The `EXTERNALLY-MANAGED` marker exists, but under **Python 3.12** — while `python3` on
PATH resolves to `/usr/local/bin/python3` → **3.11.15**, whose toolchain has no marker. A
bare `pip install networkx` succeeds, exit 0.

The flag stays in the setup script as a hedge against a future image wiring `python3` to
3.12. What changed is the claim, not the script.

### The test suite was not portable — `DISPROVEN`, and it was a real defect

`cloud-sessions/tests/run.sh` reported **57 of 232 checks failing** on the VM, including
its own "was the stub actually invoked" self-guard. Root cause, printed before the suite
began:

```
mktemp: too few X's in template 'cs-tests'
```

`mktemp -d -t cs-tests` is accepted by BSD/macOS and **rejected by GNU coreutils**. The
work directory was never created, so everything downstream cascaded. Fixed to
`mktemp -d -t cs-tests.XXXXXX`, which both accept.

A suite that is green on the author's laptop and broken on the target platform is the
same class of mistake as the rest of this file: it reported a state that was true only
where it was written.

### The suite still was not portable — a second, larger BSD/GNU bug — `2026-08-14`

Probe 2 re-ran on the VM after the `mktemp` fix and reported **58 of 233 failing** — barely
different from probe 1's 57. The `mktemp` warning was gone, so that fix held; it simply was
not the dominant cause.

The real one was `trun()`, the helper that runs `cs` under a pty:

```
$ script -q /dev/null echo hello
script: unexpected number of arguments
```

The two implementations take incompatible arguments:

| | |
|---|---|
| BSD / macOS | `script [-q] file command args...` |
| GNU util-linux | `script [-q] -c "command args" file` — **rejects a trailing command** |

Every `trun`-based assertion — `harness/stub-actually-invoked`, the whole `gate/*`
section, every `start/*` and `handoff/brief-*` check — got an empty `$OUT` and failed for a
reason unrelated to what it was testing. **One root cause, 58 symptoms.**

`trun()` now probes the flavour once at startup and uses the right form. This was written
as *"both suites are green on macOS; probe 3 confirms Linux"* — **before probe 3 had run**.
It did not confirm it: probe 3 came back with 38 failures from two further causes, and
green on Linux was still two rounds away. Corrected here rather than quietly deleted,
because writing down the result you expect from a verification you have not done yet is the
exact habit this file exists to catch.

The lesson is the same one twice in a row: *a suite that has only ever run on the author's
platform has only ever tested the author's platform.* Two consecutive portability bugs hid
behind each other, and the second was invisible until the first was fixed.

### A THIRD portability bug — this time in `cs` itself — `2026-08-14`

Probe 3 ran after the `trun()` fix. That fix held — `script: unexpected number of
arguments` was gone — and the suite still reported **38 failures**, because the same two
constructs I had fixed *in the test harness* were still present *in the tool the harness
tests*:

| `cloud-sessions/bin/cs` | Bug |
|---|---|
| `mktemp -t cs-dispatch` | GNU rejects it — dispatch aborts before `script` is ever reached |
| `script -q "$log" "$CLAUDE" …` | the BSD argument form, and swallowed by `\|\| true` so it fails **silently** |
| `open "https://…"` in `cmd_open`/`cmd_web` | macOS-only binary, absent on Linux; masked in tests by a stub |

All three now go through `run_under_pty`, `cs_mktemp` and `open_url`, which probe the
platform once at startup.

**This is the most instructive mistake in the whole workstream.** I fixed the test and not
the thing it tests, then reported the suite green — on the one platform where both were
already fine. Three portability bugs, found one at a time, each hidden behind the last, and
each one only visible because something actually ran on the target platform.

Probe 3 also found `cs doctor` reporting `ok repo … on HEAD` for a detached HEAD, while
`git_gate` in the same file refuses to dispatch for exactly that reason — the two
disagreeing about the same condition. Cloud checkouts start detached, so this was live.
Doctor now warns and marks the repo not dispatchable.

### And a fourth: GNU `script` swallows the exit code — `2026-08-14`

Probe 4 found 14 remaining failures, **12 of them sharing one cause**, and it is the
nastiest of the four because it fails in the *safe-looking* direction:

```
$ script -q -c "false" /dev/null; echo $?
0
```

GNU `script` returns **its own** exit status, not the child's, unless `-e/--return` is
passed. BSD `script` propagates the child's status and has no `-e`. So every `rc_nonz`
refusal check — `gate/detached-head-nonzero`, `gate/dirty-tree-nonzero`, all eight
`gate/*`, `start/*-nonzero`, `D2c`, `D5b` — was asserting "cs refused" against a value
that was **always zero**.

On macOS those checks passed for the right reason. On Linux they failed. But the
important part is the third case: had the suite been written on Linux without `-e`, they
would have *passed for no reason at all* — a green suite measuring nothing. Fixed by
adding `-e` to the GNU branch only.

The other two failures were a test whose premise evaporates as root: it makes `~/.claude`
read-only to prove `cs env set` cannot truncate, and **root ignores mode bits**. Cloud VMs
run as root, so the injection silently succeeded and the check measured nothing. Now
skipped loudly when `id -u` is 0 — a skipped check is not a passed check.

**Four portability bugs, found one at a time, each hidden behind the last.** Two in the
harness, two in the tool. The only reason any of them surfaced is that something actually
ran on the target platform; every one of them was invisible from the machine they were
written on.

### Green on both platforms — `VERIFIED 2026-08-14`

Probe 5, against `e9b7330`, on the cloud VM:

| Suite | Linux (probe 5, `e9b7330`) | macOS (same commit) |
|---|---|---|
| `cloud-sessions/tests/run.sh` | **231 passed, 0 failed, 2 skipped**, exit 0 | 233 passed, 0 failed, 0 skipped |
| `atlas-os/tests/heartbeat/run.sh` | 90 passed, 0 failed | 90 passed, 0 failed |
| `atlas-os/tests/worktree/run_tests.sh` | 53 passed, 0 failed | 53 passed, 0 failed |
| `atlas-os/bin/verify_system.sh` | 21 passed, 1 failed | same — the failure is the known `append-only:atlas-os/telemetry/runs` |

**Corrected 2026-08-16 (G08):** this row used to call that failure "correctly left alone as
I10 human-only" — a framing that says the check is fine and only the fix is gated. That is
not what probe 2 found. `atlas-os/telemetry/runs` is a **directory**; the check `wc -c`'s it
(`cloud-vm-probe-2-2026-08-14.md:198`, `wc: atlas-os/telemetry/runs: Is a directory`), so it
always reads 0 bytes and always reports `SHRANK` — it has been unable to evaluate its own
subject since it was written. Filed as `V-013`. **The check is broken; that is a distinct
claim from "the invariant is violated,"** and this row previously conflated the two. Because
the check is red for that known-bad reason, it cannot currently go red for a real
append-only violation — the protection this row implies is unproven, not merely
human-gated. The fix is `atlas-os/bin/verify_system.sh`, so I10 still applies to who may
apply it; it does not apply to naming what is actually wrong.

**Read the check counts as dated, not as a target.** They differ by platform *and* by
commit: 231 Linux vs 233 macOS at `e9b7330` is the two root-only skips, but 232 at
`74bc197` → 233 → 240 at `d63ac00` → 249 + 2 skipped at `f44fbf9` (probe 7) is the suite
**growing** as checks are added. A later number that does not match this table is the
normal case; a *smaller* one is the thing to investigate. The living count is the tally at
the end of [`../tests/RESULTS.md`](../tests/RESULTS.md), which every run rewrites — and
that file records the run, not the truth: a `tamper/bin-cs-unmodified` failure there means
`bin/cs` had uncommitted edits when the suite ran, not that `cs` is broken.

At `e9b7330` the two skips on Linux were `D3a`/`D3b`, skipped because `chmod` cannot make a
directory unwritable for root and every cloud VM runs as root. **Closed 2026-08-16 (G11):**
a `chmod`-only injection was the actual gap, not root itself — a read-only bind mount is
enforced by the VFS layer rather than the permission check root is exempt from, so it
defeats root too. `D3a`/`D3b` now run and pass on a root VM, proven with a positive control
that the injection genuinely fired; see `cloud-sessions/tests/run.sh` and
[`gap-closure-2026-08-16.md`](gap-closure-2026-08-16.md). The `-e` fix was confirmed
directly: `script -q -e -c "false" /dev/null` → 1, `script -q -c "false" /dev/null` → 0.

Probe 5 also swept from the **opposite direction** — constructs that pass on GNU and could
fail on BSD, which is the one failure mode four rounds of Mac-authored bugs could not have
surfaced. It found two candidates and could not test either, because it was running on
Linux: `sort -V` (in `cs`'s VS Code-binary resolution, `bin/cs:37`) and a `grep` BRE
alternation (`atlas-os/tests/worktree/run_tests.sh:278`). Both were then recorded here as
"cleared" **with no output quoted** — a `VERIFIED` tag resting on nobody's measurement.
Run on this Mac 2026-08-14 (macOS 26.5.1, arm64, bash 3.2.57), which is the platform in
question:

```
$ printf '1.1\n1.10\n1.2\n' | sort -V
1.1
1.2
1.10                      # version-ordered, not lexical: accepted

$ printf 'wt-root\nother\nworktrees\n' | grep -c 'wt-root\|worktrees'
2                         # both alternatives matched: \| is alternation here
```

Both **accepted on this machine's BSD userland**, so neither is a live bug. Note the
scope: this clears *this* Mac, not every BSD. If `sort -V` were ever rejected the failure
is silent — `ls … | sort -V | tail -1` inside `$( … || true )` would leave `bundled` empty
and `resolve_claude` would simply skip the VS Code fallback.

### Cloud sessions ARE logged — but the record dies with the VM — `VERIFIED 2026-08-14`

Probe 6 ran inside a cloud session and answered the question directly.

**Hooks fire.** `ledger_hook.py` ran on SessionStart, UserPromptSubmit, PreToolUse (×21)
and PostToolUse (×19), with the session id matching the running session, into
`atlas-os/telemetry/raw/2026-08-14.jsonl`. No `hook-errors.log`. This is only true because
`.claude/` is now committed; while it was untracked a cloud VM got no hooks at all.

**The record does not survive.** `raw/` is gitignored (`.gitignore:11`). Promotion to the
committed `runs/` copy is `atlas-os/telemetry/promote.py`, and **nothing calls it
automatically** — its `--quiet` flag is described as "for the nightly job", which runs on
your Mac. When the VM is reclaimed, every line from that session is discarded.

So "recorded" and "survives" were two different claims and only the first was true.

Probe 6 also confirmed on Linux: `cs new`'s five refusal paths all correct, suites at
**240/0/2 skip**, heartbeat 90, worktree 53, and `/cloud` visible in the cloud session's own
command list — though not usable to dispatch from there, since `claude --cloud` needs a
terminal.

#### The first fix was wrong, and was reverted the same hour — `SUPERSEDED 2026-08-14`

This section said, for about an hour, that the gap was **"closed" by the `cs handoff`
brief, which now instructs every dispatched session to promote and commit its own
telemetry as a separate final commit** (commit `b71e0c6`, regression check
`handoff/brief-preserves-session-telemetry`). That is not what the brief says now, and the
reasoning behind the reversal is more useful than the fix was:

- **It converts an involuntary record into a cooperative one.** The committed ledger's
  entire value is that recording is physics, not protocol — the hook fires whether or not
  the agent wants it to. A session that *chooses* to promote and commit its own trace
  produces a cooperative record wearing an involuntary one's costume, and every consumer of
  `runs/` would read it as the latter.
- **It is silently truncated.** `Stop` and `SessionEnd` fire *after* the last commit, so a
  VM-committed trajectory always stops short of its own ending — identically and invisibly
  on every cloud run. Partial data that looks complete is worse than absent data.

Reverted in `1ad5228`. `bin/cs`'s brief now says the opposite, explicitly and with the
reason attached: *"**Do NOT promote or commit `atlas-os/telemetry/`.**"* The regression
check was renamed to match and now asserts the refusal:
`handoff/brief-forbids-committing-telemetry`.

**Adopted instead: the dispatch-side record.** `cloud-sessions/state/sessions.jsonl` was
itself gitignored, so the one record that *can* survive a VM was being discarded too. It is
now tracked, and [`../state/README.md`](../state/README.md) explains why it is the
exception to "runtime state is not committed". A cloud session's record is therefore: the
dispatch row written on the machine that sent it, plus the branch and PR it produces.

**The measurement that settled it**, from the telemetry lane
([`../../atlas-os/tests/telemetry/CLOUD-LOGGING.md`](../../atlas-os/tests/telemetry/CLOUD-LOGGING.md)):
all five cloud probes put **zero** lines into the ledger. Proven by platform fingerprint
rather than by id matching, because a cloud session's `run_id` is a Claude Code UUID that
would not match a `cse_…` dispatch id — of the 10 distinct `session_start` events across
`raw/` + `runs/` (5127 lines), 7 carry an `env` block and **every one is `Darwin`/`arm64`
under `/Users/…`**; zero are `Linux`, zero `x86_64`, zero under `/home/user/`. Three
independent gates are each fatal on their own: the ledger path is `__file__`-derived onto
VM disk, `raw/` is gitignored, and only a pushed commit escapes a VM.

Open question, filed as **V-020**: `atlas-os/backend/DECISION.md`'s reversal trigger fires
on *hooks becoming available* in cloud sessions — which has now happened — rather than on
cloud telemetry *arriving* in `runs/`, which has not. Left for a human
([`../../atlas-os/queue/QUEUE.md`](../../atlas-os/queue/QUEUE.md)).

### Why lanes go STALE while actively working — `VERIFIED 2026-08-14`

`heartbeat.py` registers rows under a human lane name (`lane-chooser`), but its liveness
oracle keys on the ledger's `session_id`, which is a UUID. **A human lane name can never
appear in the ledger**, so every such row reports `no ledger entry` regardless of how hard
the agent is working.

Registering with `--session "$CLAUDE_CODE_SESSION_ID"` and carrying the lane name in
`--goal` fixes the visibility. **That is a proposal, not a rule that has been adopted** —
it is filed as `V-021`, status OPEN, in
[`../../atlas-os/queue/QUEUE.md`](../../atlas-os/queue/QUEUE.md), and until a human decides
it, lanes keep going STALE while working and a stale row stays indistinguishable from a
dead one. The measurement below is `VERIFIED`; the fix is `PROPOSED`. Tested
deterministically with the clock override rather than by waiting:

```
$ heartbeat.py --now <+45min> validate
WARN  row 8 (lane-chooser):        unproven ACTIVE row: NO LEDGER ENTRY; board self-report … (70m ago)
WARN  row 9 (lane-cloud-logging):  unproven ACTIVE row: NO LEDGER ENTRY; board self-report … (70m ago)
ERROR row 10 (a57f07bb-…):         STALE ACTIVE row: LEDGER 2026-08-14T20:19:30Z (45m ago)
```

Read the difference carefully. The named lanes say *no ledger entry* — invisible, and
indistinguishable from a lane that died on registration. The session-id lane says *the
ledger last saw you 45 minutes ago* — visible, dated, and it would not have expired at all
had the agent still been emitting events. The fix does not stop a row expiring; it makes
the row **observable**, which is the thing that was actually broken.

**Cloud sessions cannot use this fix.** Their ledger writes never leave the VM, so a cloud
lane is invisible to the oracle no matter how it is keyed. That is the cleanest statement
of the difference between the two halves of the problem. Filed as **V-021**.

### I10 is enforced at the tool layer, not the filesystem — `DISPROVEN 2026-08-14`

Probe 7 tested, from inside a cloud session, whether the deny rules that make
`atlas-os/bin/**` human-only actually refuse. Six write attempts:

| Attempt | Result |
|---|---|
| `Write` tool | **BLOCKED** |
| `Edit` tool | **BLOCKED** |
| Bash `cp` | **BLOCKED** |
| Bash `echo … >` | **BLOCKED** |
| Bash `tee` | **BLOCKED** |
| `python3 -c "open(path,'w').write(...)"` | **SUCCEEDED — guard bypassed** |

Five of six refused. The sixth wrote the file with **no error, no warning, and no
interception of any kind**. The guard pattern-matches tool names and Bash command
prefixes; it is not enforced at the filesystem. Anything reaching the filesystem through
a path the matcher does not recognise — an interpreter, a compiled binary, a different
language's write call — walks straight through.

A control write to a non-guarded path succeeded, so the test had teeth.

**This is true on the laptop too.** It is not a cloud-specific hole; the cloud is just
where it was finally measured, and where it matters most, because a dispatched agent runs
unattended and can write and execute arbitrary scripts.

**What this does NOT change.** Every agent in this workstream that hit the guard stopped —
five of them — and that remains the correct behaviour. A gap in enforcement is not
permission. The deny rule states an intent, and `atlas-os/bin/` staying human-only is a
decision the invariant file makes, not one the matcher makes on its behalf. Nothing here
has been installed into `atlas-os/bin/` using the bypass, and nothing should be.

Filed as **V-023**. Until it is decided, `INVARIANTS.md` describing I10 as ENFORCED
overstates what the mechanism delivers: it holds against agents that cooperate with it.

### First pull request opened by a cloud session — `VERIFIED 2026-08-14`

**Corrected 2026-08-16 (G07):** the sentence below used to read *"Probe 7 also closed the
**last** open item in this file's own 'not verified' table"* — false; the table a few
sections down still carries five open rows, and did at the time this was written. What
Probe 7 actually closed was **one specific row**: whether a cloud session can open a pull
request. Named here instead of claimed generically.

Probe 7 committed to `claude/probe7-i10-cloud`, pushed, and opened
**PR #1 — "Probe 7: does I10 hold in cloud sessions?"** via `gh pr create`. Suite on that
VM: **249 passed, 0 failed, 2 skipped, exit 0**.

The full loop is now proven end to end: dispatch → clone → work → branch → push → PR.

### `cs start`'s own record path — `VERIFIED 2026-08-14`

The docs audit caught this: every ledger row was `mode: tracked`, meaning the dispatch that
proved cloud sessions work was `claude --cloud` typed by hand. **`cs start`'s own path —
wrap in a pty, parse the `View:` line, write the ledger row — had never run against the
real CLI.** The suite exercises it against a stub, which cannot catch a change in the CLI's
output shape.

Closed by dispatching for real. The main repo had 33 uncommitted files from concurrent
lanes and the git gate correctly refused, so the probe ran from a one-file scratch repo
with `--bundle`, which exercises the identical pty→parse→record path:

```
✔ Created cloud session: Record path confirmation
View: https://claude.ai/code/session_015p9NFN42raZjT3qwGBH8BD?from=cli&m=0
recorded  session_015p9NFN42raZjT3qwGBH8BD
```

Ledger row: `mode: bundle`, `repo: csreal@master` — the first non-`tracked` row in the
file. Removed with `cs rm` afterwards, since a scratch repo is not worth remembering.

**The first attempt stalled on the workspace-trust prompt**, in a fresh directory, exactly
as this folder documents — *"it will stall silently if nothing is typing."* Written up here
because being caught by your own documented trap is the strongest evidence the trap is real.

The stub blind spot remains and is unchanged: if Anthropic alters the `View:` line, the
suite stays green while `cs start` silently stops recording. Only a real dispatch catches
that, and this is the one that did.

### Confirmed, and worth knowing

| | |
|---|---|
| VM | Ubuntu 24.04.4, `x86_64`, **4 vCPU**, **15 GB RAM**, **running as root** |
| Python / Node / git | 3.11.15 · v22.22.2 · 2.43.0 |
| Disk | `df` reports a 252 GB filesystem with only **30 GB available** — a per-session allowance on a larger backing volume. Do not read the 252 GB as headroom |
| `gh` | absent, as documented. `apt-get install -y gh` took **15s** and needed no `sudo` |
| Whole setup script | **~19 seconds**, well inside the five-minute budget |
| Repo | cloned (not bundled), 130 MB, 2155 tracked files at probe 1 — 2185 on this Mac two days later. Read it as scale, not as a checksum. `twenty/` is an uninitialized gitlink |
| `CLAUDE.md` | present and importing `AGENT.md`, so cloud sessions now arrive with the repo's agent guide loaded |
| **HEAD is detached** | not on `main`. Anything assuming `git rev-parse --abbrev-ref HEAD` returns a branch name gets `HEAD` |
| Two APT PPAs (deadsnakes, ondrej/php) | 403 through the proxy on every `apt-get update`. Harmless — the main archive still resolves — but it makes the setup script's output look alarming |

## A cloud session could have run code on this Mac — `DISPROVEN`, fixed 2026-08-15

The safety audit found the one genuine hole, and it was created by this folder's own setup
instructions.

Four facts composed into a cloud→laptop execution path:

| Fact | Evidence |
|---|---|
| `cloud-sessions/bin` was **first** on PATH, ahead of `/usr/bin` | measured; and `README.md`, `bin/README.md` and `01-setup.md` all told you to put it there |
| It is **not** deny-listed | `grep -c cloud-sessions .claude/settings.json` → `0`. Only `atlas-os/bin/**` is guarded |
| A file there **shadows a real binary** | demonstrated: a file named `git` in a PATH[0] directory ran instead of `/usr/bin/git` |
| The exec bit **survives a clone** | `git ls-files -s` → `100755` |

A cloud session has `Bash` and is told to commit and push as it goes. It could commit
`cloud-sessions/bin/git`. That file reaches this Mac through a merged PR, a branch checkout,
or `cs tp` — and then runs the next time you type `git`, in any directory.

Nothing has done this; the directory holds only `cs` and `README.md`. **The hole was
structural, not exploited.**

**Fixed at the root** rather than patched: `cloud-sessions/bin` is no longer on PATH at all.
`cs` is symlinked into `~/.local/bin`, which is not agent-writable, so a file committed into
the repo's `bin/` is now inert. The three docs that recommended the PATH entry are corrected
and say why.

Deny-listing `cloud-sessions/bin/**` was considered as defence in depth and **not** applied:
it would block maintaining `cs` itself, and the deny rules are tool-layer only
([V-023](#i10-is-enforced-at-the-tool-layer-not-the-filesystem--disproven-2026-08-14)) so a
`python3 open()` would pass through anyway. Removing the directory from PATH removes the
mechanism; that is the stronger move. Raised for a decision rather than taken unilaterally.

**The lesson generalises past this bug.** The dangerous instruction was in the setup
playbook, written to make the tool convenient. Convenience advice in a repo that agents can
write to is itself an attack surface, and none of the seven cloud probes, four portability
rounds or ten-defect review looked at it — because they were all auditing behaviour, and this
was in prose.

## The suite's check-count gap was never platform — it was *where it runs* — `VERIFIED 2026-08-18`

`G24` was filed as "the suite runs ~206 fewer checks on Linux". A cloud session diagnosed
it on the platform where it reproduces and the framing was wrong: the split is **run from
inside a cloud/CCR session** versus **run on an operator's own machine**, and it reproduces
on any platform. Three independent causes, and the second is worse than the missing checks:

1. **Operator environment leaked into the hook fixtures.** `hook_null()`/`hook_garbage()`
   invoked the real hook with `env VAR=val bash "$HOOK"`, which *adds* variables but never
   clears the parent shell's exports — so `CLAUDE_CODE_REMOTE*` passed straight through
   whenever the suite itself ran inside a Claude Code Remote session. Fixed by unsetting
   that prefix alongside the neutralisation the harness already did for
   `CLAUDE_CODE_SESSION_ID`.

2. **~10 checks were passing vacuously.** `session_register.sh:121` no-ops unconditionally
   when `id -u` is 0 — true on *every* cloud VM, as `G11` had already established. The
   write-dependent hook checks assert "no change", which cannot distinguish a correctly
   guarded refusal from **a hook that never tried at all**. They were green for the wrong
   reason. A one-time `HOOK_CAN_REGISTER` probe now converts them into named, reasoned
   skips. This is ground rule 3 — a probe a bogus control also passes is not a probe —
   caught inside the suite that enforces it.

3. **Two collapse lines stood in for 91 checks without saying so.** Sections 13–14 and 15
   fell back to a single `exodus/ALL` / `hook/ALL` skip when their fixture gate failed,
   silently representing **62** and **29** individual checks. Both now name their count, a
   `COLLAPSED` counter surfaces it in the summary, and a new section 18 re-derives both
   counts *from the live script text* by section marker so they cannot drift — with a
   negative control (`nc/acct-parity-can-go-red`) proving the parity check can fail.

Measured after the fix:

| Where | passed | failed | skipped |
|---|---|---|---|
| Cloud VM, root, inside CCR | 500 | 0 | 14, each named and reasoned |
| This Mac, operator account | **631** | 0 | 0 |

The counts still differ by environment, and that is correct — some checks genuinely cannot
run as root. What changed is that the shortfall is now **declared** instead of printed as a
confident tally. Before: 8 unexplained failures plus at least 2 checks passing vacuously.

## `allowed_tools` does not restrict a cloud session's tools — `VERIFIED 2026-08-17`

The routine payload carried
`"allowed_tools": ["Bash","Read","Write","Edit","Glob","Grep","Task"]`. The dispatched
session then successfully used **`ToolSearch`, `TaskCreate` and `TaskUpdate`** — none of
which are in that list — before doing any of its actual work.

So `allowed_tools` behaves as an **auto-approval list, not an allowlist that removes
capability**. Listing a tool spares it a permission prompt; omitting one does not take it
away. This resolves half of the `UNVERIFIED` note in
[`../../.claude/commands/cloud-workflow.md`](../../.claude/commands/cloud-workflow.md):
listing `Task` is harmless but was never load-bearing, and you cannot use `allowed_tools`
to *deny* a cloud session anything.

**Consequence for safety design:** do not treat `allowed_tools` as a sandbox. The controls
that actually bind a cloud session are the repo's `.claude/settings.json` deny rules (which
travel, since the file is tracked) and the prompt itself. `UNVERIFIED`: whether an empty
`allowed_tools` behaves differently from a populated one.

## A cloud VM's `origin/main` is stale on arrival — `VERIFIED 2026-08-17`

The VM checked out the correct commit — `HEAD` at `f144b17`, the tip at dispatch time — but
its remote-tracking ref pointed days back:

```
$ git rev-parse HEAD origin/main
f144b17b88fe891951566e262f60da8ce39c0b4e     <- correct, today
2258ab1a47f2da0ee030473f1192ce636c3d48a0     <- "Correct cloud-sessions: dispatch was
                                                bundling, not cloning" — days old
```

`HEAD` is right, so work proceeds correctly. The trap is any session that *reasons about*
`origin/main`: `git diff origin/main HEAD` reported a 59.8KB diff the session had to
persist to a file, and `git log origin/main..HEAD` would list commits that are long since
merged. A session asking "am I up to date with main?" gets a wrong answer, and one asking
"what did I change?" gets days of other people's work mixed in.

**Fix in a brief:** tell cloud sessions to diff against `HEAD` at dispatch, or to
`git fetch origin` first if they need the real main. Do not compare against the
remote-tracking ref a fresh VM hands you.

## An explicit empty `mcp_connections: []` does NOT prevent connector inheritance — `VERIFIED 2026-08-17`

This folder has said since 2026-08-13 that routines silently inherit every connected
claude.ai connector, and prescribed the fix: "Pass an explicit `mcp_connections` list, or
review the routine after creating it." **The first half of that advice is wrong**, and it
took dispatching one to find out.

Created with `"mcp_connections": []` in the body. The response came back carrying four:

```
Google_Calendar   calendarmcp.googleapis.com
Claude_Code_Remote  api.anthropic.com/v1/code/mcp/meta   (infrastructure, expected)
Gmail             gmailmcp.googleapis.com
Google_Drive      drivemcp.googleapis.com
```

An empty list reads as "unspecified", not as "none" — so the account's connectors are
attached exactly as if the field had been omitted. A docs-only workflow was handed Gmail
and Drive.

**What actually works is unknown.** Whether a non-empty list *restricts* (rather than
adds), and whether any value means "none at all", are both `UNVERIFIED` — testing them
costs a dispatch each. Until then the only reliable control is the second half of the old
advice: **`get` the routine after creating it and read `mcp_connections` back.** Treat
Gmail or Drive appearing there as a finding, and disable rather than fire if the work has
no business holding them.

This is the same shape as ground rule 4: a field that looks like a control, silently
doing nothing. Tracked as `V-041`.

## Subagents work inside a cloud session — `VERIFIED 2026-08-16`

Tested from inside a cloud VM, because the entire local way of working depends on fanning
out to subagents and nobody had checked whether that survives the move.

| Question | Answer |
|---|---|
| Is the Agent/Task tool available? | **Yes** — types `claude`, `claude-code-guide`, `Explore`, `general-purpose`, `Plan`, `statusline-setup` |
| Do subagent answers match ground truth? | **Yes** — exact match on both facts, verified independently by the parent rather than believed |
| Do several run concurrently? | **Yes** — overlapping execution confirmed from telemetry timestamps at n=3; starts are staggered, not simultaneous |
| Do they inherit git credentials? | **Yes** — `git fetch --dry-run origin` authenticated inside a subagent |
| Same detached HEAD as the parent? | **Yes**, identical commit |
| **Are they logged?** | **Yes — five distinct child `session_id`s in `atlas-os/telemetry/raw/`, alongside the parent's** |
| Concurrency cap? | not hit at n=3; not probed beyond |

**So one cloud session can replace a machine full of local ones.** That is the answer to
"can I get subagents from the other sessions in the cloud" — not by migrating anything, but
because a cloud session fans out exactly the way a local one does.

The logging row matters as much as the capability. Locally, subagents appear in the ledger
under their own `session_id`; in the cloud they do too. The organisation is identical. What
still differs is *durability*, not *structure*: those lines live on the VM's disk and die
with it, which is why the dispatch-side ledger carries provenance instead.

## Six gaps closed by a cloud session, unattended — `VERIFIED 2026-08-16`

Dispatched with the laptop shut. It closed six of the agent-closable gaps, used subagents to
parallelise, ran every suite before finishing, and opened a PR. Two results are worth
keeping beyond the fixes themselves.

**G11 is the one to read.** `D3a`/`D3b` prove that a failed `cs env set` cannot truncate
`settings.json`, and injected the failure with `chmod 500` on the parent directory. **Root
ignores mode bits, and every cloud VM runs as root** — so on the one platform where the
defect matters most, both checks *skipped* rather than passed. The cloud session replaced the
injection with a **read-only bind mount**, which the VFS enforces and root cannot bypass,
falling back to `chmod` for a non-root operator and to a loud `_skip` only when
`CAP_SYS_ADMIN` is unavailable. It then added `D3z`, a positive control asserting the
injection genuinely fired — so the check cannot quietly go vacuous again.

Verified on macOS after merging: `D3z`, `D3a`, `D3b` all **pass**, no skips.

**It also caught the register lying about itself.** `OPEN-GAPS.md`'s prose said five
agent-closable gaps; its own table listed six. The session worked the table, reported the
off-by-one, and accounted for all six.

`doc-drift.sh` is new and now guards four documentation claims that had drifted, so the
corrections cannot silently rot.

## Carried forward from the July record

These were verified then, are unchanged in kind, and still matter. Full detail in
[`../archive/2026-07-29/`](../archive/2026-07-29/).

| Claim | Status | Why it still matters |
|---|---|---|
| The cloud VM clones your **git remote**, not your disk — unpushed work is invisible, and the failure reads like a git error | `VERIFIED` (July) + `DOCS` | `cs start` gates on it explicitly rather than letting you discover it from a confusing startup error |
| No macOS API can prevent lid-close sleep; `caffeinate` cannot do it, per three Apple primary sources | `VERIFIED` (July) | Why moving work off the Mac beats keeping the Mac awake |
| `caffeinate -s` is AC-power-only; a held `PreventSystemSleep` did **not** stop a sleep at 24% battery | `VERIFIED` (July, observed) | A live power assertion is not proof the machine will stay awake |
| `nohup … & disown` does not detach a process — the child keeps the launcher's process group and dies to a group-addressed signal | `VERIFIED` (July, reproduced) | Relevant to any local supervision you build; `setsid(1)` does not exist on macOS |
| GitHub hosted-runner AUP forbids general agent hosting | `VERIFIED` (July, primary source) | See [`surfaces.md`](surfaces.md#what-is-deliberately-not-here) |

## Not verified, and named as such

| Claim | Status | What would settle it |
|---|---|---|
| Cloud session idle expiry duration | `UNVERIFIED` — documented to exist, no number published | Leave a session idle and time it |
| Remote Control drives from a phone | **PARTIALLY VERIFIED** — server mode connects ([evidence above](#remote-control-server-mode-registers-a-bridge-environment--verified)); the phone half is untested | open the printed URL on the phone and send a message |
| Daily routine run cap for Max | `DOCS` say a per-account daily cap exists; the July record cites Pro 5 / Max 15 / Team 25, current docs no longer publish numbers | Check claude.ai/settings/usage |
| Whether a lid-shut Mac survives a full physical close cycle | `UNVERIFIED` since July — the flag reads back correctly, but nobody has ever actually shut the lid for 15 minutes and checked for a gap | Only matters if you fall back to the local approach |
| That the real CLI **still** prints the `View:` line `cs start` parses, as a standing, recurring risk | `UNVERIFIED` — proven true once, on 2026-08-14 (see the "`cs start`'s own record path" section above), but nothing keeps it true after that date | The regression suite **stubs** that output shape, so it stays green if Anthropic changes it and `cs` silently stops recording ids. Named as the suite's own biggest blind spot in [`../tests/README.md`](../tests/README.md). This row is corrected 2026-08-16 (G07): it is not "never checked," it is "checked once, and could silently regress." Only a fresh real dispatch, read by eye, settles it again |
| Whether `--environment` with an `env_…` (Anthropic-managed) id does anything | `UNVERIFIED` — the CLI documents `--environment` for self-hosted `ccpool_…` only; `cs start --env` warns and passes it through anyway | Dispatch once with an `env_…` id and check which environment the session actually ran in |
| Whether cloud-session telemetry can ever reach the committed `runs/` ledger | `UNVERIFIED`, and deliberately not attempted — the one mechanism that would do it (a session committing its own trace) was tried and **reverted**; see the telemetry correction above | A human decision on `V-020`, not a measurement |
| Whether `atlas-os/bin/**` is human-only in any enforceable sense | `DISPROVEN` as stated — probe 7 wrote into it from a cloud session via `python3 -c open()`. Filed as `V-023` | A human decision on whether to enforce it below the tool layer, or to restate I10 as an intent |
