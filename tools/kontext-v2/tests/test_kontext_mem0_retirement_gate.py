import importlib.util
import json
from pathlib import Path


def load_module():
    path = Path("tools/kontext-v2/scripts/kontext_mem0_retirement_gate.py")
    spec = importlib.util.spec_from_file_location("kontext_mem0_retirement_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _typed_state_trial(**overrides):
    trial = {
        "ok": True,
        "mode": "judged_bundle_state_trial",
        "counts": {
            "state_questions": 6,
            "projection_selection_loss_questions": 0,
            "state_event_extraction_loss_questions": 0,
            "active_answer_overlap_questions": 6,
        },
        "raw_payload_hits": 0,
        "runs_model_calls": True,
        "actual_judged_accuracy_proven": True,
    }
    for key, value in overrides.items():
        if key == "counts":
            trial["counts"].update(value)
        else:
            trial[key] = value
    return trial


def _official_judged_accuracy(**overrides):
    gate = {
        "status": "pass",
        "evidence": {
            "actual_judged_accuracy_proven": True,
            "total_questions": 30,
            "weighted_accuracy": 0.8,
            "min_total_questions": 30,
            "min_weighted_accuracy": 0.8,
        },
    }
    for key, value in overrides.items():
        if key == "evidence":
            gate["evidence"].update(value)
        else:
            gate[key] = value
    return gate


def _passing_payload(**overrides):
    payload = {
        "client_routing": {
            "codex": {"kontext": True, "mem0": False},
            "claude": {"kontext": True, "mem0": False},
            "chatgpt_visible": {"kontext": True, "mem0": False},
        },
        "kontext_health": {"ok": True, "mirror_count": 209224},
        "sync_proof": {"mem0_visible_ids": 4238, "kontext_matches": 4238, "missing": 0},
        "quality_gate": {
            "name": "locomo_longmem_paired_beam_replacement",
            "locomo30": {"passed": 28, "total": 30, "raw_payload_hits": 0, "runs_model_calls": True},
            "longmemeval30": {"passed": 24, "total": 30, "raw_payload_hits": 0, "runs_model_calls": True},
            "paired_beam": {
                "questions": 30,
                "same_question_hashes": True,
                "kontext_passed": 24,
                "mem0_passed": 23,
                "raw_payload_hits": 0,
                "runs_model_calls": True,
                "legacy_backend": "legacy-mem0-offline",
            },
        },
        "official_judged_accuracy": _official_judged_accuracy(),
        "canary": {"ok": True, "writes_applied": 0, "tool_count": 18},
        "write_audit": {"ok": True, "read_after_write_ok": True, "error_count": 0},
        "typed_state_trial": _typed_state_trial(runs_model_calls=True, actual_judged_accuracy_proven=True),
        "legacy_mem0": {
            "writes_enabled": False,
            "runtime_enabled": True,
            "recent_write_count": 0,
            "recent_read_count": 0,
            "monitor_hours": 72,
        },
        "backups": {"kontext_db": True, "mem0_export": True},
    }
    for key, value in overrides.items():
        payload[key] = value
    return payload


def test_retirement_gate_requires_official_judged_accuracy_gate():
    module = load_module()
    payload = _passing_payload()
    payload.pop("official_judged_accuracy")

    report = module.build_report(payload)

    assert report["ok"] is False
    official = next(row for row in report["gates"] if row["name"] == "official_judged_accuracy")
    assert official["status"] == "fail"


def test_retirement_gate_rejects_paired_beam_tie_without_n30_accuracy():
    module = load_module()

    report = module.build_report(
        _passing_payload(
            quality_gate={
                "name": "locomo_longmem_paired_beam_replacement",
                "locomo30": {"passed": 28, "total": 30, "raw_payload_hits": 0, "runs_model_calls": True},
                "longmemeval30": {"passed": 24, "total": 30, "raw_payload_hits": 0, "runs_model_calls": True},
                "paired_beam": {
                    "questions": 6,
                    "same_question_hashes": True,
                    "kontext_passed": 4,
                    "mem0_passed": 4,
                    "raw_payload_hits": 0,
                    "runs_model_calls": True,
                    "legacy_backend": "legacy-mem0-offline",
                },
            }
        )
    )

    assert report["ok"] is False
    quality = next(row for row in report["gates"] if row["name"] == "retirement_quality_gate")
    assert quality["status"] == "fail"
    assert quality["evidence"]["paired_beam"]["accuracy"] == 0.6667
    assert quality["evidence"]["paired_beam"]["min_accuracy"] == 0.8


def test_retirement_gate_rejects_typed_state_trial_without_judged_evidence():
    module = load_module()

    report = module.build_report(
        _passing_payload(typed_state_trial=_typed_state_trial(runs_model_calls=False, actual_judged_accuracy_proven=False))
    )

    assert report["ok"] is False
    typed_state = next(row for row in report["gates"] if row["name"] == "typed_state_trial_gate")
    assert typed_state["status"] == "fail"
    assert typed_state["evidence"]["actual_judged_accuracy_proven"] is False


def test_retirement_gate_rejects_no_call_plan_quality_gate():
    module = load_module()

    report = module.build_report(
        _passing_payload(
            quality_gate={
                "ok": True,
                "name": "judged-micro-slice-plan",
                "mode": "judged-micro-slice-plan",
                "runs_model_calls": False,
                "actual_judged_accuracy_proven": False,
            }
        )
    )

    assert report["ok"] is False
    quality = next(row for row in report["gates"] if row["name"] == "retirement_quality_gate")
    assert quality["status"] == "fail"


def test_retirement_gate_passes_only_with_kontext_only_runtime_and_backups():
    module = load_module()

    report = module.build_report(
        _passing_payload(
            client_routing={
                "codex": {"kontext": True, "mem0": False},
                "claude": {"kontext": True, "mem0": False},
                "chatgpt_visible": {"kontext": True, "mem0": False},
                "hooks": {"kontext": True, "mem0_runtime": False},
            },
            kontext_health={"ok": True, "mirror_count": 12066},
        )
    )

    assert report["ok"] is True
    assert report["decision"] == "mem0_runtime_can_be_disabled_after_final_delete_approval"
    assert report["status_counts"] == {"pass": 10, "fail": 0}
    typed_state_gate = next(row for row in report["gates"] if row["name"] == "typed_state_trial_gate")
    assert typed_state_gate["status"] == "pass"


def test_retirement_gate_accepts_hard_disabled_mem0_runtime_without_waiting_48h():
    module = load_module()

    report = module.build_report(
        _passing_payload(
            kontext_health={"ok": True, "mirror_count": 136938},
            legacy_mem0={
                "writes_enabled": False,
                "runtime_enabled": False,
                "recent_write_count": 0,
                "recent_read_count": 0,
                "monitor_hours": 1.2,
            },
        )
    )

    assert report["ok"] is True
    legacy_gate = next(row for row in report["gates"] if row["name"] == "legacy_mem0_frozen_and_unused")
    assert legacy_gate["evidence"]["runtime_enabled"] is False
    assert legacy_gate["evidence"]["monitor_requirement"] == "satisfied_by_runtime_disable"


def test_retirement_gate_blocks_mem0_runtime_traffic_and_missing_backup():
    module = load_module()

    report = module.build_report(
        {
            "client_routing": {
                "codex": {"kontext": True, "mem0": False},
                "claude": {"kontext": True, "mem0": True},
                "chatgpt_visible": {"kontext": False, "mem0": True},
            },
            "kontext_health": {"ok": True, "mirror_count": 12066},
            "sync_proof": {"mem0_visible_ids": 4238, "kontext_matches": 4230, "missing": 8},
            "quality_gate": {"ok": False, "name": "beam_failed"},
            "canary": {"ok": True, "writes_applied": 0, "tool_count": 18},
            "write_audit": {"ok": False, "error_count": 1},
            "typed_state_trial": _typed_state_trial(),
            "legacy_mem0": {
                "writes_enabled": True,
                "runtime_enabled": True,
                "recent_write_count": 2,
                "recent_read_count": 7,
                "monitor_hours": 12,
            },
            "backups": {"kontext_db": True, "mem0_export": False},
        }
    )

    assert report["ok"] is False
    assert report["decision"] == "keep_mem0_available_fix_blockers"
    assert report["status_counts"]["fail"] == 7
    rendered = json.dumps(report).lower()
    assert "raw" not in rendered
    assert "secret" not in rendered


def test_retirement_gate_accepts_strict_replacement_quality_gate():
    module = load_module()

    report = module.build_report(_passing_payload())

    assert report["ok"] is True
    quality = next(row for row in report["gates"] if row["name"] == "retirement_quality_gate")
    assert quality["status"] == "pass"
    assert quality["evidence"]["replacement_gate"] == "locomo_longmem_paired_beam_replacement"
    assert quality["evidence"]["beam_delta_kontext_minus_mem0"] == 1


def test_retirement_gate_rejects_replacement_quality_gate_when_mem0_leads_or_raw_hits():
    module = load_module()

    report = module.build_report(
        {
            "client_routing": {
                "codex": {"kontext": True, "mem0": False},
                "claude": {"kontext": True, "mem0": False},
                "chatgpt_visible": {"kontext": True, "mem0": False},
            },
            "kontext_health": {"ok": True, "mirror_count": 209224},
            "sync_proof": {"mem0_visible_ids": 4238, "kontext_matches": 4238, "missing": 0},
            "quality_gate": {
                "name": "locomo_longmem_paired_beam_replacement",
                "locomo30": {"passed": 28, "total": 30, "raw_payload_hits": 0, "runs_model_calls": True},
                "longmemeval30": {"passed": 24, "total": 30, "raw_payload_hits": 0, "runs_model_calls": True},
                "paired_beam": {
                    "questions": 6,
                    "same_question_hashes": True,
                    "kontext_passed": 4,
                    "mem0_passed": 5,
                    "raw_payload_hits": 1,
                    "runs_model_calls": True,
                    "legacy_backend": "legacy-mem0-offline",
                },
            },
            "canary": {"ok": True, "writes_applied": 0, "tool_count": 18},
            "write_audit": {"ok": True, "read_after_write_ok": True, "error_count": 0},
            "typed_state_trial": _typed_state_trial(),
            "legacy_mem0": {
                "writes_enabled": False,
                "runtime_enabled": True,
                "recent_write_count": 0,
                "recent_read_count": 0,
                "monitor_hours": 72,
            },
            "backups": {"kontext_db": True, "mem0_export": True},
        }
    )

    assert report["ok"] is False
    quality = next(row for row in report["gates"] if row["name"] == "retirement_quality_gate")
    assert quality["status"] == "fail"
    rendered = json.dumps(report).lower()
    assert "private" not in rendered
    assert "secret" not in rendered


def test_retirement_gate_blocks_typed_state_trial_projection_or_extraction_loss():
    module = load_module()

    report = module.build_report(
        {
            "client_routing": {
                "codex": {"kontext": True, "mem0": False},
                "claude": {"kontext": True, "mem0": False},
                "chatgpt_visible": {"kontext": True, "mem0": False},
            },
            "kontext_health": {"ok": True, "mirror_count": 209224},
            "sync_proof": {"mem0_visible_ids": 4238, "kontext_matches": 4238, "missing": 0},
            "quality_gate": {
                "name": "locomo_longmem_paired_beam_replacement",
                "locomo30": {"passed": 28, "total": 30, "raw_payload_hits": 0, "runs_model_calls": True},
                "longmemeval30": {"passed": 24, "total": 30, "raw_payload_hits": 0, "runs_model_calls": True},
                "paired_beam": {
                    "questions": 6,
                    "same_question_hashes": True,
                    "kontext_passed": 4,
                    "mem0_passed": 4,
                    "raw_payload_hits": 0,
                    "runs_model_calls": True,
                    "legacy_backend": "legacy-mem0-offline",
                },
            },
            "canary": {"ok": True, "writes_applied": 0, "tool_count": 18},
            "write_audit": {"ok": True, "read_after_write_ok": True, "error_count": 0},
            "typed_state_trial": _typed_state_trial(
                counts={
                    "projection_selection_loss_questions": 1,
                    "state_event_extraction_loss_questions": 0,
                }
            ),
            "legacy_mem0": {
                "writes_enabled": False,
                "runtime_enabled": True,
                "recent_write_count": 0,
                "recent_read_count": 0,
                "monitor_hours": 72,
            },
            "backups": {"kontext_db": True, "mem0_export": True},
        }
    )

    assert report["ok"] is False
    typed_state = next(row for row in report["gates"] if row["name"] == "typed_state_trial_gate")
    assert typed_state["status"] == "fail"
    assert typed_state["evidence"]["projection_selection_loss_questions"] == 1
    rendered = json.dumps(report).lower()
    assert "private" not in rendered
    assert "secret" not in rendered
