from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from kontext_v2.importer import mem0_source_hash

STABLE_FRESHNESS_METADATA_KEYS = ("domains", "memory_type", "current_status", "memory_tier", "signal_strength")


def _mem0_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("external_mem0_id") or "")


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _fallback_value(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def _stable_freshness_metadata(metadata: dict[str, Any], source: Any) -> dict[str, Any]:
    stable: dict[str, Any] = {}
    raw_domains = metadata.get("domains") or _fallback_value(source, "domains") or []
    domains = sorted({str(value).strip() for value in raw_domains if str(value).strip()})
    if domains:
        stable["domains"] = domains

    for key in STABLE_FRESHNESS_METADATA_KEYS:
        if key in {"domains", "signal_strength"}:
            continue
        value = metadata.get(key) or _fallback_value(source, key)
        if not value and key in {"current_status", "memory_tier"}:
            value = "active"
        if value:
            stable[key] = str(value)

    signal_strength = metadata.get("signal_strength")
    if signal_strength is None:
        signal_strength = _fallback_value(source, "signal_strength")
    if signal_strength is not None:
        try:
            numeric = float(signal_strength)
        except (TypeError, ValueError):
            stable["signal_strength"] = str(signal_strength)
        else:
            if math.isfinite(numeric):
                stable["signal_strength"] = numeric
    return stable


def _domain_set(row: dict[str, Any]) -> set[str]:
    return {str(value) for value in _metadata(row).get("domains") or []}


def _memory_type(row: dict[str, Any]) -> str:
    metadata = _metadata(row)
    return str(metadata.get("memory_type") or row.get("memory_type") or "")


def _memory_text(row: dict[str, Any]) -> str:
    return str(row.get("memory") or row.get("text") or "").strip()


def _freshness_hash(text: str, metadata: dict[str, Any]) -> str:
    payload = {"text": str(text or "").strip(), "metadata": metadata if isinstance(metadata, dict) else {}}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mem0_freshness_hash(row: dict[str, Any]) -> str:
    return _freshness_hash(_memory_text(row), _stable_freshness_metadata(_metadata(row), row))


def _kontext_freshness_hash(memory: Any) -> str:
    metadata = getattr(memory, "metadata", {}) or {}
    return _freshness_hash(getattr(memory, "text", ""), _stable_freshness_metadata(metadata, memory))


def compare_search_results(
    query_hash: str,
    mem0_rows: list[dict[str, Any]],
    kontext_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    mem0_ids = [_mem0_id(row) for row in mem0_rows if _mem0_id(row)]
    kontext_ids = [_mem0_id(row) for row in kontext_rows if _mem0_id(row)]
    kontext_id_set = set(kontext_ids)
    shared = [memory_id for memory_id in mem0_ids if memory_id in kontext_id_set]

    return {
        "query_hash": query_hash,
        "mem0_count": len(mem0_rows),
        "kontext_count": len(kontext_rows),
        "shared_external_ids": shared,
        "mem0_domains": sorted(set().union(*[_domain_set(row) for row in mem0_rows]) if mem0_rows else set()),
        "kontext_domains": sorted(
            set().union(*[_domain_set(row) for row in kontext_rows]) if kontext_rows else set()
        ),
        "mem0_memory_types": sorted(
            {_memory_type(row) for row in mem0_rows if _memory_type(row)}
        ),
        "kontext_memory_types": sorted(
            {_memory_type(row) for row in kontext_rows if _memory_type(row)}
        ),
    }

def _is_found_mem0(row: dict[str, Any]) -> bool:
    if not row:
        return False
    return row.get("ok") is not False and not row.get("error")


def compare_exact_id_freshness(repo: Any, fetch_mem0: Any, memory_ids: list[str]) -> dict[str, Any]:
    results = []
    fresh_count = 0
    for memory_id in memory_ids:
        external_id = str(memory_id)
        mem0_row = fetch_mem0(external_id)
        mem0_found = isinstance(mem0_row, dict) and _is_found_mem0(mem0_row)
        kontext_memory = repo.fetch_by_external_id(external_id)
        kontext_found = kontext_memory is not None
        mem0_source = mem0_source_hash(mem0_row) if mem0_found else ""
        kontext_source = kontext_memory.source_hash if kontext_memory is not None else ""
        source_hash_match = bool(mem0_source and kontext_source and mem0_source == kontext_source)
        mem0_freshness = _mem0_freshness_hash(mem0_row) if mem0_found else ""
        kontext_freshness = _kontext_freshness_hash(kontext_memory) if kontext_memory is not None else ""
        canonical_hash_match = bool(mem0_freshness and kontext_freshness and mem0_freshness == kontext_freshness)
        fresh = bool(mem0_found and kontext_found and canonical_hash_match)
        if fresh:
            fresh_count += 1
        results.append(
            {
                "id": external_id,
                "mem0_found": mem0_found,
                "kontext_found": kontext_found,
                "fresh": fresh,
                "source_hash_match": source_hash_match,
                "canonical_hash_match": canonical_hash_match,
                "memory_type_match": _memory_type(mem0_row) == kontext_memory.memory_type if mem0_found and kontext_memory else None,
                "domain_overlap": bool(_domain_set(mem0_row) & set(kontext_memory.metadata.get("domains") or [])) if mem0_found and kontext_memory else None,
            }
        )
    return {"checked": len(memory_ids), "fresh": fresh_count, "stale": len(memory_ids) - fresh_count, "results": results}
