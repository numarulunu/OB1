from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from kontext_v2.models import CurrentStateFact, StateEvent, StateEventEdge

VALID_EVENT_TYPES = {
    "assertion",
    "preference_set",
    "instruction_set",
    "knowledge_update",
    "status_change",
    "correction",
    "cancellation",
    "supersession",
}
VALUE_EVENT_TYPES = VALID_EVENT_TYPES - {"cancellation"}
VALID_EDGE_TYPES = {"supersedes", "cancels", "corrects", "supports", "contradicts"}
VALID_TRUST_TIERS = {
    "manual",
    "trusted_agent",
    "benchmark_fixture",
    "legacy_memory_import",
    "deterministic_import_mapping",
    "extracted_high",
    "extracted_low",
}
TRUST_TIER_RANK = {
    "legacy_memory_import": 1,
    "extracted_low": 2,
    "extracted_high": 3,
    "deterministic_import_mapping": 4,
    "benchmark_fixture": 5,
    "trusted_agent": 6,
    "manual": 7,
}
VALID_CANDIDATE_STATUSES = {"staged", "accepted", "rejected", "needs_review"}
VALID_FACT_STATUSES = {"active", "cancelled", "ambiguous", "unknown"}
DEFAULT_PROJECTION_VERSION = "state-projection-v1"


def _strip_diacritics(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", str(text or ""))
        if unicodedata.category(char) != "Mn"
    )


def _compact_slug(text: str, *, allow_dot: bool = False, allow_colon: bool = False) -> str:
    normalized = _strip_diacritics(str(text or "").strip().lower())
    chars: list[str] = []
    for char in normalized:
        if char.isalnum():
            chars.append(char)
        elif allow_dot and char in {".", "/"}:
            chars.append(".")
        elif allow_colon and char == ":":
            chars.append(":")
        elif char in {"-", "_", " ", "\\", "/"}:
            chars.append("-")
        else:
            chars.append("-")
    slug = "".join(chars)
    slug = re.sub(r"-+", "-", slug)
    slug = re.sub(r"\.+", ".", slug)
    if allow_colon:
        slug = re.sub(r":-+", ":", slug)
    slug = slug.strip("-.")
    return slug or "unknown"


def normalize_namespace(value: Any) -> str:
    return _compact_slug(str(value or "live"), allow_colon=True)


def normalize_subject_type(value: Any) -> str:
    return _compact_slug(str(value or "subject")).replace("-", "_")


def normalize_subject_key(value: Any) -> str:
    return _compact_slug(str(value or "unknown"))


def normalize_state_key(value: Any) -> str:
    key = _compact_slug(str(value or "state"), allow_dot=True).replace("-", "_")
    return re.sub(r"_*\._*", ".", key).strip(".") or "state"


def canonical_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def value_normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): value_normalize(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [value_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [value_normalize(item) for item in value]
    if isinstance(value, str):
        text = unicodedata.normalize("NFC", value)
        text = _strip_diacritics(text)
        return re.sub(r"\s+", " ", text).strip()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def value_hash(value: Any) -> str:
    return stable_hash(canonical_json(value_normalize(value)))


def idempotency_key(
    *,
    namespace: str,
    source_hash: str,
    source_span_hash: str = "",
    extractor_version: str = "",
    subject_type: str,
    subject_key: str,
    state_key: str,
    event_type: str,
    value_hash: str,
) -> str:
    payload = {
        "namespace": normalize_namespace(namespace),
        "source_hash": str(source_hash or ""),
        "source_span_hash": str(source_span_hash or ""),
        "extractor_version": str(extractor_version or ""),
        "subject_type": normalize_subject_type(subject_type),
        "subject_key": normalize_subject_key(subject_key),
        "state_key": normalize_state_key(state_key),
        "event_type": str(event_type or "").strip().lower(),
        "value_hash": str(value_hash or ""),
    }
    return stable_hash(canonical_json(payload))


def event_hash(
    *,
    namespace: str,
    subject_id: str,
    source_hash: str,
    source_span_hash: str = "",
    extractor_version: str = "",
    state_key: str,
    event_type: str,
    value_hash: str,
    effective_at: Any = None,
    observed_at: Any = None,
) -> str:
    payload = {
        "namespace": normalize_namespace(namespace),
        "subject_id": str(subject_id or ""),
        "source_hash": str(source_hash or ""),
        "source_span_hash": str(source_span_hash or ""),
        "extractor_version": str(extractor_version or ""),
        "state_key": normalize_state_key(state_key),
        "event_type": str(event_type or "").strip().lower(),
        "value_hash": str(value_hash or ""),
        "effective_at": str(effective_at or ""),
        "observed_at": str(observed_at or ""),
    }
    return stable_hash(canonical_json(payload))


def validate_event_type(event_type: str) -> str:
    value = str(event_type or "").strip().lower()
    if value not in VALID_EVENT_TYPES:
        raise ValueError(f"Unsupported state event type: {event_type}")
    return value


def validate_edge_type(edge_type: str) -> str:
    value = str(edge_type or "").strip().lower()
    if value not in VALID_EDGE_TYPES:
        raise ValueError(f"Unsupported state edge type: {edge_type}")
    return value


def _event_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if text:
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _sort_events(events: list[StateEvent]) -> list[StateEvent]:
    return sorted(
        events,
        key=lambda event: (_event_time(event.effective_at or event.observed_at or event.created_at), _event_rank(event)),
    )


def _trust_rank(event: StateEvent) -> int:
    return TRUST_TIER_RANK.get(str(event.trust_tier or "").strip().lower(), 0)


def _extractor_version_rank(value: Any) -> tuple[str, int]:
    text = str(value or "").strip().lower()
    match = re.search(r"(?:^|[^0-9])v?(\d+)(?:[^0-9]*)$", text)
    if match:
        return (re.sub(r"v?\d+[^0-9]*$", "", text), int(match.group(1)))
    return (text, 0)


def _event_rank(event: StateEvent) -> tuple[int, tuple[str, int], str]:
    return (_trust_rank(event), _extractor_version_rank(event.extractor_version), str(event.id))


def _value_resolution_rank(event: StateEvent) -> tuple[int, datetime, tuple[str, int]]:
    return (
        _trust_rank(event),
        _event_time(event.effective_at or event.observed_at or event.created_at),
        _extractor_version_rank(event.extractor_version),
    )


def _edge_ids(edges: list[StateEventEdge], edge_type: str) -> set[str]:
    return {
        str(edge.target_event_id)
        for edge in edges
        if validate_edge_type(edge.edge_type) == edge_type
    }


def _edge_related_ids(edges: list[StateEventEdge], edge_type: str) -> set[str]:
    ids: set[str] = set()
    for edge in edges:
        if validate_edge_type(edge.edge_type) != edge_type:
            continue
        ids.add(str(edge.source_event_id))
        ids.add(str(edge.target_event_id))
    return ids


def _unique_ids(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _event_group_key(event: StateEvent) -> tuple[str, str]:
    return (str(event.source_hash or ""), str(event.source_span_hash or ""))


def _collapse_extractor_replays(events: list[StateEvent]) -> tuple[list[StateEvent], list[str]]:
    by_source_span: dict[tuple[str, str], list[StateEvent]] = defaultdict(list)
    passthrough: list[StateEvent] = []
    for event in events:
        key = _event_group_key(event)
        if key[0] and key[1]:
            by_source_span[key].append(event)
        else:
            passthrough.append(event)

    collapsed = list(passthrough)
    overridden: list[str] = []
    for grouped_events in by_source_span.values():
        if len(grouped_events) == 1:
            collapsed.extend(grouped_events)
            continue
        versions = {str(event.extractor_version or "") for event in grouped_events}
        if len(versions) <= 1:
            collapsed.extend(grouped_events)
            continue
        sorted_group = sorted(
            grouped_events,
            key=lambda event: (
                _extractor_version_rank(event.extractor_version),
                _trust_rank(event),
                _event_time(event.effective_at or event.observed_at or event.created_at),
                str(event.id),
            ),
        )
        winner = sorted_group[-1]
        collapsed.append(winner)
        overridden.extend(str(event.id) for event in sorted_group[:-1])
    return _sort_events(collapsed), sorted(overridden)


def _clear_trust_winner(events: list[StateEvent]) -> StateEvent | None:
    if len(events) < 2:
        return events[-1] if events else None
    ranked = sorted(events, key=lambda event: (_value_resolution_rank(event), str(event.id)))
    winner = ranked[-1]
    runner_up = ranked[-2]
    if _value_resolution_rank(winner) > _value_resolution_rank(runner_up):
        return winner
    return None


def build_current_state_facts(
    events: list[StateEvent],
    edges: list[StateEventEdge],
    *,
    projection_version: str = DEFAULT_PROJECTION_VERSION,
    typed_answer_emitter: Callable[[CurrentStateFact], dict[str, Any] | None] | None = None,
) -> list[CurrentStateFact]:
    accepted_events = [event for event in events if str(event.status or "accepted").lower() == "accepted"]
    events_by_group: dict[tuple[str, str, str], list[StateEvent]] = defaultdict(list)
    edges_by_group: dict[tuple[str, str, str], list[StateEventEdge]] = defaultdict(list)
    event_by_id = {str(event.id): event for event in accepted_events}

    for event in accepted_events:
        events_by_group[(normalize_namespace(event.namespace), event.subject_id, normalize_state_key(event.state_key))].append(event)
    for edge in edges:
        source = event_by_id.get(str(edge.source_event_id))
        target = event_by_id.get(str(edge.target_event_id))
        if source is None or target is None:
            continue
        source_namespace = normalize_namespace(source.namespace)
        target_namespace = normalize_namespace(target.namespace)
        if source_namespace != target_namespace or source.subject_id != target.subject_id:
            continue
        edges_by_group[(source_namespace, source.subject_id, normalize_state_key(source.state_key))].append(edge)

    facts: list[CurrentStateFact] = []
    for (namespace, subject_id, state_key), group_events in sorted(events_by_group.items()):
        sorted_events = _sort_events(group_events)
        group_edges = edges_by_group.get((namespace, subject_id, state_key), [])
        superseded_ids = _edge_ids(group_edges, "supersedes") | _edge_ids(group_edges, "corrects")
        cancelled_ids = _edge_ids(group_edges, "cancels")
        terminal_ids = superseded_ids | cancelled_ids
        supporting_ids = _edge_related_ids(group_edges, "supports")
        contradicting_ids = _edge_related_ids(group_edges, "contradicts") - terminal_ids
        cancellation_events = [event for event in sorted_events if validate_event_type(event.event_type) == "cancellation"]
        raw_value_events = [
            event
            for event in sorted_events
            if validate_event_type(event.event_type) in VALUE_EVENT_TYPES and str(event.id) not in terminal_ids
        ]
        raw_cancelled_ids: set[str] = set()
        if cancellation_events:
            raw_cancel_time = max(
                _event_time(event.effective_at or event.observed_at or event.created_at)
                for event in cancellation_events
            )
            filtered_value_events: list[StateEvent] = []
            for event in raw_value_events:
                if _event_time(event.effective_at or event.observed_at or event.created_at) <= raw_cancel_time:
                    raw_cancelled_ids.add(str(event.id))
                else:
                    filtered_value_events.append(event)
            raw_value_events = filtered_value_events
        value_events, extractor_overridden_ids = _collapse_extractor_replays(raw_value_events)
        latest_event = sorted_events[-1] if sorted_events else None

        if cancellation_events and not value_events:
            active_cancel = cancellation_events[-1]
            fact = CurrentStateFact(
                id=None,
                namespace=namespace,
                subject_id=subject_id,
                state_key=state_key,
                fact_value={},
                fact_text=str(active_cancel.value_text or ""),
                active_event_id=str(active_cancel.id),
                support_event_ids=[],
                superseded_event_ids=sorted(superseded_ids | set(extractor_overridden_ids)),
                cancelled_event_ids=sorted(cancelled_ids | raw_cancelled_ids),
                confidence=float(active_cancel.confidence or 0.0),
                trust_tier=str(active_cancel.trust_tier or ""),
                status="cancelled",
                effective_at=active_cancel.effective_at,
                projection_version=projection_version,
                metadata={},
            )
        elif value_events:
            value_hashes = {event.value_hash for event in value_events}
            trust_winner = _clear_trust_winner(value_events) if len(value_hashes) > 1 and not contradicting_ids else None
            if trust_winner is not None:
                active = trust_winner
                overridden_ids = sorted(
                    {str(event.id) for event in value_events if str(event.id) != str(active.id)} | set(extractor_overridden_ids)
                )
                support_event_ids = _unique_ids(
                    [str(event.id) for event in value_events if event.value_hash == active.value_hash]
                    + sorted(supporting_ids)
                )
                fact = CurrentStateFact(
                    id=None,
                    namespace=namespace,
                    subject_id=subject_id,
                    state_key=state_key,
                    fact_value=dict(active.value or {}),
                    fact_text=str(active.value_text or ""),
                    active_event_id=str(active.id),
                    support_event_ids=support_event_ids,
                    superseded_event_ids=sorted(superseded_ids | set(overridden_ids)),
                    cancelled_event_ids=sorted(cancelled_ids | raw_cancelled_ids),
                    confidence=float(active.confidence or 0.0),
                    trust_tier=str(active.trust_tier or ""),
                    status="active",
                    effective_at=active.effective_at,
                    projection_version=projection_version,
                    metadata={
                        "active_value_hash": active.value_hash,
                        "supporting_event_ids": support_event_ids,
                        "overridden_event_ids": overridden_ids,
                    },
                )
            elif len(value_hashes) > 1 or contradicting_ids:
                support_event_ids = _unique_ids([str(event.id) for event in value_events] + sorted(contradicting_ids))
                fact = CurrentStateFact(
                    id=None,
                    namespace=namespace,
                    subject_id=subject_id,
                    state_key=state_key,
                    fact_value={},
                    fact_text="Ambiguous current state.",
                    active_event_id=None,
                    support_event_ids=support_event_ids,
                    superseded_event_ids=sorted(superseded_ids | set(extractor_overridden_ids)),
                    cancelled_event_ids=sorted(cancelled_ids | raw_cancelled_ids),
                    confidence=0.0,
                    trust_tier="ambiguous",
                    status="ambiguous",
                    effective_at=value_events[-1].effective_at,
                    projection_version=projection_version,
                    metadata={
                        "active_value_hashes": sorted(value_hashes),
                        "contradicting_event_ids": sorted(contradicting_ids),
                    },
                )
            else:
                active = value_events[-1]
                support_event_ids = _unique_ids(
                    [str(event.id) for event in value_events if event.value_hash == active.value_hash]
                    + sorted(supporting_ids)
                )
                fact = CurrentStateFact(
                    id=None,
                    namespace=namespace,
                    subject_id=subject_id,
                    state_key=state_key,
                    fact_value=dict(active.value or {}),
                    fact_text=str(active.value_text or ""),
                    active_event_id=str(active.id),
                    support_event_ids=support_event_ids,
                    superseded_event_ids=sorted(superseded_ids | set(extractor_overridden_ids)),
                    cancelled_event_ids=sorted(cancelled_ids | raw_cancelled_ids),
                    confidence=float(active.confidence or 0.0),
                    trust_tier=str(active.trust_tier or ""),
                    status="active",
                    effective_at=active.effective_at,
                    projection_version=projection_version,
                    metadata={
                        "active_value_hash": active.value_hash,
                        "supporting_event_ids": support_event_ids,
                    },
                )
        elif cancellation_events or cancelled_ids:
            active_cancel = cancellation_events[-1] if cancellation_events else None
            fact = CurrentStateFact(
                id=None,
                namespace=namespace,
                subject_id=subject_id,
                state_key=state_key,
                fact_value={},
                fact_text=str(active_cancel.value_text if active_cancel else ""),
                active_event_id=str(active_cancel.id) if active_cancel else None,
                support_event_ids=[],
                superseded_event_ids=sorted(superseded_ids),
                cancelled_event_ids=sorted(cancelled_ids | raw_cancelled_ids),
                confidence=float(active_cancel.confidence or 0.0) if active_cancel else 0.0,
                trust_tier=str(active_cancel.trust_tier or "") if active_cancel else "",
                status="cancelled",
                effective_at=active_cancel.effective_at if active_cancel else None,
                projection_version=projection_version,
                metadata={},
            )
        else:
            fact = CurrentStateFact(
                id=None,
                namespace=namespace,
                subject_id=subject_id,
                state_key=state_key,
                fact_value={},
                fact_text="Unknown current state.",
                active_event_id=None,
                support_event_ids=[],
                superseded_event_ids=sorted(superseded_ids),
                cancelled_event_ids=sorted(cancelled_ids),
                confidence=0.0,
                trust_tier="",
                status="unknown",
                effective_at=None,
                projection_version=projection_version,
                metadata={},
            )
        if typed_answer_emitter is not None:
            typed_answer = typed_answer_emitter(fact)
            if isinstance(typed_answer, dict):
                metadata = dict(fact.metadata or {})
                metadata["typed_state_v2"] = typed_answer
                fact = replace(fact, metadata=metadata)
        facts.append(fact)
    return facts
