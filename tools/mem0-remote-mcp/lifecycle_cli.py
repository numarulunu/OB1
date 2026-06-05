
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from core import Mem0Client, Mem0Config
from lifecycle import (
    LifecyclePolicy,
    build_access_index,
    build_decay_plan,
    execute_lifecycle_plan,
    load_memories_json,
)
from memory_snapshot import snapshot_memories


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run hosted Mem0 lifecycle maintenance jobs. Default mode is dry-run.')
    parser.add_argument('--job', choices=['decay_low_use'], default='decay_low_use')
    parser.add_argument('--memories-json', default='', help='JSON list/export of sanitized memories to evaluate')
    parser.add_argument('--live-snapshot', action='store_true', help='Fetch a safe live Mem0 snapshot before planning')
    parser.add_argument('--snapshot-output', default='/data/mem0-mcp-lifecycle-snapshot.json', help='Live snapshot output path')
    parser.add_argument('--snapshot-page-size', type=int, default=100)
    parser.add_argument('--snapshot-max-pages', type=int, default=100)
    parser.add_argument('--include-snapshot-text', action='store_true', help='Include full memory text in the private live snapshot')
    parser.add_argument('--telemetry-log', required=True, help='Retrieval telemetry JSONL path')
    parser.add_argument('--output', required=True, help='Path to write the lifecycle plan/result JSON')
    parser.add_argument('--execute', action='store_true', help='Apply mutating lifecycle actions')
    parser.add_argument('--max-updates', type=int, default=None, help='Maximum update/downgrade/promote actions allowed when executing')
    parser.add_argument('--rollback-log', default='/data/mem0-mcp-lifecycle-rollback.jsonl')
    parser.add_argument('--maintenance-log', default='/data/mem0-mcp-lifecycle-maintenance.jsonl')
    parser.add_argument('--active-to-historical-epochs', type=int, default=200)
    parser.add_argument('--historical-to-cold-epochs', type=int, default=1000)
    parser.add_argument('--promote-after-access-count', type=int, default=20)
    args = parser.parse_args()
    if not args.memories_json and not args.live_snapshot:
        parser.error('--memories-json is required unless --live-snapshot is set')
    if args.execute and args.max_updates is None:
        parser.error('--max-updates is required with --execute')
    return args


def build_policy(args: argparse.Namespace) -> LifecyclePolicy:
    return LifecyclePolicy(
        active_to_historical_epochs=max(args.active_to_historical_epochs, 1),
        historical_to_cold_epochs=max(args.historical_to_cold_epochs, 1),
        promote_after_access_count=max(args.promote_after_access_count, 1),
    )


def make_client() -> Mem0Client:
    api_key = os.environ.get('MEM0_API_KEY') or os.environ.get('MEM0_API_KEY_CODEX')
    if not api_key:
        raise RuntimeError('MEM0_API_KEY or MEM0_API_KEY_CODEX is required for --execute')
    return Mem0Client(
        Mem0Config(
            base_url=os.environ.get('MEM0_BASE_URL', 'http://127.0.0.1:18888'),
            api_key=api_key,
            user_id=os.environ.get('MEM0_USER_ID', 'ionut'),
            client_name='lifecycle-cli',
            lexical_database_url=os.environ.get('MEM0_LEXICAL_DATABASE_URL', ''),
        )
    )


def main() -> int:
    args = parse_args()
    try:
        memories_json = args.memories_json
        snapshot_summary = None
        if args.live_snapshot:
            snapshot_summary = snapshot_memories(
                make_client(),
                args.snapshot_output,
                page_size=args.snapshot_page_size,
                max_pages=args.snapshot_max_pages,
                include_text=args.include_snapshot_text,
            )
            memories_json = args.snapshot_output
        memories = load_memories_json(memories_json)
    except Exception as exc:
        import sys
        print(str(exc), file=sys.stderr)
        return 1
    access_index = build_access_index(args.telemetry_log)
    plan = build_decay_plan(memories, access_index, build_policy(args))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.execute:
        result = execute_lifecycle_plan(
            plan,
            make_client(),
            rollback_log=args.rollback_log,
            maintenance_log=args.maintenance_log,
            max_updates=args.max_updates,
        )
        payload = {'plan': plan.to_dict(), 'result': result}
        if snapshot_summary:
            payload['snapshot'] = snapshot_summary
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'job': plan.job, 'executed': True, 'counts': result.get('counts', {})}, sort_keys=True))
        return 0

    payload = plan.to_dict()
    if snapshot_summary:
        payload['snapshot'] = snapshot_summary
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    stdout = {'job': plan.job, 'dry_run': True, 'summary': payload['summary']}
    if snapshot_summary:
        stdout['snapshot_count'] = snapshot_summary['count']
    print(json.dumps(stdout, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
