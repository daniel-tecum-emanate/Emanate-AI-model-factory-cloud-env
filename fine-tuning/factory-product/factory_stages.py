"""The factory's stage vocabulary — one definition, imported by everything.

Extracted in factory-graph-pr2 (C0). Before this, the mapping existed twice: as
prose in `factory.py`'s module docstring, and as a dict in `factory_sync.py`
(whose comment says it was "copied verbatim from there, not guessed"). The
display path used neither — `print_status` re-derived the code from the stage's
**position** in `STAGES` — which is where finding S0-4 came from.

Position is not the code, and the reason is `S6g`: the spend gate sits between
S6 and S7 and consumes no integer of its own (product-integration/DELTAS.md D8).
So from position 7 onward, positional numbering runs one ahead of the canonical
code, and `--status` displayed:

    S 9 eval               <- S9 is canonically the SHIP gate
    S10 ship [HUMAN GATE]

i.e. it showed the three human-held gates at S2/S7/S10 when every planning doc,
the charter, `factory_gate_requests.gate`'s CHECK constraint and the ledger's own
`node_id` all say **S2/S6g/S9**. Nothing branched on the printed number, so no
state was corrupted — it only told a human the wrong stage number at a
human-held gate, which is the one place where the human's understanding *is* the
control.

This module exists so there is nowhere left to re-derive it from. It deliberately
imports nothing: `factory.py` defers importing `factory_sync` (that import is
wrapped in try/except because the sync layer is treated as optionally available
and fail-open), and `--status` must not start depending on the DB client being
importable in order to print a number.
"""

# Stage execution order. The list's job is ordering — including the `NN-` prefix
# on `runs/<org>/NN-<stage>-report.json`, which is positional ON PURPOSE (it is a
# sort key, and report files already exist on disk under it). Do not renumber
# those to match the codes below.
STAGES = [
    "census", "governance", "export", "build", "verify", "project",
    "launch", "train", "eval", "ship", "activate", "retrain",
]

# The canonical code for each stage. Matches `factory_gate_requests.gate`'s CHECK
# constraint ('S2' | 'S6g' | 'S9') and `types.ts`'s FACTORY_STAGE_ORDER on the
# product side, so this is a cross-repo contract, not a display preference.
STAGE_CODES = {
    "census": "S1",
    "governance": "S2",
    "export": "S3",
    "build": "S4",
    "verify": "S5",
    "project": "S6",
    "launch": "S6g",
    "train": "S7",
    "eval": "S8",
    "ship": "S9",
    "activate": "S10",
    "retrain": "S11",
}

# The three human-held gates (SCOPE.md invariant 14). Nothing in the automation
# may approve these.
#
# `ship` (S9) is a decision record only — there is no automated ship ACTION to
# gate. Actually shipping a model (wiring it into the A/B-logging pipeline,
# getting its real serving API/endpoint) is done by Daniel by hand today: real
# code, a real PR. This may become automatable in the future (an actual
# `stage_ship` that wires the endpoint itself, at which point auto-approving
# small/low-risk cases could be revisited the way `governance` was) — until
# then, don't build UI or automation that implies the system can execute a
# ship, and don't fold `ship` into any "auto-approve" work aimed at
# `governance`'s cost-gate cases: this gate isn't a cost decision at all.
HUMAN_GATES = {"governance", "launch", "ship"}
