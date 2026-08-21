# 01 — One-time setup

> Originally verified against a different repo (`daniel0tgc/internal-company-tool`);
> mechanism-level claims transfer, repo-specific paths corrected 2026-08-18.

Done on this machine **2026-08-12**. Re-run the verification steps on any new machine, or
if `cs doctor` starts failing.

---

## What was already true

- `gh` authenticated as `daniel-tecum-emanate` with `repo` scope
- Claude Code signed in via **claude.ai** on a **Max** plan (not an API key — this is the
  requirement for `--cloud` and `--teleport`)

## What had to be fixed

**There was no `claude` CLI on PATH.** The only copy on the machine was the VS Code
extension's private binary. Every `claude …` command in the July archive was unrunnable
as written.

```bash
# install the real CLI from the extension's bundled copy
~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude install latest

# put it on PATH (the installer does not do this)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

Result: `~/.local/bin/claude` → **2.1.229**.

Keep the CLI and the extension on the same version where you can. Several features have
version floors — `/teleport` from inside a cloud session needs 2.1.223+, CLI GitHub
triggers need 2.1.225+, routine run history needs 2.1.227+.

## Verify

```bash
cd /path/to/your/repo
cloud-sessions/bin/cs doctor
```

Expected:

```
  ok   binary        /Users/danieltecum/.local/bin/claude (2.1.231 (Claude Code))
  ok   auth          claude.ai subscription (max) — required for --cloud and --teleport
  ok   github(local) gh authenticated (daniel-tecum-emanate)
 warn   github(cloud) NOT CHECKABLE from here. If /web-setup has never been accepted, …
  ok   repo          emanate-tecum-workflow on main
  ok   remote        https://github.com/daniel-tecum-emanate/emanate-tecum-workflow.git
  ok   worktree      clean
  ok   pushed        up to date with upstream
  ok   tty           interactive — `cs start` can dispatch
  ok   ledger        2 recorded dispatch(es) — …/cloud-sessions/state/sessions.jsonl

ready.  Dispatch with: cs handoff "<what to continue>"
```

The `warn github(cloud)` line **always** prints — it is not a problem with your setup. It is
there because the tool genuinely cannot tell from your machine whether GitHub is connected to
your Claude account, and pretending otherwise is how the bundling mistake went unnoticed for a
day.

If instead you see **`not dispatchable from here.`**, your account is fine but something local
would make `cs start` refuse — uncommitted changes, unpushed commits, or no TTY. The line
names which.

## Put `cs` on PATH

Optional but worth it — every playbook assumes bare `cs`:

```bash
# Symlink it — do NOT put cloud-sessions/bin on PATH.
mkdir -p ~/.local/bin && ln -sf "$PWD/cloud-sessions/bin/cs" ~/.local/bin/cs
```

**Why a symlink and not a PATH entry.** `cloud-sessions/bin/` is tracked, agent-writable,
and files there keep their exec bit through a clone. Putting it on PATH — which earlier
versions of these docs told you to do — means a file committed there named `git` (or `ls`,
or `python3`) shadows the real binary the next time you type it. A cloud session has Bash
and is told to commit and push, so that file reaches your Mac through a merged PR, a branch
checkout, or `cs tp`. Symlinking one known file closes the path without giving up the
command. Found by the safety audit, 2026-08-15.

## Grant repository access to cloud sessions — REQUIRED, and easy to skip

**Having `gh` authenticated on your machine is not the same as GitHub being connected to
your Claude account.** They are separate grants. Skip this and things appear to work:
`cs start` silently falls back to **bundling** the local repo, so the session runs but
**cannot push a branch or open a PR**, and **routines fail outright** with
`403 You don't have access to a repository this routine uses`.

That is exactly what happened here on 2026-08-12 — see
[verified-facts 4a](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md#4a-but-it-was-a-bundle-not-a-github-clone--verified-correction).

Two ways, either works:

| Method | How | Use when |
|---|---|---|
| **`/web-setup`** | run it inside any Claude Code session — syncs your local `gh` token to your Claude account | you already use `gh` (this machine does) |
| **GitHub App** | authorize at [github.com/apps/claude](https://github.com/apps/claude) | you want [auto-fix on PRs](https://code.claude.com/docs/en/claude-code-on-the-web#auto-fix-pull-requests), which needs webhooks |

`/web-setup` grants **cloning access only**. It does not install the App and does not
enable webhook delivery, so GitHub-triggered routines need the App as well.

Note the blast radius: a cloud session can reach **any repository the connected GitHub
account can see**, not just those the App is installed on. Restrict on GitHub if that
matters.

## Accept workspace trust in each repo

A freshly installed CLI has no trust record. The first interactive run in a directory
blocks on a trust prompt, which will silently stall anything scripted:

```bash
cd /path/to/repo && claude    # accept, then quit
```

Do this once per repo you intend to dispatch from.

## Optional: pick a default cloud environment

If you have more than the **Default** environment, run `/remote-env` in a session to
choose which one `claude --cloud` uses. It writes `remote.defaultEnvironmentId` to your
user settings, so it applies in every project. See
[06-environments](06-environments.md).

## Optional: the phone

Install the Claude app ([iOS](https://apps.apple.com/us/app/claude-by-anthropic/id6473753684) /
[Android](https://play.google.com/store/apps/details?id=com.anthropic.claude)), sign in
with the same account, and allow notifications. Then in a session run `/config` and enable
**Push when Claude decides** and/or **Push when actions required**.

This is what turns "the work continues while the laptop is shut" into "and I find out when
it needs me".

## Next

→ [02 — Dispatch and walk away](02-dispatch.md)
