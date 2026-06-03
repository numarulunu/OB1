from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_CUTOFFS = "10,20,50,200"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.0-flash-001"
MAX_TOTAL_COST_CEILING_USD = 1.00
MIN_QUESTIONS_PER_SUITE = 30
SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class SuiteSpec:
    name: str
    dataset_kind: str
    run_id: str
    max_cost_usd: float
    private_bundle: Path
    predict_report: Path
    mock_run: Path
    mock_verification: Path
    approval_packet: Path
    paid_run: Path
    paid_verification: Path
    mode: str | None = None
    judge_units_per_question: float | None = None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_cutoffs(value: str | None) -> list[int]:
    raw = value or DEFAULT_CUTOFFS
    cutoffs = sorted({min(max(int(item.strip()), 1), 200) for item in raw.split(",") if item.strip()})
    if not cutoffs:
        raise ValueError("at least one cutoff is required")
    return cutoffs


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_float(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 6)


def path_text(path: Path) -> str:
    return str(path).replace("\\", "/")


def script_path(name: str) -> str:
    return path_text(SCRIPT_DIR / name)


def chmod_private(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        # Windows and some mounted filesystems may not support POSIX modes.
        pass


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def suite_specs(args: argparse.Namespace, timestamp: str) -> list[SuiteSpec]:
    report_dir = Path(args.reports_dir) / "judged-plans"
    private_dir = Path(args.private_dir)
    prefix = args.run_id_prefix
    return [
        SuiteSpec(
            name="locomo30",
            dataset_kind="locomo",
            run_id=f"{prefix}-locomo30-{timestamp}",
            max_cost_usd=float(args.max_locomo_cost_usd),
            private_bundle=private_dir / f"locomo30-{timestamp}-private.json",
            predict_report=report_dir / f"locomo30-{timestamp}-predict.json",
            mock_run=report_dir / f"locomo30-{timestamp}-mock-run.json",
            mock_verification=report_dir / f"locomo30-{timestamp}-mock-verification.json",
            approval_packet=report_dir / f"locomo30-{timestamp}-approval.json",
            paid_run=report_dir / f"locomo30-{timestamp}-paid-run.json",
            paid_verification=report_dir / f"locomo30-{timestamp}-paid-verification.json",
        ),
        SuiteSpec(
            name="longmemeval30",
            dataset_kind="longmemeval",
            run_id=f"{prefix}-longmemeval30-{timestamp}",
            max_cost_usd=float(args.max_longmemeval_cost_usd),
            private_bundle=private_dir / f"longmemeval30-{timestamp}-private.json",
            predict_report=report_dir / f"longmemeval30-{timestamp}-predict.json",
            mock_run=report_dir / f"longmemeval30-{timestamp}-mock-run.json",
            mock_verification=report_dir / f"longmemeval30-{timestamp}-mock-verification.json",
            approval_packet=report_dir / f"longmemeval30-{timestamp}-approval.json",
            paid_run=report_dir / f"longmemeval30-{timestamp}-paid-run.json",
            paid_verification=report_dir / f"longmemeval30-{timestamp}-paid-verification.json",
        ),
        SuiteSpec(
            name="beam30",
            dataset_kind="beam",
            run_id=f"{prefix}-beam30-{timestamp}",
            max_cost_usd=float(args.max_beam_cost_usd),
            private_bundle=private_dir / f"beam30-{timestamp}-private.json",
            predict_report=report_dir / f"beam30-{timestamp}-predict.json",
            mock_run=report_dir / f"beam30-{timestamp}-mock-run.json",
            mock_verification=report_dir / f"beam30-{timestamp}-mock-verification.json",
            approval_packet=report_dir / f"beam30-{timestamp}-approval.json",
            paid_run=report_dir / f"beam30-{timestamp}-paid-run.json",
            paid_verification=report_dir / f"beam30-{timestamp}-paid-verification.json",
            mode="beam-rubric",
            judge_units_per_question=3.0,
        ),
    ]


def validate_args(args: argparse.Namespace, environ: dict[str, str]) -> list[str]:
    blocked_by: list[str] = []
    if int(args.max_questions_per_suite) < MIN_QUESTIONS_PER_SUITE:
        blocked_by.append("max questions per suite must be at least 30")
    if not environ.get(args.database_url_env):
        blocked_by.append(f"required database env var is missing: {args.database_url_env}")
    total_cost = float(args.max_locomo_cost_usd) + float(args.max_longmemeval_cost_usd) + float(args.max_beam_cost_usd)
    if total_cost > float(args.max_total_cost_usd):
        blocked_by.append("suite cost ceilings exceed max total cost ceiling")
    if float(args.max_total_cost_usd) > MAX_TOTAL_COST_CEILING_USD:
        blocked_by.append("max total cost ceiling must be <= 1.00 USD")
    if args.execute_paid:
        if not args.approve_cost:
            blocked_by.append("paid execution requires --approve-cost")
        if not environ.get(args.api_key_env):
            blocked_by.append(f"required provider env var is missing: {args.api_key_env}")
    return blocked_by


def suite_summary(spec: SuiteSpec, questions: int) -> dict[str, Any]:
    return {
        "suite": spec.name,
        "run_id": spec.run_id,
        "selected_questions": questions,
        "private_bundle": path_text(spec.private_bundle),
        "predict_report": path_text(spec.predict_report),
        "mock_run": path_text(spec.mock_run),
        "mock_verification": path_text(spec.mock_verification),
        "approval_packet": path_text(spec.approval_packet),
        "paid_run": path_text(spec.paid_run),
        "paid_verification": path_text(spec.paid_verification),
        "max_cost_usd": format_float(spec.max_cost_usd),
        "benchmark_mode": spec.mode or "answerer-judge",
    }


def build_plan(args: argparse.Namespace, environ: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    timestamp = args.timestamp or utc_timestamp()
    cutoffs = parse_cutoffs(args.cutoffs)
    specs = suite_specs(args, timestamp)
    blocked_by = validate_args(args, env)
    paid_enabled = bool(args.execute_paid and args.approve_cost and not blocked_by)
    return {
        "ok": not blocked_by,
        "mode": "judged-n30-cutover-prepare-plan",
        "runs_model_calls": False,
        "paid_execution_requested": bool(args.execute_paid),
        "paid_execution_enabled": paid_enabled,
        "approval_required": not paid_enabled,
        "timestamp": timestamp,
        "reports_dir": path_text(Path(args.reports_dir)),
        "private_dir": path_text(Path(args.private_dir)),
        "question_count_per_suite": int(args.max_questions_per_suite),
        "top_k_values": cutoffs,
        "required_env_vars": [args.database_url_env, args.api_key_env],
        "missing_env_vars": [name for name in [args.database_url_env, args.api_key_env] if not env.get(name)],
        "estimated_max_total_cost_usd": format_float(sum(spec.max_cost_usd for spec in specs)),
        "max_total_cost_usd": format_float(float(args.max_total_cost_usd)),
        "blocked_by": blocked_by,
        "suites": [suite_summary(spec, int(args.max_questions_per_suite)) for spec in specs],
        "notes": [
            "This plan does not call answerer or judge models.",
            "Private bundles must not be printed, committed, pasted, or saved to long-term memory.",
            "Paid execution requires --execute-paid, --approve-cost, provider env, and the hard total cost ceiling.",
        ],
    }


def command_result(stage: str, suite: str, result: dict[str, Any], allowed_returncodes: set[int]) -> dict[str, Any]:
    returncode = int(result.get("returncode") or 0)
    return {
        "stage": stage,
        "suite": suite,
        "ok": returncode in allowed_returncodes,
        "returncode": returncode,
        "stdout_bytes": int(result.get("stdout_bytes") or 0),
        "stderr_bytes": int(result.get("stderr_bytes") or 0),
    }


def run_command(argv: list[str], *, allowed_returncodes: set[int] | None = None) -> dict[str, Any]:
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    return {"returncode": proc.returncode, "stdout_bytes": len(proc.stdout or ""), "stderr_bytes": len(proc.stderr or "")}


def copy_predict_report(report: dict[str, Any], target: Path) -> None:
    source = None
    paths = report.get("report_paths") if isinstance(report.get("report_paths"), dict) else {}
    if paths.get("json"):
        source = Path(paths["json"])
    if source and source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return
    write_json(target, report)


def run_predict_suite(spec: SuiteSpec, args: argparse.Namespace, database_url: str) -> dict[str, Any]:
    if spec.dataset_kind == "locomo":
        from kontext_v2.benchmarks.locomo_predict import run_locomo_predict_sweep

        report = run_locomo_predict_sweep(
            database_url,
            fixture_path="tools/kontext-v2/tests/fixtures/locomo_tiny.json",
            output_dir=str(spec.predict_report.parent),
            run_id=spec.run_id,
            top_k_values=parse_cutoffs(args.cutoffs),
            dataset_url=args.locomo_dataset_url,
            max_questions=int(args.max_questions_per_suite),
            judged_bundle_output=str(spec.private_bundle),
        )
    elif spec.dataset_kind == "longmemeval":
        from kontext_v2.benchmarks.longmemeval_predict import run_longmemeval_predict_sweep

        report = run_longmemeval_predict_sweep(
            database_url,
            fixture_path="tools/kontext-v2/tests/fixtures/longmemeval_real_shape.json",
            output_dir=str(spec.predict_report.parent),
            run_id=spec.run_id,
            top_k_values=parse_cutoffs(args.cutoffs),
            dataset_url=args.longmemeval_dataset_url,
            max_questions=int(args.max_questions_per_suite),
            judged_bundle_output=str(spec.private_bundle),
        )
    else:
        from kontext_v2.benchmarks.beam_predict import run_beam_predict_sweep
        from kontext_v2.benchmarks.fixtures import DEFAULT_BEAM_CACHE_DIR

        report = run_beam_predict_sweep(
            database_url,
            fixture_path="tools/kontext-v2/tests/fixtures/beam_real_shape.json",
            dataset_path=str(
                DEFAULT_BEAM_CACHE_DIR / f"beam_{args.beam_size}_rows_{args.beam_offset}_{args.beam_length}.json"
            ),
            output_dir=str(spec.predict_report.parent),
            run_id=spec.run_id,
            top_k_values=parse_cutoffs(args.cutoffs),
            beam_size=args.beam_size,
            offset=int(args.beam_offset),
            length=int(args.beam_length),
            max_questions=int(args.max_questions_per_suite),
            question_types=args.beam_question_types,
            session_only=False,
            judged_bundle_output=str(spec.private_bundle),
        )
    copy_predict_report(report, spec.predict_report)
    return {"returncode": 0, "stdout_bytes": 0, "stderr_bytes": 0}


def mock_run_command(spec: SuiteSpec, args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        script_path("judged_benchmark_run.py"),
        "--input-bundle",
        path_text(spec.private_bundle),
        "--output",
        path_text(spec.mock_run),
        "--cutoffs",
        args.cutoffs,
        "--max-questions",
        str(args.max_questions_per_suite),
        "--execute",
        "--provider",
        "mock",
        "--verification-output",
        path_text(spec.mock_verification),
        "--verify-cutoff",
        str(parse_cutoffs(args.cutoffs)[-1]),
        "--verify-min-accuracy",
        str(args.min_accuracy),
        "--verify-mem0-target-accuracy",
        str(args.mem0_target_accuracy),
        "--verify-max-cost-usd",
        str(spec.max_cost_usd),
        "--verify-min-questions",
        str(args.max_questions_per_suite),
        "--verify-require-model-calls",
        "--verify-require-usage",
        "--verify-expected-provider",
        "openai-compatible",
    ]


def approval_command(spec: SuiteSpec, args: argparse.Namespace, base_url: str) -> list[str]:
    argv = [
        sys.executable,
        script_path("judged_benchmark_approval_packet.py"),
        "--input-bundle",
        path_text(spec.private_bundle),
        "--output",
        path_text(spec.approval_packet),
        "--run-output",
        path_text(spec.paid_run),
        "--verification-output",
        path_text(spec.paid_verification),
        "--cutoffs",
        args.cutoffs,
        "--max-questions",
        str(args.max_questions_per_suite),
        "--answerer-model",
        args.answerer_model,
        "--judge-model",
        args.judge_model,
        "--api-key-env",
        args.api_key_env,
        "--base-url",
        base_url,
        "--max-cost-usd",
        str(spec.max_cost_usd),
        "--mem0-target-accuracy",
        str(args.mem0_target_accuracy),
        "--min-accuracy",
        str(args.min_accuracy),
        "--answer-input-usd-per-1m",
        str(args.answer_input_usd_per_1m),
        "--answer-output-usd-per-1m",
        str(args.answer_output_usd_per_1m),
        "--judge-input-usd-per-1m",
        str(args.judge_input_usd_per_1m),
        "--judge-output-usd-per-1m",
        str(args.judge_output_usd_per_1m),
    ]
    if spec.mode:
        argv.extend(["--mode", spec.mode])
    if spec.judge_units_per_question is not None:
        argv.extend(["--judge-units-per-question", str(spec.judge_units_per_question)])
    return argv


def readiness_command(specs: list[SuiteSpec], args: argparse.Namespace, output: Path) -> list[str]:
    argv = [
        sys.executable,
        script_path("judged_readiness_audit.py"),
        "--private-dir",
        path_text(Path(args.private_dir)),
        "--report-dir",
        path_text(Path(args.reports_dir) / "judged-plans"),
        "--output",
        path_text(output),
    ]
    for spec in specs:
        argv.extend(
            [
                "--suite",
                "|".join(
                    [
                        spec.name,
                        spec.private_bundle.name,
                        spec.predict_report.name,
                        spec.mock_run.name,
                        spec.mock_verification.name,
                        spec.approval_packet.name,
                    ]
                ),
            ]
        )
    return argv


def batch_approval_command(specs: list[SuiteSpec], readiness_report: Path, output: Path) -> list[str]:
    argv = [
        sys.executable,
        script_path("judged_batch_approval_packet.py"),
        "--readiness-report",
        path_text(readiness_report),
        "--output",
        path_text(output),
    ]
    for spec in specs:
        argv.extend(["--packet", f"{spec.name}={path_text(spec.approval_packet)}"])
    return argv


def batch_execute_command(args: argparse.Namespace, packet: Path, output: Path, execute: bool) -> list[str]:
    argv = [
        sys.executable,
        script_path("judged_batch_execute.py"),
        "--batch-packet",
        path_text(packet),
        "--output",
        path_text(output),
        "--max-total-cost-usd",
        str(args.max_total_cost_usd),
        "--check-env",
    ]
    if execute:
        argv.extend(["--execute", "--approve-cost"])
    else:
        argv.append("--preflight")
    return argv


def batch_verify_command(packet: Path, output: Path) -> list[str]:
    return [
        sys.executable,
        script_path("judged_batch_verify.py"),
        "--batch-packet",
        path_text(packet),
        "--output",
        path_text(output),
    ]


def stage_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        if result.get("ok") is True:
            stage = str(result.get("stage") or "unknown")
            counts[stage] = counts.get(stage, 0) + 1
    return counts


def prepare(
    args: argparse.Namespace,
    *,
    runner: Callable[..., dict[str, Any]] = run_command,
    predict_runner: Callable[[SuiteSpec, argparse.Namespace, str], dict[str, Any]] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    plan = build_plan(args, env)
    if plan["blocked_by"]:
        return plan

    private_dir = Path(args.private_dir)
    report_dir = Path(args.reports_dir) / "judged-plans"
    private_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    chmod_private(private_dir, 0o700)

    timestamp = plan["timestamp"]
    specs = suite_specs(args, timestamp)
    results: list[dict[str, Any]] = []
    database_url = env[args.database_url_env]
    selected_predict_runner = predict_runner or run_predict_suite
    base_url = args.base_url or env.get(args.base_url_env) or DEFAULT_BASE_URL

    for spec in specs:
        if not args.skip_generation:
            result = selected_predict_runner(spec, args, database_url)
            row = command_result("predict_private_bundle", spec.name, result, {0})
            results.append(row)
            if not row["ok"]:
                break
        if spec.private_bundle.exists():
            chmod_private(spec.private_bundle, 0o600)
        result = runner(mock_run_command(spec, args), allowed_returncodes={0, 2})
        results.append(command_result("mock_run_expected_failure", spec.name, result, {0, 2}))
        result = runner(approval_command(spec, args, base_url), allowed_returncodes={0})
        results.append(command_result("approval_packet", spec.name, result, {0}))

    readiness_report = report_dir / f"judged-n30-readiness-{timestamp}.json"
    batch_packet = report_dir / f"judged-n30-batch-approval-{timestamp}.json"
    preflight_report = report_dir / f"judged-n30-preflight-{timestamp}.json"
    execution_report = report_dir / f"judged-n30-execution-{timestamp}.json"
    batch_verification = report_dir / f"judged-n30-batch-verification-{timestamp}.json"

    if all(row["ok"] for row in results):
        result = runner(readiness_command(specs, args, readiness_report), allowed_returncodes={0})
        results.append(command_result("readiness_audit", "batch", result, {0}))
    if all(row["ok"] for row in results):
        result = runner(batch_approval_command(specs, readiness_report, batch_packet), allowed_returncodes={0})
        results.append(command_result("batch_approval_packet", "batch", result, {0}))
    if all(row["ok"] for row in results):
        result = runner(batch_execute_command(args, batch_packet, preflight_report, execute=False), allowed_returncodes={0})
        results.append(command_result("batch_preflight", "batch", result, {0}))
    if args.execute_paid and all(row["ok"] for row in results):
        result = runner(batch_execute_command(args, batch_packet, execution_report, execute=True), allowed_returncodes={0})
        results.append(command_result("paid_batch_execution", "batch", result, {0}))
        if results[-1]["ok"]:
            result = runner(batch_verify_command(batch_packet, batch_verification), allowed_returncodes={0})
            results.append(command_result("batch_verification", "batch", result, {0}))

    manifest = {
        **plan,
        "ok": all(row["ok"] for row in results) and not plan["blocked_by"],
        "runs_model_calls": bool(args.execute_paid and any(row["stage"] == "paid_batch_execution" and row["ok"] for row in results)),
        "readiness_report": path_text(readiness_report),
        "batch_approval_packet": path_text(batch_packet),
        "preflight_report": path_text(preflight_report),
        "execution_report": path_text(execution_report) if args.execute_paid else None,
        "batch_verification_report": path_text(batch_verification) if args.execute_paid else None,
        "stage_counts": stage_counts(results),
        "stage_results": results,
        "blocked_by": plan["blocked_by"] + [f"{row['stage']} failed: {row['suite']}" for row in results if row.get("ok") is not True],
        "suites": [suite_summary(spec, int(args.max_questions_per_suite)) for spec in specs],
    }
    if args.output:
        write_json(args.output, manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare guarded n>=30 official-style judged cutover evidence.")
    parser.add_argument("--reports-dir", required=True)
    parser.add_argument("--private-dir", required=True)
    parser.add_argument("--database-url-env", default="KONTEXT_V2_DATABASE_URL")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--base-url-env", default="OPENROUTER_BASE_URL")
    parser.add_argument("--base-url")
    parser.add_argument("--answerer-model", default=DEFAULT_MODEL)
    parser.add_argument("--judge-model", default=DEFAULT_MODEL)
    parser.add_argument("--max-questions-per-suite", type=int, default=30)
    parser.add_argument("--cutoffs", default=DEFAULT_CUTOFFS)
    parser.add_argument("--max-locomo-cost-usd", type=float, default=0.12)
    parser.add_argument("--max-longmemeval-cost-usd", type=float, default=0.12)
    parser.add_argument("--max-beam-cost-usd", type=float, default=0.20)
    parser.add_argument("--max-total-cost-usd", type=float, default=0.50)
    parser.add_argument("--min-accuracy", type=float, default=0.80)
    parser.add_argument("--mem0-target-accuracy", type=float, default=0.80)
    parser.add_argument("--answer-input-usd-per-1m", type=float, default=0.10)
    parser.add_argument("--answer-output-usd-per-1m", type=float, default=0.40)
    parser.add_argument("--judge-input-usd-per-1m", type=float, default=0.10)
    parser.add_argument("--judge-output-usd-per-1m", type=float, default=0.40)
    parser.add_argument("--locomo-dataset-url", default="https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json")
    parser.add_argument(
        "--longmemeval-dataset-url",
        default="https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json",
    )
    parser.add_argument("--beam-size", default="1M", choices=["100K", "500K", "1M", "10M"])
    parser.add_argument("--beam-offset", type=int, default=0)
    parser.add_argument("--beam-length", type=int, default=5)
    parser.add_argument(
        "--beam-question-types",
        default="information_extraction,knowledge_update,instruction_following,preference_following",
    )
    parser.add_argument("--run-id-prefix", default="judged-n30-cutover")
    parser.add_argument("--timestamp")
    parser.add_argument("--output")
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--approve-cost", action="store_true")
    parser.add_argument("--skip-generation", action="store_true", help="Use already-staged private bundles and predict reports.")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., dict[str, Any]] = run_command,
    predict_runner: Callable[[SuiteSpec, argparse.Namespace, str], dict[str, Any]] | None = None,
    environ: dict[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    manifest = prepare(args, runner=runner, predict_runner=predict_runner, environ=environ)
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    return 0 if manifest.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
