from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


REQUIRED_TOOLS = {
    "search",
    "fetch",
    "ingestion_status",
    "project_search",
    "project_timeline",
    "project_fetch",
    "project_file_context",
    "list_categories",
    "list_category_memories",
    "save",
    "update",
    "delete",
    "extract_memories",
    "ingest_exchange",
    "submit_memory_override",
    "flag_memory",
}

PAYLOAD_BUDGET_BYTES = {
    "search": 12_000,
    "fetch": 20_000,
    "project_search": 12_000,
    "project_fetch": 20_000,
    "project_timeline": 20_000,
    "project_file_context": 12_000,
    "list_categories": 12_000,
    "list_category_memories": 12_000,
}
DEFAULT_JSONRPC_TIMEOUT_SECONDS = int(os.environ.get("KONTEXT_MCP_CANARY_TIMEOUT_SECONDS", "45"))


class McpSmokeError(RuntimeError):
    pass


def _jsonrpc(
    url: str,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
    timeout_seconds: int = DEFAULT_JSONRPC_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], float]:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise McpSmokeError(f"HTTP {exc.code} from MCP endpoint") from exc
    except urllib.error.URLError as exc:
        raise McpSmokeError(f"transport error: {exc.reason}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    if "error" in payload:
        message = payload.get("error", {}).get("message") or payload["error"]
        raise McpSmokeError(f"MCP {method} failed: {message}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise McpSmokeError(f"MCP {method} returned no object result")
    return result, elapsed_ms


def _tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise McpSmokeError("tool response did not include content")
    text = content[0].get("text") if isinstance(content[0], dict) else None
    if not isinstance(text, str):
        raise McpSmokeError("tool response content was not text")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpSmokeError("tool response text was not JSON") from exc
    if not isinstance(payload, dict):
        raise McpSmokeError("tool response JSON was not an object")
    return payload


def _call_tool(
    url: str,
    request_id: int,
    name: str,
    arguments: dict[str, Any],
    timeout_seconds: int = DEFAULT_JSONRPC_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], float]:
    result, elapsed = _jsonrpc(url, request_id, "tools/call", {"name": name, "arguments": arguments}, timeout_seconds)
    return _tool_payload(result), elapsed


def _payload_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _record_payload_size(sizes: dict[str, int], name: str, payload: dict[str, Any]) -> None:
    size = _payload_size(payload)
    sizes[name] = size
    budget = PAYLOAD_BUDGET_BYTES.get(name)
    if budget is not None and size > budget:
        raise McpSmokeError(f"{name} payload too large: {size} bytes > {budget} bytes")


def _rows(payload: dict[str, Any]) -> list[Any]:
    for key in ("results", "rows", "memories", "categories"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def run_smoke(url: str, marker: str) -> dict[str, Any]:
    latencies: dict[str, float] = {}
    payload_bytes: dict[str, int] = {}
    init, latencies["initialize_ms"] = _jsonrpc(
        url,
        1,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "kontext-mcp-canary-smoke", "version": "2026-05-20"},
        },
    )
    listed, latencies["tools_list_ms"] = _jsonrpc(url, 2, "tools/list")
    tools = listed.get("tools") if isinstance(listed.get("tools"), list) else []
    tool_names = sorted({str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")})
    missing_tools = sorted(REQUIRED_TOOLS.difference(tool_names))

    status, latencies["ingestion_status_ms"] = _call_tool(url, 3, "ingestion_status", {"recent_limit": 3})
    search, latencies["search_ms"] = _call_tool(
        url,
        4,
        "search",
        {"query": "Kontext V2 Mem0 benchmark status canary", "top_k": 3},
    )
    _record_payload_size(payload_bytes, "search", search)
    search_results = _rows(search)
    fetched = False
    if search_results:
        first_id = search_results[0].get("id") if isinstance(search_results[0], dict) else None
        if first_id:
            fetched_payload, latencies["fetch_ms"] = _call_tool(url, 5, "fetch", {"id": str(first_id)})
            _record_payload_size(payload_bytes, "fetch", fetched_payload)
            fetched = True

    project_search, latencies["project_search_ms"] = _call_tool(
        url,
        13,
        "project_search",
        {"query": "Kontext V2 Mem0 benchmark canary", "limit": 3},
    )
    _record_payload_size(payload_bytes, "project_search", project_search)
    project_rows = _rows(project_search)
    project_fetch_checked = False
    project_timeline_checked = False
    if project_rows:
        first_project_id = project_rows[0].get("id") if isinstance(project_rows[0], dict) else None
        if first_project_id:
            project_fetch, latencies["project_fetch_ms"] = _call_tool(url, 14, "project_fetch", {"id": str(first_project_id)})
            project_timeline, latencies["project_timeline_ms"] = _call_tool(url, 15, "project_timeline", {"anchor_id": str(first_project_id)})
            _record_payload_size(payload_bytes, "project_fetch", project_fetch)
            _record_payload_size(payload_bytes, "project_timeline", project_timeline)
            project_fetch_checked = True
            project_timeline_checked = True
    project_file, latencies["project_file_context_ms"] = _call_tool(
        url,
        16,
        "project_file_context",
        {"file_path": "tools/kontext-v2/kontext_v2/mcp_bridge.py", "limit": 3},
    )
    _record_payload_size(payload_bytes, "project_file_context", project_file)
    categories, latencies["list_categories_ms"] = _call_tool(url, 17, "list_categories", {})
    _record_payload_size(payload_bytes, "list_categories", categories)
    category_rows = _rows(categories)
    category_memories_checked = False
    if category_rows:
        first_slug = category_rows[0].get("slug") if isinstance(category_rows[0], dict) else None
        if first_slug:
            category_memories, latencies["list_category_memories_ms"] = _call_tool(
                url,
                18,
                "list_category_memories",
                {"slug": str(first_slug)},
            )
            _record_payload_size(payload_bytes, "list_category_memories", category_memories)
            category_memories_checked = True

    safe_content = f"Kontext V2 MCP canary dry-run marker {marker}. This is not a durable memory."
    save, latencies["save_ms"] = _call_tool(
        url,
        6,
        "save",
        {
            "content": safe_content,
            "domains": ["ai", "systems"],
            "memory_type": "project_state",
            "current_status": "active",
            "memory_tier": "active",
        },
    )
    update, latencies["update_ms"] = _call_tool(
        url,
        7,
        "update",
        {
            "id": f"canary-{marker}",
            "content": safe_content,
            "reason": "dry-run canary update check",
        },
    )
    delete, latencies["delete_ms"] = _call_tool(
        url,
        8,
        "delete",
        {"id": f"canary-{marker}", "reason": "dry-run canary delete-candidate check"},
    )
    flag, latencies["flag_memory_ms"] = _call_tool(
        url,
        9,
        "flag_memory",
        {"id": f"canary-{marker}", "flag_type": "stale_candidate", "reason": "dry-run canary flag check", "confidence": 0.4},
    )
    extract, latencies["extract_memories_ms"] = _call_tool(
        url,
        10,
        "extract_memories",
        {"messages": [{"role": "user", "content": safe_content}]},
    )
    ingest, latencies["ingest_exchange_ms"] = _call_tool(
        url,
        11,
        "ingest_exchange",
        {"messages": [{"role": "user", "content": safe_content}, {"role": "assistant", "content": "Canary acknowledged."}]},
    )
    override, latencies["submit_memory_override_ms"] = _call_tool(
        url,
        12,
        "submit_memory_override",
        {"content": safe_content, "reason": "dry-run canary override check", "domains": ["ai", "systems"]},
    )

    write_payloads = {
        "save": save,
        "update": update,
        "delete": delete,
        "flag_memory": flag,
        "extract_memories": extract,
        "ingest_exchange": ingest,
        "submit_memory_override": override,
    }
    write_modes = {name: payload.get("mode") for name, payload in write_payloads.items()}
    writes_applied = {name: payload.get("writes_applied") for name, payload in write_payloads.items()}
    unexpected_writes = {name: value for name, value in writes_applied.items() if value not in (0, None)}
    if missing_tools:
        raise McpSmokeError(f"missing required tools: {', '.join(missing_tools)}")
    if unexpected_writes:
        raise McpSmokeError(f"dry-run smoke unexpectedly applied writes: {unexpected_writes}")

    return {
        "ok": True,
        "initialized": bool(init.get("protocolVersion")),
        "tool_count": len(tool_names),
        "required_tools_present": sorted(REQUIRED_TOOLS),
        "search_result_count": len(search_results),
        "fetch_checked": fetched,
        "project_result_count": len(project_rows),
        "project_fetch_checked": project_fetch_checked,
        "project_timeline_checked": project_timeline_checked,
        "category_count": len(category_rows),
        "category_memories_checked": category_memories_checked,
        "status_ok": bool(status.get("ok")),
        "write_modes": write_modes,
        "writes_applied": writes_applied,
        "delete_flag_type": (delete.get("flag") or {}).get("flag_type"),
        "latency_ms": {key: round(value, 2) for key, value in sorted(latencies.items())},
        "payload_bytes": dict(sorted(payload_bytes.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a sanitized Kontext MCP dry-run canary smoke.")
    parser.add_argument("--url-env", default="KONTEXT_MCP_CANARY_URL", help="Environment variable containing the full tokenized MCP URL.")
    parser.add_argument("--marker", default=str(int(time.time())), help="Non-secret marker for this smoke run.")
    args = parser.parse_args(argv)
    url = os.environ.get(args.url_env, "").strip()
    if not url:
        print(json.dumps({"ok": False, "error": f"missing {args.url_env}"}, sort_keys=True))
        return 2
    try:
        result = run_smoke(url, args.marker)
    except McpSmokeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
