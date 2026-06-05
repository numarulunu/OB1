
from __future__ import annotations

import argparse
import json
import os
import sys

from core import Mem0Client, Mem0Config
from memory_snapshot import snapshot_memories


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Write a safe Mem0 memory snapshot for lifecycle planning.')
    parser.add_argument('--output', required=True, help='Snapshot JSON output path')
    parser.add_argument('--page-size', type=int, default=100)
    parser.add_argument('--max-pages', type=int, default=100)
    parser.add_argument('--include-text', action='store_true', help='Include full memory text in the private snapshot file')
    return parser.parse_args()


def make_client() -> Mem0Client:
    api_key = os.environ.get('MEM0_API_KEY') or os.environ.get('MEM0_API_KEY_CODEX')
    if not api_key:
        raise RuntimeError('MEM0_API_KEY or MEM0_API_KEY_CODEX is required')
    return Mem0Client(
        Mem0Config(
            base_url=os.environ.get('MEM0_BASE_URL', 'http://127.0.0.1:18888'),
            api_key=api_key,
            user_id=os.environ.get('MEM0_USER_ID', 'ionut'),
            client_name='memory-snapshot-cli',
            lexical_database_url=os.environ.get('MEM0_LEXICAL_DATABASE_URL', ''),
        )
    )


def main() -> int:
    args = parse_args()
    try:
        summary = snapshot_memories(make_client(), args.output, page_size=args.page_size, max_pages=args.max_pages, include_text=args.include_text)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
