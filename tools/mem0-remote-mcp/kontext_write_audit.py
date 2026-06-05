from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from kontext_shadow_compare import append_row, compact_text, stable_hash, utc_now_iso

SAFE_METADATA_KEYS = ("domains", "memory_type", "signal_strength", "current_status", "memory_tier")
READ_AFTER_WRITE_TOOLS = {"save", "update", "submit_memory_override"}


@dataclass(frozen=True)
class KontextWriteAuditConfig:
    enabled: bool
    mcp_url: str
    token: str
    log_path: str
    timeout: float = 2.0

    def ready(self) -> bool:
        return bool(self.enabled and self.mcp_url and self.log_path and (self.token or "{token}" not in self.mcp_url))


def endpoint_url(config: KontextWriteAuditConfig) -> str:
    if "{token}" in config.mcp_url:
        return config.mcp_url.format(token=config.token)
    if not config.token:
        return config.mcp_url
    return config.mcp_url.rstrip("/") + "/" + config.token


def parse_text_payload(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result") if isinstance(response, dict) else None
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list) or not content:
        return {}
    text = content[0].get("text") if isinstance(content[0], dict) else ""
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def kontext_rpc_tool(
    config: KontextWriteAuditConfig,
    tool: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], float, str]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint_url(config),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    started = perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=max(config.timeout, 0.1)) as response:
            raw_payload = json.loads(response.read().decode("utf-8"))
        latency_ms = (perf_counter() - started) * 1000
        if isinstance(raw_payload, dict) and raw_payload.get("error"):
            error = raw_payload.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            return {}, latency_ms, compact_text(message, 120)
        return parse_text_payload(raw_payload if isinstance(raw_payload, dict) else {}), latency_ms, ""
    except urllib.error.HTTPError as exc:
        return {}, (perf_counter() - started) * 1000, f"http_{exc.code}"
    except Exception as exc:  # noqa: BLE001
        return {}, (perf_counter() - started) * 1000, type(exc).__name__


def safe_metadata(arguments: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in SAFE_METADATA_KEYS:
        value = arguments.get(key)
        if value is None or value == "":
            continue
        if key == "domains":
            domains = [compact_text(item, 80) for item in value if compact_text(item, 80)] if isinstance(value, list) else []
            if domains:
                clean[key] = domains
        else:
            clean[key] = value
    return clean


def extract_mem0_id(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    return compact_text(result.get("id") or response.get("id") or result.get("memory_id"), 120)


def extract_kontext_ids(response: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    if not isinstance(response, dict):
        return ids
    results = response.get("results")
    if isinstance(results, list):
        for row in results:
            if isinstance(row, dict):
                memory_id = compact_text(row.get("id") or row.get("existing_id") or row.get("memory_id"), 120)
                if memory_id:
                    ids.append(memory_id)
    flag = response.get("flag") if isinstance(response.get("flag"), dict) else {}
    flag_id = compact_text(flag.get("id"), 120)
    if flag_id:
        ids.append(flag_id)
    direct_id = compact_text(response.get("id") or response.get("external_mem0_id"), 120)
    if direct_id:
        ids.append(direct_id)
    return list(dict.fromkeys(ids))


def build_write_audit_row(
    *,
    origin: str,
    tool: str,
    arguments: dict[str, Any],
    mem0_result: dict[str, Any],
    kontext_response: dict[str, Any],
    kontext_latency_ms: float,
    kontext_error: str,
    fetch_response: dict[str, Any] | None = None,
    fetch_error: str = "",
) -> dict[str, Any]:
    kontext_ids = extract_kontext_ids(kontext_response)
    checked = fetch_response is not None or bool(fetch_error)
    return {
        "ts": utc_now_iso(),
        "event": "kontext_write_audit",
        "origin": compact_text(origin, 80) or "unknown",
        "tool": compact_text(tool, 80),
        "content_hash": stable_hash(arguments.get("content")),
        "reason_hash": stable_hash(arguments.get("reason")),
        "metadata": safe_metadata(arguments),
        "mem0_id": extract_mem0_id(mem0_result),
        "kontext_ids": kontext_ids,
        "kontext_mode": compact_text(kontext_response.get("mode"), 40),
        "writes_applied": int(kontext_response.get("writes_applied") or 0),
        "kontext_latency_ms": round(float(kontext_latency_ms or 0), 3),
        "kontext_error": compact_text(kontext_error, 120),
        "read_after_write_checked": checked,
        "read_after_write_ok": bool(checked and not fetch_error and fetch_response),
        "fetch_error": compact_text(fetch_error, 120),
    }


def should_fetch_after_write(tool: str, response: dict[str, Any], error: str) -> bool:
    return (
        tool in READ_AFTER_WRITE_TOOLS
        and not error
        and response.get("mode") == "apply"
        and int(response.get("writes_applied") or 0) > 0
        and bool(extract_kontext_ids(response))
    )


def log_kontext_write_audit(
    config: KontextWriteAuditConfig,
    *,
    origin: str,
    tool: str,
    arguments: dict[str, Any],
    mem0_result: dict[str, Any],
    rpc_func: Callable[[KontextWriteAuditConfig, str, dict[str, Any]], tuple[dict[str, Any], float, str]] = kontext_rpc_tool,
) -> bool:
    if not config.ready():
        return False
    kontext_response, kontext_latency_ms, kontext_error = rpc_func(config, tool, arguments)
    fetch_response: dict[str, Any] | None = None
    fetch_error = ""
    if should_fetch_after_write(tool, kontext_response, kontext_error):
        fetch_response, _fetch_latency_ms, fetch_error = rpc_func(config, "fetch", {"id": extract_kontext_ids(kontext_response)[0]})
    row = build_write_audit_row(
        origin=origin,
        tool=tool,
        arguments=arguments,
        mem0_result=mem0_result,
        kontext_response=kontext_response,
        kontext_latency_ms=kontext_latency_ms,
        kontext_error=kontext_error,
        fetch_response=fetch_response,
        fetch_error=fetch_error,
    )
    return append_row(config.log_path, row)


def log_kontext_write_audit_async(
    config: KontextWriteAuditConfig,
    *,
    origin: str,
    tool: str,
    arguments: dict[str, Any],
    mem0_result: dict[str, Any],
) -> bool:
    if not config.ready():
        return False
    thread = threading.Thread(
        target=log_kontext_write_audit,
        kwargs={
            "config": config,
            "origin": origin,
            "tool": tool,
            "arguments": arguments,
            "mem0_result": mem0_result,
        },
        daemon=True,
    )
    thread.start()
    return True
