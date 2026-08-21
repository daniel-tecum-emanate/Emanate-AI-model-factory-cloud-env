# Surfaces — where a session can run, and what each survives

Every row is a distinct place Claude Code can execute. Pick by the **first column that
is true of your situation**, not by which sounds most capable.

Status values follow the folder rule in [`../AGENT.md`](../AGENT.md): `VERIFIED` means
someone ran it on this machine and the output is quoted in
[`verified-facts.md`](verified-facts.md).

---

## The matrix

| Surface | Claude runs on | Lid closed | Mac shut down | Steer from phone | Status on this machine |
|---|---|:---:|:---:|:---:|---|
| **Cloud session** — `cs new` → cloud / `cs start` / `claude --cloud` | Anthropic VM | **yes** | **yes** | **yes** (web + mobile) | **VERIFIED 2026-08-12**, and re-measured *inside* a VM 2026-08-14 (probes 1–7) |
| **Routine** — `/schedule` | Anthropic VM | **yes** | **yes** | yes | **VERIFIED 2026-08-12** — created and fired; all seven 2026-08-14 probes were dispatched this way |
| **Remote Control** — `cs rc` / `claude --remote-control` | your Mac | only if awake | no | **yes** | **connects**; phone half untested |
| **Background agent** — `claude --bg`, `claude agents` | your Mac | only if awake | no | no | available, not exercised |
| **Dispatch from mobile → Desktop** | your Mac | only if awake | no | yes | needs Desktop app pairing |
| **Self-hosted environment** — `--environment ccpool_…` | your infrastructure | yes | yes | yes | **not available** (Team/Enterprise only) |
| **Vercel Sandbox** — rented Linux microVM | Vercel's infrastructure | yes | yes | no (not without building it) | evaluated, **not the right tool for this** — [see below](#vercel-sandbox) |
| **Local terminal / tmux** | your Mac | no | no | no | baseline |

Only the first two rows survive a closed laptop *because the work is not on the laptop*.
Everything else on that list keeps a session alive on your Mac, which means the Mac has
to stay awake — a different problem with a different, worse answer (see
[the archive](../archive/2026-07-29/PROBLEM.md), which is entirely about that problem).

## Choosing

**"I want to close the laptop and have this keep going."**
→ Cloud session. `cs new "<task>"` and pick *cloud* — that is the front door, and it
composes the brief for you via `cs handoff`. `cs start "<task>"` is the same dispatch
without the brief. This is the default and it is proven.

**"Same, but it should happen on a schedule without me."**
→ Routine. `/schedule` in any session. Runs on the same cloud infrastructure.

**"I'm mid-way through local work and want to keep steering it from the couch."**
→ Remote Control. `cs rc`. Your files, your MCP servers, your machine — the phone is
just a window. The Mac must stay awake and online; if it sleeps, the session pauses and
reconnects on wake, and after ~10 minutes of no network it exits.

**"The work needs my local Docker/Supabase/secrets and can't move."**
→ Remote Control, and the Mac stays awake. There is no cloud answer to this; the cloud
VM is a separate machine with 4 vCPU / **15 GB** (measured) and no access to your disk.

**"It's not a GitHub repo."**
→ `cs start --bundle`. Uploads the local repo instead of cloning. Read the caveats in
[`limits.md`](limits.md#local-repository-bundles) first — untracked files are excluded
and the session cannot push back.

## What is deliberately not here

| Approach | Why not |
|---|---|
| **GitHub Actions as a session host** | Dropped on policy, not capability. GitHub's hosted-runner terms forbid "any other activity unrelated to the production, testing, deployment, or publication of the software project associated with the repository", with a penalty ladder ending at account termination. Costing also failed independently (~$259/mo for 4×6h jobs/day). Full reasoning: [archive lane 06](../archive/2026-07-29/lanes/06-github-actions-DROPPED.md). `anthropics/claude-code-action@v1` for *repo-scoped* automation remains fine. |
| **A paid always-on Linux box** (Hetzner ~€16/mo, DigitalOcean ~$24/mo) | Was the only approach that closed the attach/steer/detach gap in July. **That gap is now closed for free** by cloud sessions plus web/mobile plus `--teleport`. Do not buy a box before re-reading [archive lane 07](../archive/2026-07-29/lanes/07-always-on-vm-BLOCKED.md) and confirming the remaining gap is real. The one thing still missing is *interactive terminal attach*, and that is a rollout gate, not something a VPS fixes. |
| **`pmset disablesleep` to run lid-shut** | Still works and is still the only lever for keeping the *Mac itself* awake with the lid shut, but it is now the fallback rather than the plan. It is root-only, undocumented, global, persists across reboots, and Apple's thermal guidance is explicitly against sustained load under a closed lid. If the work can move to the cloud, move it. [archive lane 02](../archive/2026-07-29/lanes/02-lid-close-pmset.md) |
| **Cursor cloud agents** | A different vendor's product, proven working in July against a different repo. Kept in [archive lane 03](../archive/2026-07-29/lanes/03-cursor-cloud.md). Not carried forward: this folder is now about Claude Code sessions, and the first-party path costs no separate compute. |

## Vercel Sandbox

Evaluated **2026-08-12** against the question "could this host our sessions?" — from
[the docs](https://vercel.com/docs/sandbox), not exercised. Verdict: **no for hosting
sessions, yes as compute a session can reach for.**

### What it actually is

A **compute primitive**, not a session host: an isolated Firecracker microVM you rent by
the second, with root access, Docker, and a CLI/SDK (`sandbox create` / `run` / `connect`
/ `stop`, or `@vercel/sandbox`). It runs code. It has no concept of a conversation, no
mobile app, no web session list.

To "host a session" on it you would install Claude Code inside the VM and drive it
yourself. That is [lane 07](../archive/2026-07-29/lanes/07-always-on-vm-BLOCKED.md) — the
always-on Linux box — rebuilt as pay-per-second instead of a monthly VPS bill.

### Why it does not fit the goal

| | |
|---|---|
| **Authentication is the blocker** | Claude Code inside the VM needs credentials. An **API key** means paying per token *on top of* the Max subscription, and `--cloud`, `--teleport`, routines, and Remote Control all **refuse** under API-key auth. A `claude setup-token` OAuth token bills against the subscription but is explicitly rejected by Remote Control ("requires a full-scope login token"). Either way you lose the phone and the web session list — the things that make a cloud session usable while away from the desk |
| **You pay for compute** | Claude cloud sessions carry **no separate compute charge**. A Vercel-hosted session costs roughly **$0.70–0.90 per 8 hours** at 2 vCPU / 4 GB (memory is billed on wall-clock, CPU only when actually busy — agents idle on model calls, which helps) |
| **24-hour ceiling** | Max runtime is **24 h on Pro, 45 minutes on Hobby**. Hobby is unusable for this outright |
| **One region** | `iad1` only |
| **You build the steering yourself** | No `cs send` equivalent, no mobile push, no diff review UI. All of that would have to be written |

### Where it genuinely earns its place

Not as the session's home — as **compute the session reaches for** when the Claude cloud
VM is not enough:

- **Bigger machines.** Claude's cloud VM is 4 vCPU / 15 GB / 30 GB (measured). Vercel Pro goes to
  8 vCPU / 16 GB, Enterprise to **32 vCPU / 64 GB**. Memory-hungry builds and test suites
  that the cloud VM kills can be shipped out to a sandbox.
- **Running untrusted or generated code.** Its actual design purpose. Safer than executing
  agent-written code in a VM that also holds your repo credentials.
- **Workspaces that are not GitHub repos.** A cloud session needs a git repo (clone or
  bundle). A sandbox will hold any directory you copy into it.
- **Fan-out.** 10,000 concurrent sandboxes on Pro, versus rate limits shared across your
  whole Claude account.
- **System privileges** the cloud VM will not give you: FUSE mounts, VPN clients, custom
  container runtimes, mounted S3.

### The one pattern worth remembering

**Persistent sandboxes** auto-snapshot the filesystem on stop and resume by name:

```ts
const sandbox = await Sandbox.getOrCreate({
  name: 'atlas-workstation',
  onCreate: async (s) => { await s.runCommand('git', ['clone', repo, '.']) },
})
```

Because Claude Code keeps its transcripts on disk (`~/.claude/projects/`), a persisted
sandbox plus `claude --continue` is a genuinely resumable long-lived agent workstation —
your disk, your caches, your conversation, picked up where you left it. That is a real
capability and worth revisiting **if** the auth problem is ever solved, or if the work
stops needing phone access. Today it is strictly worse than `cs start` for the stated
goal.

### Cost sketch, if it is ever built

| Scenario | Cost |
|---|---|
| 8 h agent session, 2 vCPU / 4 GB, ~15% CPU busy | ~**$0.83** + Anthropic tokens |
| 30 min build, 4 vCPU / 8 GB | ~$0.34 |
| Pro plan includes a **$20/mo credit** before any of it bills | — |

Hobby: 5 CPU-hours and 420 GB-hours a month free, but the 45-minute ceiling rules it out.

## Sources

- [Vercel Sandbox](https://vercel.com/docs/sandbox) · [pricing and limits](https://vercel.com/docs/sandbox/pricing) · [persistent sandboxes](https://vercel.com/docs/sandbox/concepts/persistent-sandboxes)
- [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web)
- [Remote Control](https://code.claude.com/docs/en/remote-control)
- [Routines](https://code.claude.com/docs/en/routines)
- [Cloud environments](https://code.claude.com/docs/en/cloud-environments)
