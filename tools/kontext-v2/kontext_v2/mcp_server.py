from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kontext_v2.intake import (
    clamp_float,
    clamp_int,
    gate_exchange,
    hash_text,
    normalize_messages,
    preview_messages,
    source_hash as intake_source_hash,
)
from kontext_v2.repository import KontextRepository
from kontext_v2.state_ingestion import state_ingestion_enabled


@dataclass(frozen=True)
class SearchArgs:
    query: str
    top_k: int
    domains: list[str]
    memory_tiers: list[str]
    memory_types: list[str]
    current_statuses: list[str]
    namespace: str = ""


STRING_ARRAY_SCHEMA = {"type": "array", "items": {"type": "string"}}
MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {"role": {"type": "string"}, "content": {"type": "string"}},
}
MESSAGE_ARRAY_SCHEMA = {"type": "array", "items": MESSAGE_SCHEMA}


SAVE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "domains": STRING_ARRAY_SCHEMA,
        "memory_type": {"type": "string"},
        "signal_strength": {"type": "number", "minimum": 0, "maximum": 10},
        "current_status": {"type": "string"},
        "memory_tier": {"type": "string"},
    },
    "required": ["content"],
}


UPDATE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "content": {"type": "string"},
        "reason": {"type": "string"},
        "domains": STRING_ARRAY_SCHEMA,
        "memory_type": {"type": "string"},
        "signal_strength": {"type": "number", "minimum": 0, "maximum": 10},
        "current_status": {"type": "string"},
        "memory_tier": {"type": "string"},
    },
    "required": ["id", "content", "reason"],
}


DELETE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["id", "reason"],
}


SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        "domains": STRING_ARRAY_SCHEMA,
        "memory_tiers": STRING_ARRAY_SCHEMA,
        "memory_types": STRING_ARRAY_SCHEMA,
        "current_statuses": STRING_ARRAY_SCHEMA,
        "namespace": {"type": "string"},
    },
    "required": ["query"],
}


FETCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
}


STATUS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"recent_limit": {"type": "integer", "minimum": 1, "maximum": 5000}},
}


MESSAGE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"messages": MESSAGE_ARRAY_SCHEMA, "context_messages": MESSAGE_ARRAY_SCHEMA},
    "required": ["messages"],
}


OVERRIDE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "reason": {"type": "string"},
        "domains": STRING_ARRAY_SCHEMA,
        "memory_type": {"type": "string"},
        "current_status": {"type": "string"},
        "memory_tier": {"type": "string"},
        "signal_strength": {"type": "number", "minimum": 0, "maximum": 10},
    },
    "required": ["content", "reason"],
}


FLAG_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "flag_type": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["id", "flag_type", "reason"],
}

STATE_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "source_kind": {"type": "string"},
        "source_id": {"type": "string"},
        "source_span": {"type": "string"},
        "source_span_hash": {"type": "string"},
        "subject_type": {"type": "string"},
        "subject_key": {"type": "string"},
        "state_key": {"type": "string"},
        "event_type": {"type": "string"},
        "value": {"type": "object"},
        "value_text": {"type": "string"},
        "prior_value_text": {"type": "string"},
        "effective_at": {"type": "string"},
        "observed_at": {"type": "string"},
        "actor_role": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "trust_tier": {"type": "string"},
        "metadata": {"type": "object"},
    },
    "required": ["subject_type", "subject_key", "state_key", "event_type"],
}
STATE_STAGE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string"},
        "source_hash": {"type": "string"},
        "extractor_version": {"type": "string"},
        "state_events": {"type": "array", "items": STATE_EVENT_SCHEMA},
    },
    "required": ["source_hash", "state_events"],
}
STATE_EDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "target_event_id": {"type": "string"},
        "edge_type": {"type": "string"},
        "state_key": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["target_event_id", "edge_type"],
}
STATE_ACCEPT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "namespace": {"type": "string"},
        "edges": {"type": "array", "items": STATE_EDGE_SCHEMA},
        "rebuild_projection": {"type": "boolean"},
    },
    "required": ["id"],
}
STATE_STATUS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"namespace": {"type": "string"}},
}


PROJECT_SCOPE_PROPERTIES = {
    "project": {"type": "string"},
    "project_root": {"type": "string"},
    "cwd": {"type": "string"},
}
PROJECT_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        **PROJECT_SCOPE_PROPERTIES,
    },
    "required": ["query"],
}
PROJECT_TIMELINE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "anchor_id": {"type": "string"},
        "query": {"type": "string"},
        "before": {"type": "integer", "minimum": 0, "maximum": 50},
        "after": {"type": "integer", "minimum": 0, "maximum": 50},
        **PROJECT_SCOPE_PROPERTIES,
    },
}
PROJECT_FILE_CONTEXT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        **PROJECT_SCOPE_PROPERTIES,
    },
    "required": ["file_path"],
}


CATEGORY_SLUG_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"slug": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
    "required": ["slug"],
}
CATEGORY_UPSERT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["name"],
}
CATEGORY_ASSIGN_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "memory_id": {"type": "string"},
        "source": {"type": "string"},
    },
    "required": ["slug", "memory_id"],
}


HOOK_HEARTBEAT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook_type": {"type": "string"},
        "source": {"type": "string"},
        "marker": {"type": "string"},
    },
}

PROJECT_OBSERVATION_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "event_type": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "cwd": {"type": "string"},
        "tool_name": {"type": "string"},
        "command_category": {"type": "string"},
        "status": {"type": "string"},
        "source_hash": {"type": "string"},
    },
}


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


def _tool(name: str, description: str, input_schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": input_schema}


def _hash_payload(tool: str, payload: dict[str, Any]) -> str:
    return hash_text(json.dumps({"tool": tool, **payload}, ensure_ascii=False, sort_keys=True))


def list_tools(
    write_enabled: bool = False,
    dry_run_write_enabled: bool = False,
    category_write_enabled: bool = False,
) -> list[dict[str, Any]]:
    tools = [
        _tool("search", "Search mirrored Kontext V2 memories.", SEARCH_INPUT_SCHEMA),
        _tool("fetch", "Fetch one mirrored memory by exact ID.", FETCH_INPUT_SCHEMA),
        _tool("ingestion_status", "Return safe aggregate Kontext V2 status.", STATUS_INPUT_SCHEMA),
        _tool("project_search", "Search compact project continuity observations.", PROJECT_SEARCH_INPUT_SCHEMA),
        _tool("project_timeline", "Return compact project observation context around an anchor.", PROJECT_TIMELINE_INPUT_SCHEMA),
        _tool("project_fetch", "Fetch project observation details by exact ID.", FETCH_INPUT_SCHEMA),
        _tool("project_file_context", "Return compact project context for a file path.", PROJECT_FILE_CONTEXT_INPUT_SCHEMA),
        _tool("list_categories", "List dashboard categories and safe aggregate counts.", EMPTY_INPUT_SCHEMA),
        _tool("list_category_memories", "List safe memory headers assigned to one category.", CATEGORY_SLUG_INPUT_SCHEMA),
        _tool("hook_heartbeat", "Record a content-free client hook heartbeat.", HOOK_HEARTBEAT_INPUT_SCHEMA),
        _tool("record_project_observation", "Record compact project continuity metadata from a client hook.", PROJECT_OBSERVATION_INPUT_SCHEMA),
    ]
    if write_enabled or dry_run_write_enabled:
        tools.extend(
            [
                _tool("save", "Dry-run save one durable memory by content.", SAVE_INPUT_SCHEMA),
                _tool("update", "Dry-run update one exact memory by ID.", UPDATE_INPUT_SCHEMA),
                _tool("delete", "Dry-run delete one exact memory by ID.", DELETE_INPUT_SCHEMA),
                _tool("extract_memories", "Dry-run extract candidate memories.", MESSAGE_INPUT_SCHEMA),
                _tool("ingest_exchange", "Dry-run ingest exchange proposals.", MESSAGE_INPUT_SCHEMA),
                _tool("submit_memory_override", "Submit exact reviewed memory proposal.", OVERRIDE_INPUT_SCHEMA),
                _tool("flag_memory", "Flag exact memory for maintenance.", FLAG_INPUT_SCHEMA),
            ]
        )
    if category_write_enabled:
        tools.extend(
            [
                _tool("upsert_category", "Create or update a category.", CATEGORY_UPSERT_INPUT_SCHEMA),
                _tool("assign_category", "Assign one memory to a category.", CATEGORY_ASSIGN_INPUT_SCHEMA),
                _tool("unassign_category", "Remove one memory/category assignment.", CATEGORY_ASSIGN_INPUT_SCHEMA),
                _tool("delete_category", "Delete a category by slug.", CATEGORY_SLUG_INPUT_SCHEMA),
            ]
        )
    if state_ingestion_enabled():
        tools.append(_tool("state_ingestion_status", "Return sanitized typed-state ingestion counts.", STATE_STATUS_INPUT_SCHEMA))
        if write_enabled:
            tools.extend(
                [
                    _tool("stage_state_events", "Stage reviewed typed-state event candidates.", STATE_STAGE_INPUT_SCHEMA),
                    _tool(
                        "accept_state_event_candidate",
                        "Accept one reviewed typed-state candidate and optionally rebuild projection.",
                        STATE_ACCEPT_INPUT_SCHEMA,
                    ),
                ]
            )
    return tools


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if isinstance(value, dict):
        values = value.get("in") or value.get("values") or []
    else:
        values = value
    if not isinstance(values, Iterable) or isinstance(values, (bytes, bytearray)):
        values = [values]
    return [str(item).strip() for item in values if str(item).strip()]


def normalize_search_args(payload: dict[str, Any]) -> SearchArgs:
    query = str(payload.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    top_k_value = 5 if payload.get("top_k") is None else payload.get("top_k")
    top_k = min(max(int(top_k_value), 1), 50)
    return SearchArgs(
        query=query,
        top_k=top_k,
        domains=_list(payload.get("domains")),
        memory_tiers=_list(payload.get("memory_tiers")),
        memory_types=_list(payload.get("memory_types")),
        current_statuses=_list(payload.get("current_statuses")),
        namespace=str(payload.get("namespace") or "").strip(),
    )


def fetch_tool(repo: KontextRepository, memory_id: str) -> dict[str, Any]:
    memory = repo.fetch_by_external_id(memory_id)
    if memory is None:
        return {"ok": False, "error": "not_found", "id": memory_id}
    return {
        "id": memory.external_mem0_id,
        "memory": memory.text,
        "title": memory.title,
        "metadata": memory.metadata,
    }


def ingestion_status_tool(repo: KontextRepository, recent_limit: int = 10) -> dict[str, Any]:
    latest_sync = repo.latest_mirror_sync_run()
    checked_at = datetime.now(timezone.utc).isoformat()
    return {
        "ok": True,
        "service": "kontext-v2",
        "mode": "mirror_read_only",
        "recent_limit": min(max(int(recent_limit), 1), 5000),
        "mirror": {"memories": repo.count_memories()},
        "writes": {"enabled": False},
        "sync": {"latest": latest_sync or {"status": "unknown", "checked_at": checked_at}},
        "intake": {"audit_rows": repo.count_intake_audit(), "flags": repo.count_memory_flags()},
        "maintenance": repo.maintenance_status(recent_limit=recent_limit),
    }


def _hash_text(value: str) -> str:
    return hash_text(value)


def _confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.8
    return clamp_float(confidence, default=0.8)


def ingest_exchange_dry_run(
    messages: list[dict[str, str]],
    origin: str,
    context_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    normalized_messages = normalize_messages([*(context_messages or []), *messages])
    exchange_hash = intake_source_hash(normalized_messages)
    preview = preview_messages(normalized_messages)
    gate = gate_exchange(normalized_messages)
    gate_payload = gate.to_dict()
    preview_payload = {"hash": _hash_text(preview), "chars": len(preview)}
    proposals = []
    if gate.keep:
        proposals.append(
            {
                "action": "create_candidate",
                "source_hash": exchange_hash,
                "origin": origin,
                "reason": gate.reason,
                "domains": gate.domains,
                "preview_hash": preview_payload["hash"],
            }
        )
    return {
        "ok": True,
        "tool": "ingest_exchange",
        "mode": "dry_run",
        "writes_applied": 0,
        "source_hash": exchange_hash,
        "gate": gate_payload,
        "preview": preview_payload,
        "proposals": proposals,
    }


def save_memory_dry_run(
    *,
    content: str,
    origin: str,
    domains: list[str] | None = None,
    memory_type: str = "note",
    signal_strength: Any = None,
    current_status: str = "active",
    memory_tier: str = "active",
) -> dict[str, Any]:
    normalized_content = str(content or "").strip()
    proposal = {
        "action": "save",
        "content_hash": _hash_text(normalized_content),
        "domains": [str(value).strip().lower() for value in (domains or []) if str(value).strip()],
        "memory_type": str(memory_type or "").strip(),
        "current_status": current_status or "active",
        "memory_tier": memory_tier or "active",
    }
    if signal_strength is not None:
        proposal["signal_strength"] = clamp_int(signal_strength)
    return {
        "ok": True,
        "tool": "save",
        "mode": "dry_run",
        "writes_applied": 0,
        "origin": origin,
        "source_hash": _hash_payload("save", {"origin": origin, **proposal}),
        "proposal": proposal,
    }


def update_memory_dry_run(
    *,
    memory_id: str,
    content: str,
    reason: str,
    origin: str,
    domains: list[str] | None = None,
    memory_type: str | None = None,
    signal_strength: Any = None,
    current_status: str | None = None,
    memory_tier: str | None = None,
) -> dict[str, Any]:
    normalized_content = str(content or "").strip()
    proposal = {
        "action": "update",
        "existing_id": str(memory_id or "").strip(),
        "content_hash": _hash_text(normalized_content),
        "reason_hash": _hash_text(str(reason or "").strip()),
        "domains": [str(value).strip().lower() for value in (domains or []) if str(value).strip()],
        "memory_type": str(memory_type or "").strip(),
        "current_status": current_status or "active",
        "memory_tier": memory_tier or "active",
    }
    if signal_strength is not None:
        proposal["signal_strength"] = clamp_int(signal_strength)
    return {
        "ok": True,
        "tool": "update",
        "mode": "dry_run",
        "writes_applied": 0,
        "origin": origin,
        "source_hash": _hash_payload("update", {"origin": origin, **proposal}),
        "proposal": proposal,
    }


def delete_memory_dry_run(*, memory_id: str, reason: str, origin: str) -> dict[str, Any]:
    flag = {
        "id": str(memory_id or "").strip(),
        "flag_type": "delete_candidate",
        "reason_hash": _hash_text(str(reason or "").strip()),
        "confidence": 0.8,
    }
    return {
        "ok": True,
        "tool": "delete",
        "mode": "dry_run",
        "writes_applied": 0,
        "origin": origin,
        "source_hash": _hash_payload("delete", {"origin": origin, **flag}),
        "flag": flag,
    }


def extract_memories_dry_run(
    messages: list[dict[str, str]],
    origin: str,
    context_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload = ingest_exchange_dry_run(messages=messages, context_messages=context_messages, origin=origin)
    payload["tool"] = "extract_memories"
    return payload


def submit_memory_override_dry_run(
    *,
    content: str,
    reason: str,
    origin: str,
    domains: list[str] | None = None,
    memory_type: str = "",
    current_status: str = "active",
    memory_tier: str = "active",
    signal_strength: Any = None,
) -> dict[str, Any]:
    proposal: dict[str, Any] = {
        "action": "save",
        "content_hash": _hash_text(content),
        "reason_hash": _hash_text(reason),
        "domains": [str(value).strip().lower() for value in (domains or []) if str(value).strip()],
        "memory_type": str(memory_type or "").strip(),
        "current_status": current_status or "active",
        "memory_tier": memory_tier or "active",
    }
    if signal_strength is not None:
        proposal["signal_strength"] = clamp_int(signal_strength)
    return {
        "ok": True,
        "tool": "submit_memory_override",
        "mode": "dry_run",
        "writes_applied": 0,
        "origin": origin,
        "proposal": proposal,
    }


def flag_memory_dry_run(memory_id: str, flag_type: str, reason: str, confidence: Any, origin: str) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": "flag_memory",
        "mode": "dry_run",
        "writes_applied": 0,
        "origin": origin,
        "flag": {
            "id": memory_id,
            "flag_type": flag_type,
            "reason_hash": _hash_text(reason),
            "confidence": _confidence(confidence),
        },
    }


def project_search_empty(query: str, limit: int = 10) -> dict[str, Any]:
    _ = (query, min(max(int(limit or 10), 1), 100))
    return {"rows": [], "count": 0, "status": "not_configured"}


def project_timeline_empty(anchor_id: str = "", query: str = "", before: int = 3, after: int = 3) -> dict[str, Any]:
    _ = (anchor_id, query, min(max(int(before or 0), 0), 50), min(max(int(after or 0), 0), 50))
    return {"rows": [], "count": 0, "status": "not_configured"}


def project_fetch_empty(observation_id: str) -> dict[str, Any]:
    return {"ok": False, "error": "not_configured", "id": observation_id}


def project_file_context_empty(file_path: str, limit: int = 10) -> dict[str, Any]:
    _ = min(max(int(limit or 10), 1), 100)
    return {"rows": [], "count": 0, "status": "not_configured", "file_path": file_path}
