from __future__ import annotations

import json
from dataclasses import asdict

from kontext_v2.models import StateEvent, StateEventEdge
from kontext_v2.state_model import build_current_state_facts, value_hash


def _event(event_id: str, value_text: str, effective_at: str) -> StateEvent:
    value = {"text": value_text}
    return StateEvent(
        id=event_id,
        candidate_id=None,
        namespace="LIVE ",
        subject_id="subject-1",
        source_kind="fixture",
        source_id="source-1",
        source_hash=f"source-{event_id}",
        source_span_hash=f"span-{event_id}",
        event_hash=f"event-hash-{event_id}",
        state_key="Project Status",
        event_type="assertion",
        value=value,
        value_text=value_text,
        value_hash=value_hash(value),
        prior_value_text="",
        effective_at=effective_at,
        observed_at=effective_at,
        actor_role="agent",
        confidence=0.9,
        trust_tier="benchmark_fixture",
        status="accepted",
        extractor_version="fixture-v1",
        metadata={"source": event_id},
        created_at=effective_at,
    )


def _edge(source: str, target: str) -> StateEventEdge:
    return StateEventEdge(
        id=None,
        namespace="live",
        source_event_id=source,
        target_event_id=target,
        edge_type="supersedes",
        state_key="project.status",
        confidence=1.0,
        reason_hash=f"reason-{source}-{target}",
        metadata={},
        created_at=None,
    )


def _render(events: list[StateEvent], edges: list[StateEventEdge]) -> str:
    facts = build_current_state_facts(events, edges, projection_version="determinism-test")
    return json.dumps([asdict(fact) for fact in facts], sort_keys=True, default=str, separators=(",", ":"))


def test_projection_is_byte_identical_across_replayed_input_order():
    first = _event("event-a", "Status was red.", "2026-06-01T00:00:00+00:00")
    second = _event("event-b", "Status was amber.", "2026-06-02T00:00:00+00:00")
    third = _event("event-c", "Status is green.", "2026-06-03T00:00:00+00:00")
    edges = [_edge("event-b", "event-a"), _edge("event-c", "event-b")]

    baseline = _render([first, second, third], edges)
    replayed = _render([third, first, second], list(reversed(edges)))

    assert replayed == baseline
