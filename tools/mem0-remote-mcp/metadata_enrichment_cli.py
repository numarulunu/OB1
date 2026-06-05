from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core import Mem0Client, Mem0Config
from memory_snapshot import snapshot_memories
from metadata_enrichment import (
    MetadataEnrichmentAction,
    MetadataEnrichmentPlan,
    build_metadata_enrichment_plan,
    execute_metadata_enrichment_plan,
    load_memories_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build or execute safe Mem0 metadata enrichment plans. Default mode is dry-run.')
    parser.add_argument('--memories-json', default='', help='JSON list/export of sanitized memories to evaluate')
    parser.add_argument('--live-snapshot', action='store_true', help='Fetch a live Mem0 snapshot before planning')
    parser.add_argument('--snapshot-output', default='/data/mem0-mcp-metadata-snapshot.json')
    parser.add_argument('--snapshot-page-size', type=int, default=100)
    parser.add_argument('--snapshot-max-pages', type=int, default=100)
    parser.add_argument('--output', required=True, help='Path to write the metadata enrichment plan/result JSON')
    parser.add_argument('--execute', action='store_true', help='Apply update_type actions')
    parser.add_argument('--max-updates', type=int, default=None, help='Maximum metadata updates allowed when executing')
    parser.add_argument('--rollback-log', default='/data/mem0-mcp-metadata-rollback.jsonl')
    parser.add_argument('--maintenance-log', default='/data/mem0-mcp-metadata-maintenance.jsonl')
    parser.add_argument('--mem0-base-url', default=os.environ.get('MEM0_BASE_URL', 'http://127.0.0.1:18888'))
    parser.add_argument('--mem0-api-key-env', default='MEM0_API_KEY')
    parser.add_argument('--mem0-user-id', default=os.environ.get('MEM0_USER_ID', 'ionut'))
    args = parser.parse_args()
    if not args.memories_json and not args.live_snapshot:
        parser.error('--memories-json is required unless --live-snapshot is set')
    if args.execute and args.max_updates is None:
        parser.error('--max-updates is required with --execute')
    return args


def write_json(path: str | Path, payload: dict) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def make_client(args: argparse.Namespace, client_name: str = 'metadata-enrichment') -> Mem0Client:
    api_key = os.environ.get(args.mem0_api_key_env, '').strip()
    if not api_key:
        raise RuntimeError(f'missing Mem0 API key env: {args.mem0_api_key_env}')
    return Mem0Client(
        Mem0Config(
            base_url=args.mem0_base_url,
            api_key=api_key,
            user_id=args.mem0_user_id,
            client_name=client_name,
            lexical_database_url=os.environ.get('MEM0_LEXICAL_DATABASE_URL', ''),
        )
    )


def load_memories_for_args(args: argparse.Namespace) -> tuple[list[dict], dict | None]:
    memories_json = args.memories_json
    snapshot_summary = None
    if args.live_snapshot:
        snapshot_summary = snapshot_memories(
            make_client(args, client_name='metadata-snapshot'),
            args.snapshot_output,
            page_size=args.snapshot_page_size,
            max_pages=args.snapshot_max_pages,
            include_text=True,
        )
        memories_json = args.snapshot_output
    return load_memories_json(memories_json), snapshot_summary


def plan_from_payload(payload: dict) -> MetadataEnrichmentPlan:
    return MetadataEnrichmentPlan(
        actions=[MetadataEnrichmentAction(**action) for action in payload.get('actions') or []],
        dry_run=bool(payload.get('dry_run', True)),
        skipped_rows=int(payload.get('skipped_rows') or 0),
    )


def main() -> int:
    args = parse_args()
    try:
        memories, snapshot_summary = load_memories_for_args(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    plan = build_metadata_enrichment_plan(memories)
    payload = plan.to_dict()
    if snapshot_summary:
        payload['snapshot'] = snapshot_summary
    write_json(args.output, payload)

    update_count = payload['summary'].get('update_type', 0)
    if args.execute:
        result = execute_metadata_enrichment_plan(
            plan_from_payload(payload),
            make_client(args),
            rollback_log=args.rollback_log,
            maintenance_log=args.maintenance_log,
            max_updates=args.max_updates,
        )
        write_json(args.output, {'plan': payload, 'result': result})
        print(json.dumps({'executed': True, 'updates': result.get('counts', {}).get('updated', 0), 'failed': result.get('counts', {}).get('failed', 0)}, sort_keys=True))
        return 0

    print(json.dumps({'dry_run': True, 'output': str(args.output), 'updates': update_count, 'summary': payload['summary']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
