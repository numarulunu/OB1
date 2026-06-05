from __future__ import annotations

from kontext_v2.retention import (
    cleanup_flag_blocked_for_protected_history,
    is_protected_autobiographical_history,
)


def test_relationship_psychology_history_is_permanently_retained() -> None:
    row = {
        "metadata": {"domains": ["relationships", "psychology"]},
        "memory_type": "relationship_pattern",
        "current_status": "active",
        "memory_tier": "historical",
        "signal_strength": 8,
    }

    assert is_protected_autobiographical_history(row)
    assert cleanup_flag_blocked_for_protected_history("delete_candidate", row)
    assert cleanup_flag_blocked_for_protected_history("stale_candidate", row)
    assert cleanup_flag_blocked_for_protected_history("archive_candidate", row)


def test_family_origin_boundary_variants_are_protected() -> None:
    variants = [
        {"metadata": {"domains": ["family-origin"]}, "memory_type": "note"},
        {"metadata": {"domains": ["family_origin"]}, "memory_type": "note"},
        {"metadata": {"domains": ["Family Origin"]}, "memory_type": "note"},
        {"metadata": {"domain": "family-origin"}, "memory_type": "note"},
        {"metadata": {}, "memory_type": "family-origin"},
    ]

    for row in variants:
        row["current_status"] = "active"
        assert is_protected_autobiographical_history(row)


def test_false_deleted_or_outdated_personal_history_can_still_be_corrected() -> None:
    row = {
        "metadata": {"domains": ["relationships", "psychology"]},
        "memory_type": "relationship_pattern",
        "current_status": "false",
        "memory_tier": "historical",
        "signal_strength": 8,
    }

    assert not is_protected_autobiographical_history(row)
    assert not cleanup_flag_blocked_for_protected_history("delete_candidate", row)
