from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from kontext_v2.retrieval import (
    HIGH_SIGNAL_MEMORY_TYPES,
    LOW_SIGNAL_MEMORY_TYPES,
    SENSITIVE_DOMAINS,
    SENSITIVE_QUERY_TERMS,
    adjacent_token_score,
    expand_query,
    lexical_score,
    lexical_tokens,
    normalized_memory_type,
    normalized_text,
    query_domain_hints,
    query_proper_noun_tokens,
    recency_score_for_timestamp,
    row_domains,
    row_effective_timestamp,
    row_signal,
    row_status,
    row_superseded,
    row_tier,
)


JUNK_MEMORY_TERMS = {"lorem", "placeholder", "test test", "dummy", "n/a"}


def _env_int(values: dict[str, str], key: str, default: int) -> int:
    try:
        return int(values.get(key) or default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Mem0ParityRerankConfig:
    enabled: bool = False
    candidate_window: int = 200
    rrf_k: int = 20
    locked_head_size: int = 0

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Mem0ParityRerankConfig":
        values = environ if environ is not None else os.environ
        enabled = str(values.get("KONTEXT_BENCHMARK_MEM0_PARITY_RERANK") or "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return cls(enabled=False)
        return cls(
            enabled=True,
            candidate_window=max(_env_int(values, "KONTEXT_BENCHMARK_MEM0_PARITY_WINDOW", cls.candidate_window), 1),
            rrf_k=max(_env_int(values, "KONTEXT_BENCHMARK_MEM0_PARITY_RRF_K", cls.rrf_k), 1),
            locked_head_size=max(_env_int(values, "KONTEXT_BENCHMARK_MEM0_PARITY_LOCKED_HEAD", cls.locked_head_size), 0),
        )

    def public_summary(self) -> dict[str, int | bool]:
        return {
            "enabled": self.enabled,
            "candidate_window": self.candidate_window,
            "rrf_k": self.rrf_k,
            "locked_head_size": self.locked_head_size,
        }


def _vector_score(row: dict[str, Any]) -> float:
    for key in ("score", "rank"):
        try:
            value = float(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return min(value, 1.0)
    return 0.0


def _requested_tiers(values: Iterable[Any] | None) -> set[str]:
    tiers = set()
    for value in values or []:
        text = str(value or "").strip().lower()
        if text:
            tiers.add(text)
    return tiers


def mem0_style_score_row(
    query: str,
    row: dict[str, Any],
    *,
    requested_domains: Iterable[Any] | None = None,
    requested_tiers: Iterable[Any] | None = None,
) -> float:
    text = str(row.get("text") or row.get("memory") or "")
    metadata_domains = row_domains(row)
    requested_domain_set = {str(value).strip().lower() for value in (requested_domains or []) if str(value).strip()}
    requested_tier_set = _requested_tiers(requested_tiers)
    expanded_query = expand_query(query)
    query_domains = query_domain_hints(expanded_query)
    tokens = lexical_tokens(expanded_query)

    score = 0.0
    score += _vector_score(row) * 8.0
    score += lexical_score(expanded_query, tokens, text) * 1.2
    score += adjacent_token_score(tokens, text) * 1.6

    type_name = normalized_memory_type(row)
    haystack = normalized_text(" ".join([text, " ".join(sorted(metadata_domains)), type_name]))
    for entity in query_proper_noun_tokens(expanded_query):
        if entity in haystack:
            score += 7.0

    domain_hits = query_domains & metadata_domains
    if requested_domain_set and requested_domain_set & metadata_domains:
        score += 8.0
    elif domain_hits:
        score += 5.0
    score += min(len(domain_hits), 3) * 2.5

    strength = row_signal(row)
    score += strength * 0.8
    if type_name in HIGH_SIGNAL_MEMORY_TYPES:
        score += 3.5
    elif type_name in LOW_SIGNAL_MEMORY_TYPES and strength < 4:
        score -= 2.5
    if any(term in haystack for term in JUNK_MEMORY_TERMS):
        score -= 8.0

    tier = row_tier(row)
    sensitive_query = bool((set(tokens) & SENSITIVE_QUERY_TERMS) or (metadata_domains & SENSITIVE_DOMAINS))
    if requested_tier_set and tier in requested_tier_set:
        pass
    elif tier == "active":
        score += 2.5
    elif tier == "historical":
        score += 3.0 if sensitive_query else -1.0
    elif tier == "cold":
        score -= 8.0

    status = row_status(row)
    superseded = row_superseded(row)
    if status == "active":
        score += 2.0
    elif status in {"dormant", "paused", "unknown"}:
        score -= 0.5
    elif status in {"resolved", "inactive"}:
        score -= 2.0
    elif status in {"superseded", "false", "outdated", "deleted"}:
        score -= 8.0

    if superseded:
        score -= 5.0 if requested_tier_set else 16.0
    elif tier == "active" and status in {"", "active"}:
        score += _mem0_recency_score(row_effective_timestamp(row))
    elif tier == "historical" and sensitive_query:
        score += _mem0_recency_score(row_effective_timestamp(row)) * 0.35
    return score


def _mem0_recency_score(timestamp: datetime | None) -> float:
    if timestamp is None:
        return 0.0
    return recency_score_for_timestamp(timestamp) * 0.375


def _row_identity(item: tuple[int, float, dict[str, Any]]) -> str:
    index, _, row = item
    return str(row.get("external_mem0_id") or row.get("id") or index)


def _fuse_rankings_rrf(
    rankings: list[list[tuple[int, float, dict[str, Any]]]],
    k: int,
) -> list[tuple[int, float, dict[str, Any]]]:
    if not rankings:
        return []
    constant = max(int(k or 0), 1)
    scores: dict[str, float] = defaultdict(float)
    preferred: dict[str, tuple[int, float, dict[str, Any]]] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            row_id = _row_identity(item)
            scores[row_id] += 1.0 / (constant + rank)
            preferred[row_id] = item
    return [preferred[row_id] for row_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]


def rerank_with_mem0_parity(
    query: str,
    scored: list[tuple[int, float, dict[str, Any]]],
    config: Mem0ParityRerankConfig,
    question_category: str | None = None,
) -> list[tuple[int, float, dict[str, Any]]]:
    if not config.enabled or not scored:
        return scored
    head_size = max(int(config.locked_head_size or 0), 0)
    window = max(int(config.candidate_window or 0), head_size + 1)
    locked_head = scored[:head_size]
    rerank_slice = scored[head_size:window]
    remainder = scored[window:]
    parity_ranked = [
        (index, mem0_style_score_row(query, row, requested_tiers={"cold"}), row)
        for index, _, row in rerank_slice
    ]
    parity_ranked.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    fused = _fuse_rankings_rrf([rerank_slice, parity_ranked], config.rrf_k)
    return [*locked_head, *fused, *remainder]
