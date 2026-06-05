import json
from pathlib import Path

from auto_rollout import (
    all_proposals_are_non_writes,
    auto_skip_junk_proposals,
    reconcile_execution_report,
    summary_counts_from_results,
)


def test_auto_skip_junk_proposals_demotes_only_deterministic_junk():
    report = {
        "summary": {"proposal_count": 2},
        "proposals": [
            {
                "action": "save",
                "content": "Lesson covered larynx, breath support, and resonance as generic technique notes.",
                "domains": ["vocality"],
                "memory_type": "lesson",
                "signal_strength": 5,
                "current_status": "active",
                "memory_tier": "active",
                "source_ids": ["row-1"],
                "reason": "candidate",
            },
            {
                "action": "save",
                "content": "Vocality business decision: keep the diagnostic-first positioning as the current brand strategy.",
                "domains": ["vocality", "business"],
                "memory_type": "decision",
                "signal_strength": 8,
                "current_status": "active",
                "memory_tier": "active",
                "source_ids": ["row-2"],
                "reason": "candidate",
            },
        ],
    }

    curated, changed, audit = auto_skip_junk_proposals(report)

    assert changed == [{"proposal_index": 1, "reason": "generic_vocal_lesson_sludge"}]
    assert curated["proposals"][0]["action"] == "skip"
    assert curated["proposals"][0]["memory_tier"] == "cold"
    assert curated["proposals"][1]["action"] == "save"
    assert audit["summary"]["status"] == "passed"
    assert curated["summary"]["metadata_quality"]["status"] == "passed"


def test_all_proposals_are_non_writes_only_allows_skip_or_ask_user():
    assert all_proposals_are_non_writes({"proposals": [{"action": "skip"}, {"action": "ask_user"}]})
    assert not all_proposals_are_non_writes({"proposals": [{"action": "skip"}, {"action": "save"}]})


def test_reconcile_execution_report_replaces_failed_result_and_recounts(tmp_path):
    output = tmp_path / "batch-apply-executed.json"
    output.write_text(
        json.dumps(
            {
                "summary": {
                    "counts": {"save": 1, "update": 0, "skip": 0, "ask_user": 0, "failed": 1},
                    "results": [
                        {"action": "save", "executed": False, "reason": "URLError"},
                        {"action": "save", "executed": True, "reason": "new_memory"},
                    ],
                },
                "plans": [{"proposal": {"content": "one"}}, {"proposal": {"content": "two"}}],
            }
        ),
        encoding="utf-8",
    )

    retry_results = {0: {"action": "save", "executed": True, "reason": "new_memory"}}
    reconciled = reconcile_execution_report(output, retry_results, retry_note="retry-001.json")

    assert reconciled["summary"]["counts"] == {"save": 2, "update": 0, "skip": 0, "ask_user": 0, "failed": 0}
    assert reconciled["summary"]["reconciled_retries"] == ["retry-001.json"]


def test_summary_counts_from_results_counts_failed_and_non_write_actions():
    results = [
        {"action": "save", "executed": True},
        {"action": "skip", "executed": False},
        {"action": "save", "executed": False, "reason": "URLError"},
    ]

    assert summary_counts_from_results(results) == {"save": 1, "update": 0, "skip": 1, "ask_user": 0, "failed": 1}
