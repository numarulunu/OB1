from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
from typing import Any

import psycopg
from psycopg import sql

from kontext_v2.benchmarks.beam_predict import run_beam_predict_sweep


def database_url_for_schema(database_url: str, schema_name: str) -> str:
    parts = urllib.parse.urlsplit(database_url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema_name},public"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))


def sweep_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {
            "retrieval": f"{values.get('retrieval_matched_questions')}/{values.get('retrieval_evaluable_questions')}",
            "mrr": values.get("mrr"),
            "median_first_hit_rank": values.get("median_first_hit_rank"),
            "p90_first_hit_rank": values.get("p90_first_hit_rank"),
            "max_first_hit_rank": values.get("max_first_hit_rank"),
            "avg_ms": values.get("average_search_latency_ms"),
        }
        for key, values in sorted((report.get("sweeps") or {}).items(), key=lambda item: int(item[0]))
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = os.environ.get("KONTEXT_V2_DATABASE_URL")
    if not base_url:
        raise RuntimeError("KONTEXT_V2_DATABASE_URL is required")
    schema = args.schema or f"beam_smoke_{int(time.time())}"
    summary: dict[str, Any] = {"schema": schema, "dropped": False}

    try:
        with psycopg.connect(base_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        report = run_beam_predict_sweep(
            database_url=database_url_for_schema(base_url, schema),
            fixture_path=args.fixture_path,
            output_dir=args.output_dir or f"/tmp/{schema}",
            run_id=schema,
            top_k_values=args.top_k_sweep,
            dataset_path=args.dataset_path,
            beam_size=args.beam_size,
            offset=args.offset,
            length=args.length,
            max_questions=args.max_questions,
            question_types=args.question_types,
            semantic_rerank=args.semantic_rerank,
            cross_encoder_rerank=args.cross_encoder_rerank,
            session_only=args.session_only,
        )
        summary.update(
            {
                "dataset": report.get("dataset"),
                "total_questions": report.get("total_questions"),
                "retrieval_evaluable_questions": report.get("retrieval_evaluable_questions"),
                "skipped_no_match_rule": report.get("skipped_no_match_rule"),
                "sweeps": sweep_summary(report),
                "miss_analysis": report.get("miss_analysis"),
            }
        )
    finally:
        with psycopg.connect(base_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        summary["dropped"] = True
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an isolated BEAM predict-only smoke and drop the temp schema.")
    parser.add_argument("--schema")
    parser.add_argument("--fixture-path", default="/app/tools/kontext-v2/tests/fixtures/beam_real_shape.json")
    parser.add_argument("--dataset-path", default="/tmp/beam_rows.json")
    parser.add_argument("--output-dir")
    parser.add_argument("--top-k-sweep", default="10,20,50,200")
    parser.add_argument("--beam-size", default="10M", choices=["100K", "500K", "1M", "10M"])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--length", type=int, default=1)
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--question-types", default=None)
    parser.add_argument("--semantic-rerank", action="store_true", help="Enable opt-in semantic tail reranking")
    parser.add_argument("--cross-encoder-rerank", action="store_true", help="Enable opt-in cross-encoder reranking")
    parser.add_argument("--session-only", action="store_true", help="Index compact session observations only")
    args = parser.parse_args(argv)
    print(json.dumps(run(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
