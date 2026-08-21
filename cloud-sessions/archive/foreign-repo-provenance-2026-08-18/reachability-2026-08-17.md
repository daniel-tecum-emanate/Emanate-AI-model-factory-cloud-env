# Reachability audit — cloud-sessions/reference/ (2026-08-17)

**Dated evidence for 2026-08-17.** `VERIFIED` throughout: every finding below comes from
`grep -rn` commands run directly against the repository tree at commit `f144b17` (the
commit this worktree was checked out from), quoted or summarized below. This file is a
reachability audit only — it does not edit or assess any factual claim inside another
`cloud-sessions/reference/` file, and it does not judge whether `README.md`'s own index
table is complete (that is `lane-refidx`'s job, tracked separately).

**Method.** For each `.md` file in `cloud-sessions/reference/` (listed live via `ls`, not
assumed), searched the whole repository for markdown-link syntax — `[...](...)` — whose
target resolves to that file, via commands of the form:

```
grep -rn '\](.*<filename>' --include=*.md .
```

run from the repo root, plus a same-directory pass inside `cloud-sessions/reference/`
itself (`grep -n '\](.*<filename>' *.md`) to catch cross-links between reference files
using bare relative targets (e.g. `verified-facts.md` linking a probe report with
`[...](cloud-vm-probe-2026-08-14.md)`). A bare filename mention in prose or inside a
backtick code span (no enclosing `[...](...)`) does **not** count as a link. Special care
was taken with `README.md` because the filename is generic and collides with unrelated
`README.md` files elsewhere in the repo (including `atlas-os/reference/README.md`, whose
own table links `reference/README.md` meaning *itself*, not this folder) — those
false-positive matches were excluded by hand after inspecting each hit.

## Summary

- **Total files checked:** 21
- **Total orphans:** 3
- **Orphans:**
  - `linux-verification-2026-08-15.md`
  - `safety-audit-2026-08-15.md`
  - `subagents-in-cloud-2026-08-16.md`

All three are referenced only as bare filenames inside backtick code spans — in
`atlas-os/heartbeat/STATE.md` (heartbeat board rows naming an "owned path") and, for
`linux-verification-2026-08-15.md`, also in `docs/handoff-suite-linux-462.md` — never
inside `[...](...)` markdown link syntax. No index, playbook, or sibling reference file
links to any of the three.

## Per-file detail

### `OPEN-GAPS.md`
Linked from: [`gap-closure-2026-08-16.md`](gap-closure-2026-08-16.md) (`` [`OPEN-GAPS.md`](OPEN-GAPS.md) ``, line 4).
Not linked from `README.md`'s own table, `cloud-sessions/README.md`, `cloud-sessions/AGENT.md`,
or any file under `cloud-sessions/playbooks/`.

### `README.md` (this folder's own index)
Linked from: [`../README.md`](../README.md) (lines 80, 103) and [`../AGENT.md`](../AGENT.md) (line 147).

### `billing.md`
Linked from: `` [`OPEN-GAPS.md`](OPEN-GAPS.md) `` (multiple), `` [`safety-audit-2026-08-15.md`](safety-audit-2026-08-15.md) `` (x2, internal cross-links), plus externally from
`../README.md`, `../AGENT.md`, `autonomy/00-status/README.md`, `autonomy/00-status/REGISTRY.md`,
`autonomy/00-status/current.md`, `autonomy/05-dispatch/README.md` (x3), `atlas-os/tests/process/README.md`,
and `.claude/commands/routines-audit.md` (x2).

### `cloud-vm-probe-2-2026-08-14.md`
Linked from: [`README.md`](README.md) (probe table, row 2) and [`networking.md`](networking.md) (line 334).

### `cloud-vm-probe-2026-08-14.md`
Linked from: [`README.md`](README.md) (probe table, row 1), [`cloud-vm-probe-2-2026-08-14.md`](cloud-vm-probe-2-2026-08-14.md),
[`limits.md`](limits.md) (x2), [`networking.md`](networking.md), [`this-repo-in-the-cloud.md`](this-repo-in-the-cloud.md),
[`verified-facts.md`](verified-facts.md), plus externally [`../playbooks/06-environments.md`](../playbooks/06-environments.md) (line 67).

### `cloud-vm-probe-3-2026-08-14.md`
Linked from: [`README.md`](README.md) (probe table, row 3) only.

### `cloud-vm-probe-4-2026-08-14.md`
Linked from: [`README.md`](README.md) (probe table, row 4) only.

### `cloud-vm-probe-5-2026-08-14.md`
Linked from: [`README.md`](README.md) (probe table, row 5) only.

### `cloud-vm-probe-6-2026-08-14.md`
Linked from: [`README.md`](README.md) (probe table, row 6) only.

### `cloud-vm-probe-7-2026-08-14.md`
Linked from: [`README.md`](README.md) (probe table, row 7) and [`networking.md`](networking.md) (line 337).

### `gap-closure-2026-08-16.md`
Linked from: [`verified-facts.md`](verified-facts.md) (line 638) only. Not linked from `README.md`'s
table, `cloud-sessions/README.md`, `cloud-sessions/AGENT.md`, or any playbook.

### `how-others-build-this.md`
Linked from: [`README.md`](README.md), [`limits.md`](limits.md) (line 108), plus externally
[`../README.md`](../README.md) (line 101).

### `limits.md`
Linked from: [`README.md`](README.md), [`how-others-build-this.md`](how-others-build-this.md),
[`networking.md`](networking.md), [`surfaces.md`](surfaces.md), plus externally [`../README.md`](../README.md) (line 96),
[`../playbooks/02-dispatch.md`](../playbooks/02-dispatch.md) (line 155), [`../playbooks/06-environments.md`](../playbooks/06-environments.md) (line 16),
and [`this-repo-in-the-cloud.md`](this-repo-in-the-cloud.md) (line 58, self-referential context aside — cited as an
inbound link target from that file).

### `linux-verification-2026-08-15.md` — **ORPHAN**
No `[...](...)` markdown link to this file exists anywhere in the repository. It is named
only as a bare backtick path in `atlas-os/heartbeat/STATE.md` (line 37, a heartbeat board
row) and in `docs/handoff-suite-linux-462.md` (lines 4 and 35, prose describing what a lane
owns/produces) — neither is markdown-link syntax. Not present in `README.md`'s table,
`cloud-sessions/README.md`, `cloud-sessions/AGENT.md`, or any playbook.

### `networking.md`
Linked from: `` [`OPEN-GAPS.md`](OPEN-GAPS.md) `` (line 636, `` [`networking.md:284-291`](networking.md) ``),
plus externally [`../README.md`](../README.md) (line 99). Not present in `README.md`'s own table.

### `porting-audit-2026-08-17.md`
Linked from: externally [`../AGENT.md`](../AGENT.md) (line 16) only. No internal cross-links found
within `cloud-sessions/reference/`. Not present in `README.md`'s own table.

### `safety-audit-2026-08-15.md` — **ORPHAN**
No `[...](...)` markdown link to this file exists anywhere in the repository (checked
whole-repo and within `cloud-sessions/reference/` itself). Not present in `README.md`'s
table, `cloud-sessions/README.md`, `cloud-sessions/AGENT.md`, or any playbook. Note:
`atlas-os/heartbeat/STATE.md` line 32 names a *differently-dated* file,
`` `cloud-sessions/reference/safety-audit-2026-08-14.md` `` (2026-08-14, one day off, in a
backtick code span, not a link) — that path does not exist in this folder's current file
listing, so it is neither a link nor evidence against this file's orphan status; flagged
here only for visibility, not corrected (out of scope — this report does not edit other
files).

### `subagents-in-cloud-2026-08-16.md` — **ORPHAN**
No `[...](...)` markdown link to this file exists anywhere in the repository. Not present
in `README.md`'s table, `cloud-sessions/README.md`, `cloud-sessions/AGENT.md`, or any
playbook.

### `surfaces.md`
Linked from: [`README.md`](README.md) (x2), [`billing.md`](billing.md) (line 188),
[`how-others-build-this.md`](how-others-build-this.md) (x3), [`verified-facts.md`](verified-facts.md) (line 950),
plus externally [`../README.md`](../README.md) (x2, lines 95 and 128) and [`../playbooks/README.md`](../playbooks/README.md) (line 21).

### `this-repo-in-the-cloud.md`
Linked from: [`README.md`](README.md), [`how-others-build-this.md`](how-others-build-this.md) (x2),
[`limits.md`](limits.md) (line 58), [`networking.md`](networking.md) (line 341), [`verified-facts.md`](verified-facts.md) (line 312),
plus externally [`../README.md`](../README.md) (line 102), [`../AGENT.md`](../AGENT.md) (line 129), and
[`../playbooks/07-making-cloud-the-default.md`](../playbooks/07-making-cloud-the-default.md) (line 216).

### `troubleshooting.md`
Linked from: [`README.md`](README.md) (x2), [`verified-facts.md`](verified-facts.md) — via forward reference —
plus externally [`../README.md`](../README.md) (line 100) and [`../AGENT.md`](../AGENT.md) (line 140).

### `verified-facts.md`
Linked from: [`README.md`](README.md) (x3), `` [`OPEN-GAPS.md`](OPEN-GAPS.md) `` (extensively), all seven
`cloud-vm-probe-*.md` reports, [`limits.md`](limits.md), [`surfaces.md`](surfaces.md),
[`this-repo-in-the-cloud.md`](this-repo-in-the-cloud.md), [`troubleshooting.md`](troubleshooting.md),
[`porting-audit-2026-08-17.md`](porting-audit-2026-08-17.md), [`networking.md`](networking.md),
[`how-others-build-this.md`](how-others-build-this.md), plus a large number of external files:
`../README.md`, `../AGENT.md`, `../tests/README.md`, `../state/README.md`, `../archive/README.md`,
`../bin/README.md`, every file under `../playbooks/` that cites evidence, `autonomy/00-status/README.md`,
`autonomy/05-dispatch/README.md`, `company-memory/05-briefs/machinery.md`, `atlas-os/backend/README.md`,
`atlas-os/tests/telemetry/CLOUD-LOGGING.md`, `.claude/commands/cloud.md`, and
`.claude/commands/cloud-workflow.md`. The most heavily linked file in the folder by a wide margin.
