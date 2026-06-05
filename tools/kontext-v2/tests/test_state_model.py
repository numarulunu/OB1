from __future__ import annotations

from kontext_v2.models import StateEvent, StateEventEdge
from kontext_v2.state_model import (
    build_current_state_facts,
    event_hash,
    idempotency_key,
    normalize_namespace,
    normalize_state_key,
    normalize_subject_key,
    normalize_subject_type,
    value_hash,
)


def _event(
    event_id: str,
    *,
    value_text: str,
    value: dict | None = None,
    event_type: str = "assertion",
    namespace: str = "live",
    subject_id: str = "subject-1",
    state_key: str = "project.status",
    source_hash: str | None = None,
    source_span_hash: str = "span-1",
    trust_tier: str = "benchmark_fixture",
    extractor_version: str = "fixture-v1",
    effective_at: str = "2026-06-01T00:00:00+00:00",
) -> StateEvent:
    payload = value if value is not None else {"text": value_text}
    return StateEvent(
        id=event_id,
        candidate_id=None,
        namespace=namespace,
        subject_id=subject_id,
        source_kind="fixture",
        source_id="source-1",
        source_hash=source_hash or f"source-{event_id}",
        source_span_hash=source_span_hash,
        event_hash=f"hash-{event_id}",
        state_key=state_key,
        event_type=event_type,
        value=payload,
        value_text=value_text,
        value_hash=value_hash(payload),
        prior_value_text="",
        effective_at=effective_at,
        observed_at=effective_at,
        actor_role="user",
        confidence=0.95,
        trust_tier=trust_tier,
        status="accepted",
        extractor_version=extractor_version,
        metadata={},
        created_at=effective_at,
    )


def _edge(source: str, target: str, edge_type: str, state_key: str = "project.status") -> StateEventEdge:
    return StateEventEdge(
        id=None,
        namespace="live",
        source_event_id=source,
        target_event_id=target,
        edge_type=edge_type,
        state_key=state_key,
        confidence=1.0,
        reason_hash="reason-1",
        metadata={},
        created_at=None,
    )


def test_state_identity_normalization_is_stable():
    assert normalize_namespace("Benchmark: Run 1") == "benchmark:run-1"
    assert normalize_subject_type(" Project State ") == "project_state"
    assert normalize_subject_key(" OB1 / Kontext V2 ") == "ob1-kontext-v2"
    assert normalize_state_key("Project Status / Current") == "project_status.current"


def test_hashes_are_stable_and_semantic():
    first = {"text": "green", "count": 1}
    second = {"count": 1, "text": "green"}

    assert value_hash(first) == value_hash(second)
    assert idempotency_key(
        namespace="live",
        source_hash="source-a",
        source_span_hash="span-a",
        extractor_version="fixture-v1",
        subject_type="project",
        subject_key="ob1",
        state_key="project.status",
        event_type="assertion",
        value_hash=value_hash(first),
    ) == idempotency_key(
        namespace="live",
        source_hash="source-a",
        source_span_hash="span-a",
        extractor_version="fixture-v1",
        subject_type="project",
        subject_key="ob1",
        state_key="project.status",
        event_type="assertion",
        value_hash=value_hash(second),
    )
    assert event_hash(
        namespace="live",
        subject_id="subject-1",
        source_hash="source-a",
        source_span_hash="span-a",
        extractor_version="fixture-v1",
        state_key="project.status",
        event_type="assertion",
        value_hash=value_hash(first),
    ) != event_hash(
        namespace="live",
        subject_id="subject-2",
        source_hash="source-a",
        source_span_hash="span-a",
        extractor_version="fixture-v1",
        state_key="project.status",
        event_type="assertion",
        value_hash=value_hash(first),
    )


def test_event_hash_distinguishes_reassertions_at_different_times():
    payload_hash = value_hash({"text": "Current status is green."})

    first = event_hash(
        namespace="live",
        subject_id="subject-1",
        source_hash="source-a",
        source_span_hash="span-a",
        extractor_version="fixture-v1",
        state_key="project.status",
        event_type="assertion",
        value_hash=payload_hash,
        effective_at="2026-06-01T00:00:00+00:00",
        observed_at="2026-06-01T00:00:00+00:00",
    )
    reasserted = event_hash(
        namespace="live",
        subject_id="subject-1",
        source_hash="source-a",
        source_span_hash="span-a",
        extractor_version="fixture-v1",
        state_key="project.status",
        event_type="assertion",
        value_hash=payload_hash,
        effective_at="2026-06-03T00:00:00+00:00",
        observed_at="2026-06-03T00:00:00+00:00",
    )

    assert first != reasserted


def test_value_hash_normalizes_unicode_diacritics_and_numeric_shape_without_casefolding_names():
    assert value_hash({"text": "  Ionuț  "}) == value_hash({"text": "Ionut"})
    assert value_hash({"text": "Ionut"}) != value_hash({"text": "ionut"})
    assert value_hash({"count": 1.0}) == value_hash({"count": 1})


def test_projection_keeps_latest_superseding_fact_active():
    old = _event("event-old", value_text="OB1 state model is not built yet.")
    new = _event("event-new", value_text="OB1 state model foundation is implemented.")
    facts = build_current_state_facts(
        [old, new],
        [_edge("event-new", "event-old", "supersedes")],
        projection_version="projection-test",
    )

    assert len(facts) == 1
    fact = facts[0]
    assert fact.status == "active"
    assert fact.active_event_id == "event-new"
    assert fact.fact_text == "OB1 state model foundation is implemented."
    assert fact.superseded_event_ids == ["event-old"]


def test_projection_marks_cancelled_fact_without_deleting_history():
    old = _event("event-old", value_text="Use the old current state.")
    cancellation = _event(
        "event-cancel",
        value={},
        value_text="Cancel the old current state.",
        event_type="cancellation",
        effective_at="2026-06-02T00:00:00+00:00",
    )
    facts = build_current_state_facts(
        [old, cancellation],
        [_edge("event-cancel", "event-old", "cancels")],
        projection_version="projection-test",
    )

    assert len(facts) == 1
    fact = facts[0]
    assert fact.status == "cancelled"
    assert fact.active_event_id == "event-cancel"
    assert fact.cancelled_event_ids == ["event-old"]
    assert fact.support_event_ids == []


def test_projection_marks_unresolved_conflict_as_ambiguous():
    first = _event("event-a", value_text="Current status is green.")
    second = _event("event-b", value_text="Current status is red.")
    facts = build_current_state_facts([first, second], [], projection_version="projection-test")

    assert len(facts) == 1
    fact = facts[0]
    assert fact.status == "ambiguous"
    assert fact.active_event_id is None
    assert fact.support_event_ids == ["event-a", "event-b"]


def test_projection_prefers_newer_same_tier_value_without_same_order_contradiction():
    first = _event("event-a", value_text="Current status is red.", effective_at="2026-06-01T00:00:00+00:00")
    second = _event("event-b", value_text="Current status is green.", effective_at="2026-06-02T00:00:00+00:00")

    facts = build_current_state_facts([first, second], [], projection_version="projection-test")

    assert len(facts) == 1
    assert facts[0].status == "active"
    assert facts[0].active_event_id == "event-b"
    assert facts[0].superseded_event_ids == ["event-a"]


def test_projection_grouping_normalizes_namespace():
    old = _event("event-old", namespace="live", value_text="Current status is red.")
    new = _event(
        "event-new",
        namespace="LIVE ",
        value_text="Current status is green.",
        effective_at="2026-06-02T00:00:00+00:00",
    )

    facts = build_current_state_facts(
        [old, new],
        [_edge("event-new", "event-old", "supersedes")],
        projection_version="projection-test",
    )

    assert len(facts) == 1
    assert facts[0].namespace == "live"
    assert facts[0].active_event_id == "event-new"
    assert facts[0].superseded_event_ids == ["event-old"]


def test_projection_precedence_prefers_higher_extractor_version_for_same_source_span():
    v1 = _event(
        "event-v1",
        value_text="Current status is red.",
        source_hash="source-same",
        source_span_hash="span-same",
        extractor_version="state-extractor-v1",
    )
    v2 = _event(
        "event-v2",
        value_text="Current status is green.",
        source_hash="source-same",
        source_span_hash="span-same",
        extractor_version="state-extractor-v2",
    )

    facts = build_current_state_facts([v1, v2], [], projection_version="projection-test")

    assert len(facts) == 1
    assert facts[0].status == "active"
    assert facts[0].active_event_id == "event-v2"
    assert facts[0].fact_text == "Current status is green."


def test_projection_precedence_prefers_higher_extractor_version_over_newer_event_time_for_same_source_span():
    v1_later = _event(
        "event-v1",
        value_text="Current status is red.",
        source_hash="source-same",
        source_span_hash="span-same",
        extractor_version="state-extractor-v1",
        effective_at="2026-06-03T00:00:00+00:00",
    )
    v2_older = _event(
        "event-v2",
        value_text="Current status is green.",
        source_hash="source-same",
        source_span_hash="span-same",
        extractor_version="state-extractor-v2",
        effective_at="2026-06-02T00:00:00+00:00",
    )

    facts = build_current_state_facts([v1_later, v2_older], [], projection_version="projection-test")

    assert len(facts) == 1
    assert facts[0].status == "active"
    assert facts[0].active_event_id == "event-v2"
    assert facts[0].superseded_event_ids == ["event-v1"]


def test_projection_same_source_span_same_extractor_conflict_stays_ambiguous():
    first = _event(
        "event-first",
        value_text="Current status is red.",
        source_hash="source-same",
        source_span_hash="span-same",
        extractor_version="state-extractor-v1",
    )
    second = _event(
        "event-second",
        value_text="Current status is green.",
        source_hash="source-same",
        source_span_hash="span-same",
        extractor_version="state-extractor-v1",
    )

    facts = build_current_state_facts([first, second], [], projection_version="projection-test")

    assert len(facts) == 1
    assert facts[0].status == "ambiguous"
    assert facts[0].active_event_id is None


def test_projection_picks_manual_over_extracted_low_conflict():
    extracted = _event(
        "event-low",
        value_text="Current status is red.",
        trust_tier="extracted_low",
    )
    manual = _event(
        "event-manual",
        value_text="Current status is green.",
        trust_tier="manual",
    )

    facts = build_current_state_facts([extracted, manual], [], projection_version="projection-test")

    assert len(facts) == 1
    assert facts[0].status == "active"
    assert facts[0].active_event_id == "event-manual"
    assert facts[0].fact_text == "Current status is green."
    assert facts[0].metadata["overridden_event_ids"] == ["event-low"]


def test_projection_raw_cancellation_terminates_prior_value_events_without_edge():
    old = _event("event-old", value_text="Current status is green.")
    cancellation = _event(
        "event-cancel",
        value={},
        value_text="Cancel current status.",
        event_type="cancellation",
        effective_at="2026-06-02T00:00:00+00:00",
    )

    facts = build_current_state_facts([old, cancellation], [], projection_version="projection-test")

    assert len(facts) == 1
    assert facts[0].status == "cancelled"
    assert facts[0].active_event_id == "event-cancel"


def test_projection_raw_cancellation_terminates_equal_time_value_without_edge():
    value = _event("event-value", value_text="Current status is green.", effective_at="2026-06-02T00:00:00+00:00")
    cancellation = _event(
        "event-cancel",
        value={},
        value_text="Cancel current status.",
        event_type="cancellation",
        effective_at="2026-06-02T00:00:00+00:00",
    )

    facts = build_current_state_facts([value, cancellation], [], projection_version="projection-test")

    assert len(facts) == 1
    assert facts[0].status == "cancelled"
    assert facts[0].active_event_id == "event-cancel"
    assert facts[0].cancelled_event_ids == ["event-value"]


def test_projection_raw_cancellation_marks_prior_values_cancelled_even_when_newer_value_reasserts():
    old = _event("event-old", value_text="Current status is red.", effective_at="2026-06-01T00:00:00+00:00")
    cancellation = _event(
        "event-cancel",
        value={},
        value_text="Cancel current status.",
        event_type="cancellation",
        effective_at="2026-06-02T00:00:00+00:00",
    )
    reasserted = _event("event-new", value_text="Current status is green.", effective_at="2026-06-03T00:00:00+00:00")

    facts = build_current_state_facts([old, cancellation, reasserted], [], projection_version="projection-test")

    assert len(facts) == 1
    assert facts[0].status == "active"
    assert facts[0].active_event_id == "event-new"
    assert facts[0].cancelled_event_ids == ["event-old"]


def test_projection_buckets_terminal_edge_by_source_state_key_when_edge_key_drifted():
    old = _event("event-old", value_text="Current status is red.", effective_at="2026-06-01T00:00:00+00:00")
    new = _event("event-new", value_text="Current status is green.", effective_at="2026-06-02T00:00:00+00:00")

    facts = build_current_state_facts(
        [old, new],
        [_edge("event-new", "event-old", "supersedes", state_key="project.status.drifted")],
        projection_version="projection-test",
    )

    assert len(facts) == 1
    assert facts[0].status == "active"
    assert facts[0].active_event_id == "event-new"
    assert facts[0].superseded_event_ids == ["event-old"]


def test_projection_marks_contradiction_edge_without_supersession_as_ambiguous():
    first = _event("event-a", value_text="Current status is green.")
    second = _event("event-b", value_text="Current status is green.")

    facts = build_current_state_facts(
        [first, second],
        [_edge("event-b", "event-a", "contradicts")],
        projection_version="projection-test",
    )

    assert len(facts) == 1
    fact = facts[0]
    assert fact.status == "ambiguous"
    assert fact.active_event_id is None
    assert fact.support_event_ids == ["event-a", "event-b"]
    assert fact.metadata["contradicting_event_ids"] == ["event-a", "event-b"]


def test_projection_supports_edges_add_support_event_ids_for_active_fact():
    old = _event("event-old", value_text="Current status was green.")
    new = _event("event-new", value_text="Current status is green.")

    facts = build_current_state_facts(
        [old, new],
        [
            _edge("event-new", "event-old", "supersedes"),
            _edge("event-new", "event-old", "supports"),
        ],
        projection_version="projection-test",
    )

    assert len(facts) == 1
    fact = facts[0]
    assert fact.status == "active"
    assert fact.active_event_id == "event-new"
    assert fact.support_event_ids == ["event-new", "event-old"]
    assert fact.metadata["supporting_event_ids"] == ["event-new", "event-old"]


def test_projection_collapses_three_step_supersession_chain():
    first = _event("event-a", value_text="Current status is red.", effective_at="2026-06-01T00:00:00+00:00")
    second = _event("event-b", value_text="Current status is amber.", effective_at="2026-06-02T00:00:00+00:00")
    third = _event("event-c", value_text="Current status is green.", effective_at="2026-06-03T00:00:00+00:00")

    facts = build_current_state_facts(
        [first, second, third],
        [
            _edge("event-b", "event-a", "supersedes"),
            _edge("event-c", "event-b", "supersedes"),
        ],
        projection_version="projection-test",
    )

    assert len(facts) == 1
    fact = facts[0]
    assert fact.status == "active"
    assert fact.active_event_id == "event-c"
    assert fact.superseded_event_ids == ["event-a", "event-b"]


def test_projection_can_attach_typed_state_metadata_with_emitter():
    current = _event("event-current", value_text="Current status is green.")
    emitted: list[str] = []

    def emitter(fact):
        emitted.append(fact.active_event_id or "")
        return {
            "schema_version": "typed-state-v2",
            "status": fact.status,
            "event_relation": "supports",
            "active_event_id": fact.active_event_id,
        }

    facts = build_current_state_facts(
        [current],
        [],
        projection_version="projection-test",
        typed_answer_emitter=emitter,
    )

    assert emitted == ["event-current"]
    assert facts[0].metadata["typed_state_v2"] == {
        "schema_version": "typed-state-v2",
        "status": "active",
        "event_relation": "supports",
        "active_event_id": "event-current",
    }


def test_projection_does_not_attach_typed_state_metadata_without_emitter():
    current = _event("event-current", value_text="Current status is green.")

    facts = build_current_state_facts([current], [], projection_version="projection-test")

    assert "typed_state_v2" not in facts[0].metadata
