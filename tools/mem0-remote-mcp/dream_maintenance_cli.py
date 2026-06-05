from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core import Mem0Client, Mem0Config
from dream_maintenance import build_maintenance_plan, execute_maintenance_plan, load_maintained_group_keys, parse_audit_flags


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build or execute a safe Mem0 dream-maintenance plan from ingestion audit flags.')
    parser.add_argument('--audit-log', required=True, help='Path to hosted MCP ingestion audit JSONL')
    parser.add_argument('--output', required=True, help='Path to write dry-run maintenance plan JSON')
    parser.add_argument('--limit-groups', type=int, default=0, help='Limit number of flag groups processed')
    parser.add_argument('--execute', action='store_true', help='Execute the generated plan against Mem0')
    parser.add_argument('--max-deletes', type=int, default=None, help='Required delete cap when --execute would delete memories')
    parser.add_argument('--rollback-log', default='', help='Rollback JSONL path for live execution')
    parser.add_argument('--maintenance-log', default='', help='Maintenance result JSONL path for live execution')
    parser.add_argument('--mem0-base-url', default=os.environ.get('MEM0_BASE_URL', 'http://127.0.0.1:18888'))
    parser.add_argument('--mem0-api-key-env', default='MEM0_API_KEY', help='Environment variable containing Mem0 API key')
    parser.add_argument('--mem0-user-id', default=os.environ.get('MEM0_USER_ID', 'ionut'))
    parser.add_argument('--use-llm', action='store_true', help='Reserved for future LLM consolidation; not used by deterministic dry-run')
    parser.add_argument('--model', default='', help='Reserved model name for future LLM consolidation')
    return parser.parse_args()


def write_json(path: str | Path, payload: dict) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_plan_payload(args: argparse.Namespace) -> dict:
    maintained = load_maintained_group_keys(args.maintenance_log) if args.maintenance_log else set()
    groups, skipped = parse_audit_flags(args.audit_log, maintained_group_keys=maintained)
    if args.limit_groups and args.limit_groups > 0:
        groups = groups[: args.limit_groups]
    plan = build_maintenance_plan(groups, skipped_rows=skipped)
    return plan.to_dict()


def make_client(args: argparse.Namespace) -> Mem0Client:
    api_key = os.environ.get(args.mem0_api_key_env, '').strip()
    if not api_key:
        raise RuntimeError(f'missing Mem0 API key env: {args.mem0_api_key_env}')
    return Mem0Client(Mem0Config(base_url=args.mem0_base_url, api_key=api_key, user_id=args.mem0_user_id, client_name='dream-maintenance'))


def main() -> int:
    args = parse_args()
    payload = build_plan_payload(args)
    write_json(args.output, payload)
    delete_count = sum(1 for action in payload['actions'] if action.get('action') == 'delete')
    if args.execute and delete_count and args.max_deletes is None:
        print('--max-deletes is required when --execute would delete memories', file=sys.stderr)
        return 2
    if args.execute:
        from dream_maintenance import MaintenanceAction, MaintenancePlan

        plan = MaintenancePlan(actions=[MaintenanceAction(**action) for action in payload['actions']], skipped_rows=payload.get('skipped_rows', 0), dry_run=False)
        rollback_log = args.rollback_log or str(Path(args.output).with_suffix('.rollback.jsonl'))
        maintenance_log = args.maintenance_log or str(Path(args.output).with_suffix('.maintenance.jsonl'))
        result = execute_maintenance_plan(plan, make_client(args), rollback_log=rollback_log, maintenance_log=maintenance_log, max_deletes=args.max_deletes)
        print(json.dumps({'executed': True, **result}, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps({'dry_run': True, 'output': str(args.output), 'actions': len(payload['actions']), 'delete_actions': delete_count}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
