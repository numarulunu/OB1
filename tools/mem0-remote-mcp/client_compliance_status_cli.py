from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from audit_ledger import AuditLedger
from hook_heartbeat import build_hook_stats
from retrieval_telemetry import build_retrieval_stats


def normalize_origin(value: Any) -> str:
    return str(value or '').strip().lower()


def audit_origin_summary(path: str | Path, recent_limit: int = 5000) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for row in AuditLedger(path).iter_recent(limit=recent_limit):
        origin = normalize_origin(row.get('origin') or 'unknown') or 'unknown'
        action = str(row.get('action') or 'unknown').strip().lower() or 'unknown'
        ts = str(row.get('ts') or '')
        item = summary.setdefault(origin, {'rows': 0, 'actions': {}, 'latest_ts': '', 'errors': 0})
        item['rows'] += 1
        item['actions'][action] = item['actions'].get(action, 0) + 1
        if ts and ts > item['latest_ts']:
            item['latest_ts'] = ts
        errors = row.get('errors')
        if isinstance(errors, list):
            item['errors'] += len([error for error in errors if str(error).strip()])
        elif str(errors or row.get('error') or '').strip():
            item['errors'] += 1
    return summary


def classify_client(*, hook_seen: bool, search_seen: bool, ingest_seen: bool, errors: int = 0) -> str:
    if errors:
        return 'error'
    if hook_seen and search_seen and ingest_seen:
        return 'healthy'
    if hook_seen and not search_seen and not ingest_seen:
        return 'hook_only'
    if search_seen and not hook_seen and not ingest_seen:
        return 'search_only'
    if ingest_seen and not hook_seen and not search_seen:
        return 'write_only'
    if hook_seen or search_seen or ingest_seen:
        return 'partial'
    return 'missing'


def build_client_status_report(
    *,
    audit_log: str | Path,
    retrieval_log: str | Path,
    hook_log: str | Path,
    expected_origins: list[str],
    recent_limit: int = 5000,
) -> dict[str, Any]:
    expected = [origin for origin in [normalize_origin(item) for item in expected_origins] if origin]
    hook_stats = build_hook_stats(hook_log, recent_limit=recent_limit).to_dict()
    retrieval_stats = build_retrieval_stats(retrieval_log, recent_limit=recent_limit).to_dict()
    audit_summary = audit_origin_summary(audit_log, recent_limit=recent_limit)
    all_origins = sorted(set(expected) | set(hook_stats['origins']) | set(retrieval_stats['origins']) | set(audit_summary))
    origins: dict[str, dict[str, Any]] = {}
    for origin in all_origins:
        audit = audit_summary.get(origin, {'rows': 0, 'actions': {}, 'latest_ts': '', 'errors': 0})
        hook_seen = bool((hook_stats['origins'] or {}).get(origin))
        search_seen = bool((retrieval_stats['origins'] or {}).get(origin))
        ingest_seen = bool(audit.get('rows'))
        errors = int(audit.get('errors') or 0)
        origins[origin] = {
            'status': classify_client(hook_seen=hook_seen, search_seen=search_seen, ingest_seen=ingest_seen, errors=errors),
            'hook_seen': hook_seen,
            'search_seen': search_seen,
            'ingest_seen': ingest_seen,
            'hook_count': int((hook_stats['origins'] or {}).get(origin) or 0),
            'search_count': int((retrieval_stats['origins'] or {}).get(origin) or 0),
            'ingest_rows': int(audit.get('rows') or 0),
            'ingest_actions': audit.get('actions') or {},
            'latest_hook_ts': (hook_stats.get('last_seen_by_origin') or {}).get(origin, ''),
            'latest_search_ts': (retrieval_stats.get('last_success_by_origin') or {}).get(origin, ''),
            'latest_ingest_ts': audit.get('latest_ts') or '',
            'errors': errors,
        }
    counts: dict[str, int] = {}
    for data in origins.values():
        status = data['status']
        counts[status] = counts.get(status, 0) + 1
    return {
        'ok': all(data['status'] not in {'missing', 'error'} for origin, data in origins.items() if not expected or origin in expected),
        'recent_limit': recent_limit,
        'summary': {'expected_origins': expected, 'status_counts': counts},
        'origins': origins,
        'logs': {
            'audit_log': str(audit_log),
            'retrieval_log': str(retrieval_log),
            'hook_log': str(hook_log),
        },
    }


def format_client_status_report(report: dict[str, Any]) -> str:
    lines = ['Mem0 client status']
    lines.append(f"ok: {str(report.get('ok')).lower()}")
    lines.append(f"expected: {', '.join((report.get('summary') or {}).get('expected_origins') or [])}")
    lines.append(f"status counts: {json.dumps((report.get('summary') or {}).get('status_counts') or {}, sort_keys=True)}")
    for origin, data in sorted((report.get('origins') or {}).items()):
        lines.append(
            f"{origin}: {data.get('status')} hooks={data.get('hook_count', 0)} "
            f"searches={data.get('search_count', 0)} ingestion_rows={data.get('ingest_rows', 0)} "
            f"actions={json.dumps(data.get('ingest_actions') or {}, sort_keys=True)}"
        )
    return '\n'.join(lines) + '\n'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Summarize Mem0 hook, search, and ingestion health by client.')
    parser.add_argument('--audit-log', required=True, help='Path to mem0 MCP audit JSONL file')
    parser.add_argument('--retrieval-log', required=True, help='Path to mem0 MCP retrieval JSONL file')
    parser.add_argument('--hook-log', required=True, help='Path to mem0 MCP hook heartbeat JSONL file')
    parser.add_argument('--expected-origin', action='append', default=[], help='Expected origin, repeatable')
    parser.add_argument('--recent-limit', type=int, default=5000, help='Recent rows to scan per log')
    parser.add_argument('--json', action='store_true', help='Print JSON report')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_client_status_report(
        audit_log=args.audit_log,
        retrieval_log=args.retrieval_log,
        hook_log=args.hook_log,
        expected_origins=args.expected_origin or ['chatgpt', 'codex', 'claude', 'perplexity'],
        recent_limit=args.recent_limit,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(format_client_status_report(report), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
