from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import psycopg

from kontext_v2.benchmarks.adapter import CrossEncoderRerankConfig, KontextBenchmarkAdapter, SemanticRerankConfig
from kontext_v2.benchmarks.fixtures import (
    DEFAULT_BEAM_CACHE_DIR,
    download_beam_rows_dataset,
    load_beam_real_fixture,
)
from kontext_v2.benchmarks.mem0_parity import Mem0ParityRerankConfig
from kontext_v2.benchmarks.locomo_predict import (
    _add_conversations,
    _clear_existing_benchmark_rows,
    _parse_top_k_values,
    _run_question_searches,
    _validate_retrieval_backend,
)
from kontext_v2.benchmarks.mem0_adapter import Mem0BenchmarkAdapter
from kontext_v2.benchmarks.reporting import (
    build_private_judged_input_bundle,
    build_predict_only_report,
    build_predict_sweep_report,
    format_predict_cli_summary,
    format_sweep_cli_summary,
    write_private_judged_input_bundle,
    write_report_files,
)
from kontext_v2.schema import apply_schema


DEFAULT_BEAM_FIXTURE = Path("tools/kontext-v2/tests/fixtures/beam_real_shape.json")


def _semantic_config_for_flag(
    enabled: bool,
    environ: dict[str, str] | None = None,
) -> SemanticRerankConfig | None:
    if not enabled:
        return None
    values = dict(environ if environ is not None else os.environ)
    values["KONTEXT_BENCHMARK_SEMANTIC_RERANK"] = "1"
    return SemanticRerankConfig.from_env(values)


def _cross_encoder_config_for_flag(
    enabled: bool,
    environ: dict[str, str] | None = None,
) -> CrossEncoderRerankConfig | None:
    if not enabled:
        return None
    values = dict(environ if environ is not None else os.environ)
    values["KONTEXT_BENCHMARK_CROSS_ENCODER_RERANK"] = "1"
    return CrossEncoderRerankConfig.from_env(values)


def _mem0_parity_config_for_flag(
    enabled: bool,
    environ: dict[str, str] | None = None,
) -> Mem0ParityRerankConfig | None:
    if not enabled:
        return None
    values = dict(environ if environ is not None else os.environ)
    values["KONTEXT_BENCHMARK_MEM0_PARITY_RERANK"] = "1"
    return Mem0ParityRerankConfig.from_env(values)


def _annotate_mem0_parity_report(report: dict[str, Any], config: Mem0ParityRerankConfig | None) -> dict[str, Any]:
    if config and config.enabled:
        report["benchmark_rerank"] = "mem0_parity"
        report["benchmark_rerank_config"] = config.public_summary()
    return report


def _fixture_for_run(
    fixture_path: str | Path,
    dataset_path: str | Path | None,
    beam_size: str,
    offset: int,
    length: int,
    conversations: str | None,
    max_questions: int | None,
    question_types: str | list[str] | None,
) -> dict[str, Any]:
    resolved_path = Path(dataset_path) if dataset_path else Path(fixture_path)
    if dataset_path and not resolved_path.exists():
        resolved_path = download_beam_rows_dataset(
            beam_size=beam_size,
            offset=offset,
            length=length,
            cache_path=resolved_path,
        )
    return load_beam_real_fixture(
        resolved_path,
        beam_size=beam_size,
        conversation_indices=conversations,
        max_questions=max_questions,
        question_types=question_types,
    )


def run_beam_predict_only(
    database_url: str,
    fixture_path: str | Path,
    output_dir: str | Path,
    run_id: str,
    top_k: int = 200,
    dataset_path: str | Path | None = None,
    beam_size: str = "1M",
    offset: int = 0,
    length: int = 1,
    conversations: str | None = None,
    max_questions: int | None = None,
    question_types: str | list[str] | None = None,
    semantic_rerank: bool = False,
    cross_encoder_rerank: bool = False,
    mem0_parity_rerank: bool = False,
    session_only: bool = False,
    judged_bundle_output: str | Path | None = None,
    retrieval_backend: str = "kontext",
    mem0_retrieval_module: str | Path | None = None,
) -> dict[str, Any]:
    fixture = _fixture_for_run(
        fixture_path,
        dataset_path,
        beam_size,
        offset,
        length,
        conversations,
        max_questions,
        question_types,
    )
    backend = _validate_retrieval_backend(retrieval_backend)
    mem0_parity_config = _mem0_parity_config_for_flag(mem0_parity_rerank)
    if backend == "legacy-mem0-offline":
        if semantic_rerank or cross_encoder_rerank or mem0_parity_rerank:
            raise ValueError("legacy-mem0-offline does not support --mem0-parity-rerank or other Kontext benchmark rerank flags")
        adapter = Mem0BenchmarkAdapter(fixture["dataset"], run_id, retrieval_module_path=mem0_retrieval_module)
        _add_conversations(adapter, fixture["conversations"], session_only=session_only)
        question_results = _run_question_searches(adapter, fixture, top_k)
    else:
        with psycopg.connect(database_url) as conn:
            apply_schema(conn)
            _clear_existing_benchmark_rows(conn, fixture["dataset"], run_id)
            adapter = KontextBenchmarkAdapter(
                conn,
                fixture["dataset"],
                run_id,
                semantic_rerank_config=_semantic_config_for_flag(semantic_rerank),
                cross_encoder_rerank_config=_cross_encoder_config_for_flag(cross_encoder_rerank),
                mem0_parity_rerank_config=mem0_parity_config,
            )
            _add_conversations(adapter, fixture["conversations"], session_only=session_only)
            question_results = _run_question_searches(adapter, fixture, top_k)
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
    _annotate_mem0_parity_report(report, mem0_parity_config)
    paths = write_report_files(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    if judged_bundle_output:
        report["private_judged_bundle_path"] = str(bundle_path)
    return report


def run_beam_predict_sweep(
    database_url: str,
    fixture_path: str | Path,
    output_dir: str | Path,
    run_id: str,
    top_k_values: list[int] | str,
    dataset_path: str | Path | None = None,
    beam_size: str = "1M",
    offset: int = 0,
    length: int = 1,
    conversations: str | None = None,
    max_questions: int | None = None,
    question_types: str | list[str] | None = None,
    semantic_rerank: bool = False,
    cross_encoder_rerank: bool = False,
    mem0_parity_rerank: bool = False,
    session_only: bool = False,
    judged_bundle_output: str | Path | None = None,
    retrieval_backend: str = "kontext",
    mem0_retrieval_module: str | Path | None = None,
) -> dict[str, Any]:
    normalized_top_k = _parse_top_k_values(top_k_values)
    if not normalized_top_k:
        raise ValueError("top_k_values must contain at least one value")
    fixture = _fixture_for_run(
        fixture_path,
        dataset_path,
        beam_size,
        offset,
        length,
        conversations,
        max_questions,
        question_types,
    )
    backend = _validate_retrieval_backend(retrieval_backend)
    mem0_parity_config = _mem0_parity_config_for_flag(mem0_parity_rerank)
    if backend == "legacy-mem0-offline":
        if semantic_rerank or cross_encoder_rerank or mem0_parity_rerank:
            raise ValueError("legacy-mem0-offline does not support --mem0-parity-rerank or other Kontext benchmark rerank flags")
        adapter = Mem0BenchmarkAdapter(fixture["dataset"], run_id, retrieval_module_path=mem0_retrieval_module)
        _add_conversations(adapter, fixture["conversations"], session_only=session_only)
        question_results = _run_question_searches(adapter, fixture, normalized_top_k[-1])
    else:
        with psycopg.connect(database_url) as conn:
            apply_schema(conn)
            _clear_existing_benchmark_rows(conn, fixture["dataset"], run_id)
            adapter = KontextBenchmarkAdapter(
                conn,
                fixture["dataset"],
                run_id,
                semantic_rerank_config=_semantic_config_for_flag(semantic_rerank),
                cross_encoder_rerank_config=_cross_encoder_config_for_flag(cross_encoder_rerank),
                mem0_parity_rerank_config=mem0_parity_config,
            )
            _add_conversations(adapter, fixture["conversations"], session_only=session_only)
            question_results = _run_question_searches(adapter, fixture, normalized_top_k[-1])
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
    _annotate_mem0_parity_report(report, mem0_parity_config)
    paths = write_report_files(report, output_dir)
    report["report_paths"] = {key: str(value) for key, value in paths.items()}
    if judged_bundle_output:
        report["private_judged_bundle_path"] = str(bundle_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kontext BEAM predict-only benchmark")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--fixture-path", default=str(DEFAULT_BEAM_FIXTURE))
    parser.add_argument("--dataset-path")
    parser.add_argument("--output-dir", default="tools/kontext-v2/benchmark-results")
    parser.add_argument("--run-id", default="local-beam")
    parser.add_argument("--top-k", type=int, default=200)
    parser.add_argument("--top-k-sweep", help="Comma-separated top-k values, for example 10,20,50,200")
    parser.add_argument("--beam-size", default="1M", choices=["100K", "500K", "1M", "10M"])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--length", type=int, default=1)
    parser.add_argument("--conversations", default=None)
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--question-types", default=None)
    parser.add_argument("--semantic-rerank", action="store_true", help="Enable opt-in sentence-transformer tail reranking")
    parser.add_argument("--cross-encoder-rerank", action="store_true", help="Enable opt-in cross-encoder pairwise reranking")
    parser.add_argument("--mem0-parity-rerank", action="store_true", help="Enable benchmark-only Mem0-style RRF reranking for Kontext BEAM searches")
    parser.add_argument("--session-only", action="store_true", help="Index compact session observations only for large BEAM diagnostics")
    parser.add_argument("--judged-bundle-output", help="Optional private raw benchmark input bundle for an approved judged run.")
    parser.add_argument("--retrieval-backend", default="kontext", choices=["kontext", "legacy-mem0-offline"])
    parser.add_argument("--mem0-retrieval-module", help="Path to legacy Mem0 retrieval.py for offline comparison.")
    args = parser.parse_args()

    dataset_path = args.dataset_path
    if dataset_path is None and args.fixture_path == str(DEFAULT_BEAM_FIXTURE) and not DEFAULT_BEAM_FIXTURE.exists():
        dataset_path = DEFAULT_BEAM_CACHE_DIR / f"beam_{args.beam_size}_rows_{args.offset}_{args.length}.json"

    if args.top_k_sweep:
        report = run_beam_predict_sweep(
            args.database_url,
            args.fixture_path,
            args.output_dir,
            args.run_id,
            _parse_top_k_values(args.top_k_sweep),
            dataset_path=dataset_path,
            beam_size=args.beam_size,
            offset=args.offset,
            length=args.length,
            conversations=args.conversations,
            max_questions=args.max_questions,
            question_types=args.question_types,
            semantic_rerank=args.semantic_rerank,
            cross_encoder_rerank=args.cross_encoder_rerank,
            mem0_parity_rerank=args.mem0_parity_rerank,
            session_only=args.session_only,
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

    report = run_beam_predict_only(
        args.database_url,
        args.fixture_path,
        args.output_dir,
        args.run_id,
        top_k=args.top_k,
        dataset_path=dataset_path,
        beam_size=args.beam_size,
        offset=args.offset,
        length=args.length,
        conversations=args.conversations,
        max_questions=args.max_questions,
        question_types=args.question_types,
        semantic_rerank=args.semantic_rerank,
        cross_encoder_rerank=args.cross_encoder_rerank,
        mem0_parity_rerank=args.mem0_parity_rerank,
        session_only=args.session_only,
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
