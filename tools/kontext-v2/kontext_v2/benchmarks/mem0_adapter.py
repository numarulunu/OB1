from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kontext_v2.benchmarks.adapter import BenchmarkAddResult, BenchmarkMessage, stable_hash


DEFAULT_MEM0_RETRIEVAL_MODULE = Path("tools/mem0-remote-mcp/retrieval.py")


@dataclass(frozen=True)
class LegacyMem0Retrieval:
    rank_memories: Any


def load_legacy_mem0_retrieval(path: str | Path | None = None) -> LegacyMem0Retrieval:
    module_path = Path(path or DEFAULT_MEM0_RETRIEVAL_MODULE)
    if not module_path.exists():
        raise FileNotFoundError(f"legacy Mem0 retrieval module not found: {module_path}")
    spec = importlib.util.spec_from_file_location("legacy_mem0_retrieval_for_benchmark", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load legacy Mem0 retrieval module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "rank_memories"):
        raise RuntimeError(f"legacy Mem0 retrieval module has no rank_memories: {module_path}")
    return LegacyMem0Retrieval(rank_memories=module.rank_memories)


def _message_text(messages: list[BenchmarkMessage]) -> str:
    lines = []
    for message in messages:
        role = str(message.role or "unknown").strip().lower() or "unknown"
        content = " ".join(str(message.content or "").split())
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


class Mem0BenchmarkAdapter:
    """In-memory benchmark adapter for the legacy Mem0 MCP ranker.

    This intentionally does not call the hosted Mem0 API, write to the legacy
    Mem0 service, or create any database rows.
    """

    def __init__(
        self,
        dataset: str,
        run_id: str,
        retrieval_module_path: str | Path | None = None,
    ) -> None:
        self.dataset = dataset
        self.run_id = run_id
        self.retrieval = load_legacy_mem0_retrieval(retrieval_module_path)
        self._rows_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def close(self) -> None:
        return None

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
        external_id = f"benchmark:{self.dataset}:{self.run_id}:mem0:{digest[:16]}"
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
        self._rows_by_user[user_id].append(
            {
                "id": external_id,
                "text": text,
                "memory": text,
                "metadata": metadata,
                "memory_type": "benchmark_observation",
                "current_status": "benchmark",
                "memory_tier": "cold",
                "signal_strength": 5,
            }
        )
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

    def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 20,
        question_category: str | None = None,
    ) -> list[dict[str, Any]]:
        del question_category
        limit = min(max(int(top_k or 5), 1), 20)
        ranked = self.retrieval.rank_memories(
            query,
            list(self._rows_by_user.get(user_id, [])),
            requested_tiers=["cold"],
            top_k=limit,
        )
        results = []
        for row in ranked[:limit]:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            results.append(
                {
                    "id": row.get("id"),
                    "memory": row.get("text") or row.get("memory") or "",
                    "score": float(row.get("score") or 0.0),
                    "metadata": {
                        "source": metadata.get("source"),
                        "is_live_memory": metadata.get("is_live_memory"),
                        "benchmark_dataset": self.dataset,
                        "benchmark_run_id": self.run_id,
                        "conversation_id": metadata.get("conversation_id"),
                        "session_id": metadata.get("session_id"),
                        "timestamp": metadata.get("timestamp"),
                        "source_ids": [str(value) for value in metadata.get("source_ids") or []],
                        "observation_kind": metadata.get("observation_kind"),
                    },
                }
            )
        return results
