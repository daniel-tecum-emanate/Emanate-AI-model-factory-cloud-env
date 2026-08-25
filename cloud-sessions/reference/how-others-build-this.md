# How other providers host agent sessions in the cloud

Researched **2026-08-12** from vendor documentation and engineering blogs. Everything here
is `DOCS` — read, not exercised. The point is not to copy anyone; it is to see which
problems every provider hits, because those are the ones we will hit too.

Covered: **Cursor Cloud Agents**, **OpenAI Codex cloud**, and — for contrast —
**Vercel Sandbox** (evaluated separately in [`surfaces.md`](surfaces.md#vercel-sandbox)).

---

## The shape everyone converges on

Three vendors, three codebases, the same five-part architecture:

| Part | What it does | Claude Code | Cursor | Codex |
|---|---|---|---|---|
| **Isolated VM/container per session** | one workload, one blast radius | Anthropic-managed VM, Ubuntu 24.04 x86_64 | isolated VM, full dev environment | OpenAI-managed container, `universal` image |
| **Repo arrives by clone, not by copy** | the VM pulls from the forge | GitHub clone (bundle fallback) | GitHub, GitLab, Azure DevOps, Bitbucket | GitHub clone at branch or SHA |
| **A prepare step, cached** | dependencies installed once, reused | setup script → filesystem snapshot, ~7 day TTL | **Builds**: bootable snapshots, Dockerfile layer caching | setup script → container cache, **12 hour** TTL |
| **Results leave as a branch/PR** | the handoff is git, not a file transfer | `claude/` branches, PR from the web | separate branch → PR, plus screenshots/videos/logs | branch → PR |
| **A network policy** | egress is a deliberate choice | None / Trusted / Custom / Full | per-environment egress allowlist | **offline during agent phase by default** |

If you were building this from scratch, that table is the spec. The interesting parts are
where they diverge.

---

## Cursor — the durable-execution insight

Cursor's engineering write-up is the most architecturally useful thing in this space, and
one decision stands out.

### The agent loop does not live on the VM

> "the agent loop lives in Temporal rather than on the VM itself"

The orchestration — what turn are we on, what happens next, what to retry — runs in a
durable workflow engine, **separate from the compute doing the work**. The VM becomes a
disposable executor rather than the thing holding the session.

That one split buys everything else:

- **Hibernate and resume between messages.** They "efficiently hibernate and resume agent
  VMs between messages" — no VM burns money while waiting for a human to reply.
- **Checkpoint, restore, and fork VM images.** Forking is the notable one: branch a
  session's entire machine state, not just its git branch.
- **Prewarmed and readonly VMs**, because the loop does not care which VM it lands on.
- **Runs that stretch across days or weeks**, which no single VM lifetime could cover.

### They tried the obvious thing first and it failed

> a "work-stealing architecture" that operated at **"one 9 of reliability"** … "we were on
> the verge of rebuilding a lot of the durable execution primitives that Temporal already
> solves" → migrating "past two 9s"

Now serving "more than 50 million actions per day across more than 7 million unique
workflows", with cloud agents authoring **more than 40% of Cursor's own PRs**.

The framing they land on is the lesson worth stealing:

> moving from "porting a local agent to a server" toward **"building an operating layer
> around it"**

### Environment handling

- **Builds** are bootable snapshots prepared *ahead of* a run, with the latest successful
  one kept warm. Startup latency is moved off the critical path entirely.
- **Dockerfile layer caching** — "only the updated layers of your image rebuild when you
  change the Dockerfile. Builds that hit the cache run 70% faster."
- **Graceful degradation:** if the environment config fails, Cursor "will default to a base
  image with clear warning signs so that your cloud agents can keep running instead of
  immediately failing." Degrade loudly, do not stop.
- **Build secrets are scoped to the build step and are not passed to the running agent**,
  so a credential needed to install a private package never reaches the model's shell.
- Egress allowlists are **per environment**, so one environment can be permissive and
  another locked down.

### Steering

Read-only share links by URL; team admins can enable "team follow-ups" so colleagues can
continue someone else's agent. Results ship as "merge-ready PRs with artifacts to demo
their changes" — screenshots, videos, logs — plus **remote desktop control** to test the
software by hand without checking the branch out locally.

Pricing: API pricing for the selected model, with a spend limit prompted on first use.

---

## OpenAI Codex — the two-phase security model

Codex's distinguishing idea is a hard split in the container's lifetime.

### Setup phase, then agent phase

| | Setup phase | Agent phase |
|---|---|---|
| Network | **on** — installs dependencies | **off by default**, configurable |
| Secrets | decrypted and available | **removed before this phase starts** |
| What runs | your setup script | the agent loop |

Two properties fall out of that, and both are stronger than what we have:

1. **Secrets exist only while they are needed.** They get "an additional layer of
   encryption and are only decrypted for task execution", then are gone before the model
   ever has a shell. Compare our position: cloud environment variables are readable by
   anyone using the environment and there is **no secrets store at all**
   ([`limits.md`](limits.md#network)).
2. **The agent works offline by default.** Dependencies are already on disk, so the
   default posture is no egress — with all traffic through an HTTP/HTTPS proxy when it is
   enabled. Ours defaults to *Trusted*, an allowlist. Codex's default is stricter.

Also: default file scope is the folder or branch being worked on, with cached web search,
and elevated operations require permission.

**Container cache: 12 hours**, invalidated automatically when setup scripts, environment
variables, or secrets change. Resuming re-checks-out the branch and can run a separate
maintenance script.

Container image is `universal`, published openly as
[`openai/codex-universal`](https://github.com/openai/codex-universal) — you can read
exactly what is installed. Ours is a documented table, not a Dockerfile.

---

---

## Devin — the snapshot is the unit, the session is disposable

Devin states most explicitly what Cursor and Codex imply. **Snapshots** are frozen bootable
VM images built ahead of time, holding repos, toolchains, and secrets. **Sessions** are a
fresh copy booted per run, and *"session changes don't persist back to the snapshot"* — code
edits, new files and ad-hoc installs are all discarded. The docs call getting the snapshot
right *"the single highest-leverage thing you can do to improve Devin's effectiveness on
your codebase."*

**Environment Blueprints** are declarative YAML with four phases, and two of them are ideas
we do not have:

- **Knowledge** — lint/test/build commands loaded into the agent's *context*, not executed.
  A declarative place to say "here is how you run the tests" that is neither code nor prose
  buried in a prompt.
- **Post-build validation** — the build is not finished until something proves the
  environment works. "Degrade loudly" made mechanical.

Builds take 5–15 minutes, one active snapshot per org, pinnable or auto-refreshed every 24h.

**Steering** is three surfaces — shell, in-session VS Code, and an interactive
browser/desktop — plus a Progress tab of clickable steps, and the docs recommend
*intervening early* rather than steering continuously. Note the takeover protocol is
**manual and lossy**: you stop the session, edit yourself, then must *narrate* what you
changed, because the agent cannot observe out-of-band edits. That seam is unsolved
everywhere; worth remembering before designing any "edit the branch while it runs" flow.

**Secrets are injected as ordinary environment variables** — `$API_KEY` works, so the
agent's shell can read them. That is materially weaker than Codex. Whether they are redacted
from logs is `UNVERIFIED`, and the absence of that claim is itself notable. The one Devin
secrets idea worth copying is **OIDC federation** for short-lived cloud credentials instead
of a long-lived key.

`blockdiff`, their open-sourced snapshot format, snapshots a **20 GB disk in ~200 ms** by
reading changed blocks from filesystem metadata via `FIEMAP` rather than scanning contents —
a ~200× speed-up over EC2 snapshots. Not applicable to us directly, but it explains *why*
"fork a session" is a cheap ask rather than an extravagant one, which is useful ammunition
if we ever file that request.

## Google Jules — one good interaction idea

A short-lived Ubuntu VM per task with its own checkout, and — unusually — **full internet
access by default**, with guidance amounting to "treat it like any remote compute." The
weakest network posture of the six, and a useful counterpoint: Jules trusts the sandbox
boundary rather than the network boundary.

The idea worth stealing is interaction design, not technology. **"Run and Snapshot"**: you
write the setup commands, *run them, watch them succeed, and only then freeze*. Cursor,
Codex and we all run the setup script implicitly at session start, where a failure is
discovered late and by the wrong party. Jules makes "prove the environment builds" an
explicit human-in-the-loop gate.

Also: a **plan-approval gate** before any code changes, and `AGENTS.md` auto-discovery —
the same convention Codex uses. Repo-resident agent instructions are now a de-facto
cross-vendor standard.

## GitHub Copilot coding agent — the anti-pattern, and the best firewall

It runs in *"an ephemeral development environment, **powered by GitHub Actions**"* — GitHub
reused its own CI substrate rather than building a runtime. One branch and one PR per task,
every step visible as a commit.

**This is the anti-pattern for us, and it confirms an earlier call.** Building on CI means
inheriting CI's job ceiling (~59 minutes; `UNVERIFIED` exact figure, but the *existence* of
an inherited ceiling is the architectural point). GitHub can live with that because they own
the runner terms and can split work across sessions. We cannot — which is why GitHub
Actions was evaluated as a session host on 2026-07-29 and **DROPPED**. That call was right
for a reason beyond cost and terms of service.

**Their firewall is the best-expressed network policy surveyed.** Deny-by-default with a
recommended allowlist covering OS package repos, container registries, language registries,
**certificate authorities for TLS validation**, and Playwright browser-download hosts. Two
ideas:

1. The **category list itself** is a specification of the minimum viable allowlist for a
   coding agent. Worth checking ours covers CAs and browser downloads.
2. **Blocked egress is surfaced in the deliverable, not the logs:** *"a warning is added to
   the pull request body"* naming the blocked address **and the command that triggered it**.

Refreshingly honest about limits: the firewall only covers processes started via the agent's
Bash tool — **not MCP servers, not setup steps** — and *"should not be considered a
comprehensive security solution."*

Steering is `@copilot` mentions **in PR comments**, which means the PR is the control plane
and any GitHub mobile client can drive it. No bespoke app required.

## OpenHands — the session is a log, not a process

Open source, and by far the most readable architecture here. The
[Software Agent SDK paper](https://arxiv.org/html/2511.03690v1) is the best single source in
this sweep.

**Event sourcing is the whole design.** An immutable append-only event log; components are
stateless serializable models; `ConversationState` is the single mutable entity. Persistence
is dual-path — metadata in one `base_state.json`, events as individual JSON files — so a
long history is never rewritten. **Resume = load the metadata and replay the event
directory**, and *"agents automatically detect incomplete conversations and continue from
the last processed event."* `pause()` emits a `PauseEvent` and persists; `resume()` continues
from the last checkpoint.

**This is the same insight as Cursor's Temporal decision, from a different angle: the
session is a log, not a process.** Cursor puts the log in a durable workflow engine;
OpenHands puts it in a directory of JSON files. Either way the VM is disposable because the
session never lived on the VM. Claude Code already does the cheap version locally —
transcripts in `~/.claude/projects/` — which is exactly why the Vercel "persistent sandbox +
`claude --continue`" trick in [`surfaces.md`](surfaces.md#vercel-sandbox) works at all.

**Their secrets design is the best of anyone surveyed.** A `SecretRegistry` with
per-conversation isolation; tools access secrets **only at execution time**; the Bash tool
*"scans commands for secret keys, exports the referenced ones as environment variables, and
replaces their occurrences in results with a constant mask (`<secret-hidden>`)"*. Secrets may
be **callables** — a token refresher rather than a static value — and are redacted during
serialization so they never reach conversation history. Compare the field: Devin and Jules
put secrets in the env for the whole session, Codex deletes them before the agent runs,
OpenHands injects per-command and masks on the way out.

**Risk-graded confirmation** is the missing middle we do not have. A `SecurityAnalyzer`
rates each tool call low/medium/high/unknown, a `ConfirmationPolicy` decides whether approval
is needed, and the agent parks in `WAITING_FOR_CONFIRMATION`. That is the mechanism that
makes "run unattended for days, but stop before anything scary" expressible — as opposed to
our binary choice between approving everything and approving each step.

Also notable: the same three human surfaces as Devin — a shell, an IDE (VSCode Web), and a
browser/VNC desktop. That trio appears to be the settled answer for "let a human look
inside."

## Sandbox infrastructure — E2B, Daytona, Modal, Fly

Compute primitives, not session hosts — same category and same verdict as
[Vercel Sandbox](surfaces.md#vercel-sandbox). They are worth reading because they compete on
exactly the properties we care about, so their docs are effectively a specification of what
"good" looks like.

| | E2B | Daytona | Modal | Fly Machines |
|---|---|---|---|---|
| Memory snapshot | **yes** (default on pause) | **hot snapshot** | yes (experimental) | **yes** (Firecracker suspend) |
| Fork | not documented | **yes** | via FS snapshot | from image, not live state |
| Resume latency | **~1 s** | sub-90 ms from warm pool | not published | **few hundred ms** vs ~2 s cold |
| Max continuous run | 24 h Pro / 1 h Hobby | auto-stop intervals | 5 min default, **24 h max** | no documented ceiling |
| Paused lifetime | **indefinite** | indefinite (object storage) | FS 30 d default; **memory 7 d, non-extendable** | **not durable** |

**The pattern that repeats: pause is not stop.** Every one separates *stop* (filesystem
survives, memory dies, cold boot) from *pause/suspend* (memory survives, resume in ~1 s).
Fly snapshots *"CPU registers, memory contents, open file handles."* This is the primitive
under Cursor's "hibernate and resume between messages" — and since a coding agent is idle
most of its wall-clock life waiting on model calls and humans, suspend-between-messages is
the single biggest efficiency lever in the space.

**The most important sentence in the whole sweep** is Fly's, because they are the only ones
who say the quiet part: snapshots are *"not guaranteed to persist"* — discarded on deploys,
host migrations, hardware failures — and therefore *"always design for both resume and cold
start paths."* Any architecture that assumes resume-from-memory always works will eventually
lose a session. **Memory state is an optimisation; disk and git state are the truth.**

**Modal's egress API is the best-expressed network policy of anyone**, and one feature has
no equivalent elsewhere: `_experimental_set_outbound_network_policy` **changes the egress
policy of a running sandbox without restarting it**, motivated explicitly by *"when an
agent's trust level changes mid-session."* Blocked connections are logged to the sandbox's
own output stream, so the agent can see why it failed — the in-band version of Copilot's
PR-body warning.

Modal also separates **filesystem snapshots** from **directory snapshots** (capture specific
directories, mount them into *other* running sandboxes), for *"updating system dependencies
separately from application code."* The layering insight — toolchain and workspace change at
different rates and should be snapshotted on different clocks — is the same one Devin's
blueprint scoping encodes.

---

## What this suggests for us

Ordered by how much it would actually change, not by how clever it sounds.

### 1. Prefer the branch as the handoff, not teleport

Cursor and Codex both treat the **PR as the product**. We had this backwards for a day:
before GitHub was connected, `cs start` was bundling, so the only way to retrieve work was
`cs tp` — pulling the whole session down to a machine that has to be present. A branch is
retrievable from anywhere by anyone, including a phone. Now that pushing works
(verified 2026-08-12),
prompts should say *"open a PR"* by default and treat teleport as the exception.

### 2. Say the network posture out loud in the prompt

Codex's agent phase is offline **by default**. Ours is *Trusted*, which is permissive by
comparison and invisible unless you go looking. Since blocked requests fail with a `403`
whose body names the host — and a plain `curl` hides that body — a session that will need
a host should have it allowlisted deliberately rather than discovered mid-run.

### 3. Treat "no secrets store" as a design constraint, not a gap to work around

Codex removes secrets before the agent runs; Cursor scopes build secrets to the build
step. We have neither, and the environment dialog says so. The correct response is not to
put a key in an environment variable more carefully — it is to keep credentialed work out
of cloud sessions entirely, and let the session ask for a PR review instead of doing the
credentialed step itself.

### 4. Degrade loudly

Cursor falls back to a base image "with clear warning signs". This is the same principle as
[ground rule 4](../AGENT.md#ground-rules), and we already have a live example of getting it
wrong: dispatch silently bundling instead of cloning looked exactly like success for a
whole verification pass. `cs doctor` now names what it cannot check for precisely this
reason.

### 5. Warm the environment before you need it

Cursor keeps the latest successful Build ready to boot; Codex caches for 12 hours; ours
snapshots after the setup script and reuses it for ~7 days. **We get this for free and are
still not using it** (as of 2026-08-14) — there is no setup script on the Default
environment, so every session starts from the stock image. All seven cloud probes ran the
proposed script's commands *by hand* for that reason, which is also how we know it costs
~19 seconds and is idempotent. If sessions in this repo ever need a toolchain that is not
pre-installed, a setup script pays for itself immediately — see
[`06-environments.md`](../playbooks/06-environments.md).

### 6. The one thing worth genuinely wanting: fork a session

Cursor can "checkpoint, restore, and fork VM images." Forking a session — same machine
state, two directions of exploration — has no equivalent in what we have. `--teleport`
gets a *copy* onto one machine, one-way, and that is the closest analogue. Not something
we can build; something to ask for.

### 7. Commit incrementally, because the machine can vanish

Fly's warning generalises past Fly: **memory state is an optimisation, disk and git state
are the truth.** Our own version of the machine vanishing is idle expiry — the VM is
reclaimed, and while the conversation is restored, anything uncommitted on that disk is not.

So a long autonomous run should commit and push as it goes, not once at the end. `cs handoff`
now says so in the brief. This costs nothing and converts "the session expired and I lost
four hours" into "the session expired and I have four hours of commits."

### 8. Make blocked egress part of the deliverable

Copilot writes blocked hosts **into the PR body**, naming the command that triggered them.
Modal logs them to the sandbox's own output stream. Both beat a scrollback nobody reads.

We get a machine-detectable signal for free — a `403` whose body names the blocked host —
so a session can be *told* to collect them and report them. `cs handoff` now instructs
exactly that. (The signal is a plain-text body, not the `x-deny-reason` header this folder
first assumed; measured 2026-08-14.) It turns the single most confusing cloud-session failure ("the API is down?")
into a line in the pull request.

### 9. Validate the environment as part of building it

Devin's blueprints have a **post-build validation** phase; Jules makes you **run the setup
and watch it succeed** before freezing it. Both encode the same rule this folder already has
as ground rule 7 — a skipped check is not a passed check — and both are mechanical rather
than a matter of discipline.

Our setup script is snapshotted after it runs, and a partial failure is easy to miss. Ending
it with an explicit verification line is the cheapest available version of the same idea.

### 10. Worth asking Anthropic for

Ranked by how much they would change day-to-day work. None are things we can build.

| Ask | Who has it | Why it matters here |
|---|---|---|
| **Risk-graded confirmation** — run unattended, but park before anything destructive | OpenHands (`SecurityAnalyzer` + `ConfirmationPolicy`) | The missing middle. Today a cloud session either approves everything or needs a human at every step, which is why long unattended runs feel risky |
| **Fork a session** | Cursor, Daytona | Two directions of exploration from one machine state. `--teleport` is a one-way copy onto one machine, and it is the closest thing we have |
| **Interactive terminal attach** | — (gated off for this account) | Already known; the one gap a paid VPS would not have fixed either |
| **Change egress policy on a running session** | Modal | *"When an agent's trust level changes mid-session"* — today the only option is to start over in a different environment |
| **Secrets that are callables, not values** | OpenHands; Devin's OIDC | Would make credentialed work in cloud sessions possible at all. Currently ruled out entirely |

### 11. The architectural idea we should not need

**Do not build a durable orchestration layer.** Cursor built one because they are the
provider. We are the customer: Anthropic's infrastructure already persists the session,
restores conversation history onto a fresh VM after idle expiry, and survives our laptop
being off. Reimplementing any of that locally — which is what the July `longrun` work was
drifting toward — rebuilds someone else's operating layer badly. `bin/cs` is deliberately a
gating and bookkeeping wrapper, nothing more.

---

## Sources

- [Cursor — What we've learned building cloud agents](https://cursor.com/blog/cloud-agent-lessons)
- [Cursor — Development environments for your cloud agents](https://cursor.com/blog/cloud-agent-development-environments)
- [Cursor — Cloud Agents docs](https://cursor.com/docs/cloud-agent) · [Builds](https://cursor.com/docs/cloud-agent/builds) · [Setup](https://cursor.com/docs/cloud-agent/setup) · [API](https://cursor.com/docs/cloud-agent/api/endpoints)
- [OpenAI — Codex cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environment)
- [OpenAI — Agent approvals & security](https://developers.openai.com/codex/agent-approvals-security)
- [openai/codex-universal](https://github.com/openai/codex-universal)
- Prior art: Cursor's v0 cloud-agent API was exercised end-to-end in July 2026, including the rate limit that produced a real defect (lane evaluation 2026-07-29, no longer carried in this repo)
