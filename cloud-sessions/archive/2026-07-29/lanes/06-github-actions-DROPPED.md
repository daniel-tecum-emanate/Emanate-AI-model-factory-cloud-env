# Lane 06 — GitHub Actions as a session host — **DROPPED**

**Dropped 2026-07-29, on policy — not on capability.** It works fine technically. Using
**GitHub-hosted** runners as a general agent host is against GitHub's Acceptable Use terms, and
the penalty ladder ends at account termination.

Kept on file so it is not re-proposed. A subagent was building this lane when it died on an
unrelated network error; that work was **not** resumed, deliberately.

---

## The blocking clause — VERIFIED, primary source

From GitHub's Terms for Additional Products and Features. Actions "should not be used for":

> * Cryptomining;
> * … The provision of a stand-alone or integrated application or service offering the Actions
>   product or service … for commercial purposes;
> * **Any activity that places a burden on our servers, where that burden is disproportionate to
>   the benefits provided to users**…; or
> * **If using GitHub-hosted runners, any other activity unrelated to the production, testing,
>   deployment, or publication of the software project associated with the repository where
>   GitHub Actions are used.**

> "GitHub may monitor your use of GitHub Actions. Misuse … may result in termination of jobs,
> restrictions in your ability to use GitHub Actions, disabling of repositories … or in some
> cases, **suspension or termination of your GitHub account**."

## The careful reading

There is **no rule naming "AI agents" or "long-running jobs"**, so a long job is not prohibited
per se. The exposure is the last clause, and the line falls here:

- **Defensible:** an agent doing coding work **on the repository it runs in** — that is
  "production/testing of the software project associated with the repository." This is exactly
  what `anthropics/claude-code-action@v1` is for, and it is fine.
- **Not defensible:** a general-purpose agent host — an agent working on *other* projects, or
  parked in a 6-hour job mostly idling as a remote shell. That is the unrelated-activity and
  disproportionate-burden case.

Lane 06 as originally scoped ("somewhere to park long Claude sessions") is the second one. Hence
dropped.

**Self-hosted runners are explicitly carved out** — the unrelated-activity clause is scoped to
GitHub-hosted runners. A self-hosted runner is free and raises the per-job ceiling from 6 hours to
**5 days**, which makes it a variant of [lane 07](07-always-on-vm-BLOCKED.md) rather than of this
one. If you already have the box, the box is the lane.

## Limits, for the record — VERIFIED

| Limit | Value |
|---|---|
| Job execution, GitHub-hosted | **6 hours** — job is terminated and **fails** |
| Job execution, self-hosted | **5 days** |
| Workflow run total | 35 days (includes waiting/approval) |
| Re-runs per run | 50 |

Included minutes/month on private repos: Free **2,000**, Pro 3,000, Team 3,000, Enterprise Cloud
50,000. Standard rates: Linux 1-core `actions_linux_slim` **$0.002**/min, Linux 2-core
**$0.006**, arm64 **$0.005**, Windows $0.010, macOS $0.062. Minutes round **up per job**, billed
to the repository owner.

A 6-hour Linux 2-core job = 360 min = **$2.16** at overage. Four/day for 30 days ≈ **$259/mo** —
more than lane 07's dedicated box, for a worse experience. On Free's 2,000 minutes you get about
**5.5 six-hour jobs per month** before paying.

**Larger runners are billed differently, not just faster:** "Included minutes cannot be used for
larger runners", they are "always charged for, even when used by public repositories", and they
are Team/Enterprise-only. Also: with no valid payment method on file, usage is **blocked** once
quota is exhausted.

## Two corrections captured on the way

- Overage was assumed **$0.008/min**; the published rate is **$0.006** for Linux 2-core x64.
- "Larger runners just consume minutes faster" is **wrong** — see above.

## Useful byproduct: non-default-branch triggering — VERIFIED

Worth keeping, because it applies to any Actions work here:

- **`push` runs workflow files that are not on the default branch.** GitHub's words: "This
  includes workflows that are not merged into the default branch." The workflow executes from the
  **pushed branch's** copy. This is the clean way to test without touching `main`. Add a `paths:`
  filter to avoid noise.
- **`workflow_dispatch` genuinely requires the default branch**: "This event will only trigger a
  workflow run if the workflow file exists on the default branch."
- **Nuance, community-verified only:** once a workflow has run at least once, `gh workflow run
  <file> --ref <branch>` dispatches to a non-default branch. Usual bootstrap is a temporary
  `push:` trigger, one run, then remove it. Registration is reportedly lost if all runs are
  deleted.
- Most other events (`schedule`, `issue_comment`, `check_run`, `repository_dispatch`) share the
  default-branch-only restriction; only `push`/`pull_request`-family events read the workflow from
  the feature branch.

## What IS still worth doing here

`anthropics/claude-code-action@v1` (v1 is GA; `@beta` is superseded) for **repo-scoped** automation
— PR review, issue triage, scheduled summaries of *this* repo. That is squarely within the terms.
Auth via `anthropic_api_key`, or `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` for Pro/Max.
Flags pass through `claude_args` (e.g. `--model opus --max-turns 10`; `--max-turns` defaults to 10).
Anthropic's own guidance: set `--max-turns` and **workflow-level timeouts to avoid runaway jobs**.
The 6-hour hosted ceiling still applies.

That is a different feature from "a place to park long sessions" and should get its own lane file
if pursued.
