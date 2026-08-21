"""provider_enrich — the read-only Fireworks job enrichment must (1) skip
rows without job ids with a reason instead of erroring, (2) write nothing in
--dry-run, (3) map the provider payload honestly (no gpu_* for serverless SFT
jobs; accelerator fields only when the payload actually carries them), and
(4) never leak the API key into any output."""

import json

import pytest

import provider_enrich
from provider_enrich import (
    _job_endpoint,
    _money_to_usd,
    _redact,
    map_job_to_updates,
    parse_metrics_lines,
)

FAKE_KEY = "fw-SECRET-KEY-123456"

# A canned Fireworks supervised fine-tuning job response, field names from
# docs.fireworks.ai's gatewaySupervisedFineTuningJob schema and the real
# values DATASETS.md recorded for ptc-steel-v2-agent's job npemi0j7.
CANNED_JOB = {
    "name": "accounts/emanate/supervisedFineTuningJobs/npemi0j7",
    "state": "JOB_STATE_COMPLETED",
    "createTime": "2026-07-10T23:09:35Z",
    "completedTime": "2026-07-11T04:23:35Z",
    "dataset": "accounts/emanate/datasets/combined-v2-agent-train",
    "evaluationDataset": "accounts/emanate/datasets/qa-eval-v3",
    "baseModel": "accounts/fireworks/models/qwen3p6-27b",
    "outputModel": "accounts/emanate/models/ptc-steel-v2-agent",
    "epochs": 3,
    "learningRate": 0.000003,
    "loraRank": 32,
    "estimatedCost": {"currencyCode": "USD", "units": 45, "nanos": 159423828},
}


def _rows(job_ids):
    return [
        {
            "id": f"row-{i}",
            "model_id": f"model-{i}",
            "provider_job_id": job_id,
            "infra": {"platform": "Fireworks supervised fine-tuning (serverless)"},
            "loss_curve": [],
        }
        for i, job_id in enumerate(job_ids)
    ]


@pytest.fixture
def env_key(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", FAKE_KEY)


# ---------------------------------------------------------------------------
# Pure mapping
# ---------------------------------------------------------------------------


def test_job_endpoint_accepts_full_resource_name_and_bare_id():
    assert (
        _job_endpoint("accounts/emanate/supervisedFineTuningJobs/npemi0j7")
        == "https://api.fireworks.ai/v1/accounts/emanate/supervisedFineTuningJobs/npemi0j7"
    )
    assert (
        _job_endpoint("npemi0j7", account="emanate")
        == "https://api.fireworks.ai/v1/accounts/emanate/supervisedFineTuningJobs/npemi0j7"
    )


def test_money_mapping_matches_the_recorded_v2_cost():
    """DATASETS.md: estimatedCost units 45 + nanos 159,423,828 = $45.16."""
    assert _money_to_usd({"currencyCode": "USD", "units": 45, "nanos": 159423828}) == 45.1594
    assert _money_to_usd(None) is None
    assert _money_to_usd({"units": "not-a-number"}) is None


def test_payload_mapping_serverless_job_never_sets_gpu_fields():
    updates = map_job_to_updates(CANNED_JOB, existing_infra={"platform": "keep-me"})
    assert "gpu_count" not in updates
    assert "gpu_type" not in updates
    assert "gpu_rental_started_at" not in updates
    assert "gpu_rental_ended_at" not in updates
    infra = updates["infra"]
    # Backfilled keys survive the merge.
    assert infra["platform"] == "keep-me"
    # Job timing lands in infra — a job window is not a GPU rental window.
    assert infra["job_state"] == "JOB_STATE_COMPLETED"
    assert infra["job_create_time"] == "2026-07-10T23:09:35Z"
    assert infra["job_completed_time"] == "2026-07-11T04:23:35Z"
    assert infra["estimated_cost_usd"] == 45.1594
    assert infra["dataset"] == "accounts/emanate/datasets/combined-v2-agent-train"
    assert infra["output_model"] == "accounts/emanate/models/ptc-steel-v2-agent"


def test_payload_mapping_sets_gpu_fields_only_with_explicit_accelerators():
    """Future rental-style jobs (RunPod, accelerator-backed Fireworks jobs)
    DO populate gpu_* — and only then does the job window become a rental
    window."""
    rental_job = dict(CANNED_JOB, acceleratorCount=2, acceleratorType="NVIDIA_H200_141GB")
    updates = map_job_to_updates(rental_job)
    assert updates["gpu_count"] == 2
    assert updates["gpu_type"] == "NVIDIA_H200_141GB"
    assert updates["gpu_rental_started_at"] == "2026-07-10T23:09:35Z"
    assert updates["gpu_rental_ended_at"] == "2026-07-11T04:23:35Z"


def test_loss_points_require_a_real_series_not_a_scalar():
    one_point = [{"step": 1, "loss": 1.13, "label": "step 1 (eval loss)"}]
    assert "loss_curve" not in map_job_to_updates(CANNED_JOB, loss_points=one_point)
    two_points = one_point + [{"step": 2, "loss": 1.10, "label": "step 2 (eval loss)"}]
    assert map_job_to_updates(CANNED_JOB, loss_points=two_points)["loss_curve"] == two_points


def test_metrics_parsing_prefers_eval_loss_and_skips_junk():
    text = "\n".join(
        [
            json.dumps({"step": 100, "train_loss": 1.5, "eval_loss": 2.583}),
            "not json at all",
            json.dumps({"step": 200, "train_loss": 1.1}),  # no eval key -> not in eval series
            json.dumps({"no_step_key": True, "eval_loss": 9.9}),  # unusable
            json.dumps({"step": 300, "eval_loss": 2.542}),
        ]
    )
    points = parse_metrics_lines(text)
    assert [(p["step"], p["loss"]) for p in points] == [(100, 2.583), (300, 2.542)]
    assert all("eval loss" in p["label"] for p in points)


def test_metrics_parsing_falls_back_to_train_loss():
    text = "\n".join(
        [
            json.dumps({"step": 1, "loss": 3.544}),
            json.dumps({"step": 922, "loss": 1.07}),
        ]
    )
    points = parse_metrics_lines(text)
    assert [(p["step"], p["loss"]) for p in points] == [(1, 3.544), (922, 1.07)]
    assert all("train loss" in p["label"] for p in points)
    assert parse_metrics_lines("") == []
    assert parse_metrics_lines(None) == []


# ---------------------------------------------------------------------------
# CLI flow
# ---------------------------------------------------------------------------


def test_skips_rows_without_job_id_with_a_reason_and_exits_zero(env_key, monkeypatch, capsys):
    monkeypatch.setattr(provider_enrich, "select", lambda *a, **k: _rows([None, None]))
    update_calls = []
    monkeypatch.setattr(provider_enrich, "update", lambda *a, **k: update_calls.append(a))
    monkeypatch.setattr(
        provider_enrich, "fetch_fireworks_job", lambda *a, **k: pytest.fail("must not fetch")
    )

    rc = provider_enrich.main([])

    out = capsys.readouterr().out
    assert rc == 0
    assert update_calls == []
    assert out.count("skip model-") == 2
    assert "no provider_job_id" in out
    assert "nothing to enrich" in out


def test_exits_zero_with_summary_when_no_fireworks_rows_at_all(env_key, monkeypatch, capsys):
    monkeypatch.setattr(provider_enrich, "select", lambda *a, **k: [])
    rc = provider_enrich.main([])
    assert rc == 0
    assert "nothing to enrich" in capsys.readouterr().out


def test_dry_run_fetches_but_writes_nothing(env_key, monkeypatch, capsys):
    monkeypatch.setattr(
        provider_enrich, "select", lambda *a, **k: _rows(["accounts/emanate/supervisedFineTuningJobs/npemi0j7"])
    )
    monkeypatch.setattr(provider_enrich, "fetch_fireworks_job", lambda *a, **k: dict(CANNED_JOB))
    update_calls = []
    monkeypatch.setattr(provider_enrich, "update", lambda *a, **k: update_calls.append(a))

    rc = provider_enrich.main(["--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0
    assert update_calls == []
    assert "(dry-run) would update" in out
    assert "1 planned (dry-run)" in out


def test_real_run_updates_each_enrichable_row(env_key, monkeypatch, capsys):
    monkeypatch.setattr(
        provider_enrich,
        "select",
        lambda *a, **k: _rows(["accounts/emanate/supervisedFineTuningJobs/npemi0j7", None]),
    )
    monkeypatch.setattr(provider_enrich, "fetch_fireworks_job", lambda *a, **k: dict(CANNED_JOB))
    update_calls = []
    monkeypatch.setattr(
        provider_enrich, "update", lambda table, filters, values, **k: update_calls.append((table, filters, values))
    )

    rc = provider_enrich.main([])

    assert rc == 0
    assert len(update_calls) == 1
    table, filters, values = update_calls[0]
    assert table == "factory_models"
    assert filters == {"id": "eq.row-0"}
    assert values["infra"]["job_state"] == "JOB_STATE_COMPLETED"
    assert "1 updated" in capsys.readouterr().out


def test_api_key_never_appears_in_any_output(env_key, monkeypatch, capsys):
    """Worst case: an exception message embeds the key (e.g. a library repr
    of request headers). The redaction layer must strip it from everything
    the script prints."""
    monkeypatch.setattr(
        provider_enrich, "select", lambda *a, **k: _rows(["accounts/emanate/supervisedFineTuningJobs/xyz"])
    )

    def explode(*a, **k):
        raise provider_enrich.ProviderEnrichError(
            provider_enrich._redact(f"transport error: Authorization: Bearer {FAKE_KEY}", FAKE_KEY)
        )

    monkeypatch.setattr(provider_enrich, "fetch_fireworks_job", explode)

    rc = provider_enrich.main([])

    captured = capsys.readouterr()
    assert rc == 1
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err
    assert "***REDACTED***" in captured.err


def test_redact_handles_missing_key():
    assert _redact("boom", None) == "boom"
    assert _redact(f"key={FAKE_KEY}", FAKE_KEY) == "key=***REDACTED***"
