from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

SAFE_FILTER_KEYS = ("domains", "memory_tiers", "memory_types", "current_statuses")


@dataclass(frozen=True)
class KontextShadowConfig:
    enabled: bool
    mcp_url: str
    token: str
    log_path: str
    timeout: float = 2.0

    def ready(self) -> bool:
        if not (self.enabled and self.mcp_url and self.log_path):
            return False
        return bool(self.token or is_direct_search_url(self.mcp_url))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def compact_text(value: Any, max_len: int = 120) -> str:
    collapsed = " ".join(str(value or "").split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + "..."


def safe_id(row: dict[str, Any]) -> str:
    return compact_text(row.get("id") or row.get("external_mem0_id") or row.get("memory_id"), 120)


def safe_filters(filters: dict[str, Any] | None) -> dict[str, list[str]]:
    if not isinstance(filters, dict):
        return {}
    clean: dict[str, list[str]] = {}
    for key in SAFE_FILTER_KEYS:
        value = filters.get(key)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        normalized = [compact_text(item, 80) for item in values if compact_text(item, 80)]
        if normalized:
            clean[key] = normalized
    return clean


def top_ids(rows: list[dict[str, Any]], limit: int = 10) -> list[str]:
    ids: list[str] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        memory_id = safe_id(row)
        if memory_id:
            ids.append(memory_id)
    return ids


def parse_kontext_results(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result") if isinstance(response, dict) else None
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list) or not content:
        return []
    text = content[0].get("text") if isinstance(content[0], dict) else ""
    if not isinstance(text, str) or not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = payload.get("results") if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def is_direct_search_url(url: str) -> bool:
    return url.rstrip("/").endswith("/search")


def endpoint_url(config: KontextShadowConfig) -> str:
    if "{token}" in config.mcp_url:
        return config.mcp_url.format(token=config.token)
    if is_direct_search_url(config.mcp_url):
        return config.mcp_url
    return config.mcp_url.rstrip("/") + "/" + config.token


def kontext_rpc_search(
    config: KontextShadowConfig,
    query: str,
    top_k: int,
    filters: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], float, str]:
    arguments: dict[str, Any] = {"query": query, "top_k": top_k}
    for key, values in safe_filters(filters).items():
        arguments[key] = values
    url = endpoint_url(config)
    direct_search = is_direct_search_url(url)
    payload = arguments if direct_search else {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search", "arguments": arguments},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    started = perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=max(config.timeout, 0.1)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latency_ms = (perf_counter() - started) * 1000
        if isinstance(payload, dict) and payload.get("error"):
            message = payload.get("error")
            return [], latency_ms, compact_text(message.get("message") if isinstance(message, dict) else message, 120)
        if direct_search:
            rows = payload.get("results") if isinstance(payload, dict) else []
            return ([row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []), latency_ms, ""
        return parse_kontext_results(payload if isinstance(payload, dict) else {}), latency_ms, ""
    except urllib.error.HTTPError as exc:
        return [], (perf_counter() - started) * 1000, f"http_{exc.code}"
    except Exception as exc:  # noqa: BLE001
        return [], (perf_counter() - started) * 1000, type(exc).__name__


def build_comparison_row(
    *,
    origin: str,
    query: str,
    top_k: int,
    filters: dict[str, Any] | None,
    mem0_results: list[dict[str, Any]],
    mem0_latency_ms: float,
    kontext_results: list[dict[str, Any]],
    kontext_latency_ms: float,
    kontext_error: str,
) -> dict[str, Any]:
    mem0_ids = top_ids(mem0_results)
    kontext_ids = top_ids(kontext_results)
    shared = set(mem0_ids) & set(kontext_ids)
    return {
        "ts": utc_now_iso(),
        "event": "kontext_shadow_compare",
        "origin": compact_text(origin, 80) or "unknown",
        "query_hash": stable_hash(query),
        "top_k": max(min(int(top_k or 5), 20), 1),
        "filters": safe_filters(filters),
        "mem0_count": len(mem0_results or []),
        "kontext_count": len(kontext_results or []),
        "shared_any": bool(shared),
        "shared_count": len(shared),
        "shared_first": bool(mem0_ids and kontext_ids and mem0_ids[0] == kontext_ids[0]),
        "mem0_top_ids": mem0_ids,
        "kontext_top_ids": kontext_ids,
        "mem0_latency_ms": round(float(mem0_latency_ms or 0), 3),
        "kontext_latency_ms": round(float(kontext_latency_ms or 0), 3),
        "kontext_error": compact_text(kontext_error, 120),
    }


def append_row(path: str | Path, row: dict[str, Any]) -> bool:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError:
        return False


def log_kontext_shadow_search(
    config: KontextShadowConfig,
    *,
    origin: str,
    query: str,
    top_k: int,
    filters: dict[str, Any] | None,
    mem0_results: list[dict[str, Any]],
    mem0_latency_ms: float,
    rpc_func: Callable[[KontextShadowConfig, str, int, dict[str, Any] | None], tuple[list[dict[str, Any]], float, str]] = kontext_rpc_search,
) -> bool:
    if not config.ready():
        return False
    kontext_results, kontext_latency_ms, kontext_error = rpc_func(config, query, top_k, filters)
    row = build_comparison_row(
        origin=origin,
        query=query,
        top_k=top_k,
        filters=filters,
        mem0_results=mem0_results,
        mem0_latency_ms=mem0_latency_ms,
        kontext_results=kontext_results,
        kontext_latency_ms=kontext_latency_ms,
        kontext_error=kontext_error,
    )
    return append_row(config.log_path, row)


def log_kontext_shadow_search_async(
    config: KontextShadowConfig,
    *,
    origin: str,
    query: str,
    top_k: int,
    filters: dict[str, Any] | None,
    mem0_results: list[dict[str, Any]],
    mem0_latency_ms: float,
) -> bool:
    if not config.ready():
        return False
    thread = threading.Thread(
        target=log_kontext_shadow_search,
        kwargs={
            "config": config,
            "origin": origin,
            "query": query,
            "top_k": top_k,
            "filters": filters,
            "mem0_results": mem0_results,
            "mem0_latency_ms": mem0_latency_ms,
        },
        daemon=True,
    )
    thread.start()
    return True
