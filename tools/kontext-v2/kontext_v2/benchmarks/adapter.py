from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import search_memories


@dataclass(frozen=True)
class BenchmarkMessage:
    role: str
    content: str


@dataclass(frozen=True)
class BenchmarkAddResult:
    results: list[dict[str, Any]]


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _message_text(messages: Iterable[BenchmarkMessage]) -> str:
    lines = []
    for message in messages:
        role = str(message.role or "unknown").strip().lower() or "unknown"
        content = " ".join(str(message.content or "").split())
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


class KontextBenchmarkAdapter:
    def __init__(self, conn: Any, dataset: str, run_id: str) -> None:
        self.conn = conn
        self.repo = KontextRepository(conn)
        self.dataset = dataset
        self.run_id = run_id

    def close(self) -> None:
        self.conn.close()

    def add(
        self,
        messages: list[BenchmarkMessage],
        user_id: str,
        conversation_id: str,
        session_id: str,
        timestamp: str | None = None,
    ) -> BenchmarkAddResult:
        text = _message_text(messages)
        digest = stable_hash(
            "|".join([self.dataset, self.run_id, user_id, conversation_id, session_id, text])
        )
        external_id = f"benchmark:{self.dataset}:{self.run_id}:{digest[:16]}"
        metadata = {
            "source": "benchmark",
            "benchmark_dataset": self.dataset,
            "benchmark_run_id": self.run_id,
            "benchmark_user_id": user_id,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "timestamp": timestamp,
            "profile": "benchmark",
            "is_live_memory": False,
            "domains": ["benchmark"],
            "memory_type": "benchmark_observation",
            "current_status": "benchmark",
            "memory_tier": "cold",
            "signal_strength": 5,
        }
        record = MemoryRecord(
            external_mem0_id=external_id,
            title=f"{self.dataset} {conversation_id} {session_id}",
            text=text,
            metadata=metadata,
            memory_type="benchmark_observation",
            current_status="benchmark",
            memory_tier="cold",
            signal_strength=5,
            source_hash=digest,
        )
        self.repo.upsert_memory(record, version_source="benchmark")
        self.conn.commit()
        return BenchmarkAddResult(
            results=[
                {
                    "id": external_id,
                    "event": "ADD",
                    "metadata": {
                        "benchmark_dataset": self.dataset,
                        "benchmark_run_id": self.run_id,
                    },
                }
            ]
        )

    def search(self, query: str, user_id: str, top_k: int = 200) -> list[dict[str, Any]]:
        rows = search_memories(
            self.repo,
            query=query,
            top_k=top_k,
            domains=[],
            memory_types=["benchmark_observation"],
            memory_tiers=["cold"],
            current_statuses=["benchmark"],
        )
        results = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if metadata.get("source") != "benchmark":
                continue
            if metadata.get("benchmark_dataset") != self.dataset:
                continue
            if metadata.get("benchmark_run_id") != self.run_id:
                continue
            if metadata.get("benchmark_user_id") != user_id:
                continue
            results.append(
                {
                    "id": row.get("external_mem0_id") or row.get("id"),
                    "memory": row.get("text") or row.get("memory") or "",
                    "score": float(row.get("score") or 0.0),
                    "metadata": {
                        "benchmark_dataset": self.dataset,
                        "benchmark_run_id": self.run_id,
                        "conversation_id": metadata.get("conversation_id"),
                        "session_id": metadata.get("session_id"),
                    },
                }
            )
        return results[:top_k]
