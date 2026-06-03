from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple


FORBIDDEN_RAW_KEYS = {
    "question",
    "ground_truth_answer",
    "retrieved_memories_by_top_k",
    "memory",
    "messages",
    "content",
    "conversation",
}

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

DEFAULT_SUITES = [
    "locomo1|locomo1-20260521-private.json|locomo1-predict-20260521.json|locomo1-auto-mock-run-20260521.json|locomo1-auto-mock-verification-20260521.json|locomo1-paid-approval-packet-20260521.json",
    "longmemeval1|longmemeval1-20260521-private.json|longmemeval1-predict-20260521.json|longmemeval1-auto-mock-run-20260521.json|longmemeval1-auto-mock-verification-20260521.json|longmemeval1-paid-approval-packet-20260521.json",
]


class SuiteSpec(NamedTuple):
    name: str
    bundle: str
    predict: str
    mock: str
    verification: str
    approval: str


def parse_suite_spec(value: str) -> SuiteSpec:
    parts = value.split("|")
    if len(parts) != 6 or any(not part.strip() for part in parts):
        raise ValueError("suite must be name|bundle|predict|mock|verification|approval")
    return SuiteSpec(*(part.strip() for part in parts))


def load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json at line {exc.lineno}"
    if not isinstance(payload, dict):
        return None, "json root is not an object"
    return payload, None


def gate(ok: bool, **details: Any) -> dict[str, Any]:
    return {"ok": bool(ok), **details}


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def private_permissions(path: Path, required: bool) -> dict[str, Any]:
    if not path.exists():
        return gate(False, required=required, reason="missing")
    mode = path.stat().st_mode & 0o777
    supported = os.name != "nt"
    if not required:
        return gate(True, required=False, check_supported=supported, mode_octal=oct(mode))
    if not supported:
        return gate(True, required=True, check_supported=False, mode_octal=oct(mode))
    return gate((mode & 0o077) == 0, required=True, check_supported=True, mode_octal=oct(mode))


def raw_payload_hits(value: Any) -> list[str]:
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = f"{path}/{key}" if path else str(key)
                if key in FORBIDDEN_RAW_KEYS:
                    hits.append(child_path)
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
        elif isinstance(node, str):
            if any(pattern.search(node) for pattern in SECRET_PATTERNS):
                hits.append(path)

    walk(value, "")
    return hits


def public_raw_scan(named_payloads: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    hits: list[str] = []
    scanned = 0
    for name, payload in named_payloads.items():
        if payload is None:
            continue
        scanned += 1
        hits.extend(f"{name}/{hit}" for hit in raw_payload_hits(payload))
    return {
        "ok": len(hits) == 0,
        "files_scanned": scanned,
        "hit_count": len(hits),
        "hit_paths": hits[:50],
    }


def audit_private_bundle(payload: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    if payload is None:
        return gate(False, reason=error)
    questions = payload.get("questions")
    question_count = len(questions) if isinstance(questions, list) else 0
    return gate(
        payload.get("mode") == "private-judged-input-bundle"
        and payload.get("contains_live_user_memory") is False
        and payload.get("runs_model_calls") is False
        and question_count > 0,
        mode=payload.get("mode"),
        dataset=payload.get("dataset"),
        run_id=payload.get("run_id"),
        contains_live_user_memory=payload.get("contains_live_user_memory"),
        runs_model_calls=payload.get("runs_model_calls"),
        question_count=question_count,
    )


def audit_predict_report(payload: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    if payload is None:
        return gate(False, reason=error)
    top_k_values = payload.get("top_k_values")
    ok_value = payload.get("ok")
    runs_model_calls = payload.get("runs_model_calls")
    return gate(
        ok_value is not False
        and runs_model_calls is not True
        and isinstance(top_k_values, list)
        and bool(top_k_values),
        mode=payload.get("mode"),
        dataset=payload.get("dataset"),
        run_id=payload.get("run_id"),
        runs_model_calls=runs_model_calls,
        top_k_values=top_k_values if isinstance(top_k_values, list) else [],
    )


def audit_mock_run(payload: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    if payload is None:
        return gate(False, reason=error)
    return gate(
        payload.get("ok") is True
        and payload.get("mode") == "mock-judged-benchmark-run"
        and payload.get("provider") == "mock"
        and payload.get("runs_model_calls") is False
        and safe_int(payload.get("selected_questions")) > 0,
        dataset=payload.get("dataset"),
        run_id=payload.get("run_id"),
        provider=payload.get("provider"),
        runs_model_calls=payload.get("runs_model_calls"),
        selected_questions=safe_int(payload.get("selected_questions")),
    )


def audit_mock_verification(payload: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    if payload is None:
        return gate(False, reason=error)
    gates = payload.get("gates") if isinstance(payload.get("gates"), dict) else {}

    def failed_gate(name: str) -> bool:
        row = gates.get(name) if isinstance(gates.get(name), dict) else {}
        return row.get("ok") is False

    raw_payload = gates.get("raw_payload") if isinstance(gates.get("raw_payload"), dict) else {}
    return gate(
        payload.get("ok") is False
        and payload.get("runs_model_calls") is False
        and failed_gate("model_calls")
        and failed_gate("provider")
        and failed_gate("usage")
        and raw_payload.get("ok") is True,
        source_mode=payload.get("source_mode"),
        runs_model_calls=payload.get("runs_model_calls"),
        expected_failed_gates=[
            name for name in ("model_calls", "provider", "usage") if failed_gate(name)
        ],
        raw_payload_ok=raw_payload.get("ok"),
    )


def audit_approval_packet(payload: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    if payload is None:
        return gate(False, reason=error)
    estimated_cost_payload = payload.get("estimated_cost_usd")
    if isinstance(estimated_cost_payload, dict):
        estimated_cost = safe_float(estimated_cost_payload.get("total_usd"))
    else:
        estimated_cost = safe_float(estimated_cost_payload)
    max_cost = safe_float(payload.get("max_cost_usd"))
    required_env_vars = payload.get("required_env_vars")
    total_calls = 0
    calls = payload.get("estimated_llm_calls")
    if isinstance(calls, dict):
        total_calls = safe_int(calls.get("total_calls"))
    cost_ok = estimated_cost is not None and max_cost is not None and estimated_cost <= max_cost
    return gate(
        payload.get("ok") is True
        and payload.get("mode") == "judged-benchmark-approval-packet"
        and payload.get("approval_required") is True
        and payload.get("runs_model_calls") is False
        and safe_int(payload.get("selected_questions")) > 0
        and total_calls > 0
        and cost_ok
        and isinstance(required_env_vars, list)
        and bool(required_env_vars),
        dataset=payload.get("dataset"),
        run_id=payload.get("run_id"),
        approval_required=payload.get("approval_required"),
        runs_model_calls=payload.get("runs_model_calls"),
        selected_questions=safe_int(payload.get("selected_questions")),
        estimated_cost_usd=estimated_cost,
        max_cost_usd=max_cost,
        estimated_total_calls=total_calls,
        required_env_vars=required_env_vars if isinstance(required_env_vars, list) else [],
        benchmark_mode=payload.get("benchmark_mode"),
        judge_units_total=payload.get("judge_units_total"),
        judge_units_per_question=payload.get("judge_units_per_question"),
    )


def audit_suite(spec: SuiteSpec, private_dir: Path, report_dir: Path, require_private_permissions: bool) -> dict[str, Any]:
    paths = {
        "private_bundle": private_dir / spec.bundle,
        "predict_report": report_dir / spec.predict,
        "mock_run": report_dir / spec.mock,
        "mock_verification": report_dir / spec.verification,
        "approval_packet": report_dir / spec.approval,
    }
    private_payload, private_error = load_json(paths["private_bundle"])
    predict_payload, predict_error = load_json(paths["predict_report"])
    mock_payload, mock_error = load_json(paths["mock_run"])
    verification_payload, verification_error = load_json(paths["mock_verification"])
    approval_payload, approval_error = load_json(paths["approval_packet"])
    raw_scan = public_raw_scan(
        {
            spec.predict: predict_payload,
            spec.mock: mock_payload,
            spec.verification: verification_payload,
            spec.approval: approval_payload,
        }
    )
    gates = {
        "private_bundle": audit_private_bundle(private_payload, private_error),
        "private_permissions": private_permissions(paths["private_bundle"], require_private_permissions),
        "predict_report": audit_predict_report(predict_payload, predict_error),
        "mock_run": audit_mock_run(mock_payload, mock_error),
        "mock_verification_expected_failure": audit_mock_verification(verification_payload, verification_error),
        "approval_packet": audit_approval_packet(approval_payload, approval_error),
        "public_raw_scan": gate(raw_scan["ok"], hit_count=raw_scan["hit_count"], files_scanned=raw_scan["files_scanned"]),
    }
    ready = all(row["ok"] for row in gates.values())
    return {
        "ok": ready,
        "ready_for_paid_judged_run": ready,
        "files": {key: path.name for key, path in paths.items()},
        "gates": gates,
        "public_raw_scan": raw_scan,
    }


def build_audit(args: argparse.Namespace) -> dict[str, Any]:
    private_dir = Path(args.private_dir)
    report_dir = Path(args.report_dir)
    suite_specs = [parse_suite_spec(value) for value in (args.suite or DEFAULT_SUITES)]
    suites = {
        spec.name: audit_suite(spec, private_dir, report_dir, bool(args.require_private_permissions))
        for spec in suite_specs
    }
    ready_count = sum(1 for suite in suites.values() if suite["ready_for_paid_judged_run"])
    blocked_by: list[str] = []
    if ready_count != len(suites):
        blocked_by.append("readiness gate failures")
    if ready_count:
        blocked_by.append("explicit paid-run approval")
    return {
        "ok": ready_count == len(suites),
        "mode": "judged-readiness-audit",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_model_calls": False,
        "summary": {
            "suites_total": len(suites),
            "suites_ready_for_paid_judged_run": ready_count,
            "actual_judged_accuracy_proven": False,
            "blocked_by": blocked_by,
        },
        "suites": suites,
        "notes": [
            "This audit consolidates no-cost readiness evidence only.",
            "It does not prove official-style judged answer accuracy.",
            "A paid judged micro-run still requires explicit approval and cost ceiling.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit no-cost readiness evidence for judged benchmark micro-runs.")
    parser.add_argument("--private-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output")
    parser.add_argument(
        "--suite",
        action="append",
        help="Suite mapping: name|bundle|predict|mock|verification|approval. Defaults to current staged LoCoMo and LongMemEval files.",
    )
    parser.add_argument("--skip-private-permissions", dest="require_private_permissions", action="store_false")
    parser.set_defaults(require_private_permissions=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    audit = build_audit(args)
    text = json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if audit.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
