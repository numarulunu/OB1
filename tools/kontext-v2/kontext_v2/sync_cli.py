from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg

from kontext_v2.live_mem0 import Mem0ApiClient
from kontext_v2.mirror_sync import run_live_snapshot_sync
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


def read_refresh_id_file(path: str) -> list[str]:
    if not path:
        return []
    text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = [line.strip() for line in text.splitlines()]
    if isinstance(payload, dict):
        payload = payload.get("ids") or payload.get("missing_ids") or payload.get("refresh_ids") or []
    if isinstance(payload, str):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for value in payload:
        memory_id = str(value or "").strip()
        if memory_id and memory_id not in seen:
            seen.add(memory_id)
            output.append(memory_id)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a safe Mem0 -> Kontext V2 mirror sync.")
    parser.add_argument("--database-url", default=os.environ.get("KONTEXT_V2_DATABASE_URL", ""))
    parser.add_argument("--mem0-base-url", default=os.environ.get("MEM0_API_BASE_URL") or os.environ.get("MEM0_BASE_URL", ""))
    parser.add_argument("--mem0-api-key", default=os.environ.get("MEM0_API_KEY", ""))
    parser.add_argument("--mem0-user-id", default=os.environ.get("MEM0_USER_ID", ""))
    parser.add_argument("--mem0-lexical-database-url", default=os.environ.get("MEM0_LEXICAL_DATABASE_URL", ""))
    parser.add_argument("--cap", type=int, default=None)
    parser.add_argument(
        "--refresh-existing-limit",
        type=int,
        default=0,
        help="Also fetch this many existing Kontext external Mem0 IDs exactly, so stale rows outside the public snapshot can be refreshed.",
    )
    parser.add_argument(
        "--refresh-existing-offset",
        type=int,
        default=0,
        help="Offset into existing Kontext external Mem0 IDs for rolling exact refresh windows.",
    )
    parser.add_argument(
        "--refresh-id-file",
        default="",
        help="Plain-text or JSON list of exact Mem0 IDs to fetch and mirror, used for eval-assisted backfill.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply writes. Default is dry-run.")
    parser.add_argument("--source", default="mem0")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.database_url:
        raise RuntimeError("--database-url is required")
    if not args.mem0_base_url or not args.mem0_api_key or not args.mem0_user_id:
        raise RuntimeError("Mem0 base URL, API key, and user ID are required")
    client = Mem0ApiClient(
        base_url=args.mem0_base_url,
        api_key=args.mem0_api_key,
        user_id=args.mem0_user_id,
        lexical_database_url=getattr(args, "mem0_lexical_database_url", ""),
    )
    with psycopg.connect(args.database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        return run_live_snapshot_sync(
            repo,
            client,
            cap=args.cap,
            dry_run=not args.apply,
            source=args.source,
            refresh_existing_limit=getattr(args, "refresh_existing_limit", 0),
            refresh_existing_offset=getattr(args, "refresh_existing_offset", 0),
            refresh_ids=read_refresh_id_file(getattr(args, "refresh_id_file", "")),
        )


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps(run(args), default=str, sort_keys=True))


if __name__ == "__main__":
    main()
