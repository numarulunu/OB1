from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import psycopg

from kontext_v2.benchmarks.adapter import BenchmarkMessage, KontextBenchmarkAdapter
from kontext_v2.benchmarks.fixtures import (
    DEFAULT_LOCOMO_CACHE_PATH,
    download_locomo_dataset,
    load_locomo_real_fixture,
    load_locomo_tiny_fixture,
)
from kontext_v2.benchmarks.reporting import build_predict_only_report, build_predict_sweep_report, write_report_files
from kontext_v2.schema import apply_schema


def _messages(raw: list[dict[str, Any]]) -> list[BenchmarkMessage]:
    return [
        BenchmarkMessage(
            str(item.get("role") or "user"),
            str(item.get("content") or ""),
            str(item.get("source_id") or "") or None,
        )
        for item in raw
        if str(item.get("content") or "").strip()
    ]


def _matched(search_results: list[dict[str, Any]], expected_terms: list[str] | None = None, evidence: list[str] | None = None) -> bool:
    evidence_ids = {str(value).strip() for value in (evidence or []) if str(value).strip()}
    if evidence_ids:
        for row in search_results:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            source_ids = {str(value).strip() for value in metadata.get("source_ids") or [] if str(value).strip()}
            if evidence_ids & source_ids:
                return True
        return False
    terms = [str(term).lower() for term in (expected_terms or [])]
    haystack = "\n".join(str(row.get("memory") or "").lower() for row in search_results)
    return all(term in haystack for term in terms)


def _fixture_for_run(
    fixture_path: str | Path,
    dataset_path: str | Path | None,
    dataset_url: str | None,
    conversations: str | None,
    max_questions: int | None,
) -> dict[str, Any]:
    if dataset_path or dataset_url:
        resolved_dataset_path = Path(dataset_path) if dataset_path else DEFAULT_LOCOMO_CACHE_PATH
        if dataset_url and (dataset_path is None or not resolved_dataset_path.exists()):
            resolved_dataset_path = download_locomo_dataset(dataset_url, resolved_dataset_path)
        return load_locomo_real_fixture(
            resolved_dataset_path,
            conversation_indices=conversations,
            max_questions=max_questions,
        )
    return load_locomo_tiny_fixture(fixture_path)

def _clear_existing_benchmark_rows(conn: psycopg.Connection, dataset: str, run_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM memories
            WHERE metadata->>'source' = 'benchmark'
              AND metadata->>'benchmark_dataset' = %s
              AND metadata->>'benchmark_run_id' = %s
            """,
            (dataset, run_id),
        )
    conn.commit()



def _add_conversations(adapter: KontextBenchmarkAdapter, conversations: list[dict[str, Any]]) -> None:
    for conversation in conversations:
        for session in conversation["sessions"]:
            messages = _messages(session["messages"])
            sourced_messages = [message for message in messages if message.source_id]
            if sourced_messages:
                for message in sourced_messages:
                    adapter.add(
                        [message],
                        conversation["user_id"],
                        conversation["conversation_id"],
                        session["session_id"],
                        session.get("date"),
                        source_ids=[message.source_id] if message.source_id else [],
                    )
            else:
                adapter.add(
                    messages,
                    conversation["user_id"],
                    conversation["conversation_id"],
                    session["session_id"],
                    session.get("date"),
                    source_ids=session.get("source_ids") or [],
                )


def _parse_top_k_values(value: str | list[int] | None) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        return sorted({min(max(int(part.strip()), 1), 200) for part in value.split(",") if part.strip()})
    return sorted({min(max(int(item), 1), 200) for item in value})


def _run_question_searches(
    adapter: KontextBenchmarkAdapter,
    fixture: dict[str, Any],
    top_k: int,
) -> list[dict[str, Any]]:
    conversations_by_id = {item["conversation_id"]: item for item in fixture["conversations"]}
    question_results = []
    for question in fixture["questions"]:
        start = time.monotonic()
        rows = adapter.search(
            question["question"],
            conversations_by_id[question["conversation_id"]]["user_id"],
            top_k=top_k,
        )
        question_results.append(
            {
                "question_id": question["question_id"],
                "category": question.get("category") or "unknown",
                "matched": _matched(
                    rows,
                    expected_terms=question.get("expected_terms") or [],
                    evidence=question.get("evidence") or [],
                ),
                "expected_terms": question.get("expected_terms") or [],
                "evidence": question.get("evidence") or [],
                "search_latency_ms": (time.monotonic() - start) * 1000,
                "result_ids": [str(row.get("id") or "") for row in rows],
                "search_results": rows,
            }
        )
    return question_results


def run_locomo_predict_only(
    database_url: str,
    fixture_path: str | Path,
    output_dir: str | Path,
    run_id: str,
    top_k: int = 200,
    dataset_path: str | Path | None = None,
    dataset_url: str | None = None,
    conversations: str | None = None,
    max_questions: int | None = None,
) -> dict[str, Any]:
    fixture = _fixture_for_run(fixture_path, dataset_path, dataset_url, conversations, max_questions)
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        _clear_existing_benchmark_rows(conn, fixture["dataset"], run_id)
        adapter = KontextBenchmarkAdapter(conn, fixture["dataset"], run_id)
        _add_conversations(adapter, fixture["conversations"])
        question_results = _run_question_searches(adapter, fixture, top_k)
    report = build_predict_only_report(fixture["dataset"], run_id, top_k, question_results)
    paths = write_report_files(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    return report


def run_locomo_predict_sweep(
    database_url: str,
    fixture_path: str | Path,
    output_dir: str | Path,
    run_id: str,
    top_k_values: list[int] | str,
    dataset_path: str | Path | None = None,
    dataset_url: str | None = None,
    conversations: str | None = None,
    max_questions: int | None = None,
) -> dict[str, Any]:
    normalized_top_k = _parse_top_k_values(top_k_values)
    if not normalized_top_k:
        raise ValueError("top_k_values must contain at least one value")
    fixture = _fixture_for_run(fixture_path, dataset_path, dataset_url, conversations, max_questions)
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        _clear_existing_benchmark_rows(conn, fixture["dataset"], run_id)
        adapter = KontextBenchmarkAdapter(conn, fixture["dataset"], run_id)
        _add_conversations(adapter, fixture["conversations"])
        question_results = _run_question_searches(adapter, fixture, normalized_top_k[-1])
    report = build_predict_sweep_report(fixture["dataset"], run_id, normalized_top_k, question_results)
    paths = write_report_files(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    return report
def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kontext LoCoMo predict-only benchmark")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--fixture-path", default="tools/kontext-v2/tests/fixtures/locomo_tiny.json")
    parser.add_argument("--dataset-path")
    parser.add_argument("--dataset-url", default=None)
    parser.add_argument("--output-dir", default="tools/kontext-v2/benchmark-results")
    parser.add_argument("--run-id", default="local-tiny")
    parser.add_argument("--top-k", type=int, default=200)
    parser.add_argument("--top-k-sweep", help="Comma-separated top-k values, for example 5,10,20,50")
    parser.add_argument("--conversations", default=None)
    parser.add_argument("--max-questions", type=int, default=None)
    args = parser.parse_args()
    if args.top_k_sweep:
        report = run_locomo_predict_sweep(
            args.database_url,
            args.fixture_path,
            args.output_dir,
            args.run_id,
            _parse_top_k_values(args.top_k_sweep),
            dataset_path=args.dataset_path,
            dataset_url=args.dataset_url,
            conversations=args.conversations,
            max_questions=args.max_questions,
        )
        sweep_summary = " ".join(
            f"top_k={top_k}:{values['matched_questions']}/{values['total_questions']}"
            for top_k, values in sorted((report.get("sweeps") or {}).items(), key=lambda item: int(item[0]))
        )
        print(
            f"dataset={report['dataset']} run_id={report['run_id']} "
            f"{sweep_summary} json={report['report_paths']['json']}"
        )
        return
    report = run_locomo_predict_only(
        args.database_url,
        args.fixture_path,
        args.output_dir,
        args.run_id,
        args.top_k,
        dataset_path=args.dataset_path,
        dataset_url=args.dataset_url,
        conversations=args.conversations,
        max_questions=args.max_questions,
    )
    print(
        f"dataset={report['dataset']} run_id={report['run_id']} "
        f"matched={report['matched_questions']}/{report['total_questions']} "
        f"json={report['report_paths']['json']}"
    )


if __name__ == "__main__":
    main()
