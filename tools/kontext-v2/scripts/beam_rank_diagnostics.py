from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
from typing import Any

import psycopg
from psycopg import sql

from kontext_v2.benchmarks.adapter import (
    KontextBenchmarkAdapter,
    rank_benchmark_candidates,
    rerank_benchmark_candidates_semantically,
    rerank_benchmark_candidates_with_cross_encoder,
)
from kontext_v2.benchmarks.beam_predict import _cross_encoder_config_for_flag, _fixture_for_run, _semantic_config_for_flag
from kontext_v2.benchmarks.locomo_predict import _add_conversations
from kontext_v2.retrieval import _score_row_with_explanation, expand_query, lexical_tokens
from kontext_v2.schema import apply_schema


SAFE_FEATURES = {
    "base_rank",
    "lexical_own",
    "lexical_context",
    "adjacent_own",
    "adjacent_context",
    "session_observation",
    "proper_noun_own",
    "proper_noun_context",
    "date_own",
    "signal_strength",
    "requested_tier",
    "tier_cold",
}


def database_url_for_schema(database_url: str, schema_name: str) -> str:
    parts = urllib.parse.urlsplit(database_url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema_name},public"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))


def feature_map(explanation: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for feature in explanation.get("features") or []:
        name = str(feature.get("name") or "")
        if name not in SAFE_FEATURES:
            continue
        try:
            values[name] = round(float(feature.get("value") or 0.0), 4)
        except (TypeError, ValueError):
            values[name] = 0.0
    return values


def diagnose_question(
    adapter: KontextBenchmarkAdapter,
    question: dict[str, Any],
    window: int,
    semantic_rerank: bool = False,
    cross_encoder_rerank: bool = False,
) -> dict[str, Any]:
    evidence_ids = {str(value).strip() for value in question.get("evidence") or [] if str(value).strip()}
    query_tokens = lexical_tokens(expand_query(str(question.get("question") or "")))
    scored = []
    explanations: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(adapter._candidate_rows(str(question["user_id"]))):
        score, explanation = _score_row_with_explanation(
            str(question.get("question") or ""),
            row,
            requested_domains=set(),
            requested_tiers={"cold"},
        )
        row["_benchmark_adjacent_own"] = next(
            (
                float(feature.get("value") or 0.0)
                for feature in explanation.get("features", [])
                if feature.get("name") == "adjacent_own"
            ),
            0.0,
        )
        row_id = str(row.get("external_mem0_id") or "")
        explanations[row_id] = explanation
        scored.append((index, score, row))

    ranked = rank_benchmark_candidates(query_tokens, scored)
    if semantic_rerank and adapter.semantic_rerank_config.enabled:
        ranked = rerank_benchmark_candidates_semantically(
            str(question.get("question") or ""),
            ranked,
            adapter.semantic_rerank_config,
            adapter._load_semantic_model(),
            adapter._semantic_embedding_cache,
        )
    if cross_encoder_rerank and adapter.cross_encoder_rerank_config.enabled:
        ranked = rerank_benchmark_candidates_with_cross_encoder(
            str(question.get("question") or ""),
            ranked,
            adapter.cross_encoder_rerank_config,
            adapter._load_cross_encoder_model(),
            question_category=str(question.get("category") or ""),
        )
    first_hit = None
    rows = []
    for rank, (_index, score, row) in enumerate(ranked, start=1):
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        source_ids = {str(value).strip() for value in metadata.get("source_ids") or [] if str(value).strip()}
        is_evidence = bool(evidence_ids & source_ids)
        if is_evidence and first_hit is None:
            first_hit = rank
        row_id = str(row.get("external_mem0_id") or "")
        if is_evidence or rank <= window:
            rows.append(
                {
                    "rank": rank,
                    "is_evidence": is_evidence,
                    "score": round(float(score), 4),
                    "observation_kind": metadata.get("observation_kind"),
                    "source_count": len(metadata.get("source_ids") or []),
                    "features": feature_map(explanations.get(row_id, {})),
                }
            )
        if first_hit is not None and rank > max(first_hit + 3, window):
            break
    return {
        "question_id": question.get("question_id"),
        "category": question.get("category"),
        "evidence_count": len(evidence_ids),
        "first_hit_rank": first_hit,
        "rows": rows,
    }


def summarize_diagnostics(diagnostics: list[dict[str, Any]], cutoffs: list[int] | None = None) -> dict[str, Any]:
    cutoffs = cutoffs or [10, 20, 50, 200]
    ranks: list[int | None] = []
    for row in diagnostics:
        value = row.get("first_hit_rank")
        try:
            ranks.append(int(value) if value is not None else None)
        except (TypeError, ValueError):
            ranks.append(None)
    found = [rank for rank in ranks if rank is not None]
    total = len(ranks)
    hits_at_k: dict[str, dict[str, Any]] = {}
    for cutoff in cutoffs:
        bounded = [rank for rank in found if rank <= cutoff]
        hits_at_k[str(cutoff)] = {
            "passed": len(bounded),
            "total": total,
            "rate": round(len(bounded) / total, 4) if total else 0.0,
            "mrr": round(sum(1.0 / rank for rank in bounded) / total, 4) if total else 0.0,
        }
    return {
        "question_count": total,
        "evidence_found_count": len(found),
        "missing_evidence_count": total - len(found),
        "first_hit_ranks": ranks,
        "max_first_hit_rank": max(found) if found else None,
        "hits_at_k": hits_at_k,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = os.environ.get("KONTEXT_V2_DATABASE_URL")
    if not base_url:
        raise RuntimeError("KONTEXT_V2_DATABASE_URL is required")
    schema = args.schema or f"beam_diag_{int(time.time())}"
    summary: dict[str, Any] = {"schema": schema, "dropped": False}
    try:
        with psycopg.connect(base_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        with psycopg.connect(database_url_for_schema(base_url, schema)) as conn:
            apply_schema(conn)
            fixture = _fixture_for_run(
                args.fixture_path,
                args.dataset_path,
                args.beam_size,
                args.offset,
                args.length,
                args.conversations,
                args.max_questions,
                args.question_types,
            )
            semantic_config = _semantic_config_for_flag(args.semantic_rerank, getattr(args, "environ", None))
            cross_encoder_config = _cross_encoder_config_for_flag(args.cross_encoder_rerank, getattr(args, "environ", None))
            adapter = KontextBenchmarkAdapter(
                conn,
                fixture["dataset"],
                schema,
                semantic_rerank_config=semantic_config,
                cross_encoder_rerank_config=cross_encoder_config,
            )
            _add_conversations(adapter, fixture["conversations"], session_only=args.session_only)
            diagnostics = [
                diagnose_question(
                    adapter,
                    question,
                    args.window,
                    semantic_rerank=args.semantic_rerank,
                    cross_encoder_rerank=args.cross_encoder_rerank,
                )
                for question in fixture["questions"]
                if not args.question_id or str(question.get("question_id")) == args.question_id
            ]
        summary.update({
            "dataset": fixture["dataset"],
            "summary": summarize_diagnostics(diagnostics),
            "diagnostics": diagnostics,
        })
    finally:
        with psycopg.connect(base_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        summary["dropped"] = True
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sanitized BEAM first-hit rank diagnostics in an isolated schema.")
    parser.add_argument("--schema")
    parser.add_argument("--fixture-path", default="/app/tools/kontext-v2/tests/fixtures/beam_real_shape.json")
    parser.add_argument("--dataset-path", default="/tmp/beam_rows_diag.json")
    parser.add_argument("--beam-size", default="10M", choices=["100K", "500K", "1M", "10M"])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--length", type=int, default=1)
    parser.add_argument("--conversations", default=None, help="Optional comma-separated conversation row indices within the loaded file")
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--question-types", default=None)
    parser.add_argument("--question-id")
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--session-only", action="store_true", help="Index compact session observations only")
    parser.add_argument("--semantic-rerank", action="store_true", help="Run the opt-in semantic tail reranker")
    parser.add_argument("--cross-encoder-rerank", action="store_true", help="Run the opt-in cross-encoder reranker")
    print(json.dumps(run(parser.parse_args(argv)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
