# Networking — how a cloud session actually reaches the internet

The single answer to "does a cloud session have internet?" It does **not**, in the sense
people mean. It has **two separate outbound paths with different rules**, plus **two
bypasses that skip both**, and the default posture is an allowlist that refuses an
arbitrary host outright.

Status tags per [`../AGENT.md`](../AGENT.md): `VERIFIED` (run in a real VM, output quoted
below) · `DOCS` (Anthropic's documentation, not exercised here) · `UNVERIFIED` ·
`DISPROVEN`. Measurements are from probes 1 and 2 on **2026-08-14**, both at network level
**Trusted** (the account default). No probe has yet measured a VM at None, Custom or Full.

---

## If you need X, do Y

| If you need | Do this | Status |
|---|---|---|
| `pip` / `npm` / `cargo` / `go` installs | Nothing. Those registries are on the **direct** list and never touch the allowlist | `VERIFIED` |
| clone, push, `gh pr create` on the repo the session is attached to | Nothing. GitHub rides a **dedicated proxy**, not the allowlist | `VERIFIED` (`api.github.com` → 200; PR opened from a VM in probe 7) |
| the agent's own API calls | Nothing. `*.anthropic.com` is on the direct list, and the API is reachable even at **None** | `VERIFIED` (direct list) + `DOCS` (None) |
| Gmail, Drive, Slack, or any other connector | Use an **MCP connector**. That traffic routes via Anthropic's servers and never hits the allowlist | `DOCS` |
| to call your own API, scrape a page, or hit a SaaS endpoint | **Custom** environment listing the domain, or **Full**. Web-only to create → then `cs env set <id>` | `DOCS` |
| to fetch from a GitHub repo **not** attached to the session | Attach it, or list its host under Custom. The GitHub proxy is scoped to attached repos | `DOCS` |
| to find out why a request "hung" | It is a 403 with the reason in the **body**. Retrieve it with the raw-socket `CONNECT` below — `curl` hides it | `VERIFIED` |
| `apt-get update` to stop printing 403 warnings | Nothing. Two preinstalled PPAs are blocked and always will be. They are harmless | `VERIFIED` |
| a guarantee that nothing leaves the VM | **None does not give you this.** The Anthropic API stays reachable, so data can still leave | `DOCS` |

---

## The mechanism: two paths, not one

This is the part nobody realises. Outbound traffic does not all go through one place.

| | **DIRECT path** | **PROXIED path** |
|---|---|---|
| How a request lands on it | its host is in `no_proxy` | everything else |
| Route | straight out of the VM | `CONNECT` tunnel through a local HTTP proxy |
| Who decides yes/no | nobody — the allowlist never sees it | the proxy's allowlist |
| A refusal looks like | an ordinary DNS/TCP error | **HTTP 403 at the CONNECT stage**, reason in the body |
| Changed by a Custom allowlist | no | yes |

Both were measured in probe 1 (`VERIFIED`):

| Host | Result | Path |
|---|---|---|
| `https://pypi.org` | **200** | direct, per `no_proxy` |
| `https://api.github.com` | **200** | proxied and allowed |
| `https://example.com` | **403** (CONNECT tunnel failed) | proxied and blocked |

### The direct list — `VERIFIED`

Observed in probe 1 via `curl -v`. `no_proxy` pre-allows, for **direct** (non-proxied)
connection:

```
pypi.org  files.pythonhosted.org  registry.npmjs.org  jsr.io
index.crates.io  proxy.golang.org  *.anthropic.com
```

Package registries are therefore reached *outside the proxy's filtering logic entirely*.
That is why a setup script's `pip install` and `npm ci` work at Trusted without anyone
allowlisting anything — and why they would keep working even if the allowlist were
tightened. `*.anthropic.com` being on this list is also the mechanical reason the agent
itself keeps running when egress is restricted.

### The proxy — `VERIFIED`, but do not hardcode the port

Probe 1 observed the outbound proxy at `http://127.0.0.1:46101`. Probe 2, on a different
VM the same day, reached it at `127.0.0.1:46269`. **Two VMs, two ports.** Read it from the
environment rather than copying a number out of a probe report:

```bash
env | grep -i proxy      # http_proxy / https_proxy / no_proxy
```

Whether the port is randomised per VM, per session, or rotates on some other basis is
`UNVERIFIED` — only that it is not stable across VMs.

### The third path nobody has measured — `UNVERIFIED`

The proxy is wired up through the `http_proxy` / `https_proxy` / `no_proxy` environment
variables. A client that **ignores** those variables — a raw socket, some Go and Java HTTP
stacks, anything with a custom dialer — is on neither path. Whether such a client reaches
the host, is blocked at a lower layer, or hangs has **not been measured**. If a
non-`curl` client behaves differently from what this file describes, that is the first
thing to check.

Likewise `UNVERIFIED`: whether the proxy passes anything other than HTTPS `CONNECT` on
:443. Allowlisting a domain under Custom adds it to the **proxy's** rules, not to
`no_proxy` — so `git+ssh`, non-443 ports, and long-lived websockets to a custom-allowed
host are all untested.

---

## The four access levels

Set per **cloud environment**, applying to every session that environment starts —
`cs start`, the web, mobile, routines, Slack. Levels and their descriptions are `DOCS`
(Anthropic, as of 2026-08-12); the *behaviour of Trusted* is `VERIFIED`.

| Level | Reaches | Status |
|---|---|---|
| **None** | nothing outbound. The Anthropic API is still reachable — **so data can still leave the VM** | `DOCS` |
| **Trusted** *(default)* | an **allowlist**: package registries, GitHub, cloud SDKs, container registries | `DOCS`, allowlist behaviour `VERIFIED` |
| **Custom** | exactly the domains you list, optionally plus the Trusted list | `DOCS` |
| **Full** | unrestricted | `DOCS` |

### "Trusted" is not "has internet" — `VERIFIED`

Trusted is an **allowlist, not a denylist**. Named hosts are permitted; an arbitrary host
is refused outright. `example.com` — about as innocuous as a hostname gets — is a `403`.

A session that needs to call your own API, scrape a page, hit a SaaS endpoint, or fetch a
release asset from a random domain **will be refused by default**, and (see below) the
refusal does not look like a refusal. Plan the allowlist before the run, not after the
failure.

Which hosts beyond the three measured ones are on the Trusted allowlist is `UNVERIFIED`.
"Cloud SDKs and container registries" is Anthropic's description (`DOCS`); nobody here has
enumerated it. Do not assume a host is covered because it feels like infrastructure.

---

## What a block actually looks like

**The deny signal is a plain-text 403 body. There is no header.** The
`x-deny-reason: host_not_allowed` header this folder once documented is `DISPROVEN` —
probe 1 found it absent, probe 2 re-confirmed independently with a fresh raw-socket
implementation. `bin/cs` was instructing every dispatched session to look for a header
that does not exist.

The full response, retrieved by hand (`VERIFIED`, probe 1, re-confirmed probe 2):

```
HTTP/1.1 403 Forbidden
Content-Type: text/plain; charset=utf-8
X-Content-Type-Options: nosniff
Content-Length: 69

request blocked: no rule or allowlist entry allows host "example.com"
```

### Why it presents as a hang — `VERIFIED`

`curl` treats a non-2xx response to `CONNECT` as a fatal tunnel failure and **discards the
body before you can see it**:

```
$ curl -sS -o body.txt -w "HTTP_CODE=%{http_code}\n" https://example.com
curl: (56) CONNECT tunnel failed, response 403
HTTP_CODE=000
(body.txt was never created — curl swallowed it)
```

`curl -v` shows the negotiation and a bare `403` status line — `Content-Type`,
`X-Content-Type-Options`, `Content-Length: 69` — and still no body. So the single most
informative string in the whole system, the one naming the exact host to allowlist, is
invisible to the tool everyone reaches for first. **A reader who stops here concludes the
remote service is down.** It is not; the environment refused it.

This is the folder's fourth ground rule in the wild: a degraded condition reading as a
meaningless value.

### The command that does reveal it — `VERIFIED`

Open a raw socket to the proxy and issue `CONNECT` yourself. Substitute the port from
`env | grep -i proxy`, and the host you were actually trying to reach:

```bash
python3 - <<'PY'
import socket
s = socket.create_connection(("127.0.0.1", PORT), timeout=10)   # PORT: from `env | grep -i proxy`
s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
print(s.recv(4096).decode())
PY
```

That is the construct probe 2 used (verbatim apart from the port and the final `print`),
and it is the only way anyone here has read the deny reason. Two probes wrote it
independently and got the same body.

**When you hit this in a session:** do not work around it. Record every blocked host with
the command that triggered it and list them in a "Blocked network access" section of the
PR body, so the allowlist gets fixed once instead of being rediscovered. That instruction
is already baked into the `cs handoff` brief (`bin/cs`).

---

## What bypasses the allowlist entirely

Two things do not go through the network level at all, which is why they keep working in
an environment that refuses everything else.

**1. MCP connector traffic** — `DOCS`. Routed via Anthropic's servers, so connectors work
without adding any host to the allowlist. The corollary is a security one, and it is
`VERIFIED` for routines: every connected claude.ai connector is attached to a routine by
default, including Gmail and Drive, with no approval prompts — and any tool from an
attached connector can be called, **including writes**. A locked-down network level does
not constrain this. Pass an explicit `mcp_connections` list.

**2. GitHub traffic** — `DOCS` for the mechanism (a dedicated proxy), with the effect
`VERIFIED`: `api.github.com` returned 200 through the proxy in probe 1, and probe 7 pushed
a branch and opened the first PR from a cloud VM. The scoping is the part that bites:
the GitHub proxy is **scoped to repositories attached to the session**. A setup script
fetching release assets from an unattached repo gets a `403` (`DOCS`).

The concrete prediction nobody has run yet, `UNVERIFIED`: `twenty/` in this repo is an
uninitialized gitlink, so `git submodule update --init` would fetch `twentyhq/twenty`,
which is *not* attached to the session — expected to 403. Reasoned, not measured. Related
and separate: a cloud session can reach **any repository the connecting GitHub account can
see**, not merely those the Claude GitHub App is installed on — App installation governs
PR webhooks, not session-level access (`DOCS`).

---

## How to actually get open internet

There is **no CLI and no API for creating or editing environments** — it is web-only
(`DOCS`). `cs env` prints these steps and the level table on demand (`VERIFIED`, exit 0
inside a VM in probes 3 and 5).

1. Open **https://claude.ai/code**
2. **Add cloud environment**, or hover an existing one and click the settings icon
3. Set **Network access** to **Custom** (list your domains, one per line, and tick
   *"Also include default list of common package managers"* unless you genuinely want only
   your list) — or **Full** for unrestricted
4. Back in a terminal:

```bash
cs env set <the-new-environment-id>   # writes remote.defaultEnvironmentId to USER settings
cs env clear                          # back to the account default
cs env                                # show the current pin + the level explainer
```

`cs env set` writes to **user** settings, so the pin applies in every project on the
machine. Per-dispatch override without changing the default:
`cs start --env <id> "…"`. `/remote-env` inside a session does the same with a picker, and
likewise cannot create or edit an environment.

**Wildcard semantics:** a leading `*.` matches all subdomains — `*.example.com` covers
`api.example.com`, `cdn.example.com` (`DOCS`). Whether it *also* matches the bare apex
`example.com` is `UNVERIFIED`; list the apex separately rather than find out mid-run.

Two things to know before you widen the network:

- **Changing the allowed hosts re-runs the setup script** and rebuilds the environment
  cache (`DOCS`). Expect the next session to be slower.
- **There is no secrets store.** Environment variables and setup scripts are readable by
  anyone who uses the environment. Widening egress and storing a key in the same place is
  how a credential leaves. Keep credentialed work out of cloud sessions entirely.

Self-hosted environments (`--environment ccpool_…`, runners inside your own network) are
Team/Enterprise only and **not available on this Max account** (`DOCS`).

---

## The practical failure that will bite you first

**Two APT PPAs 403 on every `apt-get update`** — `VERIFIED`, probe 1 and probe 2, on both
a first and an immediately-repeated run:

```
W: Failed to fetch https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu/dists/noble/InRelease  Invalid response from proxy: HTTP/1.1 403 Forbidden ...
W: Failed to fetch https://ppa.launchpadcontent.net/ondrej/php/ubuntu/dists/noble/InRelease  Invalid response from proxy: HTTP/1.1 403 Forbidden ...
W: Some index files failed to download. They have been ignored, or old ones used instead.
```

Both PPAs are **preinstalled on the VM image** — nothing in this repo added them — and both
are outside the Trusted allowlist. `apt-get` continues on the main Ubuntu archive and
installs fine: `gh` 2.45.0 in 15 seconds, whole setup script ~19 seconds, exit 0, and
idempotent on a second run.

**They are harmless and they are not fixable from here.** The only cost is that a perfectly
healthy setup script prints two red-looking failures every time, which is exactly the
shape of thing that gets someone debugging the wrong problem for an hour. Read past them.

---

## The cloud VM rewrites GitHub URLs — `VERIFIED 2026-08-16`

Not a network policy exactly, but it lives in the same proxy and it will confuse you.

The VM's agent proxy injects git config **per invocation**, through the environment rather
than any config file:

```
GIT_CONFIG_COUNT=3
GIT_CONFIG_KEY_1=url.https://github.com/.insteadOf   VALUE_1=git@github.com:
GIT_CONFIG_KEY_2=url.https://github.com/.insteadOf   VALUE_2=ssh://git@github.com/
```

So **every** scp-style or `ssh://` GitHub remote is silently rewritten to `https://` for
every git command, including `git remote get-url`:

```
$ git remote add origin git@github.com:example/gate-ssh.git
$ git remote get-url origin
https://github.com/example/gate-ssh.git
```

It is in none of `~/.gitconfig`, `/etc/gitconfig`, or the repo's own config — searching
those finds nothing, which is what makes it hard to diagnose.

**Why it exists:** the GitHub proxy authenticates over https with scoped credentials, so an
SSH remote would bypass the credential broker entirely. Rewriting is how a session can be
handed a repo without ever holding an SSH key.

**What it breaks:** anything asserting the literal text of a GitHub remote. It found one
check in this repo's own suite, which now skips loudly instead of failing
(`gate/ssh-origin-fixture-really-is-ssh`). The gate's actual *behaviour* with an SSH-style
origin was unaffected and its three behavioural checks passed.

## Open questions

| Question | Status | What would settle it |
|---|---|---|
| What else is on the Trusted allowlist beyond `api.github.com` and the direct-list registries | `UNVERIFIED` | Probe a spread of hosts (a cloud SDK endpoint, a container registry) from a Trusted VM |
| Whether `no_proxy` is trimmed at network level **None** | `UNVERIFIED` — docs imply the Anthropic API survives; the rest is untested | Dispatch one session into a None environment and dump `env` |
| Whether a client that ignores `*_proxy` env vars reaches anything | `UNVERIFIED` | Raw-socket a non-allowlisted host directly, bypassing the env vars |
| Whether a Custom-allowed host works on non-443 ports or over ssh/websockets | `UNVERIFIED` | Allowlist a host and try `git+ssh` and a non-443 port |
| Whether `*.example.com` matches the apex `example.com` | `UNVERIFIED` | Allowlist only the wildcard, then request the apex |
| Whether an unattached GitHub repo really 403s (the `twenty/` submodule case) | `UNVERIFIED` — reasoned from the documented scoping | `git submodule update --init` in a cloud session |
| Whether the agent's own `WebFetch` / `WebSearch` tools are subject to the allowlist or route via Anthropic | `UNVERIFIED` | `WebFetch` a non-allowlisted host from inside a Trusted VM |
| Whether the proxy port is randomised per VM or per session | `UNVERIFIED` — only that it differed between two VMs | Dump `env \| grep -i proxy` across several dispatches |

---

## Where the evidence is

Everything above was measured on **2026-08-14** from inside real cloud VMs, across three
probes. The probe write-ups themselves were dated evidence about a *different* repository
and are no longer carried here (they remain in git history); what they established is
stated inline in the tables above rather than cited. Specifically:

- **Probe 1** established the proxy address, the `no_proxy` list, the three-host result
  table, the raw-socket 403 body, the PPA warnings, and the `x-deny-reason` disproof.
- **Probe 2** independently re-confirmed all of it on a second VM (with a different proxy
  port), captured the exact `curl` swallow-the-body output, and showed setup-script
  idempotency with the same two PPA 403s.
- **Probe 7** opened the first PR from a cloud VM — the GitHub path working end to end.

**Trusted is sufficient for this repository.** Its outbound needs are GitHub, package
registries, and the Supabase/Trigger.dev relay endpoints the pipeline calls; there is no
scraping or arbitrary-SaaS traffic in the S1→S10 path. If a call does get refused, the 403
body names the host — add that host to a **Custom** environment rather than switching to
**Full**.

- [`limits.md`](limits.md) and [`../playbooks/06-environments.md`](../playbooks/06-environments.md)
  — the surrounding quota and environment-configuration context.

Sources for the `DOCS` claims: [Cloud environments](https://code.claude.com/docs/en/cloud-environments)
· [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web).
