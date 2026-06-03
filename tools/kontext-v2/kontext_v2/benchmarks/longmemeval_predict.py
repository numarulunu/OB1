from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from kontext_v2.benchmarks.fixtures import (
    DEFAULT_LONGMEMEVAL_CACHE_PATH,
    download_longmemeval_dataset,
    load_longmemeval_real_fixture,
)
from kontext_v2.benchmarks.locomo_predict import (
    _parse_top_k_values,
    _run_backend_question_searches,
    _validate_retrieval_backend,
)
from kontext_v2.benchmarks.reporting import (
    build_private_judged_input_bundle,
    build_predict_only_report,
    build_predict_sweep_report,
    format_predict_cli_summary,
    format_sweep_cli_summary,
    write_private_judged_input_bundle,
    write_report_files,
)
def _parse_question_types(value: str | list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.lower() in {"all", "*"}:
            return None
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _fixture_for_run(
    fixture_path: str | Path,
    dataset_path: str | Path | None,
    dataset_url: str | None,
    max_questions: int | None,
    question_types: str | list[str] | None,
) -> dict[str, Any]:
    resolved_dataset_path = Path(dataset_path) if dataset_path else Path(fixture_path)
    if dataset_url and (dataset_path is None or not resolved_dataset_path.exists()):
        resolved_dataset_path = download_longmemeval_dataset(dataset_url, DEFAULT_LONGMEMEVAL_CACHE_PATH)
    return load_longmemeval_real_fixture(
        resolved_dataset_path,
        max_questions=max_questions,
        question_types=_parse_question_types(question_types),
    )


def run_longmemeval_predict_only(
    database_url: str,
    fixture_path: str | Path,
    output_dir: str | Path,
    run_id: str,
    top_k: int = 200,
    dataset_path: str | Path | None = None,
    dataset_url: str | None = None,
    max_questions: int | None = None,
    question_types: str | list[str] | None = None,
    judged_bundle_output: str | Path | None = None,
    retrieval_backend: str = "kontext",
    mem0_retrieval_module: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _fixture_for_run(fixture_path, dataset_path, dataset_url, max_questions, question_types)
    backend = _validate_retrieval_backend(retrieval_backend)
    question_results = _run_backend_question_searches(
        database_url,
        fixture,
        run_id,
        top_k,
        retrieval_backend=backend,
        mem0_retrieval_module=mem0_retrieval_module,
    )
    if judged_bundle_output:
        bundle = build_private_judged_input_bundle(
            fixture["dataset"],
            run_id,
            top_k,
            question_results,
            retrieval_backend=backend,
        )
        bundle_path = write_private_judged_input_bundle(bundle, judged_bundle_output)
    report = build_predict_only_report(fixture["dataset"], run_id, top_k, question_results, retrieval_backend=backend)
    paths = write_report_files(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    if judged_bundle_output:
        report["private_judged_bundle_path"] = str(bundle_path)
    return report


def run_longmemeval_predict_sweep(
    database_url: str,
    fixture_path: str | Path,
    output_dir: str | Path,
    run_id: str,
    top_k_values: list[int] | str,
    dataset_path: str | Path | None = None,
    dataset_url: str | None = None,
    max_questions: int | None = None,
    question_types: str | list[str] | None = None,
    judged_bundle_output: str | Path | None = None,
    retrieval_backend: str = "kontext",
    mem0_retrieval_module: str | Path | None = None,
) -> dict[str, Any]:
    normalized_top_k = _parse_top_k_values(top_k_values)
    if not normalized_top_k:
        raise ValueError("top_k_values must contain at least one value")
    fixture = _fixture_for_run(fixture_path, dataset_path, dataset_url, max_questions, question_types)
    backend = _validate_retrieval_backend(retrieval_backend)
    question_results = _run_backend_question_searches(
        database_url,
        fixture,
        run_id,
        normalized_top_k[-1],
        retrieval_backend=backend,
        mem0_retrieval_module=mem0_retrieval_module,
    )
    if judged_bundle_output:
        bundle = build_private_judged_input_bundle(
            fixture["dataset"],
            run_id,
            normalized_top_k,
            question_results,
            retrieval_backend=backend,
        )
        bundle_path = write_private_judged_input_bundle(bundle, judged_bundle_output)
    report = build_predict_sweep_report(fixture["dataset"], run_id, normalized_top_k, question_results, retrieval_backend=backend)
    paths = write_report_files(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    if judged_bundle_output:
        report["private_judged_bundle_path"] = str(bundle_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kontext LongMemEval predict-only benchmark")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--fixture-path", default="tools/kontext-v2/tests/fixtures/longmemeval_real_shape.json")
    parser.add_argument("--dataset-path")
    parser.add_argument("--dataset-url", default=None)
    parser.add_argument("--output-dir", default="tools/kontext-v2/benchmark-results")
    parser.add_argument("--run-id", default="local-longmemeval")
    parser.add_argument("--top-k", type=int, default=200)
    parser.add_argument("--top-k-sweep", help="Comma-separated top-k values, for example 10,20,50,200")
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--question-types", default=None)
    parser.add_argument("--judged-bundle-output", help="Optional private raw benchmark input bundle for an approved judged run.")
    parser.add_argument("--retrieval-backend", default="kontext", choices=["kontext", "legacy-mem0-offline"])
    parser.add_argument("--mem0-retrieval-module", help="Path to legacy Mem0 retrieval.py for offline comparison.")
    args = parser.parse_args()
    if args.top_k_sweep:
        report = run_longmemeval_predict_sweep(
            args.database_url,
            args.fixture_path,
            args.output_dir,
            args.run_id,
            _parse_top_k_values(args.top_k_sweep),
            dataset_path=args.dataset_path,
            dataset_url=args.dataset_url,
            max_questions=args.max_questions,
            question_types=args.question_types,
            judged_bundle_output=args.judged_bundle_output,
            retrieval_backend=args.retrieval_backend,
            mem0_retrieval_module=args.mem0_retrieval_module,
        )
        sweep_summary = format_sweep_cli_summary(report)
        print(
            f"dataset={report['dataset']} run_id={report['run_id']} "
            f"{sweep_summary} json={report['report_paths']['json']}"
        )
        return
    report = run_longmemeval_predict_only(
        args.database_url,
        args.fixture_path,
        args.output_dir,
        args.run_id,
        args.top_k,
        dataset_path=args.dataset_path,
        dataset_url=args.dataset_url,
        max_questions=args.max_questions,
        question_types=args.question_types,
        judged_bundle_output=args.judged_bundle_output,
        retrieval_backend=args.retrieval_backend,
        mem0_retrieval_module=args.mem0_retrieval_module,
    )
    print(
        f"dataset={report['dataset']} run_id={report['run_id']} "
        f"{format_predict_cli_summary(report)} "
        f"json={report['report_paths']['json']}"
    )

if __name__ == "__main__":
    main()
