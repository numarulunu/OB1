from __future__ import annotations

from kontext_v2.typed_state import (
    render_current_state_typed,
    summarize_typed_state_objects,
    typed_object_summary_enabled,
    typed_state_v2_enabled,
)


def test_typed_state_v2_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv("KONTEXT_TYPED_STATE_V2", raising=False)

    assert typed_state_v2_enabled() is False


def test_typed_state_v2_flag_accepts_truthy_values(monkeypatch):
    monkeypatch.setenv("KONTEXT_TYPED_STATE_V2", "1")

    assert typed_state_v2_enabled() is True


def test_typed_object_summary_flag_is_separate(monkeypatch):
    monkeypatch.setenv("KONTEXT_TYPED_OBJECT_SUMMARY", "true")
    monkeypatch.delenv("KONTEXT_TYPED_STATE_V2", raising=False)

    assert typed_object_summary_enabled() is True
    assert typed_state_v2_enabled() is False


def test_render_current_state_typed_uses_event_relation_not_fact_status():
    typed = render_current_state_typed(
        {
            "namespace": "live",
            "subject_id": "subject-1",
            "state_key": "project.status",
            "fact_value": {"text": "green"},
            "text": "Current project status is green.",
            "active_event_id": "event-new",
            "support_event_ids": ["event-new", "event-support"],
            "superseded_event_ids": ["event-old"],
            "cancelled_event_ids": [],
            "confidence": 0.92,
            "trust_tier": "benchmark_fixture",
            "state_status": "active",
            "projection_version": "projection-test",
            "metadata": {
                "private_debug": "must not be copied",
                "active_event_id": "event-new",
                "support_event_ids": ["event-new", "event-support"],
                "superseded_event_ids": ["event-old"],
            },
        }
    )

    assert typed["schema_version"] == "typed-state-v2"
    assert typed["status"] == "active"
    assert typed["event_relation"] == "supersedes"
    assert typed["active_event_id"] == "event-new"
    assert typed["support_event_ids"] == ["event-new", "event-support"]
    assert typed["superseded_event_ids"] == ["event-old"]
    assert typed["cancelled_event_ids"] == []
    assert typed["value"] == {"text": "green"}
    assert typed["value_text"] == "Current project status is green."
    assert typed["projection_version"] == "projection-test"
    assert "private_debug" not in typed


def test_render_current_state_typed_maps_cancelled_and_ambiguous_relations():
    cancelled = render_current_state_typed(
        {
            "namespace": "live",
            "subject_id": "subject-1",
            "state_key": "project.status",
            "state_status": "cancelled",
            "active_event_id": "event-cancel",
            "cancelled_event_ids": ["event-old"],
        }
    )
    ambiguous = render_current_state_typed(
        {
            "namespace": "live",
            "subject_id": "subject-1",
            "state_key": "project.status",
            "state_status": "ambiguous",
            "support_event_ids": ["event-a", "event-b"],
        }
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["event_relation"] == "cancels"
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["event_relation"] == "contradicts"


def test_render_current_state_typed_cancelled_status_overrides_stale_explicit_relation():
    typed = render_current_state_typed(
        {
            "namespace": "live",
            "subject_id": "subject-1",
            "state_key": "project.status",
            "state_status": "cancelled",
            "event_relation": "supports",
            "active_event_id": "event-cancel",
            "cancelled_event_ids": ["event-old"],
        }
    )

    assert typed["status"] == "cancelled"
    assert typed["event_relation"] == "cancels"


def test_render_current_state_typed_non_active_empty_fact_value_does_not_fall_back_to_stale_metadata_value():
    typed = render_current_state_typed(
        {
            "namespace": "live",
            "subject_id": "subject-1",
            "state_key": "project.status",
            "fact_value": {},
            "state_status": "cancelled",
            "metadata": {"fact_value": {"text": "stale active value"}},
            "active_event_id": "event-cancel",
            "cancelled_event_ids": ["event-old"],
        }
    )

    assert typed["status"] == "cancelled"
    assert typed["value"] == {}


def test_summarize_typed_state_objects_counts_without_value_text():
    summary = summarize_typed_state_objects(
        [
            {"schema_version": "typed-state-v2", "status": "active", "event_relation": "supersedes", "value_text": "raw"},
            {"schema_version": "typed-state-v2", "status": "cancelled", "event_relation": "cancels", "value": {"text": "raw"}},
            {"schema_version": "bad", "status": "active", "event_relation": "supports"},
        ]
    )

    assert summary == {
        "total": 3,
        "valid_schema": 2,
        "statuses": {"active": 2, "cancelled": 1},
        "event_relations": {"cancels": 1, "supersedes": 1, "supports": 1},
    }
