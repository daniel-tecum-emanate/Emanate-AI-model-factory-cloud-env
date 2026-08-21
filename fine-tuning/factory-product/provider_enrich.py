#!/usr/bin/env python3
"""provider_enrich — read-only training-provider enrichment for factory_models.

For every `factory_models` row with `training_provider='fireworks'` AND a
non-null `provider_job_id`, GET the supervised fine-tuning job from the
Fireworks REST API and upsert what the platform actually reports:

  - job state + createTime/completedTime + estimated cost + dataset/base/output
    model into `infra` (merged over the existing keys, never replacing the
    whole object),
  - loss events into `loss_curve` when the job exposes a readable metrics file
    (`metricsFileSignedUrl`) that parses to a real series (>= 2 points — one
    point is a scalar, and the scalar already renders via train_eval_loss),
  - `gpu_count`/`gpu_type`/`gpu_rental_*` ONLY when the job payload carries
    explicit accelerator fields. Fireworks SUPERVISED FINE-TUNING is
    serverless — the SFT job schema has no accelerator fields at all
    (verified against docs.fireworks.ai's gatewaySupervisedFineTuningJob) —
    so for every Fireworks SFT job the gpu_* columns stay NULL and the job
    window lands in `infra` (a job window is not a GPU rental window). The
    accelerator mapping exists for future rental-style jobs (RunPod, or a
    Fireworks job type that does report accelerators).

Endpoints used (all GET, read-only):
  - https://api.fireworks.ai/v1/accounts/{account}/supervisedFineTuningJobs/{id}
    (or https://api.fireworks.ai/v1/{provider_job_id} when the stored id is
    already the full `accounts/...` resource name — which is how
    models_backfill.py stores them)
  - the job's `metricsFileSignedUrl` (pre-signed, no auth header)

Key handling: the Fireworks key is read from the FIREWORKS_API_KEY env var,
sent only as an Authorization header, and NEVER printed — every line this
script emits passes through `_redact()`, which strips the key even from
exception text that might somehow embed it.

Usage:
    export FACTORY_SUPABASE_URL=...
    export FACTORY_SUPABASE_SERVICE_ROLE_KEY=...
    export FIREWORKS_API_KEY=...
    python3 provider_enrich.py --dry-run   # prints what it would write
    python3 provider_enrich.py             # writes to factory_models

Exit codes: 0 when every eligible row enriched or there was nothing to do
(rows without job ids are skipped with a per-row reason, not an error);
1 when any row's provider fetch or DB write actually failed.
"""

import argparse
import json
import os
import sys

import requests

from supabase_rest import SupabaseRestError, select, update

FIREWORKS_API_BASE = "https://api.fireworks.ai/v1"
DEFAULT_ACCOUNT = "emanate"
DEFAULT_TIMEOUT_S = 30


class ProviderEnrichError(RuntimeError):
    """Raised on any provider API failure — message is pre-redacted."""


def _redact(text, api_key):
    """Strip the API key from any outbound line. Defense in depth: the key
    should never appear in a URL or response body, but a proxy error or a
    library repr could embed request headers — nothing leaves this script
    unredacted."""
    text = str(text)
    if api_key:
        text = text.replace(api_key, "***REDACTED***")
    return text


def _job_endpoint(provider_job_id, account=DEFAULT_ACCOUNT):
    """`provider_job_id` may be a bare id or the full resource name
    (`accounts/<acct>/supervisedFineTuningJobs/<id>`) — models_backfill.py
    stores the full form, matching how DATASETS.md records v1/v2."""
    if provider_job_id.startswith("accounts/"):
        return f"{FIREWORKS_API_BASE}/{provider_job_id}"
    return f"{FIREWORKS_API_BASE}/accounts/{account}/supervisedFineTuningJobs/{provider_job_id}"


def fetch_fireworks_job(provider_job_id, api_key, account=DEFAULT_ACCOUNT, timeout=DEFAULT_TIMEOUT_S):
    endpoint = _job_endpoint(provider_job_id, account)
    try:
        resp = requests.get(
            endpoint, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )
    except requests.RequestException as exc:
        raise ProviderEnrichError(_redact(f"GET {endpoint} transport error: {exc}", api_key)) from exc
    if not resp.ok:
        raise ProviderEnrichError(
            _redact(f"GET {endpoint} -> {resp.status_code} {resp.text[:300]}", api_key)
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderEnrichError(_redact(f"GET {endpoint} returned non-JSON", api_key)) from exc


def fetch_metrics_text(signed_url, timeout=DEFAULT_TIMEOUT_S):
    """The metrics file URL is pre-signed — no Authorization header (sending
    the key to a non-api.fireworks.ai signed host would leak it)."""
    try:
        resp = requests.get(signed_url, timeout=timeout)
    except requests.RequestException:
        return None
    if not resp.ok:
        return None
    return resp.text


_STEP_KEYS = ("step", "global_step", "steps", "epoch")
_EVAL_LOSS_KEYS = ("eval_loss", "eval/loss", "validation_loss", "valid_loss")
_TRAIN_LOSS_KEYS = ("loss", "train_loss", "train/loss", "training_loss")


def parse_metrics_lines(text):
    """Parse a Fireworks metrics file (JSONL of per-step records) into
    ModelLossPoint dicts. Eval-loss series preferred when present (it is the
    quality signal the program actually reads); train-loss otherwise. Unknown
    lines are skipped, never guessed at."""
    eval_points = []
    train_points = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        step = next((rec[k] for k in _STEP_KEYS if isinstance(rec.get(k), (int, float))), None)
        if step is None:
            continue
        eval_loss = next(
            (rec[k] for k in _EVAL_LOSS_KEYS if isinstance(rec.get(k), (int, float))), None
        )
        train_loss = next(
            (rec[k] for k in _TRAIN_LOSS_KEYS if isinstance(rec.get(k), (int, float))), None
        )
        if eval_loss is not None:
            eval_points.append(
                {"step": int(step), "loss": float(eval_loss), "label": f"step {int(step)} (eval loss)"}
            )
        if train_loss is not None:
            train_points.append(
                {"step": int(step), "loss": float(train_loss), "label": f"step {int(step)} (train loss)"}
            )
    return eval_points if eval_points else train_points


def _money_to_usd(money):
    """typeMoney {currencyCode, units, nanos} -> float dollars."""
    if not isinstance(money, dict):
        return None
    units = money.get("units", 0)
    nanos = money.get("nanos", 0)
    try:
        return round(int(units) + int(nanos) / 1e9, 4)
    except (TypeError, ValueError):
        return None


def map_job_to_updates(job, existing_infra=None, loss_points=None):
    """Pure mapping from a Fireworks SFT job payload to a factory_models
    UPDATE dict. `infra` is merged over `existing_infra` (backfilled keys
    survive; provider-fetched keys win on collision)."""
    infra = dict(existing_infra or {})

    field_map = {
        "job_state": job.get("state"),
        "job_create_time": job.get("createTime"),
        "job_completed_time": job.get("completedTime"),
        "estimated_cost_usd": _money_to_usd(job.get("estimatedCost")),
        "dataset": job.get("dataset"),
        "evaluation_dataset": job.get("evaluationDataset"),
        "base_model": job.get("baseModel"),
        "output_model": job.get("outputModel"),
        "batch_size_samples": job.get("batchSizeSamples"),
    }
    for key, value in field_map.items():
        if value is not None and value != "":
            infra[key] = value

    updates = {"infra": infra}

    # Accelerator fields: absent from every Fireworks SFT job (serverless) —
    # only a job type that actually reports rented hardware populates gpu_*,
    # and only then does the job window become a rental window.
    accelerator_count = job.get("acceleratorCount")
    accelerator_type = job.get("acceleratorType")
    if accelerator_count is not None:
        updates["gpu_count"] = int(accelerator_count)
        if accelerator_type:
            updates["gpu_type"] = str(accelerator_type)
        if job.get("createTime"):
            updates["gpu_rental_started_at"] = job["createTime"]
        if job.get("completedTime"):
            updates["gpu_rental_ended_at"] = job["completedTime"]

    if loss_points and len(loss_points) >= 2:
        updates["loss_curve"] = loss_points

    return updates


def enrich_row(row, api_key, account, dry_run, url=None, service_role_key=None):
    """Returns 'skipped' | 'planned' | 'updated'. Raises ProviderEnrichError
    on a real provider/DB failure."""
    model_id = row.get("model_id", row.get("id"))
    job_id = row.get("provider_job_id")
    if not job_id:
        print(f"  skip {model_id}: no provider_job_id (nothing to query the provider for)")
        return "skipped"

    job = fetch_fireworks_job(job_id, api_key, account=account)

    loss_points = None
    metrics_url = job.get("metricsFileSignedUrl")
    if metrics_url:
        metrics_text = fetch_metrics_text(metrics_url)
        if metrics_text:
            loss_points = parse_metrics_lines(metrics_text)

    updates = map_job_to_updates(job, existing_infra=row.get("infra"), loss_points=loss_points)

    summary = {
        "infra_keys": sorted(updates["infra"].keys()),
        "loss_curve_points": len(updates.get("loss_curve", [])) or "unchanged",
        "gpu_fields": "set" if "gpu_count" in updates else "untouched (serverless)",
    }
    print(f"  {model_id}: {_redact(json.dumps(summary), api_key)}")

    if dry_run:
        print(f"  (dry-run) would update factory_models id={row['id']}")
        return "planned"

    try:
        update(
            "factory_models",
            {"id": f"eq.{row['id']}"},
            updates,
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        raise ProviderEnrichError(_redact(f"DB update failed for {model_id}: {exc}", api_key)) from exc
    print(f"  updated factory_models id={row['id']}")
    return "updated"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print what would be written, write nothing")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT, help="Fireworks account id (default: emanate)")
    parser.add_argument("--url", default=None)
    parser.add_argument("--service-role-key", default=None)
    args = parser.parse_args(argv)

    api_key = os.environ.get("FIREWORKS_API_KEY")
    if not api_key:
        print("provider_enrich: FIREWORKS_API_KEY not set — cannot query the provider.", file=sys.stderr)
        return 1

    rows = select(
        "factory_models",
        params={
            "training_provider": "eq.fireworks",
            "select": "id,model_id,provider_job_id,infra,loss_curve",
            "order": "iteration.asc",
        },
        url=args.url,
        service_role_key=args.service_role_key,
    )
    if not rows:
        print("provider_enrich: no factory_models rows with training_provider='fireworks' — nothing to enrich.")
        return 0

    print(f"provider_enrich: {len(rows)} fireworks row(s){' (dry-run)' if args.dry_run else ''}")
    counts = {"skipped": 0, "planned": 0, "updated": 0, "failed": 0}
    for row in rows:
        try:
            outcome = enrich_row(
                row,
                api_key,
                account=args.account,
                dry_run=args.dry_run,
                url=args.url,
                service_role_key=args.service_role_key,
            )
            counts[outcome] += 1
        except ProviderEnrichError as exc:
            counts["failed"] += 1
            print(f"  FAILED {row.get('model_id', row.get('id'))}: {_redact(exc, api_key)}", file=sys.stderr)

    enrichable = counts["planned"] + counts["updated"] + counts["failed"]
    if enrichable == 0:
        print(
            f"provider_enrich: nothing to enrich — {counts['skipped']} row(s) have no provider_job_id yet. "
            "That's expected until a run records its job id."
        )
        return 0

    print(
        "provider_enrich: done — "
        f"{counts['updated']} updated, {counts['planned']} planned (dry-run), "
        f"{counts['skipped']} skipped, {counts['failed']} failed"
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
