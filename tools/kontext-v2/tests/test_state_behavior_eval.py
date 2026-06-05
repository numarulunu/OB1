from __future__ import annotations

from pathlib import Path

from scripts.kontext_state_behavior_eval import run_eval, write_report


def test_state_behavior_eval_passes_all_cases_without_writes():
    report = run_eval()

    assert report["ok"] is True
    assert report["writes_applied"] == 0
    assert report["passed"] == report["total"]
    assert {case["id"] for case in report["cases"]} >= {
        "newer_active_fact_precedence",
        "superseded_fact_demotion",
        "cold_tier_demoted_by_default",
        "cold_tier_filter_is_explicit",
        "dry_run_delete_maps_to_delete_candidate",
        "missing_update_maps_to_conflict_candidate",
        "score_explanations_are_sanitized",
    }


def test_state_behavior_eval_report_is_sanitized_and_json_serializable(tmp_path: Path):
    output = tmp_path / "state-behavior.json"

    report = write_report(output)

    text = output.read_text(encoding="utf-8")
    assert report["ok"] is True
    assert "Kontext V2 currently runs" not in text
    assert "production writes are enabled" not in text
