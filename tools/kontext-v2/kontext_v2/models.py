from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryRecord:
    external_mem0_id: str
    title: str
    text: str
    metadata: dict[str, Any]
    memory_type: str
    current_status: str
    memory_tier: str
    signal_strength: float | None
    source_hash: str


@dataclass(frozen=True)
class StateSubject:
    id: str | None
    namespace: str
    subject_type: str
    subject_key: str
    display_name: str
    aliases: list[str]
    scope: dict[str, Any]
    created_at: Any = None
    updated_at: Any = None


@dataclass(frozen=True)
class StateEventCandidate:
    id: str | None
    namespace: str
    source_kind: str
    source_id: str
    source_hash: str
    source_span_hash: str
    subject_type: str
    subject_key: str
    state_key: str
    event_type: str
    value: dict[str, Any]
    value_text: str
    value_hash: str
    idempotency_key: str
    prior_value_text: str
    effective_at: Any
    observed_at: Any
    actor_role: str
    confidence: float
    trust_tier: str
    status: str
    extractor_version: str
    metadata: dict[str, Any]
    created_at: Any = None


@dataclass(frozen=True)
class StateEvent:
    id: str
    candidate_id: str | None
    namespace: str
    subject_id: str
    source_kind: str
    source_id: str
    source_hash: str
    source_span_hash: str
    event_hash: str
    state_key: str
    event_type: str
    value: dict[str, Any]
    value_text: str
    value_hash: str
    prior_value_text: str
    effective_at: Any
    observed_at: Any
    actor_role: str
    confidence: float
    trust_tier: str
    status: str
    extractor_version: str
    metadata: dict[str, Any]
    created_at: Any = None


@dataclass(frozen=True)
class StateEventEdge:
    id: int | None
    namespace: str
    source_event_id: str
    target_event_id: str
    edge_type: str
    state_key: str
    confidence: float
    reason_hash: str
    metadata: dict[str, Any]
    created_at: Any = None


@dataclass(frozen=True)
class CurrentStateFact:
    id: str | None
    namespace: str
    subject_id: str
    state_key: str
    fact_value: dict[str, Any]
    fact_text: str
    active_event_id: str | None
    support_event_ids: list[str]
    superseded_event_ids: list[str]
    cancelled_event_ids: list[str]
    confidence: float
    trust_tier: str
    status: str
    effective_at: Any
    projection_version: str
    metadata: dict[str, Any]
    rebuilt_at: Any = None
