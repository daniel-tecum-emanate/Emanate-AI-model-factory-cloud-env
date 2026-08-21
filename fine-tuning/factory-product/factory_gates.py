"""Human-gate integrity: detect and revoke an approval the automation wrote itself.

Added in factory-graph-pr2 (C4) for a reason found while building C4, not from
the plan. C4's mandate is a synthesized subagent that is "gate-denied", and
writing that guarantee required knowing what an approval actually *is*. It is
this, and only this (`factory.py:119`):

    def gate_approved(slug, gate):
        return (run_dir(slug) / "approvals" / f"{gate}.json").exists()

A file exists, therefore a human approved. No content check, no provenance, no
countersignature. Meanwhile every dispatched node is handed
`("Read","Grep","Glob","Write","Edit","Bash")` by default
(`factory_node_run.DEFAULT_ALLOWED_TOOLS`) and runs with `cwd=REPO`.

So any of the ~10 agents S4-S10 dispatch could approve S2, S6g or S9 by writing
one small JSON file — including S6g, the spend gate, whose entire purpose is that
a human sees the bill before it is incurred. SCOPE.md invariant 14 and PRRules
rule 1 both say nothing in the automation may pass a human gate, and until this
module the mechanism enforcing that was an agent's disinclination to write a
particular path. This needs no malice to fire: an agent asked to report on gate
status, holding `Write`, working in a repo where state lives in
`runs/<org>/**.json`, writing `runs/<org>/approvals/launch.json` is an ordinary
mistake with the worst available blast radius.

**What this module does and does not achieve.** A node holding `Bash` can write
any path the invoking user can write, so no filesystem location and no secret
readable from the repo can *prevent* this. Prevention is therefore only possible
by not granting the tools — which is exactly what C4 does for the specs it
synthesizes (`factory_gap_spawn.READ_ONLY_TOOLS`), because those are new
capability whose grant is still ours to choose. For the pre-existing broad grants
the honest primitive is detection with automatic revocation:

  * snapshot the approvals directory immediately before a node is spawned,
  * verify it immediately after,
  * on any change: quarantine the file so it stops granting passage, fail the
    node loudly, and leave the evidence in place.

Detection is strictly weaker than prevention and is named as such rather than
dressed up (same discipline as C3's `NOT-SEPARABLE` credential dimension). The
residual — that a node can still *momentarily* hold a forged approval, and that
`gate_approved` remains unauthenticated for anything not spawned through
`dispatch_node` — is filed for Daniel as V-082 rather than quietly accepted here.

**Why the detection window has no legitimate writer.** The only sanctioned writer
is `record_approval`, reached only from `factory.py --approved`, which is a
separate, synchronous CLI invocation that dispatches nothing. One stage runs per
process. So during a dispatch window the correct number of approval writes is
zero, and any observed write is illegitimate by construction — not a heuristic.
The one exception is a human running `--approved` in a second terminal while a
stage is live; quarantine is reversible and the error message says how, which is
the right trade when the alternative is trusting a write nobody can attribute.
"""

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("factory.gates")

HERE = Path(__file__).resolve().parent

# Imported from the vocabulary module rather than restated: a gate list that
# drifts out of step with the real one would silently stop watching a gate.
from factory_stages import HUMAN_GATES  # noqa: E402


class GateForgeryError(Exception):
    """A human-gate approval was created, modified or removed by the automation.

    Hard-fails the run. This is deliberately one of the very few exceptions in
    the factory allowed to abort rather than degrade: every other failure path
    ends in a report saying what went wrong, but an unnoticed forged approval
    means the next stage spends money a human never authorised, and the report
    would say `ok`.
    """


def approvals_dir(org_slug, stream="per-account"):
    path = HERE / "runs" / org_slug
    if stream != "per-account":
        path = path / stream
    return path / "approvals"


def _digest(path):
    """Content digest, or `None` for absent. Content and not just existence, so
    editing an existing approval (changing its note, or its gate) is caught as
    well as creating one."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError as exc:
        # Unreadable is not absent. Returning None here would let an unreadable
        # approval compare equal to a deleted one.
        log.warning("factory_gates: cannot read %s: %s", path, exc)
        return f"unreadable:{exc.errno}"


def snapshot(org_slug, stream="per-account"):
    """Digest every human gate's approval file. Cheap enough (three stats) to run
    around every single node dispatch, which is what makes it usable as a guard
    rather than a nightly audit."""
    d = approvals_dir(org_slug, stream=stream)
    return {gate: _digest(d / f"{gate}.json") for gate in sorted(HUMAN_GATES)}


def diff(before, after):
    """Gates whose approval state changed, as `{gate: (before, after)}`.

    Includes deletions and modifications, not only creations. A node that
    *removes* an approval has not granted itself anything, but it has silently
    reverted a decision a human made, so the run must not continue as if the
    approval were still there.
    """
    return {
        gate: (before.get(gate), after.get(gate))
        for gate in set(before) | set(after)
        if before.get(gate) != after.get(gate)
    }


def quarantine(org_slug, gate, stream="per-account"):
    """Move a suspect approval aside so it stops granting passage, preserving it.

    Moved, not deleted: `gate_approved` is existence-based, so moving it out of
    the way is precisely what revokes it, and the file is the only evidence of
    what was written and by which run. Returns the new path, or `None` if there
    was nothing there (the deletion case).
    """
    src = approvals_dir(org_slug, stream=stream) / f"{gate}.json"
    if not src.exists():
        return None
    dest_dir = approvals_dir(org_slug, stream=stream) / "quarantine"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = dest_dir / f"{gate}.{stamp}.json"
    shutil.move(str(src), str(dest))
    log.error("factory_gates: QUARANTINED forged approval %s -> %s", src, dest)
    return dest


def _record_forgery(
    org_slug, gate, node_id, before_after, quarantined,
    stream="per-account",
):
    """Append to a tamper log. Best-effort: this must never be the reason a
    forgery goes unraised, so every failure here is swallowed after logging."""
    try:
        path = approvals_dir(org_slug, stream=stream) / "FORGERY-LOG.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "org_slug": org_slug,
                "model_stream": stream,
                "gate": gate,
                "node_id": node_id,
                "digest_before": before_after[0],
                "digest_after": before_after[1],
                "quarantined_to": str(quarantined) if quarantined else None,
                "pid": os.getpid(),
            }) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("factory_gates: could not write forgery log: %s", exc)


def assert_unchanged(
    org_slug, before, node_id="unknown", stream="per-account"
):
    """Verify no approval moved while a node ran; quarantine and raise if one did.

    Called on both the success and failure paths of a live dispatch, because a
    node that wrote an approval and then exited non-zero has still written it.
    """
    changed = diff(before, snapshot(org_slug, stream=stream))
    if not changed:
        return

    quarantined = {}
    for gate, states in sorted(changed.items()):
        dest = quarantine(org_slug, gate, stream=stream)
        quarantined[gate] = dest
        _record_forgery(
            org_slug, gate, node_id, states, dest, stream=stream
        )

    gates = ", ".join(sorted(changed))
    raise GateForgeryError(
        f"node '{node_id}' changed human-gate approval(s) [{gates}] for org "
        f"'{org_slug}' while it ran. The automation may not approve a human gate "
        f"(SCOPE.md invariant 14). The file(s) have been quarantined under "
        f"runs/{org_slug}/"
        f"{'' if stream == 'per-account' else stream + '/'}"
        f"approvals/quarantine/ and the gate is UNAPPROVED again. "
        f"If you approved this gate yourself in another terminal during this run, "
        f"re-run `factory {org_slug} --stage <gate> --approved` and the approval "
        f"stands; nothing is lost. Details: runs/{org_slug}/"
        f"{'' if stream == 'per-account' else stream + '/'}"
        "approvals/FORGERY-LOG.jsonl"
    )
