#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _counter_dict(counter: Counter) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((len(values) - 1) * percentile)))
    return round(values[index], 1)


_FEATURE_FLAGS = ("temporal", "current_state", "autobiographical")
_REPEATED_QUERY_TOP_N = 5


def _empty_feature_breakdown() -> dict[str, Any]:
    return {
        "total": 0,
        "length_buckets": Counter(),
        **{flag: 0 for flag in _FEATURE_FLAGS},
    }


def _finalize_feature_breakdown(breakdown: dict[str, Any], *, threshold_ms: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"total": int(breakdown.get("total") or 0)}
    if threshold_ms is not None:
        result["threshold_ms"] = round(float(threshold_ms), 1)
    for flag in _FEATURE_FLAGS:
        result[flag] = int(breakdown.get(flag) or 0)
    result["length_buckets"] = _counter_dict(breakdown.get("length_buckets") or Counter())
    return result


def _accumulate_feature_breakdown(breakdown: dict[str, Any], features: dict[str, Any]) -> None:
    breakdown["total"] = int(breakdown.get("total") or 0) + 1
    bucket = str(features.get("length_bucket") or "")
    if bucket:
        breakdown["length_buckets"][bucket] += 1
    for flag in _FEATURE_FLAGS:
        if bool(features.get(flag)):
            breakdown[flag] = int(breakdown.get(flag) or 0) + 1


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profiles: Counter = Counter()
    services: Counter = Counter()
    domains: Counter = Counter()
    length_buckets: Counter = Counter()
    top_k_values: Counter = Counter()
    query_hashes: Counter = Counter()
    feature_counts: Counter = Counter()
    latencies: list[float] = []
    zero_result_count = 0
    rows_with_top_k = 0
    rows_with_query_features = 0
    prepared: list[tuple[dict[str, Any], dict[str, Any], float, bool]] = []
    zero_breakdown = _empty_feature_breakdown()

    for row in rows:
        profiles[str(row.get("profile") or "unknown")] += 1
        services[str(row.get("service") or "unknown")] += 1
        query_hash = str(row.get("query_hash") or "")
        if query_hash:
            query_hashes[query_hash] += 1

        filters = _as_dict(row.get("filters"))
        for domain in filters.get("domains") or []:
            if str(domain or "").strip():
                domains[str(domain)] += 1
        if filters.get("top_k") is not None:
            rows_with_top_k += 1
            top_k_values[str(filters.get("top_k"))] += 1

        features = _as_dict(filters.get("query_features"))
        if features:
            rows_with_query_features += 1
        bucket = str(features.get("length_bucket") or "")
        if bucket:
            length_buckets[bucket] += 1
        for flag in _FEATURE_FLAGS:
            if bool(features.get(flag)):
                feature_counts[flag] += 1

        latency = 0.0
        try:
            latency = float(row.get("latency_ms") or 0.0)
        except (TypeError, ValueError):
            latency = 0.0
        latencies.append(latency)

        zero_result = not _as_list(row.get("result_external_ids"))
        if zero_result:
            zero_result_count += 1
            _accumulate_feature_breakdown(zero_breakdown, features)
        prepared.append((row, features, latency, zero_result))

    duplicate_query_hashes = sum(count - 1 for count in query_hashes.values() if count > 1)
    repeated_query_top_n = [
        {"query_hash": query_hash, "count": int(count)}
        for query_hash, count in query_hashes.most_common(_REPEATED_QUERY_TOP_N)
        if int(count) > 1
    ]

    p95_latency = _percentile(latencies, 0.95)
    high_latency_threshold = p95_latency if p95_latency > 0 else 0.0
    high_breakdown = _empty_feature_breakdown()
    if high_latency_threshold > 0 and prepared:
        for _row, features, latency, _zero in prepared:
            if latency >= high_latency_threshold:
                _accumulate_feature_breakdown(high_breakdown, features)

    return {
        "ok": True,
        "mode": "retrieval-shadow-report",
        "rows_seen": len(rows),
        "unique_query_hashes": len(query_hashes),
        "duplicate_query_hashes": duplicate_query_hashes,
        "zero_result_count": zero_result_count,
        "zero_result_rate": round(zero_result_count / len(rows), 4) if rows else 0.0,
        "profiles": _counter_dict(profiles),
        "services": _counter_dict(services),
        "requested_domains": _counter_dict(domains),
        "telemetry_coverage": {
            "rows_with_query_features": rows_with_query_features,
            "rows_with_top_k": rows_with_top_k,
        },
        "top_k": _counter_dict(top_k_values),
        "length_buckets": _counter_dict(length_buckets),
        "query_features": _counter_dict(feature_counts),
        "latency_ms": {
            "avg": round(mean(latencies), 1) if latencies else 0.0,
            "p95": p95_latency,
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
        "zero_result_features": _finalize_feature_breakdown(zero_breakdown),
        "high_latency_features": _finalize_feature_breakdown(
            high_breakdown, threshold_ms=high_latency_threshold
        ),
        "repeated_query_top_n": repeated_query_top_n,
    }


def load_rows(database_url: str, *, limit: int, hours: int | None) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if hours is not None:
        where = "WHERE created_at >= now() - (%s::text || ' hours')::interval"
        params.append(int(hours))
    params.append(min(max(int(limit or 500), 1), 10000))
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT query_hash, service, profile, filters, result_external_ids, latency_ms, created_at
                FROM retrieval_queries
                {where}
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize sanitized Kontext retrieval shadow telemetry.")
    parser.add_argument("--database-url", default=os.environ.get("KONTEXT_V2_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args(argv)

    if not args.database_url:
        raise SystemExit("database URL is required")
    report = build_report(load_rows(args.database_url, limit=args.limit, hours=args.hours))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).expanduser().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
