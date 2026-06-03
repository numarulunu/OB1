from __future__ import annotations

import hashlib
from typing import Any


SCORER_VERSION = "retrieval-20260526-multilingual-v1"
TEMPORAL_TERMS = {"after", "ago", "before", "date", "did", "older", "time", "timeline", "when", "year", "years"}
CURRENT_STATE_TERMS = {"current", "latest", "now", "recent", "status", "today"}
AUTOBIOGRAPHICAL_TERMS = {
    "attachment",
    "family",
    "origin",
    "personal",
    "psychology",
    "relationship",
    "relationships",
    "trauma",
    "trust",
}


def _query_tokens(query: str) -> list[str]:
    text = str(query or "").lower().replace("-", " ").replace("_", " ")
    normalized = "".join(ch if ch.isalnum() else " " for ch in text)
    return [part for part in normalized.split() if len(part) > 2]


def _length_bucket(char_len: int) -> str:
    if char_len <= 64:
        return "short"
    if char_len <= 180:
        return "medium"
    return "long"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def safe_query_features(query: str) -> dict[str, Any]:
    tokens = _query_tokens(query)
    token_set = set(tokens)
    char_len = len(str(query or ""))
    return {
        "char_len": char_len,
        "token_count": len(tokens),
        "length_bucket": _length_bucket(char_len),
        "temporal": bool(token_set & TEMPORAL_TERMS),
        "current_state": bool(token_set & CURRENT_STATE_TERMS),
        "autobiographical": bool(token_set & AUTOBIOGRAPHICAL_TERMS),
        "term_hashes": sorted({_hash_token(token) for token in token_set})[:16],
    }


def shadow_result_metrics(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    clean_rows = [row for row in (rows or []) if isinstance(row, dict)]
    scores: list[float] = []
    for row in clean_rows:
        try:
            scores.append(float(row.get("_retrieval_score")))
        except (TypeError, ValueError):
            continue
    top_score = max(scores) if scores else None
    if top_score is None:
        score_band = "none"
    elif top_score >= 50:
        score_band = "high"
    elif top_score >= 20:
        score_band = "medium"
    else:
        score_band = "low"
    return {
        "result_count": len(clean_rows),
        "top_score": round(top_score, 4) if top_score is not None else None,
        "score_band": score_band,
    }


def shadow_search_filters(
    *,
    query: str,
    top_k: int,
    domains: list[str],
    memory_types: list[str],
    memory_tiers: list[str],
    current_statuses: list[str],
) -> dict[str, Any]:
    return {
        "domains": list(domains or []),
        "memory_types": list(memory_types or []),
        "memory_tiers": list(memory_tiers or []),
        "current_statuses": list(current_statuses or []),
        "top_k": int(top_k or 0),
        "query_features": safe_query_features(query),
        "scorer_version": SCORER_VERSION,
        "result_metrics": shadow_result_metrics(),
    }
