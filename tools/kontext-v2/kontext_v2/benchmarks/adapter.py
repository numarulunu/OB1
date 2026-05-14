from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from psycopg.rows import dict_row

from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import score_row


@dataclass(frozen=True)
class BenchmarkMessage:
    role: str
    content: str
    source_id: str | None = None


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
        source_ids: list[str] | None = None,
        observation_kind: str = "turn",
    ) -> BenchmarkAddResult:
        text = _message_text(messages)
        ids = [str(value).strip() for value in (source_ids or []) if str(value).strip()]
        if not ids:
            ids = [str(message.source_id).strip() for message in messages if message.source_id]
        digest = stable_hash(
            "|".join(
                [
                    self.dataset,
                    self.run_id,
                    user_id,
                    conversation_id,
                    session_id,
                    observation_kind,
                    text,
                    ",".join(ids),
                ]
            )
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
            "source_ids": ids,
            "observation_kind": observation_kind,
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
                        "source_ids": ids,
                        "observation_kind": observation_kind,
                    },
                }
            ]
        )

    def _candidate_rows(self, user_id: str) -> list[dict[str, Any]]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT external_mem0_id, title, text, metadata, memory_type,
                       current_status, memory_tier, signal_strength, updated_at,
                       0.0::double precision AS rank
                FROM memories
                WHERE metadata->>'source' = 'benchmark'
                  AND metadata->>'benchmark_dataset' = %s
                  AND metadata->>'benchmark_run_id' = %s
                  AND metadata->>'benchmark_user_id' = %s
                  AND memory_type = 'benchmark_observation'
                  AND current_status = 'benchmark'
                  AND memory_tier = 'cold'
                ORDER BY updated_at ASC
                LIMIT 10000
                """,
                (self.dataset, self.run_id, user_id),
            ).fetchall()

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            item = dict(row)
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            conversation_key = str(metadata.get("conversation_id") or "")
            session_key = str(metadata.get("session_id") or "")
            grouped[(conversation_key, session_key)].append(item)

        enriched: list[dict[str, Any]] = []
        for group_rows in grouped.values():
            group_rows.sort(key=lambda row: row.get("updated_at") or 0)
            texts = [str(row.get("text") or "").strip() for row in group_rows]
            for index, row in enumerate(group_rows):
                context_parts = [text for idx, text in enumerate(texts) if idx != index and text]
                row["context_text"] = " ".join(context_parts)
                enriched.append(row)
        return enriched

    def search(self, query: str, user_id: str, top_k: int = 200) -> list[dict[str, Any]]:
        limit = min(max(int(top_k or 5), 1), 200)
        scored = [
            (index, score_row(query, row, requested_domains=set(), requested_tiers={"cold"}), row)
            for index, row in enumerate(self._candidate_rows(user_id))
        ]
        scored.sort(key=lambda item: (item[1], -item[0]), reverse=True)
        results = []
        for _, score, row in scored[:limit]:
            metadata = row.get("metadata") or {}
            results.append(
                {
                    "id": row.get("external_mem0_id"),
                    "memory": row.get("text") or "",
                    "score": float(score),
                    "metadata": {
                        "benchmark_dataset": self.dataset,
                        "benchmark_run_id": self.run_id,
                        "conversation_id": metadata.get("conversation_id"),
                        "session_id": metadata.get("session_id"),
                        "source_ids": [str(value) for value in metadata.get("source_ids") or []],
                        "observation_kind": metadata.get("observation_kind"),
                    },
                }
            )
        return results
