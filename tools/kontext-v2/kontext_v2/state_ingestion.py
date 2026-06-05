from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from kontext_v2.state_model import (
    canonical_json,
    idempotency_key as make_idempotency_key,
    normalize_namespace,
    normalize_state_key,
    normalize_subject_key,
    normalize_subject_type,
    stable_hash,
    validate_edge_type,
    validate_event_type,
    value_hash as make_value_hash,
)

DEFAULT_STATE_EXTRACTOR_VERSION = "state-ingestion-v1"
DEFAULT_REVIEW_STATUS = "needs_review"


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def state_model_enabled() -> bool:
    return _env_bool("KONTEXT_STATE_MODEL_ENABLED")


def state_ingestion_enabled() -> bool:
    return state_model_enabled() and _env_bool("KONTEXT_STATE_INGESTION_ENABLED")


def state_auto_accept_enabled() -> bool:
    return state_ingestion_enabled() and _env_bool("KONTEXT_STATE_AUTO_ACCEPT_ENABLED")


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


def _json_payload(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid state event JSON") from exc
    else:
        payload = raw
    if not isinstance(payload, dict):
        raise ValueError("state event payload must be an object")
    return payload


def _event_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("state_events", payload.get("proposals"))
    if not isinstance(items, list):
        raise ValueError("state_events field is required")
    return [item for item in items if isinstance(item, dict)]


def _span_hash(item: dict[str, Any], *, source_hash: str, index: int) -> str:
    explicit = str(item.get("source_span_hash") or item.get("span_hash") or "").strip()
    if explicit:
        return explicit
    source_span = str(item.get("source_span") or item.get("span") or "").strip()
    if source_span:
        return stable_hash(source_span)
    return stable_hash(f"{source_hash}:{index}")


def _value_payload(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("value")
    if isinstance(value, dict):
        return dict(value)
    text = str(item.get("value_text") or item.get("text") or "").strip()
    return {"text": text} if text else {}


def _value_text(item: dict[str, Any], value: dict[str, Any]) -> str:
    text = str(item.get("value_text") or item.get("text") or "").strip()
    if text:
        return text
    if "text" in value:
        return str(value.get("text") or "").strip()
    return canonical_json(value)


@dataclass(frozen=True)
class StateEventProposal:
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
    extractor_version: str
    metadata: dict[str, Any]

    def stage_kwargs(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "source_span_hash": self.source_span_hash,
            "subject_type": self.subject_type,
            "subject_key": self.subject_key,
            "state_key": self.state_key,
            "event_type": self.event_type,
            "value": self.value,
            "value_text": self.value_text,
            "prior_value_text": self.prior_value_text,
            "effective_at": self.effective_at,
            "observed_at": self.observed_at,
            "actor_role": self.actor_role,
            "confidence": self.confidence,
            "trust_tier": self.trust_tier,
            "status": DEFAULT_REVIEW_STATUS,
            "extractor_version": self.extractor_version,
            "metadata": self.metadata,
        }

    def safe_result(self, candidate_id: str = "", review_status: str = DEFAULT_REVIEW_STATUS) -> dict[str, Any]:
        return {
            "action": "stage_candidate",
            "id": candidate_id,
            "namespace": self.namespace,
            "subject_type": self.subject_type,
            "subject_key": self.subject_key,
            "state_key": self.state_key,
            "event_type": self.event_type,
            "value_hash": self.value_hash,
            "idempotency_key": self.idempotency_key,
            "trust_tier": self.trust_tier,
            "confidence": self.confidence,
            "review_status": review_status,
        }


def normalize_state_event_proposals(
    raw: str | dict[str, Any],
    *,
    namespace: str = "live",
    source_hash: str,
    extractor_version: str = DEFAULT_STATE_EXTRACTOR_VERSION,
) -> list[StateEventProposal]:
    payload = _json_payload(raw)
    normalized_namespace = normalize_namespace(namespace or payload.get("namespace") or "live")
    normalized_source_hash = str(source_hash or payload.get("source_hash") or "").strip()
    if not normalized_source_hash:
        raise ValueError("source_hash is required")
    normalized_version = str(extractor_version or payload.get("extractor_version") or DEFAULT_STATE_EXTRACTOR_VERSION)

    proposals: list[StateEventProposal] = []
    for index, item in enumerate(_event_items(payload)):
        value = _value_payload(item)
        text = _value_text(item, value)
        if not text:
            continue
        subject_type = normalize_subject_type(item.get("subject_type"))
        subject_key = normalize_subject_key(item.get("subject_key"))
        state_key = normalize_state_key(item.get("state_key"))
        event_type = validate_event_type(str(item.get("event_type") or "assertion"))
        payload_hash = make_value_hash(value)
        span_hash = _span_hash(item, source_hash=normalized_source_hash, index=index)
        proposals.append(
            StateEventProposal(
                namespace=normalized_namespace,
                source_kind=str(item.get("source_kind") or payload.get("source_kind") or "state_ingestion"),
                source_id=str(item.get("source_id") or payload.get("source_id") or ""),
                source_hash=normalized_source_hash,
                source_span_hash=span_hash,
                subject_type=subject_type,
                subject_key=subject_key,
                state_key=state_key,
                event_type=event_type,
                value=value,
                value_text=text,
                value_hash=payload_hash,
                idempotency_key=make_idempotency_key(
                    namespace=normalized_namespace,
                    source_hash=normalized_source_hash,
                    source_span_hash=span_hash,
                    extractor_version=normalized_version,
                    subject_type=subject_type,
                    subject_key=subject_key,
                    state_key=state_key,
                    event_type=event_type,
                    value_hash=payload_hash,
                ),
                prior_value_text=str(item.get("prior_value_text") or ""),
                effective_at=item.get("effective_at"),
                observed_at=item.get("observed_at"),
                actor_role=str(item.get("actor_role") or ""),
                confidence=_clamp_confidence(item.get("confidence")),
                trust_tier=str(item.get("trust_tier") or "extracted_low"),
                extractor_version=normalized_version,
                metadata={
                    **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
                    "proposal_index": index,
                    "ingestion_source": "state_event_proposal",
                },
            )
        )
    return proposals


def stage_state_event_proposals(
    repo: Any,
    raw: str | dict[str, Any],
    *,
    namespace: str = "live",
    source_hash: str,
    extractor_version: str = DEFAULT_STATE_EXTRACTOR_VERSION,
    allow_auto_accept: bool = False,
) -> dict[str, Any]:
    if not state_ingestion_enabled():
        return {
            "mode": "disabled",
            "enabled": False,
            "writes_applied": 0,
            "source_hash": source_hash,
            "counts": {"staged": 0, "auto_accepted": 0, "skipped": 0, "errors": []},
            "results": [],
        }

    proposals = normalize_state_event_proposals(
        raw,
        namespace=namespace,
        source_hash=source_hash,
        extractor_version=extractor_version,
    )
    transactional_auto_accept = (
        allow_auto_accept
        and state_auto_accept_enabled()
        and callable(getattr(repo, "transaction", None))
    )
    if transactional_auto_accept:
        try:
            with repo.transaction():
                counts = {"staged": 0, "auto_accepted": 0, "skipped": 0, "errors": []}
                results: list[dict[str, Any]] = []
                writes_applied = 0
                touched_namespaces: set[str] = set()
                for proposal in proposals:
                    candidate = repo.stage_state_event_candidate(**proposal.stage_kwargs())
                    counts["staged"] += 1
                    writes_applied += 1
                    result = proposal.safe_result(
                        candidate_id=str(getattr(candidate, "id", "") or ""),
                        review_status=str(getattr(candidate, "status", DEFAULT_REVIEW_STATUS) or DEFAULT_REVIEW_STATUS),
                    )
                    event = repo.accept_state_event_candidate(
                        str(getattr(candidate, "id", "")),
                        namespace=proposal.namespace,
                    )
                    event_namespace = normalize_namespace(getattr(event, "namespace", proposal.namespace))
                    touched_namespaces.add(event_namespace)
                    result["auto_accepted"] = True
                    result["event_id"] = str(getattr(event, "id", "") or "")
                    counts["auto_accepted"] += 1
                    results.append(result)
                for touched_namespace in sorted(touched_namespaces):
                    repo.rebuild_current_state_projection(namespace=touched_namespace)
                return {
                    "mode": "stage",
                    "enabled": True,
                    "writes_applied": writes_applied,
                    "source_hash": source_hash,
                    "counts": counts,
                    "results": results,
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "mode": "stage",
                "enabled": True,
                "writes_applied": 0,
                "source_hash": source_hash,
                "counts": {"staged": 0, "auto_accepted": 0, "skipped": 0, "errors": [type(exc).__name__]},
                "results": [{"action": "error", "reason": type(exc).__name__}],
            }
    counts = {"staged": 0, "auto_accepted": 0, "skipped": 0, "errors": []}
    results: list[dict[str, Any]] = []
    writes_applied = 0
    touched_namespaces: set[str] = set()

    for proposal in proposals:
        try:
            candidate = repo.stage_state_event_candidate(**proposal.stage_kwargs())
            counts["staged"] += 1
            writes_applied += 1
            result = proposal.safe_result(
                candidate_id=str(getattr(candidate, "id", "") or ""),
                review_status=str(getattr(candidate, "status", DEFAULT_REVIEW_STATUS) or DEFAULT_REVIEW_STATUS),
            )
            if allow_auto_accept and state_auto_accept_enabled():
                event = repo.accept_state_event_candidate(
                    str(getattr(candidate, "id", "")),
                    namespace=proposal.namespace,
                )
                event_namespace = normalize_namespace(getattr(event, "namespace", proposal.namespace))
                touched_namespaces.add(event_namespace)
                result["auto_accepted"] = True
                result["event_id"] = str(getattr(event, "id", "") or "")
                counts["auto_accepted"] += 1
            else:
                result["auto_accepted"] = False
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            counts["errors"].append(type(exc).__name__)
            results.append({"action": "error", "reason": type(exc).__name__, "state_key": proposal.state_key})

    for touched_namespace in sorted(touched_namespaces):
        try:
            repo.rebuild_current_state_projection(namespace=touched_namespace)
        except Exception as exc:  # noqa: BLE001
            counts["errors"].append(type(exc).__name__)

    return {
        "mode": "stage",
        "enabled": True,
        "writes_applied": writes_applied,
        "source_hash": source_hash,
        "counts": counts,
        "results": results,
    }


def _edge_requests(edges: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [edge for edge in (edges or []) if isinstance(edge, dict)]


def accept_reviewed_state_candidate(
    repo: Any,
    candidate_id: str,
    *,
    namespace: str = "live",
    edges: list[dict[str, Any]] | None = None,
    rebuild_projection: bool = True,
) -> dict[str, Any]:
    transaction = getattr(repo, "transaction", None)
    if callable(transaction):
        with transaction():
            return _accept_reviewed_state_candidate(
                repo,
                candidate_id,
                namespace=namespace,
                edges=edges,
                rebuild_projection=rebuild_projection,
            )
    return _accept_reviewed_state_candidate(
        repo,
        candidate_id,
        namespace=namespace,
        edges=edges,
        rebuild_projection=rebuild_projection,
    )


def _accept_reviewed_state_candidate(
    repo: Any,
    candidate_id: str,
    *,
    namespace: str = "live",
    edges: list[dict[str, Any]] | None = None,
    rebuild_projection: bool = True,
) -> dict[str, Any]:
    expected_namespace = normalize_namespace(namespace)
    event = repo.accept_state_event_candidate(str(candidate_id), namespace=expected_namespace)
    event_namespace = normalize_namespace(getattr(event, "namespace", "live"))
    event_id = str(getattr(event, "id", "") or "")
    event_state_key = normalize_state_key(getattr(event, "state_key", "state"))
    saved_edges: list[dict[str, Any]] = []
    for edge in _edge_requests(edges):
        target_event_id = str(edge.get("target_event_id") or "").strip()
        if not target_event_id:
            continue
        edge_type = validate_edge_type(str(edge.get("edge_type") or "supports"))
        edge_state_key = normalize_state_key(edge.get("state_key") or event_state_key)
        reason_hash = stable_hash(str(edge.get("reason") or ""))
        repo.insert_state_event_edge(
            namespace=event_namespace,
            source_event_id=event_id,
            target_event_id=target_event_id,
            edge_type=edge_type,
            state_key=edge_state_key,
            reason_hash=reason_hash,
        )
        saved_edges.append(
            {
                "target_event_id": target_event_id,
                "edge_type": edge_type,
                "state_key": edge_state_key,
                "reason_hash": reason_hash,
            }
        )

    projection_fact_count = 0
    if rebuild_projection:
        projection_fact_count = len(repo.rebuild_current_state_projection(namespace=event_namespace))

    return {
        "mode": "accept",
        "writes_applied": 1 + len(saved_edges),
        "candidate_id": str(candidate_id),
        "event_id": event_id,
        "namespace": event_namespace,
        "state_key": event_state_key,
        "event_type": str(getattr(event, "event_type", "") or ""),
        "value_hash": str(getattr(event, "value_hash", "") or ""),
        "review_status": "accepted",
        "edges": [
            {
                "source_event_id": event_id,
                "target_event_id": edge["target_event_id"],
                "edge_type": edge["edge_type"],
                "state_key": edge["state_key"],
                "reason_hash": edge["reason_hash"],
            }
            for edge in saved_edges
        ],
        "projection_rebuilt": rebuild_projection,
        "projection_fact_count": projection_fact_count,
    }


def state_ingestion_status(repo: Any, *, namespace: str = "live") -> dict[str, Any]:
    normalized_namespace = normalize_namespace(namespace)
    counts = repo.state_model_counts(namespace=normalized_namespace)
    return {
        "enabled": state_ingestion_enabled(),
        "namespace": normalized_namespace,
        "candidates_by_status": dict(counts.get("candidates_by_status") or {}),
        "events": int(counts.get("events") or 0),
        "facts_by_status": dict(counts.get("facts_by_status") or {}),
    }
