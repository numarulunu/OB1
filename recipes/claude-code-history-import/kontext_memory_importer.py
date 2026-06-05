"""Import existing Kontext entries into Open Brain."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from claude_history_importer import ingest_thought_endpoint, ingest_thought_supabase


DEFAULT_KONTEXT_ROOT = Path.home() / "Desktop" / "Claude" / "Kontext"
DEFAULT_SYNC_LOG_PATH = Path(__file__).resolve().parent / "kontext-memory-sync-log.json"
LOG_PATH = Path(__file__).resolve().parent / "_import-kontext-memory.log"


def log_line(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{timestamp} {message}\n")


def load_sync_log(path: str | Path | None = None) -> dict[str, Any]:
    sync_path = Path(path) if path else DEFAULT_SYNC_LOG_PATH
    try:
        with sync_path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"ingested_ids": {}, "last_sync": ""}


def save_sync_log(log: dict[str, Any], path: str | Path | None = None) -> None:
    sync_path = Path(path) if path else DEFAULT_SYNC_LOG_PATH
    sync_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(log, handle, indent=2)


def open_kontext_connection(kontext_root: Path = DEFAULT_KONTEXT_ROOT):
    sys.path.insert(0, str(kontext_root))
    from db import KontextDB

    db = KontextDB()
    return db.conn


def rows_from_connection(conn, limit: int = 0) -> Iterable[dict[str, Any]]:
    sql = """
        select id, file, fact, source, grade, tier, created_at, updated_at, memory_type
        from entries
        where fact is not null and trim(fact) != ''
        order by id
    """
    if limit:
        sql += " limit ?"
        cursor = conn.execute(sql, (limit,))
    else:
        cursor = conn.execute(sql)

    for row in cursor:
        file_name = row["file"] if isinstance(row, sqlite3.Row) else row[1]
        fact = row["fact"] if isinstance(row, sqlite3.Row) else row[2]
        entry_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        source = row["source"] if isinstance(row, sqlite3.Row) else row[3]
        grade = row["grade"] if isinstance(row, sqlite3.Row) else row[4]
        tier = row["tier"] if isinstance(row, sqlite3.Row) else row[5]
        created_at = row["created_at"] if isinstance(row, sqlite3.Row) else row[6]
        updated_at = row["updated_at"] if isinstance(row, sqlite3.Row) else row[7]
        memory_type = row["memory_type"] if isinstance(row, sqlite3.Row) else row[8]
        yield {
            "content": f"[Kontext: {file_name}] {fact}",
            "embed_text": fact,
            "metadata": {
                "source": "kontext",
                "kontext_entry_id": entry_id,
                "kontext_file": file_name,
                "kontext_source": source,
                "grade": grade,
                "tier": tier,
                "memory_type": memory_type,
                "created_at": created_at,
                "updated_at": updated_at,
            },
        }


def validate_live_environment(args: Any) -> None:
    if args.ingest_endpoint:
        if not os.environ.get("INGEST_URL"):
            raise SystemExit("INGEST_URL is required with --ingest-endpoint.")
        if not os.environ.get("INGEST_KEY"):
            raise SystemExit("INGEST_KEY is required with --ingest-endpoint.")
    else:
        if not os.environ.get("SUPABASE_URL"):
            raise SystemExit("SUPABASE_URL is required for live Supabase import.")
        if not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
            raise SystemExit("SUPABASE_SERVICE_ROLE_KEY is required for live Supabase import.")
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required for live import embeddings.")


def export_rows(path: str | Path | None, rows: list[dict[str, Any]]) -> int:
    if not path:
        return 0
    export_path = Path(path)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    with export_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def run_import(args: Any, conn=None) -> dict[str, int]:
    if not args.dry_run:
        validate_live_environment(args)
    conn = conn or open_kontext_connection(DEFAULT_KONTEXT_ROOT)
    sync_log = load_sync_log(getattr(args, "sync_log", None))
    rows = list(rows_from_connection(conn, getattr(args, "limit", 0)))
    exported = export_rows(getattr(args, "export", None), rows)
    stats = {"found": len(rows), "exported": exported, "ingested": 0, "skipped": 0, "errors": 0}

    if args.dry_run:
        log_line(f"dry-run stats={stats}")
        return stats

    for row in rows:
        entry_id = str(row["metadata"].get("kontext_entry_id"))
        if entry_id in sync_log.get("ingested_ids", {}):
            stats["skipped"] += 1
            continue
        result = ingest_thought_endpoint(row["content"], row["metadata"]) if args.ingest_endpoint else ingest_thought_supabase(row["content"], row["metadata"], row["embed_text"])
        if result.get("ok"):
            stats["ingested"] += 1
            sync_log["ingested_ids"][entry_id] = {"imported_at": datetime.now(timezone.utc).isoformat()}
            sync_log["last_sync"] = datetime.now(timezone.utc).isoformat()
            save_sync_log(sync_log, getattr(args, "sync_log", None))
        else:
            stats["errors"] += 1
            log_line(f"ingest failed entry={entry_id} error={result.get('error')}")
    log_line(f"finish stats={stats}")
    return stats


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import existing Kontext memory entries into Open Brain")
    parser.add_argument("--dry-run", action="store_true", help="Export/count without writing to Open Brain")
    parser.add_argument("--export", help="Write JSONL export")
    parser.add_argument("--limit", type=int, default=0, help="Max entries to process")
    parser.add_argument("--sync-log", default=str(DEFAULT_SYNC_LOG_PATH), help="Sync log path")
    parser.add_argument("--ingest-endpoint", action="store_true", help="Use INGEST_URL/INGEST_KEY instead of Supabase direct insert")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    stats = run_import(args)
    print(f"Found {stats['found']} Kontext entries; exported {stats['exported']}; ingested {stats['ingested']}.")


if __name__ == "__main__":
    main()
