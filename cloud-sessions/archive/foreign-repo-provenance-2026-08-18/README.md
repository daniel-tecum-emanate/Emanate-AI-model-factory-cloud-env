# foreign-repo-provenance-2026-08-18/

**Read-only. Do not fix, update, or delete it** — corrections go in
[`../../reference/`](../../reference/), never into this archive. Same rule as
[`../README.md`](../README.md) for the 2026-07-29 investigation.

## What this is

`cloud-sessions/`'s entire reference corpus — every dated probe report, the evidence
record, the ledger, and the exodus manifest — was written and verified against **a
different repository**, `daniel0tgc/internal-company-tool` (an account-scoped repo, now
404 under the account authenticated here), not this one
(`daniel-tecum-emanate/emanate-tecum-workflow`). That corpus was bulk-committed into this
repo in commit `7a5690b` ("sync accumulated workflow-repo state", 2026-08-06) along with
the rest of `cloud-sessions/`, without running the folder's own porting checklist
([`../../playbooks/09-porting-to-another-repo.md`](../../playbooks/09-porting-to-another-repo.md)).

Found and fixed 2026-08-18, per Daniel's confirmation that this content is fine to
strip/relabel (`PRs/model-factory-finetune-launcher-v1/PRD.md`, decision 2).

## What moved here, and why

These files are **real, evidenced R&D** — genuine command transcripts, genuine test-run
counts, genuine dispatch timestamps — just about the other repo's environment, commit
history, and account state. Rewriting them to look like they happened in
`emanate-tecum-workflow` would fabricate a false history for this repo. Archiving them
whole, unedited, preserves the record honestly instead.

**Not archived, and left in place, corrected:** files in `../../playbooks/`, `../../bin/`,
`../../AGENT.md`, `../../README.md`, `../../reference/README.md` and
`../../reference/troubleshooting.md` — these describe the `cs`/`cloud-sessions` mechanism
itself (dispatch, steering, teleport, routines, the git gate, the test harness), which is
account-level and repo-agnostic, not something that happened to one specific repo. Those
got a provenance note and their foreign identity literals (`daniel0tgc`,
`internal-company-tool`) corrected to this repo's real values instead of being archived.

| Path here | What it was | Why it's archived, not rewritten |
|---|---|---|
| `cloud-vm-probe-2026-08-14.md` … `cloud-vm-probe-7-2026-08-14.md` (7 files) | Seven dispatched cloud sessions measuring the folder's claims from inside a real VM, each against a named commit of `internal-company-tool` | Dated, commit-specific, quoted command output from the other repo's VM and git history |
| `verified-facts.md` | The evidence record: every load-bearing claim with the command that checked it, against `internal-company-tool` | Wall-to-wall `VERIFIED`/command-output claims tied to that repo's account and commits |
| `this-repo-in-the-cloud.md` | *What does a cloud VM get when it clones **this** repository* — "this repository" meant `internal-company-tool` when written | The whole file's subject is the other repo |
| `linux-verification-2026-08-15.md` | Full-suite Linux verdict for that repo's checkout | Dated, machine- and repo-specific counts |
| `subagents-in-cloud-2026-08-16.md` | Concurrent-subagent probe against that repo's checkout | Session ids, commit, and telemetry rows are that repo's |
| `gap-closure-2026-08-16.md` | Six gaps closed against that repo's `OPEN-GAPS.md` register | The gaps closed are that repo's gaps |
| `porting-audit-2026-08-17.md` | A real transplant test: copying `cloud-sessions/` **out of** `internal-company-tool` into a bare repo | Measures that repo's coupling surface specifically |
| `reachability-2026-08-17.md` | Link-reachability audit of that repo's `reference/` folder at a named commit | Commit- and file-count-specific to that checkout |
| `safety-audit-2026-08-15.md` | Adversarial security audit of that repo's checkout, including a real finding about its `.env` | Repo- and filesystem-path-specific (`/Users/danieltecum/internal-company-tool/...`) |
| `workflow-bootstrap-2026-08-17.md` | First end-to-end `/cloud-workflow` dispatch against that repo | Dispatch- and commit-specific |
| `founder-decisions-2026-08-18.md` | A sheet of decisions pending for that repo's founder, referencing that repo's `atlas-os/queue/QUEUE.md` | Every row cites that repo's queue and V-numbers |
| `billing.md` | Billing/rate-limit evidence gathered against that repo's account session | Dated evidence tied to that account |
| `OPEN-GAPS.md` | The ranked gap register (G06–G24 etc.) for that repo's `cloud-sessions/` coupling to `atlas-os/` | The gaps are about that repo's `atlas-os/` dependency, which does not exist here at all |
| `sessions.jsonl` | The dispatch ledger — every row's `repo` field says `internal-company-tool` | Literal per-dispatch history of the other repo; `emanate-tecum-workflow`'s own ledger starts empty (truncated in place, per playbook 09 step 2) |
| `exodus-manifest.json` | An exodus partition plan whose `repo_root`/`git_repository` point at `/Users/danieltecum/internal-company-tool` and its GitHub URL | Regenerated on demand by `cs exodus`; the old one is that repo's plan, not a template worth keeping live |
| `RESULTS.md` | The last recorded run of `tests/run.sh`, including three companion `atlas-os` test suites (`heartbeat`, `worktree`, `process`) that don't exist in this repo | Rewritten fresh the next time `tests/run.sh` runs here; the old numbers are the other repo's `atlas-os/` suites |

## What this means for `cloud-sessions/` today, in this repo

- **`atlas-os/` does not exist in `emanate-tecum-workflow`.** Per
  [`../../playbooks/09-porting-to-another-repo.md`](../../playbooks/09-porting-to-another-repo.md)'s
  own decision 1 ("does the new repo get a heartbeat board? No →"), that means `cs board`,
  `cs exodus`, and the auto-registration hook are expected to refuse loudly here — not a
  bug, the documented behavior for a repo without a heartbeat board. Single-session
  `/cloud` (dispatch, steer, teleport, Remote Control, routines) is unaffected.
- This repo's own heartbeat/session-tracking system lives at
  [`../../../coordination/heartbeat/STATE.md`](../../../coordination/heartbeat/STATE.md)
  and `scripts/hooks/register_heartbeat_session.py` — a real, different system, not a
  drop-in replacement for `atlas-os/bin/heartbeat.py`'s CLI (`register`/`overlap`/`update`
  subcommands, claim-conflict gating). No attempt is made in this cleanup to wire the two
  together; that is Phase 1's job (`PRs/model-factory-finetune-launcher-v1/PRContext.md`,
  T1-1/T1-2), not this hygiene pass's.
- `cloud-sessions/state/sessions.jsonl` now exists at its original path, truncated empty,
  so `cs ls`/`cs board`/`cs fleet` read a real (empty) ledger for this repo instead of the
  other repo's twelve rows.

Do not cite anything in this folder as current fact about `emanate-tecum-workflow`. Cite
it only as *what was measured and verified about `daniel0tgc/internal-company-tool`, as of
the dates each file names*.
