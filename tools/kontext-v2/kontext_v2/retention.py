from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


PROTECTED_AUTOBIOGRAPHICAL_DOMAINS = {
    "attachment",
    "family",
    "family_origin",
    "identity",
    "personal_history",
    "personal_life",
    "psychology",
    "relationship",
    "relationships",
    "trauma",
}

PROTECTED_AUTOBIOGRAPHICAL_MEMORY_TYPES = {
    "attachment_pattern",
    "family_context",
    "family_origin",
    "formative_event",
    "identity_pattern",
    "personal_history",
    "psychology_pattern",
    "relationship_context",
    "relationship_pattern",
    "trauma_context",
}

FALSE_OR_REMOVED_STATUSES = {"deleted", "false", "outdated"}

CLEANUP_FLAG_TERMS = {
    "archive",
    "cold",
    "cleanup",
    "decay",
    "delete",
    "downgrade",
    "duplicate",
    "obsolete",
    "prune",
    "purge",
    "stale",
}


def _value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _metadata(row: Any) -> dict[str, Any]:
    raw = _value(row, "metadata")
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _tokens(value: Any) -> set[str]:
    if isinstance(value, str):
        items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = []
    return {_normalize_token(item) for item in items if _normalize_token(item)}


def memory_domains(row: Any) -> set[str]:
    metadata = _metadata(row)
    return _tokens(_value(row, "domains")) | _tokens(metadata.get("domains")) | _tokens(metadata.get("domain"))


def memory_type(row: Any) -> str:
    metadata = _metadata(row)
    return _normalize_token(_value(row, "memory_type") or metadata.get("memory_type"))


def current_status(row: Any) -> str:
    metadata = _metadata(row)
    return _normalize_token(_value(row, "current_status") or metadata.get("current_status") or "active")


def is_protected_autobiographical_history(row: Any) -> bool:
    if row is None:
        return False
    if current_status(row) in FALSE_OR_REMOVED_STATUSES:
        return False
    domains = memory_domains(row)
    type_name = memory_type(row)
    return bool(
        domains & PROTECTED_AUTOBIOGRAPHICAL_DOMAINS
        or type_name in PROTECTED_AUTOBIOGRAPHICAL_MEMORY_TYPES
    )


def cleanup_flag_blocked_for_protected_history(flag_type: str, row: Any) -> bool:
    if not is_protected_autobiographical_history(row):
        return False
    flag = _normalize_token(flag_type)
    return any(term in flag for term in CLEANUP_FLAG_TERMS)
