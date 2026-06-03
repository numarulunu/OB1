from __future__ import annotations

import hashlib
import math
import os
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from psycopg.rows import dict_row

from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.benchmarks.mem0_parity import Mem0ParityRerankConfig, rerank_with_mem0_parity
from kontext_v2.retrieval import (
    _adjacent_token_score_prepared,
    _score_row_with_explanation,
    _score_text_cache,
    expand_query,
    lexical_tokens,
    prepare_score_row,
)


_BENCHMARK_MODEL_CACHE_MAX_SIZE = 4
_BENCHMARK_MODEL_CACHE: OrderedDict[tuple[str, str, str, int | None], Any] = OrderedDict()


def clear_benchmark_model_cache_for_tests() -> None:
    _BENCHMARK_MODEL_CACHE.clear()


def _benchmark_model_cache_key(
    kind: str,
    model_name: str,
    model_loader: Callable[[str], Any] | None,
) -> tuple[str, str, str, int | None]:
    if model_loader is None:
        return (kind, model_name, 'default', None)
    return (kind, model_name, 'loader', id(model_loader))


def _load_cached_benchmark_model(
    kind: str,
    model_name: str,
    model_loader: Callable[[str], Any] | None,
    default_loader: Callable[[str], Any],
) -> Any:
    key = _benchmark_model_cache_key(kind, model_name, model_loader)
    if key in _BENCHMARK_MODEL_CACHE:
        _BENCHMARK_MODEL_CACHE.move_to_end(key)
        return _BENCHMARK_MODEL_CACHE[key]

    model = model_loader(model_name) if model_loader is not None else default_loader(model_name)
    _BENCHMARK_MODEL_CACHE[key] = model
    _BENCHMARK_MODEL_CACHE.move_to_end(key)
    while len(_BENCHMARK_MODEL_CACHE) > _BENCHMARK_MODEL_CACHE_MAX_SIZE:
        _BENCHMARK_MODEL_CACHE.popitem(last=False)
    return model


@dataclass(frozen=True)
class BenchmarkMessage:
    role: str
    content: str
    source_id: str | None = None


@dataclass(frozen=True)
class BenchmarkAddResult:
    results: list[dict[str, Any]]


@dataclass(frozen=True)
class SemanticRerankConfig:
    enabled: bool = False
    model_name: str = "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
    locked_head_size: int = 40
    candidate_window: int = 200
    score_weight: float = 20.0
    max_chars: int = 2400
    batch_size: int = 128
    fusion_rrf_k: int = 10
    query_focused_text: bool = False
    model_loader: Callable[[str], Any] | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "SemanticRerankConfig":
        values = environ if environ is not None else os.environ
        enabled = str(values.get("KONTEXT_BENCHMARK_SEMANTIC_RERANK") or "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return cls(enabled=False)
        return cls(
            enabled=True,
            model_name=str(values.get("KONTEXT_BENCHMARK_SEMANTIC_MODEL") or cls.model_name),
            locked_head_size=_env_int(values, "KONTEXT_BENCHMARK_SEMANTIC_LOCKED_HEAD", cls.locked_head_size),
            candidate_window=_env_int(values, "KONTEXT_BENCHMARK_SEMANTIC_WINDOW", cls.candidate_window),
            score_weight=_env_float(values, "KONTEXT_BENCHMARK_SEMANTIC_WEIGHT", cls.score_weight),
            max_chars=_env_int(values, "KONTEXT_BENCHMARK_SEMANTIC_MAX_CHARS", cls.max_chars),
            batch_size=_env_int(values, "KONTEXT_BENCHMARK_SEMANTIC_BATCH_SIZE", cls.batch_size),
            fusion_rrf_k=_env_int(values, "KONTEXT_BENCHMARK_SEMANTIC_RRF_K", cls.fusion_rrf_k),
            query_focused_text=_env_bool(
                values,
                "KONTEXT_BENCHMARK_SEMANTIC_QUERY_FOCUSED_TEXT",
                cls.query_focused_text,
            ),
        )


@dataclass(frozen=True)
class CrossEncoderRerankConfig:
    enabled: bool = False
    model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    locked_head_size: int = 0
    candidate_window: int = 100
    score_weight: float = 10.0
    max_chars: int = 1800
    batch_size: int = 64
    fusion_rrf_k: int = 0
    query_focused_text: bool = False
    text_windows: int = 1
    text_window_overlap: int = 0
    line_windows: int = 0
    source_count_penalty_weight: float = 0.0
    adjacent_penalty_weight: float = 0.0
    normalization_question_types: tuple[str, ...] = ()
    model_loader: Callable[[str], Any] | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "CrossEncoderRerankConfig":
        values = environ if environ is not None else os.environ
        enabled = str(values.get("KONTEXT_BENCHMARK_CROSS_ENCODER_RERANK") or "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return cls(enabled=False)
        return cls(
            enabled=True,
            model_name=str(values.get("KONTEXT_BENCHMARK_CROSS_ENCODER_MODEL") or cls.model_name),
            locked_head_size=_env_nonnegative_int(
                values,
                "KONTEXT_BENCHMARK_CROSS_ENCODER_LOCKED_HEAD",
                cls.locked_head_size,
            ),
            candidate_window=_env_int(values, "KONTEXT_BENCHMARK_CROSS_ENCODER_WINDOW", cls.candidate_window),
            score_weight=_env_float(values, "KONTEXT_BENCHMARK_CROSS_ENCODER_WEIGHT", cls.score_weight),
            max_chars=_env_int(values, "KONTEXT_BENCHMARK_CROSS_ENCODER_MAX_CHARS", cls.max_chars),
            batch_size=_env_int(values, "KONTEXT_BENCHMARK_CROSS_ENCODER_BATCH_SIZE", cls.batch_size),
            fusion_rrf_k=_env_nonnegative_int(values, "KONTEXT_BENCHMARK_CROSS_ENCODER_RRF_K", cls.fusion_rrf_k),
            query_focused_text=_env_bool(
                values,
                "KONTEXT_BENCHMARK_CROSS_ENCODER_QUERY_FOCUSED_TEXT",
                cls.query_focused_text,
            ),
            text_windows=_env_int(values, "KONTEXT_BENCHMARK_CROSS_ENCODER_TEXT_WINDOWS", cls.text_windows),
            text_window_overlap=_env_nonnegative_int(
                values,
                "KONTEXT_BENCHMARK_CROSS_ENCODER_TEXT_WINDOW_OVERLAP",
                cls.text_window_overlap,
            ),
            line_windows=_env_nonnegative_int(
                values,
                "KONTEXT_BENCHMARK_CROSS_ENCODER_LINE_WINDOWS",
                cls.line_windows,
            ),
            source_count_penalty_weight=_env_float(
                values,
                "KONTEXT_BENCHMARK_CROSS_ENCODER_SOURCE_COUNT_PENALTY",
                cls.source_count_penalty_weight,
            ),
            adjacent_penalty_weight=_env_float(
                values,
                "KONTEXT_BENCHMARK_CROSS_ENCODER_ADJACENT_PENALTY",
                cls.adjacent_penalty_weight,
            ),
            normalization_question_types=_env_csv_tuple(
                values,
                "KONTEXT_BENCHMARK_CROSS_ENCODER_NORMALIZATION_QUESTION_TYPES",
            ),
        )


def _env_int(values: dict[str, str], key: str, default: int) -> int:
    try:
        return max(int(values.get(key) or default), 1)
    except (TypeError, ValueError):
        return default


def _env_nonnegative_int(values: dict[str, str], key: str, default: int) -> int:
    raw_value = values.get(key)
    if raw_value is None or str(raw_value).strip() == "":
        raw_value = default
    try:
        return max(int(raw_value), 0)
    except (TypeError, ValueError):
        return default


def _env_float(values: dict[str, str], key: str, default: float) -> float:
    try:
        return float(values.get(key) or default)
    except (TypeError, ValueError):
        return default


def _env_bool(values: dict[str, str], key: str, default: bool) -> bool:
    raw_value = values.get(key)
    if raw_value is None or str(raw_value).strip() == "":
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _env_csv_tuple(values: dict[str, str], key: str) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in str(values.get(key) or "").split(",")
        if part.strip()
    )


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


def _compact_context_for_index(
    texts: list[str],
    index: int,
    neighbor_window: int = 6,
    max_chars: int = 6000,
) -> str:
    start = max(0, index - max(int(neighbor_window), 0))
    end = min(len(texts), index + max(int(neighbor_window), 0) + 1)
    parts = [text for idx, text in enumerate(texts[start:end], start=start) if idx != index and text]
    context = " ".join(parts)
    if len(context) <= max_chars:
        return context
    return context[:max_chars].rsplit(" ", 1)[0]


def benchmark_candidate_sort_key(index: int, score: float, row: dict[str, Any]) -> tuple[float, float, float, int]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    source_count = len(metadata.get("source_ids") or [])
    is_benchmark_session = (
        row.get("memory_type") == "benchmark_observation"
        and metadata.get("observation_kind") == "session"
        and source_count > 0
        and score < 50.0
    )
    score_bucket = math.floor(score) if is_benchmark_session else score
    source_span = math.log1p(source_count) if is_benchmark_session else 0.0
    return (float(score_bucket), float(source_span), float(score), -index)


def benchmark_candidate_score(query_tokens: list[str], row: dict[str, Any], base_score: float) -> float:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if row.get("memory_type") != "benchmark_observation" or metadata.get("observation_kind") != "session":
        return base_score
    adjacent_own = row.get("_benchmark_adjacent_own")
    if adjacent_own is None:
        text_cache = _score_text_cache(row)
        adjacent_own = _adjacent_token_score_prepared(
            query_tokens,
            str(text_cache.get("own_token_text") or ""),
            int(text_cache.get("own_token_count") or 0),
        ) * 1.2
    return base_score + adjacent_own * 0.75


def benchmark_candidate_score_for_category(
    query_tokens: list[str],
    row: dict[str, Any],
    base_score: float,
    question_category: str | None = None,
) -> float:
    score = benchmark_candidate_score(query_tokens, row, base_score)
    category = str(question_category or "").strip().lower()
    if category not in {"instruction_following", "knowledge_update", "preference_following"}:
        return score
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if row.get("memory_type") != "benchmark_observation" or metadata.get("observation_kind") != "turn":
        return score
    text = str(row.get("text") or "")
    lowered = text.lower()
    role_boost = 10.0 if lowered.lstrip().startswith("user:") else 0.0
    marker_terms = {
        "instruction_following": ("instruction", "must", "should", "need", "use", "format", "reply", "respond", "stop", "instead"),
        "knowledge_update": ("actually", "correction", "current", "latest", "now", "replace", "updated", "instead"),
        "preference_following": ("prefer", "preference", "like", "want", "favorite", "style", "rather", "instead"),
    }[category]
    marker_boost = min(sum(1 for term in marker_terms if term in lowered), 3) * 4.0
    return score + role_boost + marker_boost


def cross_encoder_normalization_penalty(
    query_tokens: list[str],
    row: dict[str, Any],
    config: CrossEncoderRerankConfig,
    question_category: str | None = None,
) -> float:
    if (
        config.normalization_question_types
        and (question_category or "") not in config.normalization_question_types
    ):
        return 0.0
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if row.get("memory_type") != "benchmark_observation" or metadata.get("observation_kind") != "session":
        return 0.0
    penalty = 0.0
    if config.source_count_penalty_weight:
        penalty += len(metadata.get("source_ids") or []) * config.source_count_penalty_weight
    if config.adjacent_penalty_weight:
        text_cache = _score_text_cache(row)
        adjacent_own = _adjacent_token_score_prepared(
            query_tokens,
            str(text_cache.get("own_token_text") or ""),
            int(text_cache.get("own_token_count") or 0),
        )
        penalty += adjacent_own * config.adjacent_penalty_weight
    return penalty


def rank_benchmark_candidates(
    query_tokens: list[str],
    scored: list[tuple[int, float, dict[str, Any]]],
    score_func: Callable[[list[str], dict[str, Any], float], float] = benchmark_candidate_score,
    locked_head_size: int = 20,
) -> list[tuple[int, float, dict[str, Any]]]:
    base_ranked = sorted(
        scored,
        key=lambda item: benchmark_candidate_sort_key(item[0], item[1], item[2]),
        reverse=True,
    )
    head_size = max(int(locked_head_size or 0), 0)
    if head_size <= 0:
        reranked = [
            (index, score_func(query_tokens, row, score), row)
            for index, score, row in base_ranked
        ]
        reranked.sort(key=lambda item: benchmark_candidate_sort_key(item[0], item[1], item[2]), reverse=True)
        return reranked
    if len(base_ranked) <= head_size:
        return base_ranked
    locked_head = base_ranked[:head_size]
    reranked_tail = [
        (index, score_func(query_tokens, row, score), row)
        for index, score, row in base_ranked[head_size:]
    ]
    reranked_tail.sort(key=lambda item: benchmark_candidate_sort_key(item[0], item[1], item[2]), reverse=True)
    return [*locked_head, *reranked_tail]


def _truncate_at_word(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rsplit(" ", 1)[0] or value[:max_chars]


def _query_focused_text(value: str, query: str, max_chars: int) -> str:
    tokens = []
    seen = set()
    for token in lexical_tokens(query):
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    if not tokens:
        return ""

    lower = value.lower()
    positions: list[int] = []
    phrase = " ".join(str(query or "").lower().split())
    if phrase:
        phrase_index = lower.find(phrase)
        if phrase_index >= 0:
            positions.append(phrase_index)
    for token in tokens:
        start = 0
        for _ in range(3):
            position = lower.find(token, start)
            if position < 0:
                break
            positions.append(position)
            start = position + len(token)
    if not positions:
        return ""

    best_score = 0.0
    best_segment = ""
    max_chars = max(int(max_chars or 0), 1)
    for position in positions:
        start = max(0, position - max_chars // 3)
        if start + max_chars > len(value):
            start = max(0, len(value) - max_chars)
        segment = value[start : start + max_chars].strip()
        segment_lower = segment.lower()
        score = float(sum(1 for token in tokens if token in segment_lower))
        if phrase and phrase in segment_lower:
            score += len(tokens) + 3.0
        if score > best_score:
            best_score = score
            best_segment = segment
    return _truncate_at_word(best_segment, max_chars) if best_score > 0 else ""


def _semantic_candidate_text(row: dict[str, Any], max_chars: int, query: str | None = None) -> str:
    text = str(row.get("text") or "")
    context = str(row.get("context_text") or "")
    value = f"{text} {context}".strip()
    if len(value) <= max_chars:
        return value
    if query:
        focused = _query_focused_text(value, query, max_chars)
        if focused:
            return focused
    return _truncate_at_word(value, max_chars)


def _cross_encoder_candidate_text_views(
    row: dict[str, Any],
    max_chars: int,
    query: str | None = None,
    max_windows: int = 1,
    overlap: int = 0,
    line_windows: int = 0,
) -> list[str]:
    text = str(row.get("text") or "")
    context = str(row.get("context_text") or "")
    value = f"{text} {context}".strip()
    max_chars = max(int(max_chars or 0), 1)
    max_windows = max(int(max_windows or 1), 1)
    line_windows = max(int(line_windows or 0), 0)
    if line_windows > 0:
        max_windows = max(max_windows, line_windows)
    if (max_windows <= 1 and line_windows <= 0) or len(value) <= max_chars:
        return [_semantic_candidate_text(row, max_chars, query=query)]

    starts: list[int] = []
    max_start = max(len(value) - max_chars, 0)
    if max_windows == 2:
        starts = [0, max_start]
    else:
        step = max_start / float(max_windows - 1)
        starts = [round(step * index) for index in range(max_windows)]

    bounded_overlap = min(max(int(overlap or 0), 0), max_chars - 1)
    if bounded_overlap:
        stride = max(max_chars - bounded_overlap, 1)
        stride_starts = list(range(0, max_start + 1, stride))
        if stride_starts and stride_starts[-1] != max_start:
            stride_starts.append(max_start)
        if len(stride_starts) <= max_windows:
            starts = stride_starts

    views: list[str] = []
    seen: set[str] = set()
    for segment in _query_ranked_line_windows(value, query or "", max_chars, line_windows):
        if segment and segment not in seen:
            views.append(segment)
            seen.add(segment)
        if len(views) >= max_windows:
            break
    if query:
        focused = _query_focused_text(value, query, max_chars)
        if focused:
            views.append(focused)
            seen.add(focused)

    for start in starts:
        segment = _truncate_at_word(value[max(min(start, max_start), 0) : max(min(start, max_start), 0) + max_chars].strip(), max_chars)
        if segment and segment not in seen:
            views.append(segment)
            seen.add(segment)
        if len(views) >= max_windows:
            break
    return views or [_truncate_at_word(value, max_chars)]


def _query_ranked_line_windows(value: str, query: str, max_chars: int, max_windows: int) -> list[str]:
    max_windows = max(int(max_windows or 0), 0)
    if max_windows <= 0:
        return []
    tokens = [token for token in lexical_tokens(query) if len(token) >= 3]
    if not tokens:
        return []
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    scored: list[tuple[float, int, str]] = []
    max_chars = max(int(max_chars or 0), 1)
    for index, line in enumerate(lines):
        window_lines = lines[max(0, index - 1) : min(len(lines), index + 2)]
        window = _truncate_at_word(" ".join(window_lines), max_chars)
        lowered = window.lower()
        score = float(sum(1 for token in set(tokens) if token in lowered))
        if score <= 0:
            continue
        scored.append((score, -index, window))
    scored.sort(reverse=True)
    views: list[str] = []
    seen: set[str] = set()
    for _, _, window in scored:
        if window not in seen:
            views.append(window)
            seen.add(window)
        if len(views) >= max_windows:
            break
    return views


def _vector_values(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]


def _cosine_similarity(left: Any, right: Any) -> float:
    left_values = _vector_values(left)
    right_values = _vector_values(right)
    if not left_values or not right_values or len(left_values) != len(right_values):
        return 0.0
    dot = sum(left * right for left, right in zip(left_values, right_values))
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _row_identity(item: tuple[int, float, dict[str, Any]]) -> str:
    index, _, row = item
    return str(row.get("external_mem0_id") or index)


def fuse_rankings_rrf(
    rankings: list[list[tuple[int, float, dict[str, Any]]]],
    k: int = 10,
) -> list[tuple[int, float, dict[str, Any]]]:
    if not rankings:
        return []
    if len(rankings) == 1:
        return rankings[0]
    constant = max(int(k or 0), 1)
    scores: dict[str, float] = defaultdict(float)
    preferred_item: dict[str, tuple[int, float, dict[str, Any]]] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            row_id = _row_identity(item)
            scores[row_id] += 1.0 / (constant + rank)
            preferred_item[row_id] = item
    return [
        preferred_item[row_id]
        for row_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ]


def rerank_benchmark_candidates_semantically(
    query: str,
    ranked: list[tuple[int, float, dict[str, Any]]],
    config: SemanticRerankConfig,
    model: Any,
    embedding_cache: dict[str, Any] | None = None,
) -> list[tuple[int, float, dict[str, Any]]]:
    if not config.enabled:
        return ranked
    head_size = max(int(config.locked_head_size or 0), 0)
    window = max(int(config.candidate_window or 0), head_size)
    if len(ranked) <= head_size or window <= head_size:
        return ranked
    locked_head = ranked[:head_size]
    rerank_slice = ranked[head_size:window]
    remainder = ranked[window:]
    text_query = query if config.query_focused_text else None
    row_texts = [(_semantic_candidate_text(row, config.max_chars, query=text_query), row) for _, _, row in rerank_slice]
    if not row_texts:
        return ranked
    query_vector = model.encode(
        [query],
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=config.batch_size,
    )[0]
    candidate_vectors: list[Any | None] = []
    missing: list[tuple[int, str, str]] = []
    for position, (text, row) in enumerate(row_texts):
        row_id = str(row.get("external_mem0_id") or position)
        cache_key = f"{row_id}:{stable_hash(text)[:16]}"
        cached = embedding_cache.get(cache_key) if embedding_cache is not None else None
        candidate_vectors.append(cached)
        if cached is None:
            missing.append((position, cache_key, text))
    if missing:
        encoded = model.encode(
            [text for _, _, text in missing],
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=config.batch_size,
        )
        for (position, cache_key, _), vector in zip(missing, encoded):
            candidate_vectors[position] = vector
            if embedding_cache is not None:
                embedding_cache[cache_key] = vector
    reranked_tail = []
    for item, vector in zip(rerank_slice, candidate_vectors):
        index, score, row = item
        semantic_score = _cosine_similarity(query_vector, vector)
        reranked_tail.append((index, score + semantic_score * config.score_weight, row))
    reranked_tail.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    semantic_ranked = [*locked_head, *reranked_tail, *remainder]
    if config.fusion_rrf_k > 0:
        return fuse_rankings_rrf([ranked, semantic_ranked], config.fusion_rrf_k)
    return semantic_ranked


def _score_values(scores: Any) -> list[float]:
    if hasattr(scores, "tolist"):
        scores = scores.tolist()
    if isinstance(scores, (int, float)):
        return [float(scores)]
    values = []
    for item in scores or []:
        if hasattr(item, "tolist"):
            item = item.tolist()
        if isinstance(item, (list, tuple)):
            values.append(float(item[-1]) if item else 0.0)
        else:
            values.append(float(item))
    return values


def rerank_benchmark_candidates_with_cross_encoder(
    query: str,
    ranked: list[tuple[int, float, dict[str, Any]]],
    config: CrossEncoderRerankConfig,
    model: Any,
    question_category: str | None = None,
) -> list[tuple[int, float, dict[str, Any]]]:
    if not config.enabled:
        return ranked
    head_size = max(int(config.locked_head_size or 0), 0)
    window = max(int(config.candidate_window or 0), head_size)
    if len(ranked) <= head_size or window <= head_size:
        return ranked
    locked_head = ranked[:head_size]
    rerank_slice = ranked[head_size:window]
    remainder = ranked[window:]
    query_tokens = lexical_tokens(expand_query(query))
    pairs: list[tuple[str, str]] = []
    pair_positions: list[int] = []
    for position, (_, _, row) in enumerate(rerank_slice):
        views = _cross_encoder_candidate_text_views(
            row,
            config.max_chars,
            query=query if config.query_focused_text else None,
            max_windows=config.text_windows,
            overlap=config.text_window_overlap,
            line_windows=config.line_windows,
        )
        pairs.extend((query, view) for view in views)
        pair_positions.extend([position] * len(views))
    if not pairs:
        return ranked
    try:
        raw_scores = model.predict(pairs, batch_size=config.batch_size, show_progress_bar=False)
    except TypeError:
        raw_scores = model.predict(pairs)
    pairwise_scores = _score_values(raw_scores)
    best_pair_scores: list[float | None] = [None] * len(rerank_slice)
    for position, pairwise_score in zip(pair_positions, pairwise_scores):
        current = best_pair_scores[position]
        best_pair_scores[position] = pairwise_score if current is None else max(current, pairwise_score)
    reranked_tail = []
    for item, pairwise_score in zip(rerank_slice, best_pair_scores):
        index, score, row = item
        normalized_score = (
            score
            + float(pairwise_score or 0.0) * config.score_weight
            - cross_encoder_normalization_penalty(query_tokens, row, config, question_category)
        )
        reranked_tail.append((index, normalized_score, row))
    if len(reranked_tail) != len(rerank_slice):
        reranked_tail.extend(rerank_slice[len(reranked_tail) :])
    reranked_tail.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    cross_ranked = [*locked_head, *reranked_tail, *remainder]
    if config.fusion_rrf_k > 0:
        return fuse_rankings_rrf([ranked, cross_ranked], config.fusion_rrf_k)
    return cross_ranked


class KontextBenchmarkAdapter:
    DEFAULT_CANDIDATE_LIMIT = 50000

    def __init__(
        self,
        conn: Any,
        dataset: str,
        run_id: str,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
        semantic_rerank_config: SemanticRerankConfig | None = None,
        cross_encoder_rerank_config: CrossEncoderRerankConfig | None = None,
        mem0_parity_rerank_config: Mem0ParityRerankConfig | None = None,
    ) -> None:
        self.conn = conn
        self.repo = KontextRepository(conn)
        self.dataset = dataset
        self.run_id = run_id
        self.candidate_limit = max(int(candidate_limit or self.DEFAULT_CANDIDATE_LIMIT), 1)
        self._candidate_cache: dict[str, list[dict[str, Any]]] = {}
        self.semantic_rerank_config = semantic_rerank_config or SemanticRerankConfig.from_env()
        self.cross_encoder_rerank_config = cross_encoder_rerank_config or CrossEncoderRerankConfig.from_env()
        self.mem0_parity_rerank_config = mem0_parity_rerank_config or Mem0ParityRerankConfig.from_env()
        self._semantic_model: Any | None = None
        self._cross_encoder_model: Any | None = None
        self._semantic_embedding_cache: dict[str, Any] = {}

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
        self._candidate_cache.pop(user_id, None)
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

    def _load_candidate_rows(self, user_id: str) -> list[dict[str, Any]]:
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
                LIMIT %s
                """,
                (self.dataset, self.run_id, user_id, self.candidate_limit),
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
                row["context_text"] = _compact_context_for_index(texts, index)
                prepare_score_row(row)
                enriched.append(row)
        return enriched

    def _candidate_rows(self, user_id: str) -> list[dict[str, Any]]:
        if user_id not in self._candidate_cache:
            self._candidate_cache[user_id] = self._load_candidate_rows(user_id)
        return self._candidate_cache[user_id]

    def _load_semantic_model(self) -> Any:
        if self._semantic_model is not None:
            return self._semantic_model
        config = self.semantic_rerank_config

        def load_default(model_name: str) -> Any:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("sentence-transformers is required for semantic benchmark reranking") from exc
            return SentenceTransformer(model_name)

        self._semantic_model = _load_cached_benchmark_model(
            "semantic",
            config.model_name,
            config.model_loader,
            load_default,
        )
        return self._semantic_model

    def _load_cross_encoder_model(self) -> Any:
        if self._cross_encoder_model is not None:
            return self._cross_encoder_model
        config = self.cross_encoder_rerank_config

        def load_default(model_name: str) -> Any:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError("sentence-transformers is required for cross-encoder benchmark reranking") from exc
            return CrossEncoder(model_name)

        self._cross_encoder_model = _load_cached_benchmark_model(
            "cross_encoder",
            config.model_name,
            config.model_loader,
            load_default,
        )
        return self._cross_encoder_model

    def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 200,
        question_category: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = min(max(int(top_k or 5), 1), 200)
        query_tokens = lexical_tokens(expand_query(query))
        scored = []
        for index, row in enumerate(self._candidate_rows(user_id)):
            score, explanation = _score_row_with_explanation(query, row, requested_domains=set(), requested_tiers={"cold"})
            row["_benchmark_adjacent_own"] = next(
                (
                    float(feature.get("value") or 0.0)
                    for feature in explanation.get("features", [])
                    if feature.get("name") == "adjacent_own"
                ),
                0.0,
            )
            scored.append((index, score, row))
        state_category = str(question_category or "").strip().lower() in {
            "instruction_following",
            "knowledge_update",
            "preference_following",
        }
        scored = rank_benchmark_candidates(
            query_tokens,
            scored,
            score_func=lambda tokens, row, base: benchmark_candidate_score_for_category(
                tokens,
                row,
                base,
                question_category,
            ),
            locked_head_size=0 if state_category else 20,
        )
        if self.mem0_parity_rerank_config.enabled:
            scored = rerank_with_mem0_parity(
                query,
                scored,
                self.mem0_parity_rerank_config,
                question_category=question_category,
            )
        if self.semantic_rerank_config.enabled:
            scored = rerank_benchmark_candidates_semantically(
                query,
                scored,
                self.semantic_rerank_config,
                self._load_semantic_model(),
                self._semantic_embedding_cache,
            )
        if self.cross_encoder_rerank_config.enabled:
            scored = rerank_benchmark_candidates_with_cross_encoder(
                query,
                scored,
                self.cross_encoder_rerank_config,
                self._load_cross_encoder_model(),
                question_category=question_category,
            )
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
                        "timestamp": metadata.get("timestamp"),
                        "source_ids": [str(value) for value in metadata.get("source_ids") or []],
                        "observation_kind": metadata.get("observation_kind"),
                    },
                }
            )
        return results
