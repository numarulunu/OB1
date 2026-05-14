
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Any

import psycopg

from kontext_v2.live_mem0 import Mem0ApiClient
from kontext_v2.parity import compare_exact_id_freshness
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema

DEFAULT_MEM0_BASE_URL = "https://mem0-api.ionutrosu.xyz"


def summarize_freshness(report: dict[str, Any]) -> dict[str, Any]:
    rows = report.get("results") if isinstance(report.get("results"), list) else []
    reason_counts: Counter[str] = Counter()
    for row in rows:
        if row.get("fresh"):
            reason_counts["fresh"] += 1
        elif not row.get("mem0_found"):
            reason_counts["mem0_missing"] += 1
        elif not row.get("kontext_found"):
            reason_counts["kontext_missing"] += 1
        elif not row.get("canonical_hash_match"):
            reason_counts["canonical_mismatch"] += 1
        else:
            reason_counts["other"] += 1
    stale_ids = [str(row.get("id") or "") for row in rows if not row.get("fresh") and row.get("id")]
    return {
        "ok": int(report.get("stale") or 0) == 0,
        "checked": int(report.get("checked") or 0),
        "fresh": int(report.get("fresh") or 0),
        "stale": int(report.get("stale") or 0),
        "reason_counts": dict(sorted(reason_counts.items())),
        "raw_source_hash_mismatch_but_canonical_fresh": sum(
            1 for row in rows if row.get("fresh") and not row.get("source_hash_match")
        ),
        "type_mismatch": sum(1 for row in rows if row.get("memory_type_match") is False),
        "domain_no_overlap": sum(1 for row in rows if row.get("domain_overlap") is False),
        "stale_count_capped": len(stale_ids[:20]),
    }


def run(database_url: str, mem0_base_url: str, mem0_api_key: str, mem0_user_id: str) -> dict[str, Any]:
    client = Mem0ApiClient(
        base_url=mem0_base_url,
        api_key=mem0_api_key,
        user_id=mem0_user_id,
        timeout=90,
    )
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        ids = [row["external_mem0_id"] for row in repo.list_memory_rows(limit=10000) if row.get("external_mem0_id")]
        report = compare_exact_id_freshness(repo, client.fetch, ids)
    return summarize_freshness(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate Kontext exact-ID freshness against Mem0.")
    parser.add_argument("--database-url", default=os.environ.get("KONTEXT_V2_DATABASE_URL", ""))
    parser.add_argument("--mem0-base-url", default=os.environ.get("MEM0_API_BASE_URL") or os.environ.get("MEM0_BASE_URL") or DEFAULT_MEM0_BASE_URL)
    parser.add_argument("--mem0-api-key-env", default="MEM0_API_KEY")
    parser.add_argument("--mem0-user-id", default=os.environ.get("MEM0_USER_ID", "ionut"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.environ.get(args.mem0_api_key_env, "").strip()
    if not args.database_url:
        raise RuntimeError("KONTEXT_V2_DATABASE_URL is required")
    if not api_key:
        raise RuntimeError("Mem0 API key environment variable is missing")
    payload = run(
        database_url=args.database_url,
        mem0_base_url=args.mem0_base_url,
        mem0_api_key=api_key,
        mem0_user_id=args.mem0_user_id,
    )
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
