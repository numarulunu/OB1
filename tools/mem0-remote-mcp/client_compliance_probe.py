from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from audit_ledger import AuditLedger


WRITE_ACTIONS = {'save', 'update'}


def build_canary_exchange(origin: str, phrase: str) -> list[dict[str, str]]:
    clean_origin = ' '.join(str(origin or 'client').strip().lower().split()) or 'client'
    clean_phrase = ' '.join(str(phrase or 'memory compliance canary').strip().split()) or 'memory compliance canary'
    return [
        {'role': 'user', 'content': f'Mem0 compliance canary for {clean_origin}: {clean_phrase}.'},
        {'role': 'assistant', 'content': f'Acknowledged the {clean_origin} Mem0 compliance canary.'},
    ]


def row_has_error(row: dict[str, Any]) -> bool:
    errors = row.get('errors')
    if isinstance(errors, list):
        return any(str(error).strip() for error in errors)
    if isinstance(errors, str):
        return bool(errors.strip())
    return bool(str(row.get('error') or '').strip())


def normalize_origin(value: str) -> str:
    return str(value or '').strip().lower()


def build_compliance_report(
    audit_log: str | Path,
    expected_origins: list[str],
    recent_limit: int = 5000,
) -> dict[str, Any]:
    ledger = AuditLedger(audit_log)
    rows = ledger.iter_recent(limit=recent_limit)
    expected = [normalize_origin(origin) for origin in expected_origins if normalize_origin(origin)]
    by_origin: dict[str, dict[str, Any]] = {
        origin: {'status': 'missing', 'latest_ts': '', 'actions': {}, 'rows': 0, 'errors': 0}
        for origin in expected
    }
    for row in rows:
        origin = normalize_origin(row.get('origin') or 'unknown') or 'unknown'
        if expected and origin not in by_origin:
            continue
        summary = by_origin.setdefault(origin, {'status': 'missing', 'latest_ts': '', 'actions': {}, 'rows': 0, 'errors': 0})
        action = str(row.get('action') or 'unknown').strip().lower() or 'unknown'
        ts = str(row.get('ts') or '')
        summary['rows'] += 1
        summary['actions'][action] = summary['actions'].get(action, 0) + 1
        if ts and ts > summary['latest_ts']:
            summary['latest_ts'] = ts
        if row_has_error(row):
            summary['errors'] += 1
    for summary in by_origin.values():
        actions = set(summary['actions'])
        if summary['errors']:
            summary['status'] = 'error'
        elif actions & WRITE_ACTIONS:
            summary['status'] = 'writing'
        elif summary['rows']:
            summary['status'] = 'seen_no_write'
        else:
            summary['status'] = 'missing'
    return {
        'audit_log': str(audit_log),
        'recent_limit': recent_limit,
        'summary': {
            'expected_origins': expected,
            'missing': [origin for origin, data in by_origin.items() if data['status'] == 'missing'],
            'error_origins': [origin for origin, data in by_origin.items() if data['status'] == 'error'],
            'writing_origins': [origin for origin, data in by_origin.items() if data['status'] == 'writing'],
        },
        'origins': by_origin,
    }


def format_report(report: dict[str, Any]) -> str:
    lines = ['Mem0 client compliance']
    summary = report.get('summary') or {}
    lines.append(f"expected: {', '.join(summary.get('expected_origins') or [])}")
    lines.append(f"writing: {', '.join(summary.get('writing_origins') or []) or 'none'}")
    lines.append(f"missing: {', '.join(summary.get('missing') or []) or 'none'}")
    lines.append(f"errors: {', '.join(summary.get('error_origins') or []) or 'none'}")
    for origin, data in sorted((report.get('origins') or {}).items()):
        lines.append(
            f"{origin}: {data.get('status')} rows={data.get('rows', 0)} "
            f"actions={json.dumps(data.get('actions') or {}, sort_keys=True)} latest={data.get('latest_ts') or 'n/a'}"
        )
    return '\n'.join(lines) + '\n'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Check whether real clients are writing Mem0 ingestion audit rows.')
    parser.add_argument('--audit-log', required=True, help='Path to hosted MCP audit JSONL file')
    parser.add_argument('--expected-origin', action='append', default=[], help='Expected client origin, repeatable')
    parser.add_argument('--recent-limit', type=int, default=5000, help='Recent audit rows to scan')
    parser.add_argument('--json', action='store_true', help='Print JSON report')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected = args.expected_origin or ['chatgpt', 'codex', 'claude']
    report = build_compliance_report(args.audit_log, expected_origins=expected, recent_limit=args.recent_limit)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(format_report(report), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
