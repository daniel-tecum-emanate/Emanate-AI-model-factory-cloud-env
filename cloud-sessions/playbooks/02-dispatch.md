# 02 — Dispatch and walk away

> Originally verified against a different repo (`daniel0tgc/internal-company-tool`);
> mechanism-level claims transfer, repo-specific paths corrected 2026-08-18.

The core move: hand work to a cloud VM, close the laptop, get on with your day.

---

## The command

```bash
cd /path/to/repo
cs new "Finish the auth refactor and get the test suite green"
```

`cs new` is the front door, and the only interactive command in the tool. It asks where
the work should run and then does the right thing:

| Choice | What happens |
|---|---|
| **1 here** | launches a normal local session. No git gate — local work has no remote to be invisible to |
| **2 cloud** | runs the full git gate, composes the handoff brief, dispatches |
| **3 here + phone** | Remote Control: runs on this machine, drivable from the Claude app |

It refuses to guess. With no terminal to ask in it exits rather than picking a side, and
`--cloud` without a task is refused because a cloud session runs unattended. For scripts,
`--here` and `--cloud` skip the question.

Inside an existing session, `/cloud` prepares a handoff from the conversation you are
already in and hands you the command to paste — it cannot dispatch directly, because
`claude --cloud` needs a terminal.

`cs handoff` is the same cloud path without the question:

```bash
cs handoff "Finish the auth refactor and get the test suite green"
```

`cs handoff` is the "I am closing the laptop now" command. It checks the repo is clean and
pushed, then composes a self-contained brief from the current state — your instruction,
the branch, recent history, and the standard finishing contract — and dispatches it. You do
not have to write the brief.

```bash
cs handoff --plan docs/migration-plan.md "Execute this"
```

`--plan` points the session at a plan you already wrote, and **refuses if that file is not
committed** — because the VM clones origin and would otherwise start work against a plan it
cannot read.

Use `cs start` directly when you want to write the whole prompt yourself:

```bash
cs start "Execute the migration plan in docs/migration-plan.md"
```

That returns in seconds. The work is now running on an Anthropic-managed VM and has no
further relationship with your Mac.

```
dispatching  repo=emanate-tecum-workflow branch=main
✔ Created cloud session: Execute the migration plan
View: https://claude.ai/code/session_01B3GF…

recorded  session_01B3GF…
  watch     https://claude.ai/code/session_01B3GF…
  steer     cs send last "…"        (works with the laptop shut)
  return    cs tp last
```

## Two things `cs start` refuses to do

Both refusals exist because the failure they prevent is silent.

**It will not run without a TTY.** `claude --cloud` in a pipe or a script would
*silently ignore the flag and run the work locally*. `cs start` stops first and tells you
to use a real terminal. Corollary: **cloud dispatch cannot be automated** from a cron job,
a hook, or another agent. Use a [routine](05-routines.md) for scheduled cloud work.

**It will not dispatch uncommitted or unpushed work.** The VM clones your **GitHub
remote**, not your disk. Local-only commits and uncommitted edits do not exist as far as
it is concerned, and the resulting startup error reads like a git problem rather than
"you forgot to push". So:

```
error: 3 unpushed commit(s) on 'feat/thing'. Run: git push
```

Push, then dispatch.

## Ask for a branch and a PR, not a teleport

Cursor and Codex both treat the **pull request as the deliverable**, and it is the right
default here too: a branch is retrievable from anywhere, by anyone, including from a phone
— whereas `cs tp` requires you to be at a machine with the repo checked out.

So end cloud prompts with the handoff you want:

> "…when the tests pass, commit to a `claude/` branch and open a PR describing what
> changed and what you could not verify."

Pushing is verified working
([4c](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md#4c-a-cloud-session-can-push-a-branch--verified)).
Claude pushes to `claude/`-prefixed branches, which are always accepted; pushes to other
branches are rejected if the branch is protected, carries someone else's commits, or has
someone else's open PR. Note the commit author is recorded as
`Claude <noreply@anthropic.com>` even though the push authenticates as you.

Use teleport when you want to *keep working* on it yourself, not merely to collect output.

## Write the prompt like the author is leaving

The session runs autonomously. You will not be there to clarify. What separates a good
cloud task from a bad one is entirely in the prompt:

- **name the finish line** — "all tests in `tests/api/` pass" beats "fix the tests"
- **point at a committed plan** rather than describing it inline. `cs start "Execute the
  plan in docs/migration-plan.md"` gives the session something it can re-read after
  compaction
- **say what not to touch** — it has full permissions and no approval prompts
- **assume Ubuntu x86_64**, not your Mac. Pinned arm64 binaries will not install

## Plan locally, execute remotely

The strongest pattern, and the reason `--permission-mode plan` exists:

```bash
claude --permission-mode plan          # collaborate on the approach; no edits made
# … agree on a plan, save it to docs/plan.md …
git add docs/plan.md && git commit -m "plan" && git push
cs start "Execute the plan in docs/plan.md"
```

You keep control of the strategy. The cloud does the hours.

## Parallel work

Each dispatch is an independent session with its own VM.

```bash
cs start "Fix the flaky test in auth.spec.ts"
cs start "Update the API documentation"
cs start "Refactor the logger to use structured output"
```

They all run at once. They share your account's rate limits proportionately — three
sessions burn limits roughly three times as fast — but there is no per-VM compute charge.

## Repos without GitHub

```bash
cs start --bundle "Run the test suite and fix any failures"
```

Uploads the local repository instead of cloning. Read
[the bundle limits](../reference/limits.md#local-repository-bundles) first — the two that
bite are **untracked files are excluded** (`git add` them) and **the session cannot push
back to a remote**.

## Choosing a different environment

```bash
cs start --env <environment-id> "…"
```

Only needed if the default environment lacks a dependency or a network host. See
[06 — Environments](06-environments.md).

## Then close the laptop

Nothing else is required. The session persists on Anthropic's infrastructure. Watch it
from [claude.ai/code](https://claude.ai/code) or the mobile app whenever you like.

## Next

→ [03 — Steer it, then bring it home](03-steer-and-return.md)
