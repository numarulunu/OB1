from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

SCHEMA_VERSION = "typed-state-v2"
VALID_EVENT_RELATIONS = {
    "current",
    "supersedes",
    "cancels",
    "corrects",
    "supports",
    "contradicts",
    "unknown",
}


@dataclass(frozen=True)
class TypedStateEventAnswer:
    schema_version: str
    namespace: str
    subject_id: str
    state_key: str
    status: str
    event_relation: str
    value: dict[str, Any]
    value_text: str
    active_event_id: str
    support_event_ids: list[str]
    superseded_event_ids: list[str]
    cancelled_event_ids: list[str]
    confidence: float
    trust_tier: str
    projection_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def typed_state_v2_enabled() -> bool:
    return _env_bool("KONTEXT_TYPED_STATE_V2")


def typed_object_summary_enabled() -> bool:
    return _env_bool("KONTEXT_TYPED_OBJECT_SUMMARY")


def _increment(bucket: dict[str, int], key: str) -> None:
    bucket[key] = int(bucket.get(key) or 0) + 1


def summarize_typed_state_objects(values: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    event_relations: dict[str, int] = {}
    valid_schema = 0
    total = 0
    for value in values:
        if not isinstance(value, dict):
            continue
        total += 1
        if value.get("schema_version") == SCHEMA_VERSION:
            valid_schema += 1
        _increment(statuses, str(value.get("status") or "unknown"))
        _increment(event_relations, str(value.get("event_relation") or "unknown"))
    return {
        "total": total,
        "valid_schema": valid_schema,
        "statuses": dict(sorted(statuses.items())),
        "event_relations": dict(sorted(event_relations.items())),
    }


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        metadata = value.get("metadata")
    else:
        metadata = getattr(value, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _get(value: Any, key: str, default: Any = None) -> Any:
    metadata = _metadata(value)
    if isinstance(value, dict):
        item = value.get(key)
    else:
        item = getattr(value, key, None)
    if item is None or item == "":
        return metadata.get(key, default)
    return item


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_ids(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if value:
        return [str(value)]
    return []


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _status(value: Any) -> str:
    raw = _get(value, "state_status") or _get(value, "status") or _get(value, "current_status") or "unknown"
    status = str(raw).strip().lower()
    return status if status in {"active", "cancelled", "ambiguous", "unknown"} else "unknown"


def _event_relation(value: Any, *, status: str, support_ids: list[str], superseded_ids: list[str], cancelled_ids: list[str]) -> str:
    explicit = str(_get(value, "event_relation") or "").strip().lower()
    if explicit in VALID_EVENT_RELATIONS and status == "active":
        return explicit
    event_type = str(_get(value, "state_event_type") or _get(value, "event_type") or "").strip().lower()
    if status == "cancelled" or event_type == "cancellation":
        return "cancels"
    if status == "ambiguous":
        return "contradicts"
    if status == "unknown":
        return "unknown"
    if event_type == "correction":
        return "corrects"
    if event_type == "supersession" or superseded_ids:
        return "supersedes"
    if support_ids:
        return "supports"
    if cancelled_ids:
        return "cancels"
    return "current"


def render_current_state_typed(value: Any) -> dict[str, Any]:
    metadata = _metadata(value)
    status = _status(value)
    def direct_get(key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    fact_value = direct_get("current_value")
    if fact_value is None:
        fact_value = direct_get("fact_value")
    if fact_value is None and status == "active":
        fact_value = metadata.get("current_value")
    if fact_value is None and status == "active":
        fact_value = metadata.get("fact_value")
    if fact_value is None:
        fact_value = {}
    value_text = (
        _get(value, "fact_text")
        or _get(value, "value_text")
        or _get(value, "text")
        or metadata.get("value_text")
        or ""
    )
    active_event_id = str(_get(value, "active_event_id") or "")
    support_ids = _coerce_ids(_get(value, "support_event_ids"))
    superseded_ids = _coerce_ids(_get(value, "superseded_event_ids"))
    cancelled_ids = _coerce_ids(_get(value, "cancelled_event_ids"))
    answer = TypedStateEventAnswer(
        schema_version=SCHEMA_VERSION,
        namespace=str(_get(value, "namespace") or "live"),
        subject_id=str(_get(value, "subject_id") or ""),
        state_key=str(_get(value, "state_key") or ""),
        status=status,
        event_relation=_event_relation(
            value,
            status=status,
            support_ids=support_ids,
            superseded_ids=superseded_ids,
            cancelled_ids=cancelled_ids,
        ),
        value=_coerce_dict(fact_value),
        value_text=str(value_text),
        active_event_id=active_event_id,
        support_event_ids=support_ids,
        superseded_event_ids=superseded_ids,
        cancelled_event_ids=cancelled_ids,
        confidence=_coerce_float(_get(value, "confidence")),
        trust_tier=str(_get(value, "trust_tier") or ""),
        projection_version=str(_get(value, "projection_version") or ""),
    )
    return answer.to_dict()


def attach_typed_state_metadata(row: dict[str, Any]) -> dict[str, Any]:
    clean_row = dict(row)
    metadata = _metadata(clean_row)
    typed = render_current_state_typed(clean_row)
    metadata["typed_state_v2"] = typed
    clean_row["metadata"] = metadata
    clean_row["typed_state_v2"] = typed
    return clean_row
