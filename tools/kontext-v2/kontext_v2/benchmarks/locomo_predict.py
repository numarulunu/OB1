from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import psycopg

from kontext_v2.benchmarks.adapter import BenchmarkMessage, KontextBenchmarkAdapter
from kontext_v2.benchmarks.fixtures import load_locomo_tiny_fixture
from kontext_v2.benchmarks.reporting import build_predict_only_report, write_report_files
from kontext_v2.schema import apply_schema


def _messages(raw: list[dict[str, Any]]) -> list[BenchmarkMessage]:
    return [
        BenchmarkMessage(str(item.get("role") or "user"), str(item.get("content") or ""))
        for item in raw
        if str(item.get("content") or "").strip()
    ]


def _matched(search_results: list[dict[str, Any]], expected_terms: list[str]) -> bool:
    haystack = "\n".join(str(row.get("memory") or "").lower() for row in search_results)
    return all(str(term).lower() in haystack for term in expected_terms)


def run_locomo_predict_only(
    database_url: str,
    fixture_path: str | Path,
    output_dir: str | Path,
    run_id: str,
    top_k: int = 200,
) -> dict[str, Any]:
    fixture = load_locomo_tiny_fixture(fixture_path)
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        adapter = KontextBenchmarkAdapter(conn, fixture["dataset"], run_id)
        conversations = {item["conversation_id"]: item for item in fixture["conversations"]}
        for conversation in fixture["conversations"]:
            for session in conversation["sessions"]:
                adapter.add(
                    _messages(session["messages"]),
                    conversation["user_id"],
                    conversation["conversation_id"],
                    session["session_id"],
                    session.get("date"),
                )
        question_results = []
        for question in fixture["questions"]:
            start = time.monotonic()
            rows = adapter.search(
                question["question"],
                conversations[question["conversation_id"]]["user_id"],
                top_k=top_k,
            )
            question_results.append(
                {
                    "question_id": question["question_id"],
                    "category": question.get("category") or "unknown",
                    "matched": _matched(rows, question.get("expected_terms") or []),
                    "search_latency_ms": (time.monotonic() - start) * 1000,
                    "result_ids": [str(row.get("id") or "") for row in rows],
                    "search_results": rows,
                }
            )
    report = build_predict_only_report(fixture["dataset"], run_id, top_k, question_results)
    paths = write_report_files(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kontext tiny LoCoMo predict-only benchmark")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--fixture-path", default="tools/kontext-v2/tests/fixtures/locomo_tiny.json")
    parser.add_argument("--output-dir", default="tools/kontext-v2/benchmark-results")
    parser.add_argument("--run-id", default="local-tiny")
    parser.add_argument("--top-k", type=int, default=200)
    args = parser.parse_args()
    report = run_locomo_predict_only(
        args.database_url,
        args.fixture_path,
        args.output_dir,
        args.run_id,
        args.top_k,
    )
    print(
        f"dataset={report['dataset']} run_id={report['run_id']} "
        f"matched={report['matched_questions']}/{report['total_questions']} "
        f"json={report['report_paths']['json']}"
    )


if __name__ == "__main__":
    main()
