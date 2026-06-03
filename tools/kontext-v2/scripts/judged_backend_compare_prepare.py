from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    sys.path.insert(0, str(candidate))

from kontext_v2.benchmarks.beam_predict import run_beam_predict_sweep
from kontext_v2.benchmarks.fixtures import (
    DEFAULT_BEAM_CACHE_DIR,
    DEFAULT_LOCOMO_CACHE_PATH,
    DEFAULT_LONGMEMEVAL_CACHE_PATH,
)
from kontext_v2.benchmarks.locomo_predict import run_locomo_predict_sweep
from kontext_v2.benchmarks.longmemeval_predict import run_longmemeval_predict_sweep


DEFAULT_REPORTS_DIR = Path("/opt/kontext/reports")
DEFAULT_PRIVATE_DIR = Path("/opt/kontext/private/judged-bundles")
DEFAULT_APPROVAL_DIR = DEFAULT_REPORTS_DIR / "judged-plans"
DEFAULT_MEM0_RETRIEVAL_MODULE = Path("/opt/mem0-remote-mcp/retrieval.py")


@dataclass(frozen=True)
class SuiteSpec:
    suite: str
    runner: Callable[..., dict[str, Any]]
    fixture_path: str
    dataset_path_arg: str | None = None
    dataset_url_arg: str | None = None
    extra_args: dict[str, Any] | None = None


SUITES = (
    SuiteSpec(
        suite="locomo30",
        runner=run_locomo_predict_sweep,
        fixture_path="tools/kontext-v2/tests/fixtures/locomo_tiny.json",
        dataset_path_arg="locomo_dataset_path",
        dataset_url_arg="locomo_dataset_url",
        extra_args={"conversations": None},
    ),
    SuiteSpec(
        suite="longmemeval30",
        runner=run_longmemeval_predict_sweep,
        fixture_path="tools/kontext-v2/tests/fixtures/longmemeval_real_shape.json",
        dataset_path_arg="longmemeval_dataset_path",
        dataset_url_arg="longmemeval_dataset_url",
    ),
    SuiteSpec(
        suite="beam30",
        runner=run_beam_predict_sweep,
        fixture_path="tools/kontext-v2/tests/fixtures/beam_real_shape.json",
        dataset_path_arg="beam_dataset_path",
        extra_args={"session_only": False},
    ),
)


def load_approval_module():
    script_path = Path(__file__).with_name("judged_benchmark_approval_packet.py")
    spec = importlib.util.spec_from_file_location("judged_benchmark_approval_packet", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def chmod_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def approval_args(args: argparse.Namespace, suite: str, backend: str, bundle_path: Path, run_id: str) -> argparse.Namespace:
    stem = f"{run_id}-paid"
    run_output = args.approval_dir / f"{stem}-run.json"
    verification_output = args.approval_dir / f"{stem}-verification.json"
    mode = "beam-rubric" if suite == "beam30" else "answerer-judge"
    return argparse.Namespace(
        input_bundle=str(bundle_path),
        output=str(args.approval_dir / f"{run_id}-approval.json"),
        run_output=str(run_output),
        verification_output=str(verification_output),
        cutoffs=str(args.cutoff),
        max_questions=args.max_questions_per_suite,
        question_offset=0,
        answerer_model=args.answerer_model,
        judge_model=args.judge_model,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
        max_cost_usd=args.max_suite_cost_usd,
        mem0_target_accuracy=args.min_accuracy,
        min_accuracy=args.min_accuracy,
        mode=mode,
        judge_units_per_question=args.judge_units_per_question,
        answer_max_memories=args.answer_max_memories,
        answer_memory_max_chars=args.answer_memory_max_chars,
        answer_total_max_chars=args.answer_total_max_chars,
        temporal_fact_extraction=suite == "locomo30" and args.temporal_fact_extraction,
        beam_evidence_windows=suite == "beam30" and args.beam_evidence_windows,
        beam_answer_contract=suite == "beam30" and args.beam_answer_contract,
        beam_structured_evidence=suite == "beam30" and args.beam_structured_evidence,
        private_debug_output=None,
        omit_temperature=args.omit_temperature,
        answer_input_usd_per_1m=args.answer_input_usd_per_1m,
        answer_output_usd_per_1m=args.answer_output_usd_per_1m,
        judge_input_usd_per_1m=args.judge_input_usd_per_1m,
        judge_output_usd_per_1m=args.judge_output_usd_per_1m,
    )


def build_suite_bundle(args: argparse.Namespace, spec: SuiteSpec, backend: str, timestamp: str) -> dict[str, Any]:
    run_id = f"{spec.suite}-{backend}-{timestamp}"
    bundle_path = args.private_dir / f"{run_id}-private.json"
    output_dir = args.reports_dir / "benchmark-results" / run_id
    kwargs = dict(spec.extra_args or {})
    dataset_path = getattr(args, spec.dataset_path_arg) if spec.dataset_path_arg else None
    dataset_url = getattr(args, spec.dataset_url_arg) if spec.dataset_url_arg else None
    if spec.suite == "beam30":
        kwargs["beam_size"] = args.beam_size
        kwargs["offset"] = args.beam_offset
        kwargs["length"] = args.beam_length
        if dataset_path is None:
            dataset_path = DEFAULT_BEAM_CACHE_DIR / f"beam_{args.beam_size}_rows_{args.beam_offset}_{args.beam_length}.json"
    if dataset_url is not None:
        kwargs["dataset_url"] = dataset_url
    report = spec.runner(
        args.database_url,
        spec.fixture_path,
        output_dir,
        run_id,
        top_k_values=[args.cutoff],
        dataset_path=dataset_path,
        max_questions=args.max_questions_per_suite,
        judged_bundle_output=bundle_path,
        retrieval_backend=backend,
        mem0_retrieval_module=args.mem0_retrieval_module,
        **kwargs,
    )
    chmod_private(bundle_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    return {
        "suite": spec.suite,
        "backend": backend,
        "run_id": run_id,
        "private_bundle": str(bundle_path),
        "predict_report": report.get("report_paths", {}).get("json"),
        "private_bundle_questions": bundle.get("total_questions"),
        "retrieval_evaluable_questions": report.get("retrieval_evaluable_questions"),
        "top_k_values": report.get("top_k_values"),
    }


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    blocked_by = []
    database_url = os.environ.get(args.database_url_env, "")
    if not database_url:
        blocked_by.append(f"required database env var is missing: {args.database_url_env}")
    if args.execute_paid and not args.approve_cost:
        blocked_by.append("paid execution requires --approve-cost")
    if args.execute_paid and args.max_total_cost_usd <= 0:
        blocked_by.append("paid execution requires positive --max-total-cost-usd")
    if args.execute_paid and not os.environ.get(args.api_key_env):
        blocked_by.append(f"required provider env var is missing: {args.api_key_env}")

    args.database_url = database_url
    args.approval_dir.mkdir(parents=True, exist_ok=True)
    args.private_dir.mkdir(parents=True, exist_ok=True)

    approval_module = load_approval_module()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suite_rows = []
    approval_rows = []

    if not blocked_by:
        for spec in SUITES:
            for backend in ("kontext", "legacy-mem0-offline"):
                suite_row = build_suite_bundle(args, spec, backend, timestamp)
                suite_rows.append(suite_row)
                packet_args = approval_args(args, spec.suite, backend, Path(suite_row["private_bundle"]), suite_row["run_id"])
                packet = approval_module.build_packet(packet_args)
                write_json(Path(packet_args.output), packet)
                approval_rows.append(
                    {
                        "suite": spec.suite,
                        "backend": backend,
                        "approval_packet": packet_args.output,
                        "run_output": packet_args.run_output,
                        "verification_output": packet_args.verification_output,
                        "estimated_cost_usd": packet.get("estimated_cost_usd", {}).get("total_usd"),
                        "command_template": packet.get("command_template"),
                    }
                )

    total_estimated_cost = round(
        sum(float(row.get("estimated_cost_usd") or 0.0) for row in approval_rows),
        6,
    )
    if approval_rows and total_estimated_cost > args.max_total_cost_usd:
        blocked_by.append(f"estimated cost {total_estimated_cost} exceeds max_total_cost_usd {args.max_total_cost_usd}")
    for row in suite_rows:
        if int(row.get("private_bundle_questions") or 0) < args.max_questions_per_suite:
            blocked_by.append(
                f"{row['suite']} {row['backend']} has {row.get('private_bundle_questions')} questions, "
                f"expected {args.max_questions_per_suite}"
            )

    return {
        "ok": not blocked_by,
        "mode": "judged-backend-compare-prepare",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_model_calls": False,
        "execute_paid_requested": bool(args.execute_paid),
        "paid_execution_ready": bool(args.execute_paid and not blocked_by),
        "blocked_by": blocked_by,
        "summary": {
            "suite_count": len(SUITES),
            "backend_count": 2,
            "private_bundle_count": len(suite_rows),
            "approval_packet_count": len(approval_rows),
            "cutoff": args.cutoff,
            "max_questions_per_suite": args.max_questions_per_suite,
            "answerer_model": args.answerer_model,
            "judge_model": args.judge_model,
            "estimated_total_cost_usd": total_estimated_cost,
            "max_total_cost_usd": args.max_total_cost_usd,
        },
        "suites": suite_rows,
        "approval_packets": approval_rows,
        "notes": [
            "This prepare script writes private benchmark bundles and sanitized approval packets only.",
            "legacy-mem0-offline uses the local legacy ranker; it does not re-enable or write to Mem0 runtime.",
            "Command templates reference env var names only, never secret values.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare paired Kontext-vs-offline-Mem0 judged N30 comparison bundles.")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--private-dir", type=Path, default=DEFAULT_PRIVATE_DIR)
    parser.add_argument("--approval-dir", type=Path, default=DEFAULT_APPROVAL_DIR)
    parser.add_argument("--database-url-env", default="KONTEXT_V2_DATABASE_URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="https://api.openai.com/v1/chat/completions")
    parser.add_argument("--answerer-model", default="gpt-5-mini")
    parser.add_argument("--judge-model", default="gpt-5-mini")
    parser.add_argument("--max-questions-per-suite", type=int, default=30)
    parser.add_argument("--cutoff", type=int, default=20)
    parser.add_argument("--min-accuracy", type=float, default=0.80)
    parser.add_argument("--max-suite-cost-usd", type=float, default=0.25)
    parser.add_argument("--max-total-cost-usd", type=float, default=1.25)
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--approve-cost", action="store_true")
    parser.add_argument("--mem0-retrieval-module", type=Path, default=DEFAULT_MEM0_RETRIEVAL_MODULE)
    parser.add_argument("--locomo-dataset-path", default=str(DEFAULT_LOCOMO_CACHE_PATH))
    parser.add_argument("--locomo-dataset-url", default=None)
    parser.add_argument("--longmemeval-dataset-path", default=str(DEFAULT_LONGMEMEVAL_CACHE_PATH))
    parser.add_argument("--longmemeval-dataset-url", default=None)
    parser.add_argument("--beam-dataset-path", default=None)
    parser.add_argument("--beam-size", default="1M", choices=["100K", "500K", "1M", "10M"])
    parser.add_argument("--beam-offset", type=int, default=0)
    parser.add_argument("--beam-length", type=int, default=2)
    parser.add_argument("--judge-units-per-question", type=float)
    parser.add_argument("--answer-max-memories", type=int)
    parser.add_argument("--answer-memory-max-chars", type=int)
    parser.add_argument("--answer-total-max-chars", type=int)
    parser.add_argument("--temporal-fact-extraction", action="store_true")
    parser.add_argument("--beam-evidence-windows", action="store_true")
    parser.add_argument("--beam-answer-contract", action="store_true")
    parser.add_argument("--beam-structured-evidence", action="store_true")
    parser.add_argument("--omit-temperature", action="store_true")
    parser.add_argument("--answer-input-usd-per-1m", type=float, default=0.25)
    parser.add_argument("--answer-output-usd-per-1m", type=float, default=2.0)
    parser.add_argument("--judge-input-usd-per-1m", type=float, default=0.25)
    parser.add_argument("--judge-output-usd-per-1m", type=float, default=2.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = build_manifest(args)
    write_json(args.output, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if manifest.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
