from __future__ import annotations

import argparse
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from kontext_v2.benchmarks.adapter import KontextBenchmarkAdapter, stable_hash
from kontext_v2.benchmarks.beam_predict import DEFAULT_BEAM_FIXTURE, _fixture_for_run
from kontext_v2.benchmarks.locomo_predict import _add_conversations, _clear_existing_benchmark_rows, _run_question_searches
from kontext_v2.models import CurrentStateFact, StateEvent, StateEventEdge
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import search_memories
from kontext_v2.schema import apply_schema
from kontext_v2.state_model import (
    build_current_state_facts,
    normalize_namespace,
    normalize_state_key,
    normalize_subject_key,
    normalize_subject_type,
    value_hash as make_value_hash,
)
from kontext_v2.state_ingestion import accept_reviewed_state_candidate, stage_state_event_proposals
from kontext_v2.typed_state import (
    render_current_state_typed,
    summarize_typed_state_objects,
    typed_object_summary_enabled,
    typed_state_v2_enabled,
)


STATE_QUESTION_CATEGORIES = {
    "instruction_following": "instruction_set",
    "knowledge_update": "knowledge_update",
    "preference_following": "preference_set",
}
DEFAULT_EXTRACTOR_VERSION = "benchmark-state-trial-v1"
PRIVATE_BUNDLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "should",
    "that",
    "the",
    "this",
    "to",
    "use",
    "user",
    "what",
    "with",
}
PRIVATE_ROLE_RE = re.compile(r"^\s*(user|assistant|system)\s*:\s*(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class BenchmarkStatePlan:
    namespace: str
    source_hash: str
    extractor_version: str
    state_events: list[dict[str, Any]]
    questions: list[dict[str, Any]]


def _benchmark_enabled() -> bool:
    return str(os.environ.get("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def _benchmark_trial_namespace(namespace: str) -> str:
    raw_namespace = str(namespace or "").strip()
    normalized = normalize_namespace(raw_namespace)
    if not raw_namespace or normalized == "live" or not normalized.startswith("benchmark:"):
        raise ValueError("benchmark state trial namespace must be a non-empty benchmark:* namespace")
    return normalized


def _typed_answer_emitter_for_trial():
    if typed_state_v2_enabled() or typed_object_summary_enabled():
        return render_current_state_typed
    return None


def _typed_object_status_summary_for_facts(facts: list[CurrentStateFact]) -> dict[str, Any]:
    typed_objects = [
        fact.metadata["typed_state_v2"]
        for fact in facts
        if isinstance(fact.metadata, dict) and isinstance(fact.metadata.get("typed_state_v2"), dict)
    ]
    return summarize_typed_state_objects(typed_objects)


def _attach_typed_object_summary(report: dict[str, Any], facts: list[CurrentStateFact]) -> dict[str, Any]:
    if typed_object_summary_enabled():
        report["typed_object_status_summary"] = _typed_object_status_summary_for_facts(facts)
    return report


@contextmanager
def _state_ingestion_env_for_benchmark():
    keys = {
        "KONTEXT_STATE_MODEL_ENABLED": "1",
        "KONTEXT_STATE_INGESTION_ENABLED": "1",
        "KONTEXT_STATE_AUTO_ACCEPT_ENABLED": "0",
    }
    previous = {key: os.environ.get(key) for key in keys}
    try:
        os.environ.update(keys)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _state_routing_env_for_benchmark(namespace: str):
    keys = {
        "KONTEXT_STATE_MODEL_ENABLED": "1",
        "KONTEXT_STATE_ROUTING_ENABLED": "1",
        "KONTEXT_STATE_NAMESPACE": namespace,
    }
    previous = {key: os.environ.get(key) for key in keys}
    try:
        os.environ.update(keys)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _conversation_source_index(conversation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    order = 0
    for session in conversation.get("sessions") or []:
        for message in session.get("messages") or []:
            source_id = str(message.get("source_id") or "").strip()
            content = str(message.get("content") or "").strip()
            if not source_id or not content:
                continue
            rows[source_id] = {
                "source_id": source_id,
                "content": content,
                "role": str(message.get("role") or ""),
                "session_id": str(session.get("session_id") or ""),
                "date": session.get("date"),
                "order": order,
            }
            order += 1
    return rows


def _question_hash(question: dict[str, Any]) -> str:
    return stable_hash(str(question.get("question") or ""))[:16]


def _state_key(category: str, question_hash: str) -> str:
    return f"benchmark.{category}.{question_hash}"


def _judged_bundle_question_hash(question: dict[str, Any]) -> str:
    stable_id = str(question.get("question_id") or "").strip()
    if stable_id:
        return stable_hash(stable_id)[:16]
    return stable_hash(str(question.get("question") or ""))[:16]


def _state_event_type_for_category(category: str) -> str:
    return STATE_QUESTION_CATEGORIES[str(category or "").strip()]


def _private_memory_turns(memory: str) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    current_role: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_role, current_lines
        text = " ".join(line.strip() for line in current_lines if line.strip()).strip()
        if current_role and text:
            turns.append({"role": current_role, "text": text, "turn_index": len(turns) + 1})
        current_role = None
        current_lines = []

    for raw_line in str(memory or "").splitlines() or [str(memory or "")]:
        line = raw_line.strip()
        if not line:
            continue
        match = PRIVATE_ROLE_RE.match(line)
        if match:
            flush()
            current_role = match.group(1).lower()
            current_lines = [match.group(2).strip()]
        elif current_role:
            current_lines.append(line)
        else:
            current_role = "unknown"
            current_lines = [line]
    flush()
    return turns


def _private_state_marker(category: str, text: str) -> bool:
    lowered = str(text or "").lower()
    shared = r"\b(?:actually|now|instead|rather than|replace|revised|changed|updated|from now on|no longer)\b"
    if re.search(shared, lowered):
        return True
    category = str(category or "").lower()
    if category == "instruction_following":
        return bool(re.search(r"\b(?:must|should|need|use|format|reply|respond|follow|instruction|do not|don't)\b", lowered))
    if category == "preference_following":
        return bool(re.search(r"\b(?:prefer|preference|like|want|favorite|style|rather)\b", lowered))
    if category == "knowledge_update":
        return bool(re.search(r"\b(?:latest|current|new|correction|corrected|is now)\b", lowered))
    return False


def _private_terms(text: str) -> set[str]:
    terms = {
        item
        for item in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", str(text or "").lower())
        if item not in PRIVATE_BUNDLE_STOPWORDS
    }
    return terms


def _private_overlap_summary(*, active_text: str, ground_truth_answer: str) -> dict[str, Any]:
    truth_terms = _private_terms(ground_truth_answer)
    active_terms = _private_terms(active_text)
    overlap = truth_terms & active_terms
    ratio = round(len(overlap) / len(truth_terms), 4) if truth_terms else 0.0
    return {
        "active_answer_overlap_terms": len(overlap),
        "ground_truth_term_count": len(truth_terms),
        "active_answer_overlap_ratio": ratio,
        "active_answer_overlap": bool(overlap),
    }


def _clean_projected_value_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^(?:user|assistant|system)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^(?:i|we|you)\s+(?:now\s+|currently\s+)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^(?:the|a|an)\s+", "", text, flags=re.IGNORECASE).strip()
    text = text.strip(" \t\r\n\"'`.,;:")
    return text


def _explicit_typed_value_from_memory_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for container in (row, metadata):
        for key in ("typed_state_value", "state_value", "fact_value"):
            value = container.get(key)
            if isinstance(value, dict) and value:
                return dict(value)
    return {}


def _pattern_value(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_projected_value_text(match.group("value"))
    return ""


def _state_value_payload(category: str, text: str, explicit_value: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(explicit_value, dict) and explicit_value:
        return dict(explicit_value)
    cleaned_text = _clean_projected_value_text(text)
    category = str(category or "").strip().lower()
    if category == "preference_following":
        value = _pattern_value(
            [
                r"\bprefer(?:s|red|ring)?\s+(?P<value>[^.?!]+)",
                r"\bpreference\s*(?:is|=|:)\s*(?P<value>[^.?!]+)",
                r"\bfavorite\s*(?:is|=|:)\s*(?P<value>[^.?!]+)",
            ],
            cleaned_text,
        )
        if value:
            return {"preference": value}
    if category == "instruction_following":
        value = _pattern_value(
            [
                r"\b(?:must|should|need to|use|follow)\s+(?P<value>[^.?!]+)",
                r"\binstruction\s*(?:is|=|:)\s*(?P<value>[^.?!]+)",
            ],
            cleaned_text,
        )
        if value:
            return {"instruction": value}
    if category == "knowledge_update":
        value = _pattern_value(
            [
                r"\b(?:latest|current|new)\s+[^.?!]*\s(?:is|=|:)\s*(?P<value>[^.?!]+)",
                r"\bis now\s+(?P<value>[^.?!]+)",
            ],
            cleaned_text,
        )
        if value:
            return {"knowledge": value}
    return {"text": text}


def _projected_value_text(value: dict[str, Any], fallback_text: str) -> str:
    if not value:
        return str(fallback_text or "")
    if set(value) == {"text"}:
        return str(fallback_text or value.get("text") or "")

    def scalar_values(node: Any) -> list[str]:
        if isinstance(node, dict):
            values: list[str] = []
            for key in sorted(node):
                if str(key) == "text" and len(node) > 1:
                    continue
                values.extend(scalar_values(node[key]))
            return values
        if isinstance(node, (list, tuple)):
            values = []
            for item in node:
                values.extend(scalar_values(item))
            return values
        if node is None:
            return []
        if isinstance(node, (str, int, float, bool)):
            text = _clean_projected_value_text(node)
            return [text] if text else []
        return []

    projected_values = scalar_values(value)
    return " ".join(projected_values) if projected_values else str(fallback_text or "")


def _projected_answer_value(fact: CurrentStateFact | None) -> str:
    if fact is None:
        return ""
    value = fact.fact_value if isinstance(fact.fact_value, dict) else {}
    return _projected_value_text(value, str(fact.fact_text or ""))


def _judged_memory_projection_score(event: dict[str, Any], question_text: str) -> tuple[int, int, int, int]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    category = str(metadata.get("question_category") or "").strip()
    value_text = str(event.get("value_text") or "")
    question_overlap = len(_private_terms(question_text) & _private_terms(value_text))
    marker_score = 1 if _private_state_marker(category, value_text) else 0
    memory_rank = int(metadata.get("memory_rank") or 1_000_000)
    turn_index = int(metadata.get("turn_index") or 0)
    return (question_overlap, marker_score, -memory_rank, -turn_index)


def _facts_by_state_key(facts: list[CurrentStateFact]) -> dict[str, CurrentStateFact]:
    out: dict[str, CurrentStateFact] = {}
    for fact in facts:
        state_key = getattr(fact, "state_key", "")
        if state_key:
            out[normalize_state_key(state_key)] = fact
    return out


def build_judged_bundle_state_events(
    bundle: dict[str, Any],
    *,
    namespace: str,
    source_hash: str,
    top_k: int | str = 20,
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
    categories: set[str] | None = None,
    max_events_per_question: int = 12,
    event_granularity: str = "turn",
) -> BenchmarkStatePlan:
    granularity = str(event_granularity or "turn").strip().lower()
    if granularity not in {"turn", "memory"}:
        raise ValueError(f"Unsupported private bundle event granularity: {event_granularity}")
    included_categories = categories or set(STATE_QUESTION_CATEGORIES)
    top_k_key = str(top_k)
    state_events: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []

    for question in bundle.get("questions") or []:
        category = str(question.get("category") or "").strip()
        if category not in included_categories or category not in STATE_QUESTION_CATEGORIES:
            continue
        retrieved_by_top_k = question.get("retrieved_memories_by_top_k")
        if not isinstance(retrieved_by_top_k, dict):
            continue
        retrieved = retrieved_by_top_k.get(top_k_key) or []
        if not isinstance(retrieved, list) or not retrieved:
            continue

        q_hash = _judged_bundle_question_hash(question)
        state_key = _state_key(category, q_hash)
        question_events: list[dict[str, Any]] = []
        for memory_rank, row in enumerate(retrieved, start=1):
            if not isinstance(row, dict):
                continue
            memory_text = str(row.get("memory") or "")
            if not memory_text.strip():
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            explicit_value = _explicit_typed_value_from_memory_row(row)
            memory_hash = str(row.get("memory_hash") or stable_hash(memory_text)).strip()
            effective_at = metadata.get("timestamp") or row.get("updated_at") or row.get("created_at")
            if granularity == "memory":
                event_hash = stable_hash(f"{q_hash}:{memory_hash}:{memory_rank}:memory:{memory_text}")[:16]
                value = dict(explicit_value) if explicit_value else {"text": memory_text}
                question_events.append(
                    {
                        "source_kind": "judged_private_bundle",
                        "source_id": str(question.get("question_id") or ""),
                        "source_span": f"{q_hash}:{memory_hash}:memory",
                        "subject_type": "benchmark_question",
                        "subject_key": q_hash,
                        "state_key": state_key,
                        "event_type": _state_event_type_for_category(category),
                        "value": value,
                        "value_text": memory_text,
                        "effective_at": effective_at,
                        "actor_role": "memory",
                        "confidence": 1.0,
                        "trust_tier": "benchmark_fixture",
                        "metadata": {
                            "benchmark_dataset": str(bundle.get("dataset") or ""),
                            "question_hash": q_hash,
                            "question_id_hash": stable_hash(str(question.get("question_id") or ""))[:16],
                            "question_category": category,
                            "memory_hash": memory_hash[:32],
                            "memory_rank": memory_rank,
                            "turn_index": 0,
                            "event_hash": event_hash,
                            "private_bundle": True,
                            "event_granularity": "memory",
                        },
                    }
                )
                continue
            for turn in _private_memory_turns(memory_text):
                text = str(turn.get("text") or "").strip()
                role = str(turn.get("role") or "").lower()
                if not text:
                    continue
                if role != "user" and not _private_state_marker(category, text):
                    continue
                if not _private_state_marker(category, text) and not (_private_terms(text) & _private_terms(str(question.get("question") or ""))):
                    continue
                turn_index = int(turn.get("turn_index") or 0)
                event_hash = stable_hash(f"{q_hash}:{memory_hash}:{memory_rank}:{turn_index}:{role}:{text}")[:16]
                question_events.append(
                    {
                        "source_kind": "judged_private_bundle",
                        "source_id": str(question.get("question_id") or ""),
                        "source_span": f"{q_hash}:{memory_hash}:{turn_index}",
                        "subject_type": "benchmark_question",
                        "subject_key": q_hash,
                        "state_key": state_key,
                        "event_type": _state_event_type_for_category(category),
                        "value": _state_value_payload(category, text, explicit_value=explicit_value),
                        "value_text": text,
                        "effective_at": effective_at,
                        "actor_role": role,
                        "confidence": 1.0,
                        "trust_tier": "benchmark_fixture",
                        "metadata": {
                            "benchmark_dataset": str(bundle.get("dataset") or ""),
                            "question_hash": q_hash,
                            "question_id_hash": stable_hash(str(question.get("question_id") or ""))[:16],
                            "question_category": category,
                            "memory_hash": memory_hash[:32],
                            "memory_rank": memory_rank,
                            "turn_index": turn_index,
                            "event_hash": event_hash,
                            "private_bundle": True,
                            "event_granularity": "turn",
                        },
                    }
                )

        question_events.sort(
            key=lambda event: (
                str(event.get("effective_at") or ""),
                int((event.get("metadata") or {}).get("memory_rank") or 0),
                int((event.get("metadata") or {}).get("turn_index") or 0),
            )
        )
        selected_events = question_events[-max(max_events_per_question, 0) :] if max_events_per_question else []
        if not selected_events:
            continue
        if granularity == "memory":
            selected_events.sort(key=lambda event: _judged_memory_projection_score(event, str(question.get("question") or "")))
        state_events.extend(selected_events)
        questions.append(
            {
                "question_id": str(question.get("question_id") or ""),
                "question_hash": q_hash,
                "category": category,
                "state_key": state_key,
                "retrieved_count": len(retrieved),
                "selected_event_count": len(selected_events),
                "_question_text": str(question.get("question") or ""),
                "_ground_truth_answer": str(question.get("ground_truth_answer") or ""),
                "_retrieved_memory_texts": [
                    str(row.get("memory") or "")
                    for row in retrieved
                    if isinstance(row, dict) and str(row.get("memory") or "").strip()
                ],
            }
        )

    return BenchmarkStatePlan(
        namespace=namespace,
        source_hash=source_hash,
        extractor_version=extractor_version,
        state_events=state_events,
        questions=questions,
    )


def build_benchmark_state_events(
    fixture: dict[str, Any],
    *,
    namespace: str,
    source_hash: str,
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
    categories: set[str] | None = None,
) -> BenchmarkStatePlan:
    included_categories = categories or set(STATE_QUESTION_CATEGORIES)
    conversations = {str(item.get("conversation_id") or ""): item for item in fixture.get("conversations") or []}
    source_indexes = {
        conversation_id: _conversation_source_index(conversation)
        for conversation_id, conversation in conversations.items()
    }

    state_events: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for question in fixture.get("questions") or []:
        category = str(question.get("category") or question.get("question_type") or "").strip()
        if category not in included_categories or category not in STATE_QUESTION_CATEGORIES:
            continue
        evidence_ids = [str(value).strip() for value in question.get("evidence") or [] if str(value).strip()]
        if not evidence_ids:
            continue
        conversation_id = str(question.get("conversation_id") or "").strip()
        source_index = source_indexes.get(conversation_id) or {}
        evidence_rows = [source_index[source_id] for source_id in evidence_ids if source_id in source_index]
        if not evidence_rows:
            continue
        evidence_rows.sort(key=lambda row: int(row["order"]))
        q_hash = _question_hash(question)
        state_key = _state_key(category, q_hash)
        questions.append(
            {
                "question_id": str(question.get("question_id") or ""),
                "question_hash": q_hash,
                "category": category,
                "state_key": state_key,
                "conversation_id": conversation_id,
                "user_id": str(question.get("user_id") or ""),
                "evidence_source_ids": [row["source_id"] for row in evidence_rows],
                "_question_text": str(question.get("question") or ""),
            }
        )
        for event_index, row in enumerate(evidence_rows):
            state_events.append(
                {
                    "source_kind": "benchmark_fixture",
                    "source_id": str(question.get("question_id") or ""),
                    "source_span": f"{conversation_id}:{row['source_id']}",
                    "subject_type": "benchmark_user",
                    "subject_key": str(question.get("user_id") or conversation_id),
                    "state_key": state_key,
                    "event_type": STATE_QUESTION_CATEGORIES[category],
                    "value": _state_value_payload(category, row["content"]),
                    "value_text": row["content"],
                    "effective_at": row.get("date"),
                    "actor_role": row.get("role") or "",
                    "confidence": 1.0,
                    "trust_tier": "benchmark_fixture",
                    "metadata": {
                        "benchmark_dataset": str(fixture.get("dataset") or ""),
                        "question_hash": q_hash,
                        "question_id_hash": stable_hash(str(question.get("question_id") or ""))[:16],
                        "question_category": category,
                        "conversation_id": conversation_id,
                        "source_id": row["source_id"],
                        "evidence_index": event_index,
                    },
                }
            )
    return BenchmarkStatePlan(
        namespace=namespace,
        source_hash=source_hash,
        extractor_version=extractor_version,
        state_events=state_events,
        questions=questions,
    )


def _baseline_map(baseline_results: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {str(item.get("question_id") or ""): item for item in (baseline_results or [])}


def _event_ids_from_projection_row(row: dict[str, Any]) -> list[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    ids = []
    active_id = str(metadata.get("active_event_id") or "").strip()
    if active_id:
        ids.append(active_id)
    ids.extend(str(item).strip() for item in metadata.get("support_event_ids") or [] if str(item).strip())
    return ids


def _projection_matched(rows: list[dict[str, Any]], event_source_ids: dict[str, str], evidence_source_ids: list[str]) -> bool:
    expected = {str(item) for item in evidence_source_ids}
    for row in rows:
        for event_id in _event_ids_from_projection_row(row):
            source_id = event_source_ids.get(event_id)
            if source_id in expected:
                return True
    return False


def _preview_subject_id(*, namespace: str, subject_type: str, subject_key: str) -> str:
    return "preview-subject-" + stable_hash(f"{namespace}:{subject_type}:{subject_key}")[:16]


def _planned_event_to_preview_event(
    *,
    index: int,
    candidate: dict[str, Any],
    planned_event: dict[str, Any],
    namespace: str,
    source_hash: str,
    extractor_version: str,
) -> StateEvent:
    value = planned_event.get("value") if isinstance(planned_event.get("value"), dict) else {}
    subject_type = normalize_subject_type(planned_event.get("subject_type"))
    subject_key = normalize_subject_key(planned_event.get("subject_key"))
    metadata = planned_event.get("metadata") if isinstance(planned_event.get("metadata"), dict) else {}
    payload_hash = str(candidate.get("value_hash") or make_value_hash(value))
    return StateEvent(
        id=f"preview-event-{index + 1}",
        candidate_id=str(candidate.get("id") or ""),
        namespace=normalize_namespace(namespace),
        subject_id=_preview_subject_id(namespace=namespace, subject_type=subject_type, subject_key=subject_key),
        source_kind=str(planned_event.get("source_kind") or ""),
        source_id=str(planned_event.get("source_id") or ""),
        source_hash=str(candidate.get("source_hash") or source_hash or ""),
        source_span_hash=stable_hash(str(planned_event.get("source_span") or f"preview:{index}")),
        event_hash=str(metadata.get("event_hash") or stable_hash(f"{namespace}:{index}:{payload_hash}")[:16]),
        state_key=normalize_state_key(planned_event.get("state_key") or candidate.get("state_key")),
        event_type=str(planned_event.get("event_type") or candidate.get("event_type") or ""),
        value=dict(value),
        value_text=str(planned_event.get("value_text") or ""),
        value_hash=payload_hash,
        prior_value_text=str(planned_event.get("prior_value_text") or ""),
        effective_at=planned_event.get("effective_at"),
        observed_at=planned_event.get("observed_at"),
        actor_role=str(planned_event.get("actor_role") or ""),
        confidence=float(planned_event.get("confidence") or candidate.get("confidence") or 0.0),
        trust_tier=str(planned_event.get("trust_tier") or candidate.get("trust_tier") or ""),
        status="accepted",
        extractor_version=str(candidate.get("extractor_version") or extractor_version or ""),
        metadata=dict(metadata),
    )


def _dry_run_stage_state_event_proposals(
    state_events: list[dict[str, Any]],
    *,
    source_hash: str,
    extractor_version: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for index, planned_event in enumerate(state_events):
        if not str(planned_event.get("value_text") or "").strip():
            continue
        value = planned_event.get("value") if isinstance(planned_event.get("value"), dict) else {}
        results.append(
            {
                "action": "dry_run_preview",
                "id": f"preview-candidate-{index + 1}",
                "state_key": normalize_state_key(planned_event.get("state_key")),
                "event_type": str(planned_event.get("event_type") or ""),
                "value_hash": make_value_hash(value),
                "source_hash": source_hash,
                "confidence": float(planned_event.get("confidence") or 0.0),
                "trust_tier": str(planned_event.get("trust_tier") or ""),
                "extractor_version": extractor_version,
            }
        )
    return {
        "ok": True,
        "writes_applied": 0,
        "counts": {"staged": 0, "would_stage": len(results)},
        "results": results,
    }


def _preview_fact_rows(facts: list[CurrentStateFact], *, namespace: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, fact in enumerate(facts, start=1):
        metadata = {
            "retrieval_path": "state_projection_preview",
            "namespace": normalize_namespace(namespace),
            "subject_id": str(fact.subject_id),
            "state_key": fact.state_key,
            "state_status": fact.status,
            "active_event_id": str(fact.active_event_id or ""),
            "support_event_ids": [str(item) for item in fact.support_event_ids],
            "superseded_event_ids": [str(item) for item in fact.superseded_event_ids],
            "cancelled_event_ids": [str(item) for item in fact.cancelled_event_ids],
            "projection_version": fact.projection_version,
        }
        rows.append(
            {
                "external_mem0_id": f"state:preview:{index}",
                "title": f"Current state preview: {fact.state_key}",
                "text": "",
                "metadata": metadata,
                "memory_type": "current_state",
                "current_status": fact.status,
                "memory_tier": "active",
                "_retrieval_path": "state_projection_preview",
            }
        )
    return rows


def _search_preview_projection_rows(rows: list[dict[str, Any]], query: str, *, top_k: int) -> list[dict[str, Any]]:
    lowered = str(query or "").lower()
    matched = [
        row
        for row in rows
        if str((row.get("metadata") or {}).get("state_key") or "").lower() in lowered
    ]
    return (matched or rows)[: max(int(top_k or 1), 1)]


class _PreviewProjectionRepo:
    def __init__(self, repo: Any, rows: list[dict[str, Any]]) -> None:
        self._repo = repo
        self._rows = rows

    def search_current_state_facts(self, query: str, *, namespace: str = "live", top_k: int = 5) -> list[dict]:
        return _search_preview_projection_rows(self._rows, query, top_k=top_k)

    def list_memory_rows(self, limit: int = 1000, offset: int = 0, **kwargs: object) -> list[dict]:
        if hasattr(self._repo, "list_memory_rows"):
            return self._repo.list_memory_rows(limit=limit, offset=offset, **kwargs)
        return []


def _routed_search_rows(repo: Any, query: str, *, namespace: str, top_k: int) -> list[dict[str, Any]]:
    with _state_routing_env_for_benchmark(namespace):
        return search_memories(
            repo,
            query,
            top_k=top_k,
            domains=[],
            memory_types=[],
            memory_tiers=[],
            current_statuses=[],
            namespace=namespace,
        )


def run_benchmark_state_trial(
    repo: Any,
    fixture: dict[str, Any],
    *,
    namespace: str,
    source_hash: str,
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
    top_k: int = 5,
    baseline_results: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not _benchmark_enabled():
        return {
            "ok": False,
            "mode": "disabled",
            "enabled": False,
            "writes_applied": 0,
            "counts": {},
            "questions": [],
        }

    namespace = _benchmark_trial_namespace(namespace)
    plan = build_benchmark_state_events(
        fixture,
        namespace=namespace,
        source_hash=source_hash,
        extractor_version=extractor_version,
    )
    baseline_by_question = _baseline_map(baseline_results)
    if dry_run:
        staged = _dry_run_stage_state_event_proposals(
            plan.state_events,
            source_hash=source_hash,
            extractor_version=extractor_version,
        )
    else:
        with _state_ingestion_env_for_benchmark():
            staged = stage_state_event_proposals(
                repo,
                {"state_events": plan.state_events},
                namespace=namespace,
                source_hash=source_hash,
                extractor_version=extractor_version,
            )

    writes_applied = int(staged.get("writes_applied") or 0)
    staged_counts = staged.get("counts") or {}
    candidate_results = staged.get("results") or []
    previous_event_by_state_key: dict[str, str] = {}
    event_source_ids: dict[str, str] = {}
    question_reason_hashes: dict[str, str] = {}
    preview_events: list[StateEvent] = []
    preview_edges: list[StateEventEdge] = []
    accepted_count = 0
    edge_count = 0
    would_accept_count = 0
    would_edge_count = 0

    for index, candidate in enumerate(candidate_results):
        if candidate.get("action") == "error" or index >= len(plan.state_events):
            continue
        candidate_id = str(candidate.get("id") or "")
        if not candidate_id:
            continue
        planned_event = plan.state_events[index]
        state_key = str(planned_event.get("state_key") or candidate.get("state_key") or "")
        previous_event_id = previous_event_by_state_key.get(state_key)
        edges: list[dict[str, Any]] = []
        if previous_event_id:
            edges.append(
                {
                    "target_event_id": previous_event_id,
                    "edge_type": "supersedes",
                    "state_key": state_key,
                    "reason": f"benchmark_state_trial:{state_key}",
                }
            )
        if dry_run:
            preview_event = _planned_event_to_preview_event(
                index=index,
                candidate=candidate,
                planned_event=planned_event,
                namespace=namespace,
                source_hash=source_hash,
                extractor_version=extractor_version,
            )
            preview_events.append(preview_event)
            would_accept_count += 1
            event_id = str(preview_event.id)
            if previous_event_id:
                reason_hash = stable_hash(f"benchmark_state_trial:{state_key}")
                preview_edges.append(
                    StateEventEdge(
                        id=None,
                        namespace=normalize_namespace(namespace),
                        source_event_id=event_id,
                        target_event_id=previous_event_id,
                        edge_type="supersedes",
                        state_key=normalize_state_key(state_key),
                        confidence=1.0,
                        reason_hash=reason_hash,
                        metadata={},
                    )
                )
                question_reason_hashes[state_key] = reason_hash
                would_edge_count += 1
            previous_event_by_state_key[state_key] = event_id
            metadata = planned_event.get("metadata") if isinstance(planned_event.get("metadata"), dict) else {}
            event_source_ids[event_id] = str(metadata.get("source_id") or "")
            continue
        accepted = accept_reviewed_state_candidate(
            repo,
            candidate_id,
            namespace=namespace,
            edges=edges,
            rebuild_projection=False,
        )
        accepted_count += 1
        writes_applied += int(accepted.get("writes_applied") or 0)
        edge_count += len(accepted.get("edges") or [])
        event_id = str(accepted.get("event_id") or "")
        if event_id:
            previous_event_by_state_key[state_key] = event_id
            metadata = planned_event.get("metadata") if isinstance(planned_event.get("metadata"), dict) else {}
            event_source_ids[event_id] = str(metadata.get("source_id") or "")
            if accepted.get("edges"):
                question_reason_hashes[state_key] = str(accepted["edges"][0].get("reason_hash") or "")

    typed_answer_emitter = _typed_answer_emitter_for_trial()
    if dry_run:
        facts = build_current_state_facts(preview_events, preview_edges, typed_answer_emitter=typed_answer_emitter)
        preview_rows = _preview_fact_rows(facts, namespace=namespace)
    else:
        if typed_answer_emitter is not None and hasattr(repo, "project_typed_state"):
            facts = repo.project_typed_state(namespace=namespace)
        else:
            facts = repo.rebuild_current_state_projection(namespace=namespace)
        preview_rows = []
    question_rows: list[dict[str, Any]] = []
    projection_matched_count = 0
    baseline_matched_count = 0
    routed_projection_query_count = 0
    routed_projection_matched_count = 0
    routed_repo = _PreviewProjectionRepo(repo, preview_rows) if dry_run else repo
    for question in plan.questions:
        query = f"{question['category']} {question['state_key']}"
        if dry_run:
            rows = _search_preview_projection_rows(preview_rows, query, top_k=top_k)
        else:
            rows = repo.search_current_state_facts(
                query,
                namespace=namespace,
                top_k=top_k,
            )
        projection_hit = _projection_matched(rows, event_source_ids, question["evidence_source_ids"])
        baseline_row = baseline_by_question.get(question["question_id"], {})
        baseline_hit = bool(baseline_row.get("matched")) if baseline_row else None
        if projection_hit:
            projection_matched_count += 1
        if baseline_hit:
            baseline_matched_count += 1
        routed_query = str(question.get("_question_text") or "").strip()
        routed_rows: list[dict[str, Any]] = []
        routed_projection_hit = False
        if routed_query:
            routed_rows = _routed_search_rows(routed_repo, routed_query, namespace=namespace, top_k=top_k)
            routed_projection_query_count += 1
            routed_projection_hit = _projection_matched(routed_rows, event_source_ids, question["evidence_source_ids"])
            if routed_projection_hit:
                routed_projection_matched_count += 1
        question_rows.append(
            {
                "question_id": question["question_id"],
                "question_hash": question["question_hash"],
                "category": question["category"],
                "state_key": question["state_key"],
                "evidence_count": len(question["evidence_source_ids"]),
                "baseline_matched": baseline_hit,
                "projection_matched": projection_hit,
                "route": "state_projection_preview" if dry_run and rows else "state_projection" if rows else "no_projection_result",
                "projection_result_count": len(rows),
                "supersession_reason_hash": question_reason_hashes.get(question["state_key"], ""),
                "routed_projection_matched": routed_projection_hit,
                "routed_route": str((routed_rows[0] if routed_rows else {}).get("_retrieval_path") or "no_routed_result"),
                "routed_result_count": len(routed_rows),
            }
        )

    counts = repo.state_model_counts(namespace=namespace) if hasattr(repo, "state_model_counts") else {}
    report = {
        "ok": True,
        "mode": "benchmark_state_trial",
        "enabled": True,
        "dataset": str(fixture.get("dataset") or ""),
        "namespace": namespace,
        "source_hash": source_hash,
        "extractor_version": extractor_version,
        "dry_run": bool(dry_run),
        "writes_applied": writes_applied,
        "counts": {
            "state_events_planned": len(plan.state_events),
            "state_questions": len(plan.questions),
            "candidates_staged": int(staged_counts.get("staged") or 0),
            "candidates_would_stage": int(staged_counts.get("would_stage") or 0),
            "events_accepted": accepted_count,
            "events_would_accept": would_accept_count,
            "edges_inserted": edge_count,
            "edges_would_insert": would_edge_count,
            "projection_facts": len(facts),
            "projection_queries": len(plan.questions),
            "projection_matched": projection_matched_count,
            "routed_projection_queries": routed_projection_query_count,
            "routed_projection_matched": routed_projection_matched_count,
            "beam_routed_projection_matched": routed_projection_matched_count,
            "baseline_matched": baseline_matched_count,
            "repo": counts,
        },
        "questions": question_rows,
    }
    return _attach_typed_object_summary(report, facts)


def run_judged_bundle_state_trial(
    repo: Any,
    bundle: dict[str, Any],
    *,
    namespace: str,
    source_hash: str,
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
    top_k: int | str = 20,
    event_granularity: str = "turn",
    dry_run: bool = False,
) -> dict[str, Any]:
    if not _benchmark_enabled():
        return {
            "ok": False,
            "mode": "disabled",
            "enabled": False,
            "writes_applied": 0,
            "counts": {},
            "questions": [],
        }

    namespace = _benchmark_trial_namespace(namespace)
    plan = build_judged_bundle_state_events(
        bundle,
        namespace=namespace,
        source_hash=source_hash,
        extractor_version=extractor_version,
        top_k=top_k,
        max_events_per_question=int(top_k) if str(event_granularity or "").strip().lower() == "memory" else 12,
        event_granularity=event_granularity,
    )
    if dry_run:
        staged = _dry_run_stage_state_event_proposals(
            plan.state_events,
            source_hash=source_hash,
            extractor_version=extractor_version,
        )
    else:
        with _state_ingestion_env_for_benchmark():
            staged = stage_state_event_proposals(
                repo,
                {"state_events": plan.state_events},
                namespace=namespace,
                source_hash=source_hash,
                extractor_version=extractor_version,
            )

    writes_applied = int(staged.get("writes_applied") or 0)
    staged_counts = staged.get("counts") or {}
    candidate_results = staged.get("results") or []
    previous_event_by_state_key: dict[str, str] = {}
    preview_events: list[StateEvent] = []
    preview_edges: list[StateEventEdge] = []
    event_texts: dict[str, str] = {}
    event_projection_texts: dict[str, str] = {}
    event_metadata: dict[str, dict[str, Any]] = {}
    question_events_by_state_key: dict[str, list[str]] = {}
    accepted_count = 0
    edge_count = 0
    would_accept_count = 0
    would_edge_count = 0

    for index, candidate in enumerate(candidate_results):
        if candidate.get("action") == "error" or index >= len(plan.state_events):
            continue
        candidate_id = str(candidate.get("id") or "")
        if not candidate_id:
            continue
        planned_event = plan.state_events[index]
        state_key = str(planned_event.get("state_key") or candidate.get("state_key") or "")
        previous_event_id = previous_event_by_state_key.get(state_key)
        edges = []
        if previous_event_id:
            edges.append(
                {
                    "target_event_id": previous_event_id,
                    "edge_type": "supersedes",
                    "state_key": state_key,
                    "reason": f"judged_bundle_state_trial:{state_key}",
                }
            )
        if dry_run:
            preview_event = _planned_event_to_preview_event(
                index=index,
                candidate=candidate,
                planned_event=planned_event,
                namespace=namespace,
                source_hash=source_hash,
                extractor_version=extractor_version,
            )
            preview_events.append(preview_event)
            would_accept_count += 1
            event_id = str(preview_event.id)
            if previous_event_id:
                reason_hash = stable_hash(f"judged_bundle_state_trial:{state_key}")
                preview_edges.append(
                    StateEventEdge(
                        id=None,
                        namespace=normalize_namespace(namespace),
                        source_event_id=event_id,
                        target_event_id=previous_event_id,
                        edge_type="supersedes",
                        state_key=normalize_state_key(state_key),
                        confidence=1.0,
                        reason_hash=reason_hash,
                        metadata={},
                    )
                )
                would_edge_count += 1
            previous_event_by_state_key[state_key] = event_id
            event_texts[event_id] = str(planned_event.get("value_text") or "")
            planned_value = planned_event.get("value") if isinstance(planned_event.get("value"), dict) else {}
            event_projection_texts[event_id] = _projected_value_text(planned_value, event_texts[event_id])
            metadata = planned_event.get("metadata") if isinstance(planned_event.get("metadata"), dict) else {}
            event_metadata[event_id] = metadata
            question_events_by_state_key.setdefault(state_key, []).append(event_id)
            continue
        accepted = accept_reviewed_state_candidate(
            repo,
            candidate_id,
            namespace=namespace,
            edges=edges,
            rebuild_projection=False,
        )
        accepted_count += 1
        writes_applied += int(accepted.get("writes_applied") or 0)
        edge_count += len(accepted.get("edges") or [])
        event_id = str(accepted.get("event_id") or "")
        if event_id:
            previous_event_by_state_key[state_key] = event_id
            event_texts[event_id] = str(planned_event.get("value_text") or "")
            planned_value = planned_event.get("value") if isinstance(planned_event.get("value"), dict) else {}
            event_projection_texts[event_id] = _projected_value_text(planned_value, event_texts[event_id])
            metadata = planned_event.get("metadata") if isinstance(planned_event.get("metadata"), dict) else {}
            event_metadata[event_id] = metadata
            question_events_by_state_key.setdefault(state_key, []).append(event_id)

    typed_answer_emitter = _typed_answer_emitter_for_trial()
    if dry_run:
        facts = build_current_state_facts(preview_events, preview_edges, typed_answer_emitter=typed_answer_emitter)
        preview_rows = _preview_fact_rows(facts, namespace=namespace)
    else:
        if typed_answer_emitter is not None and hasattr(repo, "project_typed_state"):
            facts = repo.project_typed_state(namespace=namespace)
        else:
            facts = repo.rebuild_current_state_projection(namespace=namespace)
        preview_rows = []
    question_rows: list[dict[str, Any]] = []
    active_overlap_questions = 0
    best_overlap_questions = 0
    projection_selection_loss_questions = 0
    best_retrieved_overlap_questions = 0
    state_event_extraction_loss_questions = 0
    routed_projection_query_count = 0
    routed_active_overlap_questions = 0
    routed_repo = _PreviewProjectionRepo(repo, preview_rows) if dry_run else repo
    fact_by_state_key = _facts_by_state_key(facts)
    for question in plan.questions:
        query = f"{question['category']} {question['state_key']}"
        if dry_run:
            rows = _search_preview_projection_rows(preview_rows, query, top_k=int(top_k))
        else:
            rows = repo.search_current_state_facts(
                query,
                namespace=namespace,
                top_k=int(top_k),
            )
        active_fact = fact_by_state_key.get(normalize_state_key(question["state_key"]))
        active_event_id = str(active_fact.active_event_id or "") if active_fact else ""
        if not active_event_id and rows:
            active_ids = _event_ids_from_projection_row(rows[0])
            active_event_id = active_ids[0] if active_ids else ""
        active_text = (
            _projected_answer_value(active_fact)
            if active_fact
            else event_projection_texts.get(active_event_id, event_texts.get(active_event_id, ""))
        )
        overlap = _private_overlap_summary(
            active_text=active_text,
            ground_truth_answer=str(question.get("_ground_truth_answer") or ""),
        )
        best_overlap = dict(overlap)
        best_event_id = active_event_id
        for event_id in question_events_by_state_key.get(str(question["state_key"]), []):
            candidate_overlap = _private_overlap_summary(
                active_text=event_projection_texts.get(event_id, event_texts.get(event_id, "")),
                ground_truth_answer=str(question.get("_ground_truth_answer") or ""),
            )
            if (
                float(candidate_overlap["active_answer_overlap_ratio"]),
                int(candidate_overlap["active_answer_overlap_terms"]),
            ) > (
                float(best_overlap["active_answer_overlap_ratio"]),
                int(best_overlap["active_answer_overlap_terms"]),
            ):
                best_overlap = candidate_overlap
                best_event_id = event_id
        best_retrieved_overlap = dict(best_overlap)
        for memory_text in question.get("_retrieved_memory_texts") or []:
            candidate_overlap = _private_overlap_summary(
                active_text=str(memory_text or ""),
                ground_truth_answer=str(question.get("_ground_truth_answer") or ""),
            )
            if (
                float(candidate_overlap["active_answer_overlap_ratio"]),
                int(candidate_overlap["active_answer_overlap_terms"]),
            ) > (
                float(best_retrieved_overlap["active_answer_overlap_ratio"]),
                int(best_retrieved_overlap["active_answer_overlap_terms"]),
            ):
                best_retrieved_overlap = candidate_overlap
        if overlap["active_answer_overlap"]:
            active_overlap_questions += 1
        if best_overlap["active_answer_overlap"]:
            best_overlap_questions += 1
        if best_retrieved_overlap["active_answer_overlap"]:
            best_retrieved_overlap_questions += 1
        projection_gap = round(
            float(best_overlap["active_answer_overlap_ratio"]) - float(overlap["active_answer_overlap_ratio"]),
            4,
        )
        if projection_gap > 0:
            projection_selection_loss_questions += 1
        extraction_gap = round(
            float(best_retrieved_overlap["active_answer_overlap_ratio"]) - float(best_overlap["active_answer_overlap_ratio"]),
            4,
        )
        if extraction_gap > 0:
            state_event_extraction_loss_questions += 1
        metadata = event_metadata.get(active_event_id, {})
        best_metadata = event_metadata.get(best_event_id, {})
        active_memory_rank = int(metadata.get("memory_rank") or 0) if metadata else 0
        best_event_memory_rank = int(best_metadata.get("memory_rank") or 0) if best_metadata else 0
        routed_query = str(question.get("_question_text") or "").strip()
        routed_rows: list[dict[str, Any]] = []
        routed_overlap = {
            "active_answer_overlap": False,
            "active_answer_overlap_terms": 0,
            "active_answer_overlap_ratio": 0.0,
        }
        if routed_query:
            routed_rows = _routed_search_rows(routed_repo, routed_query, namespace=namespace, top_k=int(top_k))
            routed_projection_query_count += 1
            routed_event_id = ""
            if routed_rows:
                routed_ids = _event_ids_from_projection_row(routed_rows[0])
                routed_event_id = routed_ids[0] if routed_ids else ""
            routed_overlap = _private_overlap_summary(
                active_text=event_projection_texts.get(routed_event_id, event_texts.get(routed_event_id, "")),
                ground_truth_answer=str(question.get("_ground_truth_answer") or ""),
            )
            if routed_overlap["active_answer_overlap"]:
                routed_active_overlap_questions += 1
        question_rows.append(
            {
                "question_id_hash": stable_hash(str(question.get("question_id") or ""))[:16],
                "question_hash": question["question_hash"],
                "category": question["category"],
                "state_key": question["state_key"],
                "retrieved_count": int(question.get("retrieved_count") or 0),
                "selected_event_count": int(question.get("selected_event_count") or 0),
                "projection_result_count": len(rows),
                "route": "state_projection_preview" if dry_run and rows else "state_projection" if rows else "no_projection_result",
                "active_event_hash": str(metadata.get("event_hash") or ""),
                "active_memory_hash": str(metadata.get("memory_hash") or "")[:16],
                "active_memory_rank": active_memory_rank,
                "best_answer_overlap_terms": int(best_overlap["active_answer_overlap_terms"]),
                "best_answer_overlap_ratio": float(best_overlap["active_answer_overlap_ratio"]),
                "best_retrieved_answer_overlap_terms": int(best_retrieved_overlap["active_answer_overlap_terms"]),
                "best_retrieved_answer_overlap_ratio": float(best_retrieved_overlap["active_answer_overlap_ratio"]),
                "best_event_hash": str(best_metadata.get("event_hash") or ""),
                "best_event_memory_rank": best_event_memory_rank,
                "projection_overlap_gap": projection_gap,
                "state_event_extraction_gap": extraction_gap,
                "routed_active_answer_overlap": bool(routed_overlap["active_answer_overlap"]),
                "routed_active_answer_overlap_terms": int(routed_overlap["active_answer_overlap_terms"]),
                "routed_active_answer_overlap_ratio": float(routed_overlap["active_answer_overlap_ratio"]),
                "routed_route": str((routed_rows[0] if routed_rows else {}).get("_retrieval_path") or "no_routed_result"),
                "routed_result_count": len(routed_rows),
                **overlap,
            }
        )

    counts = repo.state_model_counts(namespace=namespace) if hasattr(repo, "state_model_counts") else {}
    report = {
        "ok": True,
        "mode": "judged_bundle_state_trial",
        "enabled": True,
        "dataset": str(bundle.get("dataset") or ""),
        "namespace": namespace,
        "source_hash": source_hash,
        "extractor_version": extractor_version,
        "event_granularity": str(event_granularity or "turn").strip().lower(),
        "dry_run": bool(dry_run),
        "writes_applied": writes_applied,
        "counts": {
            "state_events_planned": len(plan.state_events),
            "state_questions": len(plan.questions),
            "candidates_staged": int(staged_counts.get("staged") or 0),
            "candidates_would_stage": int(staged_counts.get("would_stage") or 0),
            "events_accepted": accepted_count,
            "events_would_accept": would_accept_count,
            "edges_inserted": edge_count,
            "edges_would_insert": would_edge_count,
            "projection_facts": len(facts),
            "projection_queries": len(plan.questions),
            "active_answer_overlap_questions": active_overlap_questions,
            "routed_projection_queries": routed_projection_query_count,
            "routed_active_answer_overlap_questions": routed_active_overlap_questions,
            "best_answer_overlap_questions": best_overlap_questions,
            "projection_selection_loss_questions": projection_selection_loss_questions,
            "best_retrieved_answer_overlap_questions": best_retrieved_overlap_questions,
            "state_event_extraction_loss_questions": state_event_extraction_loss_questions,
            "repo": counts,
        },
        "questions": question_rows,
    }
    return _attach_typed_object_summary(report, facts)


def write_state_trial_report(report: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    dataset = str(report.get("dataset") or "benchmark")
    namespace_hash = stable_hash(str(report.get("namespace") or ""))[:12]
    stem = f"{dataset}-state-trial-{namespace_hash}"
    json_path = output_path / f"{stem}.json"
    markdown_path = output_path / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# BEAM state trial",
        "",
        f"- Dataset: `{dataset}`",
        f"- Namespace hash: `{namespace_hash}`",
        f"- OK: `{report.get('ok')}`",
        f"- Mode: `{report.get('mode')}`",
        f"- Writes applied: `{report.get('writes_applied', 0)}`",
        f"- State events planned: `{(report.get('counts') or {}).get('state_events_planned', 0)}`",
        f"- State questions: `{(report.get('counts') or {}).get('state_questions', 0)}`",
        f"- Projection matched: `{(report.get('counts') or {}).get('projection_matched', 0)}`",
        f"- Baseline matched: `{(report.get('counts') or {}).get('baseline_matched', 0)}`",
        "",
        "Question hashes:",
    ]
    for question in report.get("questions") or []:
        lines.append(
            f"- `{question.get('question_hash')}` `{question.get('category')}` "
            f"baseline=`{question.get('baseline_matched')}` projection=`{question.get('projection_matched')}`"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def run_beam_state_trial(
    database_url: str,
    *,
    fixture_path: str | Path = DEFAULT_BEAM_FIXTURE,
    output_dir: str | Path,
    run_id: str,
    namespace: str,
    source_hash: str,
    top_k: int = 20,
    dataset_path: str | Path | None = None,
    beam_size: str = "1M",
    offset: int = 0,
    length: int = 1,
    conversations: str | None = None,
    max_questions: int | None = None,
    question_types: str | list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    fixture = _fixture_for_run(
        fixture_path,
        dataset_path,
        beam_size,
        offset,
        length,
        conversations,
        max_questions,
        question_types,
    )
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        _clear_existing_benchmark_rows(conn, fixture["dataset"], run_id)
        adapter = KontextBenchmarkAdapter(conn, fixture["dataset"], run_id)
        _add_conversations(adapter, fixture["conversations"])
        baseline_results = _run_question_searches(adapter, fixture, top_k)
        report = run_benchmark_state_trial(
            adapter.repo,
            fixture,
            namespace=namespace,
            source_hash=source_hash,
            extractor_version=DEFAULT_EXTRACTOR_VERSION,
            top_k=top_k,
            baseline_results=baseline_results,
            dry_run=dry_run,
        )
    paths = write_state_trial_report(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    return report


def run_private_bundle_state_trial(
    database_url: str,
    *,
    private_bundle_path: str | Path,
    output_dir: str | Path,
    namespace: str,
    source_hash: str,
    top_k: int = 20,
    event_granularity: str = "turn",
    dry_run: bool = False,
) -> dict[str, Any]:
    bundle = json.loads(Path(private_bundle_path).read_text(encoding="utf-8"))
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        report = run_judged_bundle_state_trial(
            repo,
            bundle,
            namespace=namespace,
            source_hash=source_hash,
            extractor_version=DEFAULT_EXTRACTOR_VERSION,
            top_k=top_k,
            event_granularity=event_granularity,
            dry_run=dry_run,
        )
    paths = write_state_trial_report(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run no-paid BEAM typed-state benchmark trial.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--fixture-path", default=str(DEFAULT_BEAM_FIXTURE))
    parser.add_argument("--private-bundle-path")
    parser.add_argument("--dataset-path")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--source-hash", required=True)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--private-event-granularity", default="turn", choices=["turn", "memory"])
    parser.add_argument("--beam-size", default="1M", choices=["100K", "500K", "1M", "10M"])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--length", type=int, default=1)
    parser.add_argument("--conversations")
    parser.add_argument("--max-questions", type=int)
    parser.add_argument("--question-types")
    parser.add_argument("--dry-run", action="store_true", help="Compute an in-memory projection without staging candidates or accepting events.")
    args = parser.parse_args()
    if args.private_bundle_path:
        report = run_private_bundle_state_trial(
            args.database_url,
            private_bundle_path=args.private_bundle_path,
            output_dir=args.output_dir,
            namespace=args.namespace,
            source_hash=args.source_hash,
            top_k=args.top_k,
            event_granularity=args.private_event_granularity,
            dry_run=args.dry_run,
        )
    else:
        report = run_beam_state_trial(
            args.database_url,
            fixture_path=args.fixture_path,
            output_dir=args.output_dir,
            run_id=args.run_id,
            namespace=args.namespace,
            source_hash=args.source_hash,
            top_k=args.top_k,
            dataset_path=args.dataset_path,
            beam_size=args.beam_size,
            offset=args.offset,
            length=args.length,
            conversations=args.conversations,
            max_questions=args.max_questions,
            question_types=args.question_types,
            dry_run=args.dry_run,
        )
    print(
        "beam-state-trial "
        f"ok={str(report['ok']).lower()} "
        f"planned={report['counts']['state_events_planned']} "
        f"projection={report['counts'].get('projection_matched', report['counts'].get('active_answer_overlap_questions', 0))}/"
        f"{report['counts']['projection_queries']} "
        f"writes_applied={report['writes_applied']}"
    )


if __name__ == "__main__":
    main()
