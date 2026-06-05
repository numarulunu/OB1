from __future__ import annotations

import argparse
import json

from usage_report import build_usage_report, format_usage_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Show safe Mem0 usage counts across hooks, searches, and ingestion.')
    parser.add_argument('--audit-log', required=True, help='Path to mem0 MCP audit JSONL file')
    parser.add_argument('--retrieval-log', required=True, help='Path to mem0 MCP retrieval JSONL file')
    parser.add_argument('--hook-log', required=True, help='Path to mem0 MCP hook heartbeat JSONL file')
    parser.add_argument('--recent-limit', type=int, default=5000, help='Recent rows to scan per log')
    parser.add_argument('--json', action='store_true', help='Print JSON report')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_usage_report(
        audit_log=args.audit_log,
        retrieval_log=args.retrieval_log,
        hook_log=args.hook_log,
        recent_limit=args.recent_limit,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(format_usage_report(report), end='')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
