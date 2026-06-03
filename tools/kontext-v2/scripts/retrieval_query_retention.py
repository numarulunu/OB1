#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


MIN_KEEP_DAYS = 7
DEFAULT_KEEP_DAYS = 30
DEFAULT_MAX_DELETE = 5000


def validate_retention_args(*, keep_days: int, max_delete: int) -> None:
    if int(keep_days) < MIN_KEEP_DAYS:
        raise ValueError(f"keep_days must be at least {MIN_KEEP_DAYS}")
    if int(max_delete) < 0:
        raise ValueError("max_delete must be non-negative")


def delete_sql(*, keep_days: int, max_delete: int) -> tuple[str, tuple[int, int]]:
    sql = """
    WITH doomed AS (
        SELECT id
        FROM retrieval_queries
        WHERE created_at < now() - (%s::text || ' days')::interval
        ORDER BY created_at ASC
        LIMIT %s
    )
    DELETE FROM retrieval_queries AS rq
    USING doomed
    WHERE rq.id = doomed.id
    RETURNING 1
    """
    return sql, (int(keep_days), int(max_delete))


def build_report(
    *,
    keep_days: int,
    max_delete: int,
    total_rows: int,
    eligible_rows: int,
    deleted_rows: int,
    apply: bool,
) -> dict[str, Any]:
    would_delete = min(int(eligible_rows), int(max_delete))
    attention: list[str] = []
    if int(eligible_rows) > int(max_delete):
        attention.append("retention cap reached; rerun may be needed")
    return {
        "ok": True,
        "mode": "retrieval-query-retention",
        "dry_run": not bool(apply),
        "keep_days": int(keep_days),
        "max_delete": int(max_delete),
        "total_rows": int(total_rows),
        "eligible_rows": int(eligible_rows),
        "would_delete_rows": would_delete,
        "deleted_rows": int(deleted_rows),
        "attention": attention,
        "privacy": "aggregate counts only; no raw queries, result ids, or bodies",
    }


def load_counts(conn: Any, *, keep_days: int) -> tuple[int, int]:
    total = conn.execute("SELECT count(*) AS count FROM retrieval_queries").fetchone()["count"]
    eligible = conn.execute(
        """
        SELECT count(*) AS count
        FROM retrieval_queries
        WHERE created_at < now() - (%s::text || ' days')::interval
        """,
        (int(keep_days),),
    ).fetchone()["count"]
    return int(total or 0), int(eligible or 0)


def delete_old_rows(conn: Any, *, keep_days: int, max_delete: int) -> int:
    if int(max_delete) == 0:
        return 0
    sql, params = delete_sql(keep_days=keep_days, max_delete=max_delete)
    rows = conn.execute(sql, params).fetchall()
    conn.commit()
    return len(rows)


def run_retention(
    database_url: str,
    *,
    keep_days: int = DEFAULT_KEEP_DAYS,
    max_delete: int = DEFAULT_MAX_DELETE,
    apply: bool = False,
) -> dict[str, Any]:
    validate_retention_args(keep_days=keep_days, max_delete=max_delete)
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        total_rows, eligible_rows = load_counts(conn, keep_days=keep_days)
        deleted_rows = 0
        if apply:
            deleted_rows = delete_old_rows(conn, keep_days=keep_days, max_delete=max_delete)
        return build_report(
            keep_days=keep_days,
            max_delete=max_delete,
            total_rows=total_rows,
            eligible_rows=eligible_rows,
            deleted_rows=deleted_rows,
            apply=apply,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune old sanitized Kontext retrieval query telemetry.")
    parser.add_argument("--database-url", default=os.environ.get("KONTEXT_V2_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    parser.add_argument("--max-delete", type=int, default=DEFAULT_MAX_DELETE)
    parser.add_argument("--apply", action="store_true", help="Delete eligible rows. Default is dry-run.")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args(argv)

    if not args.database_url:
        raise SystemExit("database URL is required")
    try:
        report = run_retention(
            args.database_url,
            keep_days=args.keep_days,
            max_delete=args.max_delete,
            apply=args.apply,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).expanduser().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
