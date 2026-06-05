
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_FILTER_KEYS = ('domains', 'memory_tiers', 'memory_types', 'current_statuses')


@dataclass
class RetrievalStats:
    rows_seen: int = 0
    malformed_rows: int = 0
    origins: dict[str, int] = field(default_factory=dict)
    result_count_total: int = 0
    zero_result_count: int = 0
    avg_latency_ms: float = 0.0
    last_success_by_origin: dict[str, str] = field(default_factory=dict)
    top_result_ids: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def stable_hash(value: Any) -> str:
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()


def compact_text(value: Any, max_len: int = 160) -> str:
    collapsed = ' '.join(str(value or '').split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + '...'


def sanitize_filters(filters: dict[str, Any] | None) -> dict[str, list[str]]:
    if not isinstance(filters, dict):
        return {}
    clean: dict[str, list[str]] = {}
    for key in SAFE_FILTER_KEYS:
        value = filters.get(key)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        normalized = [compact_text(item, 80) for item in values if compact_text(item, 80)]
        if normalized:
            clean[key] = normalized
    return clean


def summarize_results(results: list[dict[str, Any]] | None, limit: int = 20) -> list[dict[str, str]]:
    summary = []
    for row in (results or [])[:limit]:
        if not isinstance(row, dict):
            continue
        item: dict[str, str] = {}
        memory_id = compact_text(row.get('id'), 120)
        title = compact_text(row.get('title'), 160)
        if memory_id:
            item['id'] = memory_id
        if title:
            item['title'] = title
        if item:
            summary.append(item)
    return summary


class RetrievalTelemetry:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None

    def log_search(
        self,
        *,
        origin: str,
        query: str,
        filters: dict[str, Any] | None,
        results: list[dict[str, Any]] | None,
        latency_ms: float,
    ) -> bool:
        if not self.path:
            return False
        row = {
            'ts': utc_now_iso(),
            'event': 'search',
            'origin': compact_text(origin, 80) or 'unknown',
            'query_hash': stable_hash(query),
            'query_preview': compact_text(query, 160),
            'filters': sanitize_filters(filters),
            'result_count': len(results or []),
            'results': summarize_results(results),
            'latency_ms': round(float(latency_ms or 0), 3),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
        except OSError:
            return False
        return True


def read_recent_jsonl(path: str | Path, recent_limit: int = 5000) -> tuple[list[dict[str, Any]], int]:
    path = Path(path)
    if not path.exists():
        return [], 0
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        return [], 0
    parsed: list[dict[str, Any]] = []
    malformed = 0
    for line in lines[-max(int(recent_limit or 0), 0):]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(row, dict):
            parsed.append(row)
        else:
            malformed += 1
    return parsed, malformed


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_retrieval_stats(path: str | Path, recent_limit: int = 5000) -> RetrievalStats:
    rows, malformed = read_recent_jsonl(path, recent_limit=recent_limit)
    origins: Counter[str] = Counter()
    result_ids: Counter[str] = Counter()
    last_success_by_origin: dict[str, str] = {}
    latency_values = []
    result_count_total = 0
    zero_result_count = 0

    for row in rows:
        origin = compact_text(row.get('origin'), 80).lower() or 'unknown'
        ts = str(row.get('ts') or '')
        result_count = safe_int(row.get('result_count'))
        latency = safe_float(row.get('latency_ms'))
        origins[origin] += 1
        result_count_total += result_count
        if result_count == 0:
            zero_result_count += 1
        if latency:
            latency_values.append(latency)
        if ts:
            last_success_by_origin[origin] = ts
        for result in row.get('results') or []:
            if not isinstance(result, dict):
                continue
            memory_id = compact_text(result.get('id'), 120)
            if memory_id:
                result_ids[memory_id] += 1

    avg_latency_ms = round(sum(latency_values) / len(latency_values), 3) if latency_values else 0.0
    return RetrievalStats(
        rows_seen=len(rows),
        malformed_rows=malformed,
        origins=dict(origins),
        result_count_total=result_count_total,
        zero_result_count=zero_result_count,
        avg_latency_ms=avg_latency_ms,
        last_success_by_origin=last_success_by_origin,
        top_result_ids=dict(result_ids.most_common(20)),
    )
