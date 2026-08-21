# preflight-linter — Build and run the binding pre-upload linters; machine-verify every review claim.

Node: S5:preflight-linter | run: dddddddd-0000-0000-0000-000000000001 | graph: fine-tune-factory-v1 | org: qa-dryrun-synth

## Read first
- the corpus under review
- the review program's claimed findings
- prior linter scripts as a base
- the org battery of record + 1:1 mapping to the catalog checks: platform-alpha/finetune-out/families-grand-steel/PREFLIGHT-README.md (for grand-steel: gs_corpus/verify_corpus_gs.py [11 checks + --self-test] and gs_corpus/style_wrapper_1a.py [feeds tool-call-argument prose through the UNMODIFIED catalog style instrument + --self-test])

## Mandate
- self-test every linter on a gold sample and a seeded-fault sample before running it for real
  - STOP OR ASK: never ship a linter verdict without a passing self-test
- cross-check the linter's verdict against the review program's claims
  - STOP OR ASK: escalate any disagreement rather than silently trusting one over the other

## Laws (binding)
- single-writer discipline: own report/branch/worktree only; shared files route through the coordinator
- create the report file first (status header) before doing any work — crash/resume safety
- never interrupt mid-transfer; liveness is measured by report mtime, not chat presence
- records outrank this prompt — if a canon file and this spec disagree, the canon file wins and the disagreement is flagged, not silently resolved

## Done when
the linter self-tests on gold+seeded-fault fixtures; agree/disagree with the review program is explicit

## Final response shape
headline (one line) + counts + one top finding/prediction, so the coordinator can triage without opening files (AGENT-CATALOG.md §A.10 / MULTI-AGENT-REVIEW-PLAYBOOK §2.7)

## Required machine-readable verdict
End your final response with a line of exactly this form:
VERDICT: pass|fail|suspect
Use `suspect` when the checks could not be run to completion. Do not omit this line — a missing verdict is treated as a failure, never as a pass.