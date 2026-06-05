from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Mem0ClientConfig:
    base_url: str
    api_key: str
    user_id: str = "ionut"
    agent_id: str = "mem0-extractor"


class Mem0Client:
    def __init__(self, config: Mem0ClientConfig):
        self.config = config

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "X-API-Key": self.config.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Mem0 HTTP {exc.code}: {detail}") from exc

    def search(self, query: str, top_k: int = 5, domains: list[str] | None = None, memory_tiers: list[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "filters": {"user_id": self.config.user_id}, "top_k": min(max(int(top_k), 1), 20)}
        data = self.request("POST", "/search", body)
        rows = data.get("results") if isinstance(data, dict) else data
        results = [row for row in rows or [] if isinstance(row, dict)]
        if domains:
            requested = {domain.lower() for domain in domains}
            results = [row for row in results if requested & {str(domain).lower() for domain in ((row.get("metadata") or {}).get("domains") or row.get("domains") or [])}]
        if memory_tiers:
            requested_tiers = {tier.lower() for tier in memory_tiers}
            results = [row for row in results if str((row.get("metadata") or {}).get("memory_tier") or row.get("memory_tier") or "active").lower() in requested_tiers]
        return {"query": query, "results": results[:top_k]}

    def save(self, content: str, metadata: dict[str, Any]) -> Any:
        body = {
            "messages": [{"role": "user", "content": content}],
            "user_id": self.config.user_id,
            "agent_id": self.config.agent_id,
            "metadata": metadata,
            "infer": False,
        }
        return self.request("POST", "/memories", body)

    def update(self, memory_id: str, content: str, metadata: dict[str, Any]) -> Any:
        return self.request("PUT", f"/memories/{memory_id}", {"text": content, "metadata": metadata})
