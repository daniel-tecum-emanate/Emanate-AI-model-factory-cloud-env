# reference/ — what is true about a thing

> **Provenance note, 2026-08-18:** this folder used to also hold seven dated cloud-VM probe
> reports, an evidence record (`verified-facts.md`), a gap register (`OPEN-GAPS.md`),
> `billing.md`, and a set of dated audits. All of it was written and verified against a
> **different repository**, `daniel0tgc/internal-company-tool`, and was bulk-committed here
> by mistake. It was archived out of this folder and has since been removed from this repo
> entirely, as part of trimming this repo down to the fine-tuning harness. It stays
> recoverable from git history. Nothing below this note has been verified against *this*
> repo specifically; the five files that remain describe the `cs`/`cloud-sessions`
> mechanism itself (account-level, not repo-specific), corrected for this repo's real
> identity where they named the other one.

Claims, limits, matrices, error tables, and competitor architecture. How to *do* a thing
goes in [`../playbooks/`](../playbooks/README.md) instead. Dated probe reports and the
evidence record are gone from this folder for now (see the note above) — a fresh
`verified-facts.md` belongs here once real dispatches happen against this repo (T0-3
onward).

**Every claim in this folder carries a status tag**, per ground rule 1 in
[`../AGENT.md`](../AGENT.md): `VERIFIED` (run here, output quoted) · `DOCS` (stated by
Anthropic's documentation, not exercised here) · `UNVERIFIED` (believed, untested) ·
`DISPROVEN`. *"It should work"* is not a status.

**Where this folder sits:** `cloud-sessions/` keeps **exactly two files at its root** —
`AGENT.md` and `README.md`. Everything else is in a subfolder, and a new document belongs
either in `playbooks/` (how) or here (what is true). A file dropped at the folder root
belongs to neither category and escapes both obligations.

**Adding a file here?** It needs a row in this index the same hour it lands. Verify with a
live listing, not from memory:

```bash
cd cloud-sessions/reference && ls -1 *.md
```

---

## What is here now

| File | The question it answers |
|---|---|
| [`surfaces.md`](surfaces.md) | *Where can a session run, and what does each place survive?* The matrix, plus what was deliberately evaluated and **rejected** (the €16–24/mo VPS, GitHub Actions at ~$259/mo). Pick by the first row true of your situation |
| [`limits.md`](limits.md) | *What will run out, expire, or get blocked?* Quotas, costs, session lifetime, network, bundle size, setup scripts, routine limits, and hard blockers — `DOCS`-tagged claims not yet re-verified against this repo |
| [`troubleshooting.md`](troubleshooting.md) | *This error — what does it actually mean, and what fixes it?* Error text → meaning → fix, grouped by dispatch, steering, teleport, Remote Control, routines, and `cs`'s own refusals |
| [`networking.md`](networking.md) | *How does a cloud session reach the internet?* The direct path vs. the proxied/allowlisted path, what needs which, and how to read a refusal that looks like a hang |
| [`how-others-build-this.md`](how-others-build-this.md) | *How do Cursor, Codex, Devin, Jules, Copilot, OpenHands and the sandbox vendors architect hosted agent sessions — and what is worth borrowing?* All `DOCS` — read, not exercised |

Verify this table against disk before trusting it: `ls -1 cloud-sessions/reference/*.md`
should show exactly these five files plus this index.

## When you add something

- **New claim, verified against this repo** → a new `verified-facts.md` here, with the
  status tag and the actual command output. Start fresh and cite it as this repo's own
  evidence; do not go digging the old one out of git history — it was about a different
  repository, and reviving it would reintroduce exactly the confusion it caused.
- **New failure mode** → [`troubleshooting.md`](troubleshooting.md), as *error text → what
  it actually means → fix*.
- **New capability** → also a row in [`surfaces.md`](surfaces.md), and a numbered playbook
  in [`../playbooks/`](../playbooks/README.md).
- **A wrong claim** → marked and corrected in place, with what corrected it. Never
  deleted.

---

This index is itself `cloud-sessions/reference/README.md` and does not list itself among
its own rows.
