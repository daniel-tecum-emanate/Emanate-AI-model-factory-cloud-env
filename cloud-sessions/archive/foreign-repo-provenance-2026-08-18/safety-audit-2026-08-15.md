# Safety audit — 2026-08-15

Daniel's requirement, verbatim: *"make sure that nothing obscure will happen to my computer
or information."*

This is the adversarial answer, with the evidence. Status tags per
[`../AGENT.md`](../AGENT.md): `VERIFIED` (run here, output quoted) · `DOCS` · `UNVERIFIED` ·
`DISPROVEN`.

Scope: the cloud-sessions path — `bin/cs`, `/cloud`, `/routines-audit`, the permission deny
rules, and everything they touch on this Mac. Read-only audit; this file is the only thing
written.

---

## Top-line answer

**Yes — one thing. Everything else is sound.**

`cloud-sessions/bin` is the **first entry on Daniel's `PATH`**, it is a tracked repository
directory, and **no deny rule protects it**. Any agent that can write a repo file can drop
an executable there named after a command Daniel types daily — `git`, `python3`, `ls` — and
it will run instead of the real binary, in every shell, in every directory. Git preserves
the executable bit, so it survives a clone, a `git pull`, a merged PR, and `cs tp`. That is
a path from "a cloud agent wrote a file" to "code ran on Daniel's Mac", and the repository's
own setup instructions are what created it.

Nothing has exploited it. `cloud-sessions/bin/` contains exactly `cs` and `README.md`, both
expected. The hole is structural, not active.

Beyond that: **no API key can bill anything, nothing runs as root, nothing installs
software, nothing touches `pmset`/`caffeinate`, no code path can echo the Supabase token,
and every git gate fails safe.** Four smaller risks are listed below, and two documented
claims are narrower or wronger than they read.

| # | Finding | Verdict |
|---|---|---|
| 1 | `cloud-sessions/bin` is `PATH[0]`, tracked, agent-writable, unguarded | **CRITICAL** |
| 2 | `billing.md`'s "no API key can bill you" does not cover either loader `config.py` uses | **RISK** |
| 3 | A live Hugging Face token sits in `~/.zshrc` and in every agent's environment | **RISK** |
| 4 | `STOP` is a tracked empty **file** in HEAD while the working tree holds the **directory** | **RISK** |
| 5 | `~/.claude/settings.json` is mode `0644` and holds an inline `sbp_` token | **RISK** |
| 6 | `/cloud` hardcodes the repo URL — following it from another checkout dispatches the wrong repo | **RISK** |
| 7 | I10 is advisory; `python3 -c open()` walks through it (`V-023`) | **RISK**, bounded |
| 8 | "A cloud session can reach any repo the GitHub account can see" appears on no pre-dispatch surface | **RISK** |
| 9 | `cs sessions --json` drops the MOVABLE caveat that the human output carries | **RISK**, low |
| 10 | The nine armed routines are recorded as disarmed, but no listing output was ever quoted | **RISK**, unconfirmable from here |

---

## What this audit did NOT do

Stated so nobody reads a gap as a pass.

- **No dispatch.** `cs start`, `cs handoff`, `cs new`, `cs send`, `cs tp`, `cs rc`,
  `claude --cloud` and bare `claude` were never run. Nothing was billed, nothing was
  queued, no rate limit was consumed.
- **No routine was touched.** `RemoteTrigger` was never called. Finding 10 rests on reading
  the written record, as instructed.
- **`.env` was not read.** `/Users/danieltecum/internal-company-tool/.env` exists (184 bytes,
  2026-08-13). The repo's own deny rules forbid reading it, and the only way around them is
  the `V-023` bypass this audit exists to criticise. Bypassing a guard to audit it would
  make the audit the thing to worry about. See finding 2 for what that leaves open.
- **`cs` was under concurrent edit throughout.** It grew from 971 to 1039 lines mid-audit.
  Everything below is pinned to:

```
$ shasum -a256 cloud-sessions/bin/cs
580fe63b39ad00d67f19347c7bdeb92fb8c1074ab50d5405e1903a688def7520
$ bash -n cloud-sessions/bin/cs && echo "cs: syntax OK"
cs: syntax OK
```

Re-check findings 1 and 9 against `cs` after the concurrent lane lands.

---

## 1. Money

### Auth is a subscription, not an API key — `VERIFIED`, **SAFE**

```
$ claude auth status
{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty",
 "email": "dtecum001@gmail.com", "subscriptionType": "max"}
```

`authMethod: claude.ai`, `subscriptionType: max`, `apiProvider: firstParty`. No metered API
bill can be issued against this path. Every redirect variable is unset in this session's
environment:

```
ANTHROPIC_API_KEY unset      CLAUDE_CODE_USE_BEDROCK unset      OPENAI_API_KEY   unset
ANTHROPIC_AUTH_TOKEN unset   CLAUDE_CODE_USE_VERTEX  unset      DEEPGRAM_API_KEY unset
ANTHROPIC_BASE_URL unset     AWS_BEARER_TOKEN_BEDROCK unset     GEMINI_API_KEY   unset
ANTHROPIC_MODEL unset        SUPABASE_MCP_PAT unset             GOOGLE_API_KEY   unset

$ env | grep -Ei '^(ANTHROPIC|OPENAI|DEEPGRAM|CLAUDE_CODE_USE|AWS_BEARER|VERTEX|GEMINI|SUPABASE)'
(none matched)
```

`cs doctor` fails loudly on the first five, which is the right shape: each would silently
redirect billing *and* disable `--cloud`.

### The one condition under which this costs actual dollars

**Usage credits enabled at [claude.ai/settings/usage](https://claude.ai/settings/usage).**

That is the whole boundary, and it is worth stating precisely:

- A cloud session has **no separate compute charge** for the VM (`DOCS`).
- It consumes the **Max subscription rate limit** — the same pool as this session, the
  phone, and routines. Three parallel sessions burn it three times as fast. That is a
  *speed* cost.
- With usage credits **off**, exhausting that limit is a **refusal**. You get told no.
- With usage credits **on**, it becomes **metered overage — real money.**

**I cannot determine whether it is on. Saying so plainly is the honest answer.** The setting
lives in the claude.ai web account; there is no local file, no CLI flag and no API response
that exposes it. `claude auth status` returns subscription type and nothing about credits.
Only Daniel can read that page. Until he does, the correct statement is *"nothing here can
bill unless credits are on, and nobody here knows whether they are"* — not *"nothing can
bill."*

### The two paid-API callers fail closed — `VERIFIED`, **SAFE** with one caveat

```
meeting-recorder/lib/summarize.py:155  def _api_available() -> tuple[bool, str]:
                              :156      if not ANTHROPIC_API_KEY:
                              :157          return False, "ANTHROPIC_API_KEY missing"
                              :185      ok, why = _api_available()      # gate precedes
                              :197      client = anthropic.Anthropic(…) # the call

meeting-recorder/lib/transcribe.py:129  if DEEPGRAM_API_KEY:            # branch is gated
```

Both gate before any network call. **Unreachable from the cloud-sessions path** — `bin/cs`
contains no reference to `meeting-recorder`, imports nothing from it, and `/cloud` lists
meeting capture under "what cannot move". `VERIFIED`.

### Finding 2 — the billing verification is narrower than the claim it supports — **RISK**

[`billing.md`](billing.md) states: *"Is any API key set that could bill separately? **No** —
all unset. `VERIFIED`"*, evidenced by a list of unset shell variables.

**Those constants are not loaded from the shell environment alone.**
[`meeting-recorder/lib/config.py`](../../meeting-recorder/lib/config.py) resolves them
through two fallbacks the verification never exercised:

```python
config.py:37   _load_dotenv(PACKAGE_ROOT.parent / ".env")   # → internal-company-tool/.env
config.py:41   def _get_env_or_zshrc(key: str) -> str | None:
config.py:42       val = os.getenv(key)
config.py:45       zshrc = Path.home() / ".zshrc"           # parses ~/.zshrc directly
config.py:49       match = re.match(rf'^export\s+{key}=…', line)
config.py:75   ANTHROPIC_API_KEY = _get_env_or_zshrc("ANTHROPIC_API_KEY")
config.py:76   DEEPGRAM_API_KEY  = _get_env_or_zshrc("DEEPGRAM_API_KEY")
```

So an `export ANTHROPIC_API_KEY=…` line in `~/.zshrc` bills **even in a shell that never
sourced it**, and a key in the repo-root `.env` bills regardless of the environment. A
`printenv` check cannot see either. What is actually established:

| Source | State | Status |
|---|---|---|
| Shell environment | both keys unset | `VERIFIED` |
| `~/.zshrc` | no `ANTHROPIC_API_KEY`, no `DEEPGRAM_API_KEY` export (grepped) | `VERIFIED` |
| `internal-company-tool/.env` (exists, 184 bytes) | **not inspected — deny rules forbid it** | `UNVERIFIED` |

Net: the two callers almost certainly still fail closed, but the *stated* verification does
not prove it. Daniel settles it in one command:
`grep -c 'ANTHROPIC_API_KEY\|DEEPGRAM_API_KEY' .env` — expected `0`.

Reassuring bound: `.env` is gitignored (`.gitignore:1`) and untracked (`git ls-files .env`
→ 0 entries), so **whatever it holds never reaches a cloud VM.** This is a local-billing
question only.

### Finding 3 — a live Hugging Face token in the environment of every agent — **RISK**

```
$ grep -n TOKEN ~/.zshrc
33:export HF_TOKEN="hf_<REDACTED — 37 chars>"

$ [ -n "$HF_TOKEN" ] && echo SET
HF_TOKEN = SET in this process env (len 37, prefix hf_)
```

It is exported from `~/.zshrc`, so it is live in the environment of **every process this
Mac's shell starts** — Claude Code, `cs`, and every agent's Bash tool, including this one.

Three reasons it belongs in a billing audit:

1. Hugging Face is **named explicitly** in `AGENT.md`'s forbidden-provider rule ("Do not add
   a dependency that calls OpenAI, Gemini, Deepgram, Hugging Face or any hosted model API").
   A credential for that exact provider class is sitting armed in the environment.
2. `billing.md` enumerates `ANTHROPIC`, `OPENAI` and `DEEPGRAM` as unset and concludes *"Is
   any API key set that could bill separately? No — all unset."* **`HF_TOKEN` is set.** The
   conclusion is stated more broadly than the evidence supports.
3. HF Inference Providers meter against the token holder. Nothing bills today, but the
   guardrail is "no code uses it", not "no key exists".

**No repo code reads it** — `VERIFIED`, a repo-wide grep for `HF_TOKEN` / `hf_hub` matched
only market-research JSON where "huggingface" is a company name. So the money risk today is
nil. The *information* risk is not: it leaks into a transcript the moment any agent runs
`env`, and the deny rules protect `**/.env` but nothing protects `~/.zshrc`.

**Recommendation:** move it to a file the deny rules cover, or drop it if unused.

### Finding 10 — the nine armed routines — **RISK**, unconfirmable from here

Read from the record, as instructed; `RemoteTrigger` was not called.
[`billing.md`](billing.md) documents the 2026-08-14 incident: every probe was created as a
one-off, fired via the API, and probes 3–7 were never disabled, leaving five with future
`next_run_at` values (2026-08-17 through 2026-08-21). It then states:

> **All nine are now `enabled: false`**, confirmed by re-listing. No money was spent.

**That is an assertion, not quoted evidence** — and this folder's first ground rule is
*"`VERIFIED` (run here, output quoted)"*. No listing output appears anywhere in
`cloud-sessions/`. The record of the incident is exemplary; the record of the *fix* is the
one claim in it that does not meet the folder's own bar. It is also now a day old, and two
of the original `next_run_at` dates (08-17, 08-18) have still not passed.

Verdict: recorded as disarmed, probably true, **not confirmable from this audit**. Run
`/routines-audit` — it is report-only by default and exists precisely for this.

### Does `/cloud` make recurrence impossible? — mostly, **SAFE**

Yes, as far as an instruction can. [`.claude/commands/cloud.md`](../../.claude/commands/cloud.md)
step 5 is titled **"Dispatch: create → run → disable"** and carries:

- *"**Disable it immediately — same turn, before you report anything**"*
- the reason: *"Firing does not consume `run_once_at`"*, `next_run_at` is decoration
- *"Confirm `"enabled": false` in the response; do not assume it"*
- a read-back step, and — the good one — *"complete create → run → disable **for one
  routine before starting the next**, so a failure partway cannot leave an armed routine
  behind."*

The residual is inherent and unfixable by wording: if the session dies between `run` and
`update`, the routine stays armed. `/routines-audit` is the correct backstop, and it exists.
This is as closed as an instruction-level control gets.

---

## 2. The machine

### `cs shell-init` — **SAFE**, `VERIFIED`

**It only prints.** The whole command is one `cat <<'SH'` heredoc with no redirection
anywhere in the function. Confirmed by hashing `~/.zshrc` around a run:

```
$ cs shell-init > /dev/null
~/.zshrc UNCHANGED (shell-init only prints)
```

**It cannot recurse.** The emitted function resolves the real binary with `whence -p` (zsh)
/ `type -P` (bash), both of which return only a PATH executable and ignore functions and
aliases — the comment in the source calls out exactly why `command -v` would have been the
bug. It then invokes `command "$real" "$@"` with an absolute path. A zsh function is not
exported, so the bash script `cs` never inherits it; `cs new --here`'s `exec "$CLAUDE"`
resolves independently. No cycle exists.

**It parses in both shells:**

```
$ bash -n init.sh && echo "bash: OK"   →  bash: OK
$ zsh  -n init.sh && echo "zsh: OK"    →  zsh: OK
```

**It cannot harmfully shadow.** Only the bare, interactive, argument-free `claude` is
intercepted; anything with a flag, no TTY, or `CLAUDE_CODE_SESSION_ID` set passes straight
through. If no binary is found it returns 127 with a message rather than doing something
clever.

One inherited caveat, not a defect of this function: `whence -p claude` searches `PATH`,
whose first entry is the agent-writable directory in finding 1. That is finding 1's problem,
not `shell-init`'s.

### `cs env set` / `clear` write atomically and preserve unrelated keys — **SAFE**, `VERIFIED`

```python
cs:531   fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p))
cs:532   with os.fdopen(fd, "w") as fh:
cs:…         json.dump(d, fh, indent=2); fh.write("\n"); fh.flush(); os.fsync(fh.fileno())
cs:537   os.replace(tmp, p)
```

Temp file in the same directory, `fsync`, then `os.replace` — atomic on POSIX. **The
original is never truncated**, so a Ctrl-C at any point leaves the previous file wholly
intact. Unparseable input refuses rather than overwriting:

```python
cs:…   except (ValueError, OSError) as e:
           raise SystemExit("cannot read %s: %s\nFix or move that file, then re-run." % (p, e))
```

**Unrelated keys survive** — the code does `json.load` → mutate two keys → `json.dump` of
the whole dict, so `model`, `effortLevel`, `autoUpdatesChannel` and the entire `mcpServers`
block round-trip untouched. `clear` pops only `remote.defaultEnvironmentId` in both its flat
and nested forms.

Residual, minor: an interrupt between `mkstemp` and `os.replace` orphans a `tmp…` file in
`~/.claude/` containing a full copy of the settings — including the token — at mode `0600`.
Not a corruption and not world-readable, but it is litter worth knowing about.

### The Supabase token — no code path can emit it — **SAFE**, `VERIFIED`

`~/.claude/settings.json` carries the token inline as an argv element:

```
/mcpServers/supabase/args[3] = <<REDACTED — 44 chars, prefix sbp_>>
```

Traced every path that touches the file:

| Path | What it prints |
|---|---|
| `settings_env_id()` (`cs:492`) | loads the dict, prints **only** `remote.defaultEnvironmentId` and a layer label. Never the dict |
| `cs env set` | echoes back only the id you passed |
| `cs env clear` | prints a fixed string, or the surviving pin's id |
| `cs env` (show) | the pin and the static network explainer |
| `print(json.dumps(d))` at `cs:481` | **not the settings dict** — `d` is the `CS_EXTRA` metadata built from `cs track`'s own flags (`lane`, `owns`, `brief`, `trigger_id`). Checked because it is new code and looked exactly like a dump |

No path echoes it to stdout, a transcript, or a committed file. The deny rules additionally
forbid `Write`/`Edit` on `.claude/settings.json` (the project one).

### Finding 5 — the file holding it is world-readable — **RISK**

```
$ stat -f "%Sp %z bytes %N" ~/.claude/settings.json
-rw-r--r-- 324 bytes /Users/danieltecum/.claude/settings.json
```

Mode `0644`. Any local user or process can read a live Supabase personal access token. This
predates `cs` and is not caused by it — worth noting that `cs env set` would *improve* it,
since `mkstemp` creates at `0600` and `os.replace` carries that mode over. Fix:
`chmod 600 ~/.claude/settings.json`, and prefer an env-var reference to an inline token.

### Nothing runs as root, installs, or touches power management — **SAFE**, `VERIFIED`

```
$ grep -nE 'sudo|pmset|caffeinate|launchctl|defaults write|systemsetup|brew|apt-get|pip install|npm i|curl|wget|chmod|chown|rm -rf|osascript|security' cloud-sessions/bin/cs
376:  rm -f "$log"
```

One match in 1039 lines, and it is `cs` deleting its own `mktemp` file. No `sudo`, no
`pmset`, no `caffeinate`, no `launchctl`, no installer, no `curl`. The archived July work
did manipulate power assertions; **the current tool does not.** Confirmed independently:
no crontab and no launchd job on this Mac references the repo.

### Every write path in `cs` — **SAFE**, `VERIFIED`

Exhaustive. There are four, and none can touch a file the user did not hand it:

| Write | Target | Destructive? |
|---|---|---|
| `mkdir -p "$STATE_DIR"` (`cs:18`) | `cloud-sessions/state/` | no |
| `record()` (`cs:86`) | **appends** to `state/sessions.jsonl` | no — append-only |
| `cs rm` (`cs:728`) | rewrites the ledger via `mkstemp` + `os.replace` | removes only rows whose `id` matches; **preserves unparseable lines** rather than dropping them |
| `cs env set/clear` | `~/.claude/settings.json`, atomically | no — see above |
| `rm -f "$log"` (`cs:376`) | its own `cs_mktemp` file | no |

**No path deletes or overwrites a user file.** No recursive delete, no glob delete, no
in-place `sed`. `cs tp` refuses on a dirty worktree rather than stashing or discarding.

Injection surface is closed too: `resolve_id` validates against
`^(session|cse)_[A-Za-z0-9]+$` before any id reaches `open_url` or the CLI, so a crafted
"session id" cannot become a shell argument. The GNU `script` branch builds its command with
`printf '%q '`.

### `cs sessions` reads metadata only — **SAFE**, `VERIFIED`

```python
cs:900   # Claude Code stores transcripts under ~/.claude/projects/<cwd mangled>/
cs:903   path = os.path.expanduser(f"~/.claude/projects/{cand}/{sid}.jsonl")
cs:904   if os.path.exists(path):
cs:905       return (time.time() - os.path.getmtime(path)) / 60.0, path
```

`os.path.exists` and `os.path.getmtime` — **the file is never opened**. A repo-wide grep for
transcript reads in `cs` returns only the ledger's own `open()` calls. No transcript content
is read, copied, printed or transmitted. The `--json` output includes the transcript *path*
(and `cwd`), which is a local filesystem path, not content.

### Finding 1 — `cloud-sessions/bin` is `PATH[0]`, tracked, and unguarded — **CRITICAL**

The one real hole. Four facts, each independently verified, that compose into it.

**a. It is the first entry on `PATH`.**

```
$ echo "$PATH" | tr ':' '\n' | nl
     1  /Users/danieltecum/internal-company-tool/cloud-sessions/bin
     2  /Users/danieltecum/.local/bin
     …
    12  /usr/bin
    13  /bin
```

Ahead of `/usr/bin`, `/bin` and Homebrew. **The repository told him to do this**, in three
places:

```
cloud-sessions/README.md:24        echo 'export PATH="$HOME/internal-company-tool/cloud-sessions/bin:$PATH"' >> ~/.zshrc
cloud-sessions/bin/README.md:10    (same)
cloud-sessions/playbooks/01-setup.md:72  (same)
```

**b. Nothing guards it.** `permissions.deny` protects `atlas-os/bin/**`,
`atlas-os/INVARIANTS.md`, `.claude/settings.json`, `atlas-os/telemetry/SCHEMA.md`, `**/.env`
and `meeting-recorder/transcripts/**`. `grep -n "cloud-sessions" .claude/settings.json`
returns **nothing**. Writing there is ordinary, expected, unprompted work in this repo.

**c. Shadowing works exactly as feared.**

```
$ printf '#!/bin/sh\necho "SHADOWED"\n' > $S/git && chmod +x $S/git
$ PATH="$S:$PATH" sh -c 'command -v git; git --version'
/…/shadow/git
SHADOWED: this ran instead of the real binary
```

**d. Git carries the executable bit.**

```
$ git ls-files -s probe-bin
100755 2b663bb2174ea8bf305bbbe43dd3615957158b3f 0	probe-bin
```

Mode `100755` — a committed file arrives executable after a clone, a pull, a checkout or a
merge. No `chmod` needed by anyone.

**The chain.** A cloud session is instructed by both the `cs handoff` brief and `/cloud` to
*"commit and push as you go"* and open a PR. It has `Bash` in `allowed_tools`. It commits
`cloud-sessions/bin/git` (or `python3`, `ls`, `claude`, `gh`) on a `claude/` branch. That
file reaches Daniel's disk by any of three ordinary routes — `cs tp`, which teleport
documents as *"Checking out branch"*; a `git pull` after merging the PR; or simply checking
the branch out to review it. The next time he types that command **in any directory on the
machine**, the file runs. Nothing prompts, because on his Mac this is just a program on
`PATH`.

**Why this is worse than the I10 gap (finding 7).** `atlas-os/bin/**` is at least *stated*
to be human-only, is deny-listed, and — measured today — is executed by no scheduled job on
this machine. `cloud-sessions/bin/` is stated to be nothing, guarded by nothing, and is
wired into every shell Daniel opens.

**Bounds, honestly.** It requires a compromised or confused agent *and* the file reaching
local disk. A PR diff adding an executable to `bin/` is visible to a reviewer. Nothing has
done this: the directory holds only `cs` and `README.md`, both tracked and expected. This is
a structural hole, not an active compromise.

**Fixes, cheapest first.**

1. Add to `permissions.deny`: `Write(cloud-sessions/bin/**)`, `Edit(cloud-sessions/bin/**)`
   plus the `/`-prefixed forms, mirroring the `atlas-os/bin/**` block. Advisory like the
   rest, but it converts a silent write into a refusal for every cooperating agent — which
   was five out of six in probe 7.
2. Better: stop putting a repo directory on `PATH`. Symlink one file —
   `ln -s ~/internal-company-tool/cloud-sessions/bin/cs ~/.local/bin/cs` — and drop the
   `PATH` line from `~/.zshrc` and from the three docs that recommend it. This closes the
   class, not the instance.
3. Either way, add a repo check that `cloud-sessions/bin/` contains only `cs` and
   `README.md`.

---

## 3. Information

### Finding 8 — the blast radius of a GitHub connection is not stated before dispatch — **RISK**

A cloud session can reach **any repository the connected GitHub account can see** — not
merely the one it was dispatched against, and not merely those the Claude GitHub App is
installed on. App installation governs PR webhooks, not session-level access (`DOCS`).

That is the single widest information boundary in the system. Where it appears:

```
cloud-sessions/reference/limits.md:163      "any repository the connecting GitHub account can see"
cloud-sessions/reference/networking.md:211  (same, in a paragraph about proxy scoping)
```

Where it does **not** appear:

```
cloud-sessions/README.md          silent
cloud-sessions/AGENT.md           silent
.claude/commands/cloud.md         silent
cloud-sessions/bin/README.md      silent
```

`cs doctor` — the command whose entire job is to predict what dispatch will do — does not
mention it either. So a user dispatching for the first time reads two `WARN` lines about
uncommitted changes and nothing about the agent's reach across their entire GitHub account.
`/cloud`'s "What cannot move" list is the natural home for the inverse statement: *what
comes along that you did not ask for.* One line on any pre-dispatch surface fixes this.

`gh` scopes today are `'gist', 'read:org', 'repo'`, and `repo` covers private repositories.

### Connectors: `/cloud` handles this correctly — **SAFE**

The documented default is genuinely dangerous — a routine created via the API inherits
**every** connected claude.ai connector, Gmail and Drive included, with no approval prompts,
and any tool from them may be used **including writes** (`VERIFIED 2026-08-14`, four
connectors on a routine whose only job was a git task).

`/cloud` does all three things right:

1. **Passes an explicit list**: `"mcp_connections": []` in the create body.
2. **Warns loudly**, in bold, before the JSON: *"read this before you create anything… a
   routine whose entire job was to touch a git repository came back holding Gmail, Google
   Drive and Google Calendar, unasked."*
3. **Requires a read-back**: *"then read the routine back and report what it actually
   carries… If it picked up Gmail or Drive despite the explicit list, say so — that is a
   finding, not a detail."*

It also correctly pre-empts the false positive: `Claude_Code_Remote` attaches regardless and
is expected. This is the model for how the other findings should be documented.

### Secrets into cloud environments — **SAFE**, with one behavioural residual

No mechanical path exists. `cs` never reads `.env`, `~/.zshrc` or the settings file into a
task, a brief or an environment variable. `cs env set` — despite the name (finding 6b) —
writes an environment **id**, not variables; there is no code in `cs` that can set an env
var on a cloud session at all. `.mcp.json` references `${SUPABASE_MCP_PAT}` /
`${SUPABASE_PROJECT_REF}`, both unset on a VM, so it **fails closed** and no secret travels
(`VERIFIED`, probe 7). `.env` is gitignored and untracked, so it is not in the clone.

`/cloud` states the rule explicitly: *"Cloud environments have **no secrets store**. Never
write a key into a routine prompt, a brief, or an environment variable."*

Two residuals, both behavioural rather than mechanical:

- `cs handoff` embeds `git log -5 --oneline` in the brief. Commit *subjects* travel to the
  routine prompt. Harmless unless a commit subject ever contains a secret.
- `/cloud` step 4 instructs the agent to write `docs/handoff-<slug>.md` capturing *"what
  this conversation already established"* and commit it. Whatever an agent judges relevant
  from a conversation gets pushed. **The repository is private** (`isPrivate: true`,
  `visibility: PRIVATE`), which bounds this considerably.

Minor note: `/cloud` writes briefs to `docs/`, which **does not exist** in this repo. Following
it creates a new top-level directory that `AGENT.md`'s layout rules never mention.

### `state/sessions.jsonl` is tracked — **SAFE** today, worth watching

Read in full: nine rows, every one `mode: tracked`. Each carries a timestamp, a session id,
a repo *basename* (not a path), a branch, a probe title, and a `claude.ai/code` URL. Sample:

```json
{"ts":"2026-08-14T14:55:42-07:00","id":"cse_01FMQZBzegc91HwwxWjSGncc",
 "title":"Probe 7: does I10 hold in cloud sessions? (+ first PR from a cloud session)",
 "repo":"internal-company-tool","branch":"main","mode":"tracked", "url":"…"}
```

**Nothing sensitive.** No filesystem paths, no credentials, no transcript content. Session
ids are not secrets — they address a session on an account you must already be authenticated
to. Committing it is the deliberate 2026-08-14 decision and it is sound.

Two things to keep an eye on, neither a problem now:

- `title` is **unsanitized free text**. `cs handoff` sets `CS_TITLE="handoff: $instruction"`,
  so the first ~52 characters of whatever a user types as a handoff instruction get
  committed. A handoff phrased around a customer name or a credential would land in git
  history. Worth one line in `state/README.md`.
- The new `cs track` flags merge `lane`, `owns`, `brief`, `trigger_id` and `expect_branch`
  into rows via `CS_EXTRA`. `owns` and `brief` are repo-relative **paths**, broadening what
  the committed ledger carries. Still not secrets.

### Finding 7 — I10 is advisory; state the blast radius plainly — **RISK**, bounded

Probe 7 measured it from a cloud VM (`V-023`, `DISPROVEN` as "ENFORCED"): six write attempts
against `atlas-os/bin/**`, five blocked (`Write`, `Edit`, `cp`, `echo >`, `tee`), and

```
python3 -c "open(path,'w').write(...)"   →  SUCCEEDED — guard bypassed
```

with *"no error, no warning, and no interception of any kind"*. The guard matches **tool
names and Bash command prefixes**, not filesystem writes. Anything reaching the filesystem
through a path the matcher does not recognise — an interpreter, a compiled binary, another
language's write call — walks straight through.

**Blast radius, plainly.** A cloud session gets `Bash` in `allowed_tools` (`/cloud` notes
*"`Bash` is required for git"*). With `Bash` it has `python3`. With `python3` it can write
**any file in the repository**, and the deny list is decoration against it. Concretely, a
confused or malicious cloud session could rewrite:

- `atlas-os/bin/**` — the control plane, including `nightly.sh`, `verify_system.sh`,
  `heartbeat.py`, `ledger_hook.py`
- `.claude/settings.json` — the deny rules themselves, and the hook commands
- `atlas-os/INVARIANTS.md`, `atlas-os/telemetry/SCHEMA.md`
- `cloud-sessions/bin/**` — finding 1, which needs no bypass at all

**Two facts materially shrink this, and both should be stated with it:**

1. **A cloud session cannot write to Daniel's disk.** It writes to a VM. Nothing reaches the
   Mac except through a branch he pulls, a PR he merges, or a `cs tp` he runs. Every route
   passes through a git operation he initiates.
2. **Nothing on this Mac executes `atlas-os/bin/` automatically.** Measured today:

```
$ crontab -l
(no crontab)
$ grep -rl "internal-company-tool\|atlas" ~/Library/LaunchAgents/
(no launchd job references the repo)
```

`nightly.sh` is not scheduled. So the nightmare version — a cloud agent rewrites the nightly
job, it fires unattended and spends money — **cannot happen on this machine today.** It
becomes live the moment anyone schedules it.

The correct framing remains the one `verified-facts.md` already uses: *"A gap in enforcement
is not permission."* Every agent that hit the guard stopped. The exposure is what an agent
that does *not* cooperate could do, and the honest answer is: anything in the repo, with the
Mac protected only by the fact that Daniel initiates every transfer.

---

## 4. Surprising behaviour — "nothing obscure"

### `cs new --cloud` on hostile git states — all fail safe, `VERIFIED` — **SAFE**

`cs new --cloud` routes through `cmd_handoff` → `git_gate no`, which runs **before** the
brief is composed and before anything is dispatched. Traced each state in throwaway repos
under `/private/tmp` (`cs` itself was not invoked, per the read-only constraint; these are
the exact git commands `git_gate` runs):

| State | What `git_gate` reads | Outcome |
|---|---|---|
| **Local-only branch** | `git rev-parse --abbrev-ref @{upstream}` → fails | dies: *"branch 'x' has no upstream. Run: git push -u origin x"* — **SAFE** |
| **Fork, fully pushed** | `branch.local-only.remote` → `myfork`; `ahead` → `0` | dies: *"tracks 'myfork', not origin… the VM clones origin and will not see these commits"* — **SAFE**. Note it refuses despite `ahead=0`, which is the subtle case: fully pushed, still wrong |
| **Detached HEAD** | `git rev-parse --abbrev-ref HEAD` → literally `HEAD` | dies: *"detached HEAD. Check out a branch before dispatching."* — **SAFE** |

All three refuse **before** dispatch, with a message naming the fix. `cs doctor` agrees with
the gate rather than contradicting it (the probe-3 defect, fixed). Untracked files count as
dirty, so the gate errs conservative. This is the strongest part of the tool.

### Finding 6 — `/cloud` can dispatch against the wrong repository — **RISK**

`/cloud` step 5 hardcodes the repository in the create body:

```json
"sources": [{"git_repository": {"url": "https://github.com/daniel0tgc/internal-company-tool"}}]
```

Nowhere does it say *derive this from `git remote get-url origin`*. Step 3 is about getting
work pushed, not about which repo the routine targets. So an agent following the command
**literally** from a different checkout — another repo on this Mac, a worktree, a fork —
creates a routine pointed at `internal-company-tool` while reporting success for the repo
the user was actually in. The session then clones the wrong code and executes a brief
written for something else, unattended.

`cs` itself does not have this bug: `git_gate` reads the live `origin` and `cmd_start`
records the real `repo`/`branch`. The defect is specific to the slash command's fixed JSON.

**Fix:** one line in step 5 — *"Set `url` from `git remote get-url origin`; the value below
is this repo's, not a default"* — plus a check that it matches the repo the briefs were
committed to.

Second-order: `cs start --env` is documented for self-hosted `ccpool_…` ids only; passing an
`env_…` id is `UNVERIFIED` and `cs` warns rather than implying an override. And `/cloud`
correctly names `env_01Muitz2g8DLxNUs1K4njJRp` (`kind: bridge`) as **never use it** — a
bridge environment routes work back to this Mac and silently removes the point of the cloud.
Both handled well.

### Names that suggest one thing and do another — **RISK**, low

| Command | Reads as | Actually does | Mitigated? |
|---|---|---|---|
| `cs rm <id>` | stop/delete the session | **only forgets it locally**; the cloud session keeps running and keeps consuming limits | Yes — warns after every run (*"does NOT stop or delete it"*), and `cs help` says *"There is no `cs stop`"* |
| `cs env` | environment **variables** | the cloud **environment id** to dispatch into | Partly. Dangerous next to the standing "no secrets store" warning: a user could reasonably think `cs env set` is how you pass configuration to a session. It is not, and nothing says so |
| `cs sessions` vs `cs ls` | `sessions` → cloud sessions; `ls` → files | **inverted**: `sessions` lists **local** sessions, `ls` lists **cloud** dispatches | No. `cs help` describes both correctly but the names invite the wrong guess |
| `cs start` | start a session here | **dispatches to a cloud VM** | Yes — gated on TTY and clean git, and `cs new` is the documented front door |

None causes data loss. `cs rm` is the one that could waste money by leaving a session
running while the user believes it stopped, and it is also the best-mitigated. Worth
considering `cs forget` as an alias.

### Finding 9 — MOVABLE is caveated for humans and not for machines — **RISK**, low

The human output ends with an explicit disclaimer:

```
MOVABLE means the VM could clone what this session is working on — NOT that moving it
is useful. A session waiting on your decision is movable and pointless to move.
Use /cloud, which reads each transcript and triages intent before dispatching.
```

That is the right warning. **The `--json` branch exits before it:**

```python
cs:958   if ASJSON:
cs:959       print(json.dumps(rows, indent=2)); raise SystemExit(0)
…
cs:972   print("MOVABLE means the VM could clone what this session is working on — NOT that moving it")
```

So a consumer of `cs sessions --json` — i.e. **another agent**, which is the whole reason
`--json` exists — receives `"verdict": "MOVABLE", "detail": "clean, pushed, GitHub origin"`
with no caveat at all. The single most likely misreader of MOVABLE is the one guaranteed
never to see the warning about it.

**Can a user lose work?** Almost certainly not, and it is worth being precise about why
rather than alarming:

- MOVABLE **requires** clean and pushed (`triage()` returns `DIRTY` / `UNPUSHED` /
  `NO-UPSTREAM` otherwise), so by construction there is nothing uncommitted on disk to lose.
- Moving does not stop the original. `/cloud` is explicit: *"Never kill or interrupt a local
  agent; moving work does not stop the original."*

What is actually lost is the **conversation** — `/cloud` states *"a local agent's
conversation cannot be migrated — there is no export, no import, no attach. Only the *work*
moves."* `cs sessions` never says this; it just points at `/cloud`. The realistic bad
outcome is duplicated effort (two agents on one task), which `/cloud` correctly calls the
user's decision.

**Fix:** add `"caveat": "mechanical only; does not mean moving is useful"` to each JSON row,
or a sibling `note` key. One line.

### Finding 4 — the STOP kill switch is a file in git and a directory on disk — **RISK**

`AGENT.md` opens with an all-caps warning that `STOP` **must remain a directory**, because
`rm -f` cannot remove a directory and `verify_system.sh` ends with an unconditional
`rm -f STOP` that deleted the file form three times on 2026-08-13.

The working tree is correct. **Git disagrees with it:**

```
$ ls -ld STOP
drwxr-xr-x  2 danieltecum  staff  64 Aug 14 15:56 STOP     ← directory, correct

$ git ls-files -s STOP
100644 e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 0	STOP    ← tracked as an EMPTY FILE
$ git cat-file -t HEAD:STOP
blob
$ git status --porcelain STOP
 D STOP
```

`e69de29…` is git's empty blob. So `STOP` is committed as a zero-byte **file**, the working
tree replaced it with a directory, and git reports the file as deleted.

**This makes `AGENT.md`'s own V-034 wrong on the facts.** It states:

> Git cannot track an empty directory, so `git ls-files STOP` is empty and every fresh clone
> and cloud session starts with **no kill switch at all**.

`git ls-files STOP` returns **one entry**. A fresh clone does not start with no kill switch
— it starts with the **self-deleting file kill switch**, the exact mode the warning block
exists to prevent. `nightly.sh`'s `[ -e ]` test does halt on it, but `verify_system.sh`'s
`rm -f STOP` and `atlas go` silently remove it. The rejected fix (`STOP/.gitkeep` breaking
`rmdir`) is moot: a plain empty `STOP` is already committed.

**Two live consequences on this Mac:**

1. Any `git checkout -- STOP`, `git restore .`, `git stash`, or branch switch touching it
   will **restore the empty file over the directory** — git can do this because the
   directory is empty — silently downgrading a working kill switch to the broken one. No
   message, no prompt.
2. A `git add -A && git commit` of the current working tree records `STOP` as **deleted**,
   removing the kill switch from the repository for every future clone.

**Bounded by finding 7's measurement:** no crontab, no launchd job, `nightly.sh` unscheduled.
Nothing is currently gated on `STOP`, so the practical impact today is low. It is filed as a
RISK because it is a safety mechanism that is quietly not in the state its own documentation
believes, and because `AGENT.md` is where the next agent will go for the truth.

**This is `atlas-os/`-adjacent and I10-guarded — a founder decision, not an agent fix.** The
call is between committing a `STOP` that survives a clone and keeping `rmdir STOP` working.
Recorded here rather than acted on. Tracked as `V-033`/`V-034`.

---

## Summary of recommended actions

Ordered by value, none applied by this audit.

| # | Action | Why |
|---|---|---|
| 1 | Deny-list `cloud-sessions/bin/**`, or symlink `cs` into `~/.local/bin` and drop the `PATH` line from `~/.zshrc` and the three docs recommending it | Closes the only path from an agent write to code execution on the Mac |
| 2 | `grep -c 'ANTHROPIC_API_KEY\|DEEPGRAM_API_KEY' .env` — expected `0` | Settles the one billing question this audit could not, without violating a deny rule |
| 3 | Check **usage credits** at [claude.ai/settings/usage](https://claude.ai/settings/usage) | The single condition under which any of this costs dollars. Only Daniel can see it |
| 4 | Run `/routines-audit` (report-only) | Confirms the nine disarmed routines with quoted output, which the record lacks |
| 5 | Move `HF_TOKEN` out of `~/.zshrc`, or delete it | Forbidden-provider credential, live in every agent's environment, unprotected by the deny rules |
| 6 | `chmod 600 ~/.claude/settings.json` | World-readable file holding a live `sbp_` token |
| 7 | Correct `AGENT.md`'s V-034 and decide `STOP`'s committed form | The documented fact is measurably wrong; founder decision |
| 8 | `/cloud` step 5: derive the repo URL from `git remote get-url origin` | Prevents dispatching the wrong repository |
| 9 | State the "any repo the GitHub account can see" reach on a pre-dispatch surface | Widest information boundary; currently only in two reference files |
| 10 | Add the MOVABLE caveat to `cs sessions --json` rows | The machine consumer is the likely misreader and never sees the warning |

---

## Redaction note

Two live credentials were encountered and are recorded here only by prefix and length:
the Hugging Face token in `~/.zshrc` (`hf_`, 37 chars) and the Supabase personal access
token in `~/.claude/settings.json` (`sbp_`, 44 chars). Neither value appears in this file.
`internal-company-tool/.env` was deliberately not opened.
