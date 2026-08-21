# This repo in a cloud session

What a cloud VM actually gets when it clones `daniel0tgc/internal-company-tool`, what it
can do with it, and what will never work there no matter how the environment is configured.

Audited **2026-08-13** by reading the code, not by guessing. Claims marked `INFERRED` were
reasoned rather than executed. Reconciled against the tree and the cloud probes
**2026-08-14**; the audit's central finding was overtaken by a commit the same day and is
kept below with its correction, because the prediction it made is the reason we know the
current answer.

---

## The finding that reframed the rest — and then stopped being true

**What the 2026-08-13 audit found:** four of the most important directories were untracked
and had never been committed.

```
$ git ls-tree origin/main                      # 2026-08-13
.cursor  .gitignore  .gitmodules  AGENT.md  CLAUDE.md  CRM-and-logistics  README.md
cloud-sessions  company-memory  leads-and-relationships  market-analysis
meeting-recorder  portable-workflow  product-research  twenty

$ git status --short
?? .claude/   ?? .mcp.json   ?? atlas-os/   ?? engagements/
```

A cloud session therefore got **none** of the control plane: no hooks, no permission deny
rules, no project MCP servers, no `engagements/`. The audit's closing argument was that
this was *a decision, not a bug* — leaving them untracked keeps cloud sessions
deliberately dumber — and that drifting into either answer by accident would be the only
wrong outcome.

### `CORRECTED 2026-08-14` — they are tracked, and the decision went the other way

```
$ git ls-files .claude .mcp.json
.claude/commands/cloud.md
.claude/settings.json
.mcp.json

$ git ls-files atlas-os | wc -l        # 150
$ git ls-files engagements | wc -l     # 41
```

Committed in `23b98f0` (2026-08-13), which is *after* this audit was written. Cloud
sessions are now full participants in the control plane, and every consequence below has
been measured in a real VM rather than reasoned about:

| Was `INFERRED` here | Now |
|---|---|
| "Commit `.claude/` and hooks *would* fire in cloud sessions" | **`VERIFIED`** — probe 6 watched `ledger_hook.py` fire on SessionStart, UserPromptSubmit, PreToolUse ×21 and PostToolUse ×19 inside a cloud session, with matching session ids and no `hook-errors.log`. The prediction was exactly right |
| the deny rules "do not exist there" | **`VERIFIED` present** — probe 7 confirmed `permissions.deny` loads in a cloud session and refused five of six write attempts against `atlas-os/bin/**`. The sixth (`python3 -c open()`) walked through; that is a property of the matcher, not of the cloud, and is true on this Mac too |
| `.mcp.json` "is safe to commit … failing closed when they are unset" | **`VERIFIED`** — on a VM neither `${SUPABASE_MCP_PAT}` nor `${SUPABASE_PROJECT_REF}` is set, and probe 7's `claude doctor` there reported precisely that. Nothing loaded, no secret travelled |

One thing the correction does **not** buy: the hook record still dies with the VM, because
`atlas-os/telemetry/raw/` is gitignored and nothing promotes it automatically. "Hooks fire"
and "the session is recorded durably" are different claims and only the first is true. A
first attempt to close that by having sessions commit their own telemetry was shipped and
**reverted within the hour** — the reasoning is in
[`verified-facts.md`](verified-facts.md#the-first-fix-was-wrong-and-was-reverted-the-same-hour--superseded-2026-08-14),
and the open question is `V-020`.

Also worth carrying: `atlas-os/backend/DECISION.md` attributed the absence of cloud-session
telemetry to "hooks are a local `settings.json` mechanism". That diagnosis was wrong — the
cause was simply that the file was untracked — and the reversal trigger it carries still
fires on the wrong event, which is what `V-020` asks about.

## What the repo needs installed

Almost nothing, which is the good news.

| Component | Third-party dependencies |
|---|---|
| `atlas-os/bin/*.py` (12 scripts) | **none — 100% stdlib.** `ledger_hook.py` says so explicitly: *"stdlib only. python3. No pip."*, and `freshness.py` refuses PyYAML on purpose |
| `market-analysis/frontier-ai-atlas/infrastructure/` (~40 scripts) | **`networkx>=3.0`** — `detect_ecosystem_cycles.py` imports it unguarded; `force_layout.py` raises `SystemExit` without it, and 13 scripts import `force_layout` |
| `leads-and-relationships/04-assets/generate_atlas_capabilities_deck.py` | **`python-pptx`**, unguarded import, no requirements file of its own |
| `meeting-recorder/` | faster-whisper, anthropic, deepgram-sdk, ffmpeg, swiftc — **irrelevant in the cloud, see below** |
| `twenty/` | Node ^24.5.0, yarn 4.13.0, Docker — **unreachable, see below** |
| `portable-workflow/`, `product-research/`, `company-memory/`, `CRM-and-logistics/` | none — markdown only |

Both installable dependencies are pure or near-pure Python, so the x86_64-vs-arm64 wheel
trap does not apply.

## Setup script

Paste this into the cloud environment's **Setup script** field
([how](../playbooks/06-environments.md)). It runs once, then the filesystem is snapshotted
and reused. Estimated at ~60–90s when written; **measured at ~19 seconds** on a real VM
(probe 1: `gh` 15.1s, pip 3.6s, checks negligible), and idempotent on a second run (probe
2). Well inside the five-minute budget either way.

```bash
#!/bin/bash
set -uo pipefail

# gh — cs handoff's standard brief ends with "open a pull request", and gh is the tool that
# does that. It is explicitly NOT in the cloud image.
( apt-get update -qq && apt-get install -y -qq gh ) &

# networkx   — the only hard third-party import in market-analysis/, which is the bulk of
#              the tree (1773 of 2185 tracked files, 2026-08-14). Unguarded; 13 depend on it.
# python-pptx — generate_atlas_capabilities_deck.py, unguarded import.
( pip install --break-system-packages --quiet 'networkx>=3.0' python-pptx ) &

wait

# Post-build validation. Devin's blueprints have an explicit validation phase and Jules makes
# you watch the setup succeed before freezing it; both exist because a partially-failed setup
# gets snapshotted and then silently reused by every later session. Fail loudly here instead.
gh --version   >/dev/null || { echo "SETUP FAILED: gh not installed"; exit 1; }
python3 -c 'import networkx, pptx' || { echo "SETUP FAILED: python deps missing"; exit 1; }
echo "setup ok: gh $(gh --version | head -1), networkx + python-pptx importable"
```

The last three lines are the point of the script, not decoration. The setup script's
filesystem is **snapshotted and reused**, so a partial failure is inherited by every
subsequent session until something forces a rebuild. Exiting non-zero on a missing dependency
is the difference between finding out now and finding out mid-task a week later.

`--break-system-packages` is **not actually required** on the current image, and the
reasoning behind it was half-wrong. Measured 2026-08-14
([probe](cloud-vm-probe-2026-08-14.md)): the `EXTERNALLY-MANAGED` marker exists, but under
**Python 3.12** — while `python3` on PATH resolves to `/usr/local/bin/python3` → **3.11.15**,
whose toolchain carries no marker. A bare `pip install networkx` succeeds, exit 0.

**Keep the flag anyway.** It is accepted with no ill effect, and it is the only thing
standing between you and a silent break if a future image wires `python3` to 3.12. What
changes is the claim, not the script: this is now a hedge, not a requirement.

**If you do not care about the market-analysis graph scripts or the pitch-deck generator,
the stock image is already enough and the only line worth keeping is `apt install -y gh`.**

### Deliberately not installed

| Skipped | Why |
|---|---|
| ffmpeg, faster-whisper, deepgram-sdk | no audio exists in the clone (gitignored), no API key, and capture is macOS-only. `faster-whisper` drags in ctranslate2/onnxruntime — hundreds of MB — and downloads a ~1.5 GB model on first run. Pure cost, zero capability |
| Node 24 + `yarn install` for `twenty/` | `twenty/` is an **empty gitlink** on a fresh clone. `git submodule update --init` would fetch `twentyhq/twenty`, which is not a repository attached to the session — and GitHub proxy traffic is scoped to attached repos, so it likely returns 403. Even succeeding, a 25-package yarn-4 monorepo install blows the whole budget |
| `docker compose pull` for Twenty | caching images is cheap, but the resulting container is an **empty CRM**, not your data |
| anything `atlas-os` | stdlib-only — nothing to install. (It *is* on the remote now, as of `23b98f0`; the reason to skip it is that it needs no dependencies, not that it is absent) |

## What can never work in a cloud session

No setup script fixes these. Keep them local.

1. **Meeting capture.** `record_cmd.py` shells out to `swiftc`, `capture/sck_audio_capture.swift`
   is a macOS **ScreenCaptureKit** system-audio tap, and `ffmpeg -f avfoundation` is a macOS
   input device. Permanently local.
2. **Transcription of real meetings.** `meeting-recorder/.gitignore` excludes
   `runtime/recordings/*`, so there is nothing to process — only finished `.txt`/`.md`
   artifacts are tracked. *Reading and analysing those works fine in the cloud.*
3. **Twenty CRM.** Live pipeline state belongs to the CRM, not git. It is a local
   `docker compose` stack, and the environment snapshot **does not capture running
   processes** — so even a cached image gives you an empty database, not your data.
4. **Anything needing `.env`.** `DEEPGRAM_API_KEY`, `SUPABASE_SECRET_KEY` and friends are
   gitignored, and cloud environments have **no secrets store**.
5. **Supabase MCP.** `CORRECTED 2026-08-14`: the config and the deny list **do** reach the
   cloud now that `.mcp.json` and `.claude/settings.json` are tracked — the original reason
   given here ("untracked config … the local deny list does not reach the cloud") is
   obsolete. What still holds, and is what actually blocks it: `${SUPABASE_MCP_PAT}` and
   `${SUPABASE_PROJECT_REF}` are unset on a VM and there is no secrets store, so the server
   fails closed. Probe 7's `claude doctor` on a VM reported exactly that. The deny rules on
   `mcp__supabase__*` now apply there as well — belt *and* braces, where before there was
   only the belt.
6. **`cs new --cloud` / `cs start` / `cs handoff` / `cs tp`.** TTY-gated by design.
   Dispatching a cloud session from inside a cloud session is not a thing — probe 6
   confirmed `cs new` refuses there with *"cs new needs a terminal to ask in"*, and the
   `/cloud` slash command says the same in its own body.

## Network

**Trusted — the default — is sufficient for this repo.** Every one of the ~2000 hostnames in
the tree is a research citation inside markdown, not a runtime call. There is no HTTP client
anywhere outside `meeting-recorder` (whose SDKs are unreachable anyway) and localhost smoke
tests.

| Host | Needed by | Covered by Trusted? |
|---|---|---|
| github.com | clone, push, `gh pr create` | yes, via the dedicated proxy |
| pypi.org, registry.npmjs.org | the setup script | yes |
| api.anthropic.com | the agent itself | yes — reachable even at network **None** |
| `localhost:8000`, `localhost:3000` | viz explorer, Twenty | in-VM, no allowlist involved |

**The exception that matters:** the largest tracked subtree is a *research corpus*. If you
want cloud sessions doing live market research — fetching pages, calling APIs — Trusted will
refuse them, and you need **Custom** or **Full**
([`cs env`](../playbooks/06-environments.md)). That is a research-workflow decision, not a
dependency one.

## Linux portability defects found on the way

Both were macOS-only assumptions in code that could plausibly run on the cloud VM. One is
fixed; one is not.

- `atlas-os/bin/nightly.sh` uses `stat -f '%Sm' -t '%Y-%m-%d'` — BSD syntax; GNU coreutils
  needs `-c`. It sits in the **success gate**, so a real Linux run would report a false
  failure. The file header does say *"macOS BSD userland only"*, so this is a documented
  limitation rather than a surprise — but it means the script cannot move to the cloud
  as-is. `--dry-run` and the STOP path exit before it, so `atlas verify` still passes.
  *Status: as audited 2026-08-13, not re-checked since — `atlas-os/bin/**` is human-only
  under I10 and this lane does not read it.*
- ~~`cloud-sessions/bin/cs` uses BSD `script` argument order and calls `open`, neither of
  which works on Linux.~~ **`FIXED 2026-08-14`, and it was not low impact.** The
  "low impact" reasoning here was wrong: the same file also used `mktemp -t cs-dispatch`,
  which GNU rejects, so under `set -euo pipefail` a dispatch on Linux aborted *before*
  `script` was reached — and the `script` failure that followed was swallowed by `|| true`,
  so it failed silently. Probes 3 and 4 found all three; `run_under_pty`, `cs_mktemp` and
  `open_url` now probe the platform once at startup. Full sequence:
  [`verified-facts.md`](verified-facts.md#a-third-portability-bug--this-time-in-cs-itself--2026-08-14).
