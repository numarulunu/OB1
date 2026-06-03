from __future__ import annotations

import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from kontext_v2.intake import IngestionProposal, apply_intake_proposals
from kontext_v2.mcp_server import (
    delete_memory_dry_run,
    extract_memories_dry_run,
    fetch_tool,
    flag_memory_dry_run,
    ingest_exchange_dry_run,
    ingestion_status_tool,
    list_tools,
    normalize_search_args,
    project_fetch_empty,
    project_file_context_empty,
    project_search_empty,
    project_timeline_empty,
    save_memory_dry_run,
    submit_memory_override_dry_run,
    update_memory_dry_run,
)
from kontext_v2.retention import cleanup_flag_blocked_for_protected_history
from kontext_v2.retrieval import search_memories
from kontext_v2.retrieval_shadow import shadow_result_metrics, shadow_search_filters
from kontext_v2.state_ingestion import (
    accept_reviewed_state_candidate,
    stage_state_event_proposals,
    state_ingestion_enabled,
    state_ingestion_status,
)


JSONRPC_VERSION = "2.0"
SUPPORTED_PROTOCOL_VERSION = "2024-11-05"


@dataclass(frozen=True)
class McpProfile:
    name: str
    token: str
    can_write: bool = False
    can_dry_run_write: bool = False
    can_write_categories: bool = False


PROFILE_TOKEN_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mem0", ("KONTEXT_MCP_MEM0_TOKEN", "KONTEXT_MCP_MEM0_COMPAT_TOKEN")),
    ("codex", ("KONTEXT_MCP_CODEX_TOKEN", "MCP_CODEX_TOKEN")),
    ("claude", ("KONTEXT_MCP_CLAUDE_TOKEN", "MCP_CLAUDE_TOKEN")),
    ("chatgpt", ("KONTEXT_MCP_CHATGPT_TOKEN", "MCP_READONLY_TOKEN", "MCP_CHATGPT_TOKEN", "MCP_PATH_TOKEN")),
    ("perplexity", ("KONTEXT_MCP_PERPLEXITY_TOKEN", "MCP_PERPLEXITY_TOKEN")),
)

def _first_env(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _env_name_set(env: Mapping[str, str], *names: str) -> set[str]:
    raw = _first_env(env, *names)
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.replace(";", ",").replace(" ", ",").split(",") if item.strip()}


def _profile_can_write(profile_name: str, write_enabled: bool, write_profile_names: set[str]) -> bool:
    return bool(write_enabled or profile_name.lower() in write_profile_names)


def build_mcp_profiles(
    env: Mapping[str, str],
    write_enabled: bool = False,
    dry_run_write_enabled: bool = False,
) -> dict[str, McpProfile]:
    profiles: list[McpProfile] = []
    write_profile_names = _env_name_set(
        env,
        "KONTEXT_MCP_WRITE_PROFILES",
        "KONTEXT_MCP_WRITE_ENABLED_PROFILES",
    )
    category_write_profile_names = _env_name_set(
        env,
        "KONTEXT_MCP_CATEGORY_WRITE_PROFILES",
        "KONTEXT_MCP_CATEGORY_WRITE_ENABLED_PROFILES",
    )
    generic_token = _first_env(env, "KONTEXT_MCP_TOKEN")
    if generic_token:
        can_write = _profile_can_write("kontext", write_enabled, write_profile_names)
        profiles.append(
            McpProfile(
                name="kontext",
                token=generic_token,
                can_write=can_write,
                can_dry_run_write=bool(can_write or dry_run_write_enabled),
                can_write_categories="kontext" in category_write_profile_names,
            )
        )

    for name, keys in PROFILE_TOKEN_KEYS:
        token = _first_env(env, *keys)
        if token:
            can_write = _profile_can_write(name, write_enabled, write_profile_names)
            profiles.append(
                McpProfile(
                    name=name,
                    token=token,
                    can_write=can_write,
                    can_dry_run_write=bool(can_write or dry_run_write_enabled),
                    can_write_categories=name in category_write_profile_names,
                )
            )

    tokens = [profile.token for profile in profiles]
    if len(tokens) != len(set(tokens)):
        raise RuntimeError("Kontext MCP profile tokens must be unique")
    return {profile.token: profile for profile in profiles}


def profile_names(profiles: Mapping[str, McpProfile]) -> list[str]:
    return sorted({profile.name for profile in profiles.values()})


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": {"code": code, "message": message}}


def _text_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "isError": False}


def _compact_search_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "id": row.get("external_mem0_id") or row.get("id"),
        "title": row.get("title") or "",
        "metadata": metadata,
    }


def _compact_category_row(row: dict[str, Any]) -> dict[str, Any]:
    entries = row.get("entries") if isinstance(row.get("entries"), list) else []
    sample_entries = [str(item) for item in entries[:5]]
    count = int(row.get("count") or len(entries) or 0)
    return {
        "id": str(row.get("id") or ""),
        "slug": str(row.get("slug") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "count": count,
        "stale": int(row.get("stale") or 0),
        "uses": int(row.get("uses") or 0),
        "sources": row.get("sources") if isinstance(row.get("sources"), dict) else {},
        "sample_entries": sample_entries,
        "entries_truncated": max(count - len(sample_entries), 0),
    }


def _compact_category_memory_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    domains = metadata.get("domains") if isinstance(metadata.get("domains"), list) else []
    return {
        "id": row.get("external_mem0_id") or row.get("id"),
        "title": row.get("title") or "",
        "domains": [str(item) for item in domains[:8]],
        "memory_type": row.get("memory_type"),
        "current_status": row.get("current_status"),
        "memory_tier": row.get("memory_tier"),
        "signal_strength": row.get("signal_strength"),
        "source": row.get("source"),
    }


def _parse_call_params(params: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    name = str(params.get("name") or "").strip()
    arguments = params.get("arguments") or {}
    if not name:
        raise ValueError("tool name is required")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return name, arguments


def _request_token(request: Request) -> str:
    authorization = str(request.headers.get("authorization") or "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return str(
        request.headers.get("x-kontext-token")
        or request.headers.get("x-mcp-token")
        or request.query_params.get("token")
        or ""
    ).strip()


def _profile_from_request(request: Request, profiles: Mapping[str, McpProfile]) -> McpProfile:
    token = _request_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="token required")
    for configured_token, profile in profiles.items():
        if hmac.compare_digest(token, configured_token):
            return profile
    raise HTTPException(status_code=401, detail="invalid token")


def _mcp_probe_response(request: Request) -> Response | dict[str, str | bool]:
    if request.method == "GET" and "text/event-stream" in request.headers.get("accept", "").lower():
        return Response(status_code=405, headers={"Allow": "POST, OPTIONS"})
    return {"ok": True, "transport": "jsonrpc-http", "service": "kontext-v2"}


async def _handle_mcp_request(request: Request, service: Any, profile: McpProfile) -> Response:
    try:
        message = await request.json()
    except Exception:
        return JSONResponse(_jsonrpc_error(None, -32700, "Parse error"), status_code=400)
    if not isinstance(message, dict):
        return JSONResponse(_jsonrpc_error(None, -32600, "Invalid request"), status_code=400)

    request_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params") or {}

    if request_id is None and method.startswith("notifications/"):
        return Response(status_code=202)

    try:
        result = _handle_mcp_method(service, profile, method, params)
    except ValueError as exc:
        return JSONResponse(_jsonrpc_error(request_id, -32602, str(exc)), status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_jsonrpc_error(request_id, -32603, type(exc).__name__), status_code=500)
    return JSONResponse(_jsonrpc_result(request_id, result))


def register_mcp_routes(app: FastAPI, service: Any, profiles: Mapping[str, McpProfile]) -> None:
    @app.api_route("/mcp", methods=["GET", "DELETE"], include_in_schema=False)
    async def mcp_header_session_probe(request: Request):
        _profile_from_request(request, profiles)
        return _mcp_probe_response(request)

    @app.post("/mcp", include_in_schema=False)
    async def handle_mcp_header(request: Request) -> Response:
        profile = _profile_from_request(request, profiles)
        return await _handle_mcp_request(request, service, profile)

    @app.api_route("/mcp/{token}", methods=["GET", "DELETE"], include_in_schema=False)
    async def mcp_session_probe(token: str, request: Request):
        if token not in profiles:
            raise HTTPException(status_code=404, detail="Not found")
        return _mcp_probe_response(request)

    @app.post("/mcp/{token}", include_in_schema=False)
    async def handle_mcp(token: str, request: Request) -> Response:
        profile = profiles.get(token)
        if profile is None:
            raise HTTPException(status_code=404, detail="Not found")
        return await _handle_mcp_request(request, service, profile)




def _message_list(value: Any, name: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    rows: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append({"role": str(item.get("role") or ""), "content": str(item.get("content") or "")})
    return rows


def _string_list(value: Any) -> list[str]:
    return [str(item).strip().lower() for item in (value or []) if str(item).strip()]


def _fetch_memory_for_retention(repo: Any, memory_id: str) -> Any:
    fetch = getattr(repo, "fetch_by_external_id", None)
    if not callable(fetch):
        return None
    try:
        return fetch(memory_id)
    except Exception:
        return None


def _protected_write_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "mode": "protected",
        "writes_applied": 0,
        "protected": True,
        "protected_reason": "protected_autobiographical_history",
    }


def _int_value(value: Any, default: int = 5) -> int:
    try:
        return min(max(int(value), 1), 10)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float = 0.9) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _record_intake_audit_if_available(
    repo: Any,
    *,
    source_hash: str,
    origin: str,
    action: str,
    status: str,
    metadata: dict[str, Any],
) -> None:
    record = getattr(repo, "record_intake_audit", None)
    if callable(record):
        record(source_hash=source_hash, origin=origin, action=action, status=status, metadata=metadata)


def _proposal_from_direct_write(
    *,
    action: str,
    content: str,
    reason: str,
    arguments: dict[str, Any],
    existing_id: str = "",
) -> IngestionProposal:
    return IngestionProposal(
        action=action,
        content=content,
        domains=_string_list(arguments.get("domains")),
        memory_type=str(arguments.get("memory_type") or "note").strip(),
        signal_strength=_int_value(arguments.get("signal_strength"), default=5),
        current_status=str(arguments.get("current_status") or "active").strip() or "active",
        memory_tier=str(arguments.get("memory_tier") or "active").strip() or "active",
        confidence=_float_value(arguments.get("confidence"), default=0.9),
        reason=reason,
        existing_id=existing_id,
    )


def _write_result(
    *,
    repo: Any,
    tool: str,
    origin: str,
    source_hash: str,
    safe_proposal: dict[str, Any],
    proposal: IngestionProposal,
    apply: bool,
) -> dict[str, Any]:
    result = apply_intake_proposals(
        repo,
        proposals=[proposal],
        source_hash=source_hash,
        origin=origin,
        apply=apply,
    )
    result.update({"ok": True, "tool": tool, "proposal": safe_proposal})
    return result

def _handle_mcp_method(service: Any, profile: McpProfile, method: str, params: Any) -> dict[str, Any]:
    if method == "initialize":
        requested_version = ""
        if isinstance(params, dict):
            requested_version = str(params.get("protocolVersion") or "")
        return {
            "protocolVersion": requested_version or SUPPORTED_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": f"kontext-v2-{profile.name}", "version": "0.1.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": list_tools(
                write_enabled=profile.can_write,
                dry_run_write_enabled=profile.can_dry_run_write,
                category_write_enabled=bool(
                    profile.can_write and profile.can_write_categories and getattr(service, "category_write_enabled", False)
                ),
            )
        }
    if method == "tools/call":
        name, arguments = _parse_call_params(params)
        return _call_tool(service, profile, name, arguments)
    raise ValueError(f"unsupported method: {method}")


def _call_tool(service: Any, profile: McpProfile, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "search":
        args = normalize_search_args(arguments)
        started = time.perf_counter()
        filters = shadow_search_filters(
            query=args.query,
            top_k=args.top_k,
            domains=args.domains,
            memory_types=args.memory_types,
            memory_tiers=args.memory_tiers,
            current_statuses=args.current_statuses,
        )
        with service.repo() as repo:
            rows = search_memories(
                repo,
                query=args.query,
                top_k=args.top_k,
                domains=args.domains,
                memory_types=args.memory_types,
                memory_tiers=args.memory_tiers,
                current_statuses=args.current_statuses,
                namespace=str(arguments.get("namespace") or "live"),
            )
            filters["result_metrics"] = shadow_result_metrics(rows)
            latency_ms = (time.perf_counter() - started) * 1000
            repo.record_retrieval_query(
                query=args.query,
                origin="mcp",
                service="kontext",
                profile=profile.name,
                filters=filters,
                result_external_ids=[str(row.get("external_mem0_id") or row.get("id") or "") for row in rows],
                latency_ms=latency_ms,
            )
        return _text_result({"results": [_compact_search_row(row) for row in rows], "count": len(rows)})
    if name == "fetch":
        memory_id = str(arguments.get("id") or "").strip()
        if not memory_id:
            raise ValueError("id is required")
        with service.repo() as repo:
            payload = fetch_tool(repo, memory_id)
        return _text_result(payload)
    if name == "ingestion_status":
        recent_limit = int(arguments.get("recent_limit") or 10)
        with service.repo() as repo:
            payload = ingestion_status_tool(repo, recent_limit=recent_limit)
        payload["mcp"] = {"profile": profile.name, "write_enabled": profile.can_write, "dry_run_write_enabled": profile.can_dry_run_write}
        return _text_result(payload)
    if name == "state_ingestion_status" and state_ingestion_enabled():
        with service.repo() as repo:
            payload = state_ingestion_status(repo, namespace=str(arguments.get("namespace") or "live"))
        return _text_result(payload)
    if name == "stage_state_events" and state_ingestion_enabled() and profile.can_write:
        source_hash = str(arguments.get("source_hash") or "").strip()
        if not source_hash:
            raise ValueError("source_hash is required")
        state_events = arguments.get("state_events")
        if not isinstance(state_events, list):
            raise ValueError("state_events must be a list")
        origin = f"kontext-v2-{profile.name}"
        with service.repo() as repo:
            payload = stage_state_event_proposals(
                repo,
                {"state_events": state_events},
                namespace=str(arguments.get("namespace") or "live"),
                source_hash=source_hash,
                extractor_version=str(arguments.get("extractor_version") or ""),
            )
            _record_intake_audit_if_available(
                repo,
                source_hash=source_hash,
                origin=origin,
                action="stage_state_events",
                status=payload["mode"],
                metadata={
                    "tool": "stage_state_events",
                    "proposal_count": len(state_events),
                    "writes_applied": payload["writes_applied"],
                },
            )
        return _text_result({**payload, "tool": "stage_state_events", "ok": True})
    if name == "accept_state_event_candidate" and state_ingestion_enabled() and profile.can_write:
        candidate_id = str(arguments.get("id") or "").strip()
        if not candidate_id:
            raise ValueError("id is required")
        edges = arguments.get("edges")
        if edges is not None and not isinstance(edges, list):
            raise ValueError("edges must be a list")
        origin = f"kontext-v2-{profile.name}"
        with service.repo() as repo:
            payload = accept_reviewed_state_candidate(
                repo,
                candidate_id,
                namespace=str(arguments.get("namespace") or "live"),
                edges=edges,
                rebuild_projection=_bool_value(arguments.get("rebuild_projection"), default=True),
            )
            _record_intake_audit_if_available(
                repo,
                source_hash=payload["event_id"],
                origin=origin,
                action="accept_state_event_candidate",
                status=payload["mode"],
                metadata={
                    "tool": "accept_state_event_candidate",
                    "edge_count": len(payload.get("edges") or []),
                    "writes_applied": payload["writes_applied"],
                    "projection_rebuilt": payload["projection_rebuilt"],
                },
            )
        return _text_result({**payload, "tool": "accept_state_event_candidate", "ok": True})
    if name == "project_search":
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        with service.repo() as repo:
            return _text_result(
                repo.project_search(
                    query=query,
                    limit=int(arguments.get("limit") or 10),
                    project=str(arguments.get("project") or ""),
                    project_root=str(arguments.get("project_root") or arguments.get("cwd") or ""),
                )
            )
    if name == "project_timeline":
        with service.repo() as repo:
            return _text_result(
                repo.project_timeline(
                    anchor_id=str(arguments.get("anchor_id") or ""),
                    query=str(arguments.get("query") or ""),
                    before=int(arguments.get("before") or 3),
                    after=int(arguments.get("after") or 3),
                    project=str(arguments.get("project") or ""),
                    project_root=str(arguments.get("project_root") or arguments.get("cwd") or ""),
                )
            )
    if name == "project_fetch":
        observation_id = str(arguments.get("id") or "").strip()
        if not observation_id:
            raise ValueError("id is required")
        with service.repo() as repo:
            return _text_result(repo.project_fetch(observation_id))
    if name == "project_file_context":
        file_path = str(arguments.get("file_path") or "").strip()
        if not file_path:
            raise ValueError("file_path is required")
        with service.repo() as repo:
            return _text_result(
                repo.project_file_context(
                    file_path=file_path,
                    limit=int(arguments.get("limit") or 10),
                    project=str(arguments.get("project") or ""),
                    project_root=str(arguments.get("project_root") or arguments.get("cwd") or ""),
                )
            )
    if name == "save" and profile.can_dry_run_write:
        origin = f"kontext-v2-{profile.name}"
        content = str(arguments.get("content") or "")
        if not content.strip():
            raise ValueError("content is required")
        payload = save_memory_dry_run(
            content=content,
            origin=origin,
            domains=_string_list(arguments.get("domains")),
            memory_type=str(arguments.get("memory_type") or "note"),
            current_status=str(arguments.get("current_status") or "active"),
            memory_tier=str(arguments.get("memory_tier") or "active"),
            signal_strength=arguments.get("signal_strength"),
        )
        proposal = _proposal_from_direct_write(
            action="save",
            content=content,
            reason=str(arguments.get("reason") or "direct save"),
            arguments=arguments,
            existing_id=str(arguments.get("id") or "").strip(),
        )
        with service.repo() as repo:
            result = _write_result(
                repo=repo,
                tool="save",
                origin=origin,
                source_hash=payload["source_hash"],
                safe_proposal=payload["proposal"],
                proposal=proposal,
                apply=profile.can_write,
            )
            repo.record_intake_audit(
                source_hash=payload["source_hash"],
                origin=origin,
                action="save",
                status=result["mode"],
                metadata={"tool": "save", "proposal_count": 1, "writes_applied": result["writes_applied"]},
            )
        return _text_result(result)
    if name == "update" and profile.can_dry_run_write:
        origin = f"kontext-v2-{profile.name}"
        memory_id = str(arguments.get("id") or "").strip()
        content = str(arguments.get("content") or "")
        reason = str(arguments.get("reason") or "")
        if not memory_id:
            raise ValueError("id is required")
        if not content.strip():
            raise ValueError("content is required")
        if not reason.strip():
            raise ValueError("reason is required")
        payload = update_memory_dry_run(
            memory_id=memory_id,
            content=content,
            reason=reason,
            origin=origin,
            domains=_string_list(arguments.get("domains")),
            memory_type=str(arguments.get("memory_type") or ""),
            current_status=str(arguments.get("current_status") or "active"),
            memory_tier=str(arguments.get("memory_tier") or "active"),
            signal_strength=arguments.get("signal_strength"),
        )
        proposal = _proposal_from_direct_write(
            action="update",
            content=content,
            reason=reason,
            arguments=arguments,
            existing_id=memory_id,
        )
        with service.repo() as repo:
            result = _write_result(
                repo=repo,
                tool="update",
                origin=origin,
                source_hash=payload["source_hash"],
                safe_proposal=payload["proposal"],
                proposal=proposal,
                apply=profile.can_write,
            )
            repo.record_intake_audit(
                source_hash=payload["source_hash"],
                origin=origin,
                action="update",
                status=result["mode"],
                metadata={"tool": "update", "proposal_count": 1, "writes_applied": result["writes_applied"]},
            )
        return _text_result(result)
    if name == "delete" and profile.can_dry_run_write:
        origin = f"kontext-v2-{profile.name}"
        memory_id = str(arguments.get("id") or "").strip()
        reason = str(arguments.get("reason") or "")
        if not memory_id:
            raise ValueError("id is required")
        if not reason.strip():
            raise ValueError("reason is required")
        payload = delete_memory_dry_run(memory_id=memory_id, reason=reason, origin=origin)
        mode = "apply" if profile.can_write else "dry_run"
        writes_applied = 0
        with service.repo() as repo:
            target = _fetch_memory_for_retention(repo, memory_id)
            if cleanup_flag_blocked_for_protected_history(payload["flag"]["flag_type"], target):
                repo.record_intake_audit(
                    source_hash=payload["source_hash"],
                    origin=origin,
                    action="delete",
                    status="protected",
                    metadata={"tool": "delete", "proposal_count": 1, "writes_applied": 0, "protected": True},
                )
                return _text_result(_protected_write_payload(payload))
            if profile.can_write:
                repo.record_memory_flag(
                    external_mem0_id=payload["flag"]["id"],
                    flag_type=payload["flag"]["flag_type"],
                    reason_hash=payload["flag"]["reason_hash"],
                    confidence=payload["flag"]["confidence"],
                    origin=origin,
                    status="pending",
                    metadata={"tool": "delete", "source_hash": payload["source_hash"]},
                )
                writes_applied = 1
            repo.record_intake_audit(
                source_hash=payload["source_hash"],
                origin=origin,
                action="delete",
                status=mode,
                metadata={"tool": "delete", "proposal_count": 1, "writes_applied": writes_applied},
            )
        payload = {**payload, "mode": mode, "writes_applied": writes_applied}
        return _text_result(payload)
    if name == "list_categories":
        with service.repo() as repo:
            categories = [_compact_category_row(row) for row in repo.list_categories()]
        return _text_result({"categories": categories, "count": len(categories)})
    if name == "list_category_memories":
        slug = str(arguments.get("slug") or "").strip()
        if not slug:
            raise ValueError("slug is required")
        try:
            limit = min(max(int(arguments.get("limit") or 20), 1), 50)
        except (TypeError, ValueError):
            limit = 20
        with service.repo() as repo:
            memories = [_compact_category_memory_row(row) for row in repo.list_category_memories(slug, limit=limit)]
        return _text_result({"slug": slug, "memories": memories, "count": len(memories)})
    if name == "hook_heartbeat":
        payload = dict(arguments)
        payload["origin"] = profile.name
        with service.repo() as repo:
            result = repo.record_hook_heartbeat(payload)
        return _text_result({"ok": True, "origin": profile.name, "hook_type": result.get("hook_type")})
    if name == "record_project_observation":
        payload = dict(arguments)
        payload["origin"] = profile.name
        with service.repo() as repo:
            result = repo.record_project_observation(payload)
        return _text_result({"ok": True, "origin": profile.name, "appended": bool(result.get("appended")), "id": result.get("id")})
    can_write_categories = bool(
        profile.can_write and profile.can_write_categories and getattr(service, "category_write_enabled", False)
    )
    if name == "upsert_category" and can_write_categories:
        slug = str(arguments.get("slug") or arguments.get("name") or "").strip()
        name_value = str(arguments.get("name") or slug).strip()
        if not slug or not name_value:
            raise ValueError("slug or name is required")
        with service.repo() as repo:
            category = repo.upsert_category(
                slug=slug,
                name=name_value,
                description=str(arguments.get("description") or ""),
            )
        return _text_result({"category": category})
    if name == "assign_category" and can_write_categories:
        slug = str(arguments.get("slug") or "").strip()
        memory_id = str(arguments.get("memory_id") or arguments.get("external_mem0_id") or arguments.get("id") or "").strip()
        if not slug:
            raise ValueError("slug is required")
        if not memory_id:
            raise ValueError("memory_id is required")
        with service.repo() as repo:
            assigned = repo.assign_memory_category(
                external_mem0_id=memory_id,
                category_slug=slug,
                source=str(arguments.get("source") or f"mcp_{profile.name}"),
            )
        if not assigned:
            raise ValueError("memory or category not found")
        return _text_result({"assigned": True, "slug": slug, "memory_id": memory_id})
    if name == "unassign_category" and can_write_categories:
        slug = str(arguments.get("slug") or "").strip()
        memory_id = str(arguments.get("memory_id") or arguments.get("external_mem0_id") or arguments.get("id") or "").strip()
        if not slug:
            raise ValueError("slug is required")
        if not memory_id:
            raise ValueError("memory_id is required")
        with service.repo() as repo:
            removed = repo.unassign_memory_category(external_mem0_id=memory_id, category_slug=slug)
        return _text_result({"removed": removed, "slug": slug, "memory_id": memory_id})
    if name == "delete_category" and can_write_categories:
        slug = str(arguments.get("slug") or "").strip()
        if not slug:
            raise ValueError("slug is required")
        with service.repo() as repo:
            deleted = repo.delete_category(slug)
        return _text_result({"deleted": deleted, "slug": slug})
    if name == "extract_memories" and profile.can_dry_run_write:
        origin = f"kontext-v2-{profile.name}"
        messages = _message_list(arguments.get("messages"), "messages")
        context_messages = _message_list(arguments.get("context_messages"), "context_messages")
        payload = extract_memories_dry_run(messages=messages, context_messages=context_messages, origin=origin)
        with service.repo() as repo:
            repo.record_intake_audit(
                source_hash=payload["source_hash"],
                origin=origin,
                action="extract_memories",
                status="dry_run",
                metadata={"proposal_count": len(payload.get("proposals") or []), "tool": "extract_memories"},
            )
        return _text_result(payload)
    if name == "ingest_exchange" and profile.can_dry_run_write:
        origin = f"kontext-v2-{profile.name}"
        messages = _message_list(arguments.get("messages"), "messages")
        context_messages = _message_list(arguments.get("context_messages"), "context_messages")
        payload = ingest_exchange_dry_run(messages=messages, context_messages=context_messages, origin=origin)
        with service.repo() as repo:
            repo.record_intake_audit(
                source_hash=payload["source_hash"],
                origin=origin,
                action="ingest_exchange",
                status="dry_run",
                metadata={"proposal_count": len(payload.get("proposals") or []), "tool": "ingest_exchange"},
            )
        return _text_result(payload)
    if name == "submit_memory_override" and profile.can_dry_run_write:
        origin = f"kontext-v2-{profile.name}"
        content = str(arguments.get("content") or "")
        reason = str(arguments.get("reason") or "")
        if not content.strip():
            raise ValueError("content is required")
        if not reason.strip():
            raise ValueError("reason is required")
        payload = submit_memory_override_dry_run(
            content=content,
            reason=reason,
            origin=origin,
            domains=_string_list(arguments.get("domains")),
            memory_type=str(arguments.get("memory_type") or ""),
            current_status=str(arguments.get("current_status") or "active"),
            memory_tier=str(arguments.get("memory_tier") or "active"),
            signal_strength=arguments.get("signal_strength"),
        )
        proposal = _proposal_from_direct_write(
            action="save",
            content=content,
            reason=reason,
            arguments=arguments,
            existing_id=str(arguments.get("id") or "").strip(),
        )
        with service.repo() as repo:
            result = _write_result(
                repo=repo,
                tool="submit_memory_override",
                origin=origin,
                source_hash=payload["proposal"]["content_hash"],
                safe_proposal=payload["proposal"],
                proposal=proposal,
                apply=profile.can_write,
            )
            repo.record_intake_audit(
                source_hash=payload["proposal"]["content_hash"],
                origin=origin,
                action="submit_memory_override",
                status=result["mode"],
                metadata={"proposal_count": 1, "tool": "submit_memory_override", "writes_applied": result["writes_applied"]},
            )
        return _text_result(result)
    if name == "flag_memory" and profile.can_dry_run_write:
        origin = f"kontext-v2-{profile.name}"
        memory_id = str(arguments.get("id") or "").strip()
        flag_type = str(arguments.get("flag_type") or "").strip()
        reason = str(arguments.get("reason") or "")
        if not memory_id:
            raise ValueError("id is required")
        if not flag_type:
            raise ValueError("flag_type is required")
        if not reason.strip():
            raise ValueError("reason is required")
        payload = flag_memory_dry_run(
            memory_id=memory_id,
            flag_type=flag_type,
            reason=reason,
            confidence=arguments.get("confidence", 0.8),
            origin=origin,
        )
        mode = "apply" if profile.can_write else "dry_run"
        writes_applied = 0
        with service.repo() as repo:
            target = _fetch_memory_for_retention(repo, memory_id)
            if cleanup_flag_blocked_for_protected_history(payload["flag"]["flag_type"], target):
                return _text_result(_protected_write_payload(payload))
            if profile.can_write:
                repo.record_memory_flag(
                    external_mem0_id=payload["flag"]["id"],
                    flag_type=payload["flag"]["flag_type"],
                    reason_hash=payload["flag"]["reason_hash"],
                    confidence=payload["flag"]["confidence"],
                    origin=origin,
                    status="pending",
                    metadata={"tool": "flag_memory"},
                )
                writes_applied = 1
        payload = {**payload, "mode": mode, "writes_applied": writes_applied}
        return _text_result(payload)
    raise ValueError(f"unsupported tool: {name}")
