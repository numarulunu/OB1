from __future__ import annotations

import argparse
import json
from pathlib import Path

from monitoring import build_status_payload


def format_human(payload: dict) -> str:
    audit = payload.get('audit') or {}
    usage = payload.get('usage') or {}
    maintenance = payload.get('maintenance') or {}
    lines = [
        'Mem0 ingestion status',
        f"ok: {str(payload.get('ok')).lower()}",
        f"ingestion enabled: {str((payload.get('ingestion') or {}).get('enabled')).lower()}",
        f"model: {(payload.get('ingestion') or {}).get('model') or ''}",
        f"rows seen: {audit.get('rows_seen', 0)}",
        f"malformed rows: {audit.get('malformed_rows', 0)}",
        f"pending flags: {audit.get('pending_flags', 0)}",
        f"errors: {audit.get('error_count', 0)}",
        f"actions: {json.dumps(audit.get('actions') or {}, sort_keys=True)}",
        f"origins: {json.dumps(audit.get('origins') or {}, sort_keys=True)}",
        f"usage: {json.dumps(usage, sort_keys=True)}",
        f"maintenance due: {str(maintenance.get('due', False)).lower()}",
        f"maintenance pending flags: {maintenance.get('pending_flags', 0)}",
    ]
    return '\n'.join(lines) + '\n'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Show safe aggregate status for the hosted Mem0 ingestion layer.')
    parser.add_argument('--audit-log', required=True, help='Path to mem0 MCP audit JSONL file')
    parser.add_argument('--json', action='store_true', help='Print JSON instead of human-readable text')
    parser.add_argument('--recent-limit', type=int, default=5000, help='Recent audit rows to scan')
    parser.add_argument('--model', default='', help='Configured ingestion model name')
    parser.add_argument('--ingestion-disabled', action='store_true', help='Report ingestion as disabled')
    parser.add_argument('--maintenance-flag-threshold', type=int, default=10, help='Pending flag count that marks maintenance as due')
    parser.add_argument('--maintenance-log', default='', help='Maintenance JSONL used to suppress already-maintained flag groups')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_status_payload(
        Path(args.audit_log),
        ingestion_enabled=not args.ingestion_disabled,
        ingestion_model=args.model,
        profiles=[],
        recent_limit=args.recent_limit,
        maintenance_flag_threshold=args.maintenance_flag_threshold,
        maintenance_log=Path(args.maintenance_log) if args.maintenance_log else None,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(format_human(payload), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
