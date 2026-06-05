from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hook_heartbeat import build_hook_stats
from monitoring import build_audit_stats, extract_usage, read_recent_jsonl
from retrieval_telemetry import build_retrieval_stats


def normalize_origin(value: Any) -> str:
    return str(value or 'unknown').strip().lower() or 'unknown'


def empty_origin_row() -> dict[str, Any]:
    return {
        'hooks': 0,
        'searches': 0,
        'ingestion_rows': 0,
        'ingestion_actions': {},
        'prompt_tokens': 0,
        'completion_tokens': 0,
        'total_tokens': 0,
        'avg_search_latency_ms': 0.0,
        'search_result_count_total': 0,
    }


def add_usage(target: dict[str, Any], usage: dict[str, int]) -> None:
    prompt = int(usage.get('prompt_tokens') or 0)
    completion = int(usage.get('completion_tokens') or 0)
    total = int(usage.get('total_tokens') or (prompt + completion))
    target['prompt_tokens'] += prompt
    target['completion_tokens'] += completion
    target['total_tokens'] += total


def build_usage_report(
    *,
    audit_log: str | Path,
    retrieval_log: str | Path,
    hook_log: str | Path,
    recent_limit: int = 5000,
) -> dict[str, Any]:
    origins: dict[str, dict[str, Any]] = {}
    hook_stats = build_hook_stats(hook_log, recent_limit=recent_limit)
    retrieval_stats = build_retrieval_stats(retrieval_log, recent_limit=recent_limit)
    audit_stats = build_audit_stats(audit_log, recent_limit=recent_limit)

    for origin, count in hook_stats.origins.items():
        row = origins.setdefault(normalize_origin(origin), empty_origin_row())
        row['hooks'] += int(count or 0)

    retrieval_rows, _ = read_recent_jsonl(retrieval_log, recent_limit=recent_limit)
    latency_values: dict[str, list[float]] = {}
    for event in retrieval_rows:
        origin = normalize_origin(event.get('origin'))
        row = origins.setdefault(origin, empty_origin_row())
        row['searches'] += 1
        try:
            row['search_result_count_total'] += int(event.get('result_count') or 0)
        except (TypeError, ValueError):
            pass
        try:
            latency = float(event.get('latency_ms') or 0)
        except (TypeError, ValueError):
            latency = 0.0
        if latency:
            latency_values.setdefault(origin, []).append(latency)
    for origin, values in latency_values.items():
        if values:
            origins[origin]['avg_search_latency_ms'] = round(sum(values) / len(values), 3)

    audit_rows, _ = read_recent_jsonl(audit_log, recent_limit=recent_limit)
    for event in audit_rows:
        origin = normalize_origin(event.get('origin'))
        action = str(event.get('action') or 'unknown').strip().lower() or 'unknown'
        row = origins.setdefault(origin, empty_origin_row())
        row['ingestion_rows'] += 1
        row['ingestion_actions'][action] = row['ingestion_actions'].get(action, 0) + 1
        add_usage(row, extract_usage(event))

    summary = {
        'hooks': hook_stats.rows_seen,
        'searches': retrieval_stats.rows_seen,
        'ingestion_rows': audit_stats.rows_seen,
        'prompt_tokens': audit_stats.usage.get('prompt_tokens', 0),
        'completion_tokens': audit_stats.usage.get('completion_tokens', 0),
        'total_tokens': audit_stats.usage.get('total_tokens', 0),
        'malformed_rows': hook_stats.malformed_rows + retrieval_stats.malformed_rows + audit_stats.malformed_rows,
    }
    return {
        'ok': audit_stats.error_count == 0,
        'recent_limit': recent_limit,
        'summary': summary,
        'origins': dict(sorted(origins.items())),
        'logs': {
            'audit_log': str(audit_log),
            'retrieval_log': str(retrieval_log),
            'hook_log': str(hook_log),
        },
    }


def format_usage_report(report: dict[str, Any]) -> str:
    summary = report.get('summary') or {}
    lines = [
        'Mem0 usage report',
        f"ok: {str(report.get('ok')).lower()}",
        f"hooks: {summary.get('hooks', 0)}",
        f"searches: {summary.get('searches', 0)}",
        f"ingestion rows: {summary.get('ingestion_rows', 0)}",
        f"total tokens: {summary.get('total_tokens', 0)}",
        f"prompt tokens: {summary.get('prompt_tokens', 0)}",
        f"completion tokens: {summary.get('completion_tokens', 0)}",
        f"malformed rows: {summary.get('malformed_rows', 0)}",
    ]
    for origin, data in sorted((report.get('origins') or {}).items()):
        lines.append(
            f"{origin}: hooks={data.get('hooks', 0)} searches={data.get('searches', 0)} "
            f"ingestion_rows={data.get('ingestion_rows', 0)} total_tokens={data.get('total_tokens', 0)} "
            f"actions={json.dumps(data.get('ingestion_actions') or {}, sort_keys=True)}"
        )
    return '\n'.join(lines) + '\n'
