from __future__ import annotations

import json
from importlib import import_module
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from kontext_v2.importer import ImportReport, import_mem0_export
from kontext_v2.repository import KontextRepository

Transport = Callable[[urllib.request.Request, int], Any]


def _urlopen_transport(request: urllib.request.Request, timeout: int) -> Any:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def _normalize_mem0_database_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    memory = normalized.get("memory") or normalized.get("text") or normalized.get("content") or normalized.get("data") or ""
    if memory and not normalized.get("text"):
        normalized["text"] = memory

    metadata_value = normalized.get("metadata")
    metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
    normalized["metadata"] = {
        "domains": metadata.get("domains") or normalized.get("domains") or [],
        "memory_type": metadata.get("memory_type") or normalized.get("memory_type") or "",
        "signal_strength": metadata.get("signal_strength")
        if metadata.get("signal_strength") is not None
        else normalized.get("signal_strength"),
        "current_status": metadata.get("current_status") or normalized.get("current_status") or "",
        "memory_tier": metadata.get("memory_tier") or normalized.get("memory_tier") or "active",
    }
    return normalized


def postgres_memory_rows(lexical_database_url: str, user_id: str, limit: int = 10000) -> list[dict[str, Any]]:
    lexical_database_url = str(lexical_database_url or "").strip()
    user_id = str(user_id or "").strip() or "ionut"
    if not lexical_database_url:
        return []
    psycopg = import_module("psycopg")
    sql = """
        select id::text, payload
        from mem0_memories
        where payload->>'user_id' = %s
        order by payload->>'updated_at' desc nulls last
        limit %s
    """
    rows: list[tuple[Any, Any]] = []
    with psycopg.connect(lexical_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, [user_id, max(int(limit or 10000), 1)])
            rows = cursor.fetchall()
    memories: list[dict[str, Any]] = []
    for memory_id, payload in rows:
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            continue
        row = dict(payload)
        row["id"] = str(memory_id)
        memories.append(_normalize_mem0_database_row(row))
    return memories


class Mem0ApiClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        user_id: str = "ionut",
        timeout: int = 90,
        transport: Transport | None = None,
        lexical_database_url: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.user_id = user_id.strip() or "ionut"
        self.timeout = int(timeout or 90)
        self.transport = transport or _urlopen_transport
        self.lexical_database_url = str(lexical_database_url or "").strip()
        if not self.base_url:
            raise ValueError("base_url is required")
        if not self.api_key:
            raise ValueError("api_key is required")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
        )
        try:
            return self.transport(request, self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Mem0 HTTP {exc.code}: {detail}") from exc

    def get_all(self, page_size: int = 100, max_pages: int = 100) -> dict[str, Any]:
        page_size = min(max(int(page_size or 100), 1), 1000)
        max_pages = min(max(int(max_pages or 100), 1), 1000)
        limit = page_size * max_pages
        if self.lexical_database_url:
            rows = postgres_memory_rows(self.lexical_database_url, user_id=self.user_id, limit=limit)
        else:
            query = urllib.parse.urlencode({"user_id": self.user_id})
            payload = self._request("GET", f"/memories?{query}")
            rows = payload.get("results") if isinstance(payload, dict) else payload
        results = [row for row in (rows or []) if isinstance(row, dict)]
        return {"results": results, "count": len(results)}

    def search(self, query: str, top_k: int = 5) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/search",
            {
                "query": query,
                "filters": {"user_id": self.user_id},
                "top_k": min(max(int(top_k or 5), 1), 50),
            },
        )
        rows = payload.get("results") if isinstance(payload, dict) else payload
        return {"results": [row for row in (rows or []) if isinstance(row, dict)]}

    def fetch(self, memory_id: str) -> dict[str, Any]:
        memory_id = str(memory_id or "").strip()
        if not memory_id:
            raise ValueError("memory_id is required")
        quoted = urllib.parse.quote(memory_id, safe="")
        payload = self._request("GET", f"/memories/{quoted}")
        return payload if isinstance(payload, dict) else {}


def _rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


def _row_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("external_mem0_id") or "").strip()


def _has_memory_text(row: dict[str, Any]) -> bool:
    return bool(str(row.get("memory") or row.get("text") or "").strip())


def collect_eval_seed_rows(
    client: Any,
    cases: list[dict[str, Any]],
    top_k: int = 5,
    max_fetches: int = 100,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    fetches = 0
    for case in cases:
        query = str(case.get("query") or "").strip()
        if not query:
            continue
        for row in _rows(client.search(query, top_k=top_k)):
            memory_id = _row_id(row)
            if not memory_id or memory_id in by_id:
                continue
            if not _has_memory_text(row) and fetches < max_fetches:
                row = client.fetch(memory_id)
                fetches += 1
            by_id[memory_id] = row
    return list(by_id.values())


def import_live_snapshot(repo: KontextRepository, client: Any) -> tuple[ImportReport, int]:
    rows = _rows(client.get_all())
    report = import_mem0_export(repo, {"results": rows})
    return report, len(rows)


def import_eval_seed(
    repo: KontextRepository,
    client: Any,
    cases: list[dict[str, Any]],
    top_k: int = 5,
    max_fetches: int = 100,
) -> tuple[ImportReport, int]:
    rows = collect_eval_seed_rows(client, cases, top_k=top_k, max_fetches=max_fetches)
    report = import_mem0_export(repo, {"results": rows})
    return report, len(rows)


def safe_import_report(report: ImportReport, source: str, rows_seen: int) -> dict[str, int | str]:
    return {
        "source": source,
        "rows_seen": int(rows_seen),
        "created": report.created,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "skipped": report.skipped,
    }
