from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIN_JUDGED_QUESTIONS = 30
MIN_JUDGED_WEIGHTED_ACCURACY = 0.80


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def named_gate(readiness: dict[str, Any], name: str) -> dict[str, Any]:
    gates = readiness.get("gates") if isinstance(readiness.get("gates"), list) else []
    for gate in gates:
        if isinstance(gate, dict) and gate.get("name") == name:
            return gate
    return {}


def status_from_bool(ok: bool, missing: bool = False) -> str:
    if missing:
        return "missing"
    return "pass" if ok else "fail"


def gate_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    counts = readiness.get("status_counts") if isinstance(readiness.get("status_counts"), dict) else {}
    return {
        "ok": readiness.get("ok") is True,
        "ready_for_user_cutover_review": readiness.get("ready_for_user_cutover_review") is True,
        "status_counts": {
            "pass": safe_int(counts.get("pass")),
            "fail": safe_int(counts.get("fail")),
            "missing": safe_int(counts.get("missing")),
        },
    }


def judged_summary(readiness: dict[str, Any], runbook: dict[str, Any]) -> dict[str, Any]:
    judged_gate = named_gate(readiness, "official_judged_accuracy")
    gate_evidence = judged_gate.get("evidence") if isinstance(judged_gate.get("evidence"), dict) else {}
    runbook_summary = runbook.get("summary") if isinstance(runbook.get("summary"), dict) else {}
    estimated_calls = runbook_summary.get("estimated_llm_calls") if isinstance(runbook_summary.get("estimated_llm_calls"), dict) else {}
    estimated_tokens = runbook_summary.get("estimated_tokens") if isinstance(runbook_summary.get("estimated_tokens"), dict) else {}
    verified_questions = safe_int(
        runbook_summary.get("verified_questions")
        or runbook_summary.get("total_questions")
        or gate_evidence.get("total_questions")
    )
    verified_weighted_accuracy = safe_float(
        runbook_summary.get("verified_weighted_accuracy")
        or runbook_summary.get("weighted_accuracy")
        or gate_evidence.get("weighted_accuracy")
    )
    gate_blocked_by = gate_evidence.get("blocked_by") if isinstance(gate_evidence.get("blocked_by"), list) else []
    actual_proven = (
        runbook.get("actual_judged_accuracy_proven") is True
        and (judged_gate.get("status") or "missing") == "pass"
        and verified_questions >= MIN_JUDGED_QUESTIONS
        and verified_weighted_accuracy >= MIN_JUDGED_WEIGHTED_ACCURACY
        and not gate_blocked_by
    )
    return {
        "status": judged_gate.get("status") or "missing",
        "actual_judged_accuracy_proven": actual_proven,
        "claimed_judged_accuracy_proven": runbook.get("actual_judged_accuracy_proven") is True or gate_evidence.get("actual_judged_accuracy_proven") is True,
        "verified_questions": verified_questions,
        "min_questions": MIN_JUDGED_QUESTIONS,
        "verified_weighted_accuracy": verified_weighted_accuracy,
        "min_weighted_accuracy": MIN_JUDGED_WEIGHTED_ACCURACY,
        "ready_to_run_paid_batch": runbook.get("ready_to_run_paid_batch") is True,
        "required_user_approval": runbook.get("required_user_approval") is True,
        "missing_env_vars": [str(item) for item in runbook.get("missing_env_vars") or []],
        "estimated_calls": safe_int(estimated_calls.get("total_calls")),
        "estimated_tokens": safe_int(estimated_tokens.get("total_tokens")),
        "max_cost_usd_total": safe_float(runbook_summary.get("max_cost_usd_total")),
    }


def beam_summary(beam_cross_gate: dict[str, Any]) -> dict[str, Any]:
    summary = beam_cross_gate.get("summary") if isinstance(beam_cross_gate.get("summary"), dict) else {}
    top_k50 = round(safe_float(summary.get("top_k50_rate")), 4)
    top_k200 = round(safe_float(summary.get("top_k200_rate")), 4)
    mrr50 = round(safe_float(summary.get("mrr50")), 4)
    ok = beam_cross_gate.get("ok") is True
    status = "pass" if ok and top_k50 >= 1.0 and top_k200 >= 1.0 else "weak" if ok and top_k200 >= 1.0 else "fail"
    return {
        "status": status,
        "gate_ok": ok,
        "label": beam_cross_gate.get("label"),
        "question_count": safe_int(summary.get("question_count")),
        "top_k50_rate": top_k50,
        "top_k200_rate": top_k200,
        "mrr50": mrr50,
        "max_first_hit_rank": summary.get("max_first_hit_rank"),
        "missing_evidence_count": safe_int(summary.get("missing_evidence_count")),
    }


def state_behavior_summary(state_behavior: dict[str, Any] | None) -> dict[str, Any]:
    if not state_behavior:
        return {"status": "missing"}
    ok = state_behavior.get("ok") is True
    return {
        "status": status_from_bool(ok),
        "ok": ok,
        "passed": safe_int(state_behavior.get("passed")),
        "total": safe_int(state_behavior.get("total")),
        "writes_applied": safe_int(state_behavior.get("writes_applied")),
    }


def append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def build_scorecard(
    cutover_readiness: dict[str, Any],
    judged_runbook: dict[str, Any],
    beam_cross_gate: dict[str, Any],
    state_behavior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readiness = gate_summary(cutover_readiness)
    judged = judged_summary(cutover_readiness, judged_runbook)
    beam = beam_summary(beam_cross_gate)
    state = state_behavior_summary(state_behavior)
    production_cutover_enabled = cutover_readiness.get("production_cutover_enabled") is True

    blockers: list[str] = []
    quality_gaps: list[str] = []
    readiness_problem_count = (
        readiness["status_counts"]["fail"] + readiness["status_counts"]["missing"]
    )
    judged_gate_is_readiness_problem = judged["status"] != "pass"
    if not readiness["ok"] and (
        not judged_gate_is_readiness_problem or readiness_problem_count > 1
    ):
        append_unique(blockers, "cutover readiness is not green")
    if judged["status"] != "pass" or not judged["actual_judged_accuracy_proven"]:
        append_unique(blockers, "official judged accuracy is not proven")
    if not judged["actual_judged_accuracy_proven"]:
        for item in judged.get("missing_env_vars") or []:
            append_unique(blockers, f"{item} missing from run environment")
        if judged["required_user_approval"]:
            append_unique(blockers, "explicit paid-run approval required")
        append_unique(blockers, "paid verification reports missing")
    if production_cutover_enabled:
        append_unique(blockers, "production cutover is enabled unexpectedly")
    if state["status"] in {"fail", "missing"}:
        append_unique(blockers, "state/freshness/decay behavior gate is not green")
    if beam["top_k50_rate"] < 1.0:
        append_unique(quality_gaps, "BEAM hard-slice top-k50 remains below 1.0")
    if beam["status"] == "fail":
        append_unique(blockers, "BEAM hard-slice regression gate failed")

    gates = {
        "cutover_readiness": {
            "status": "pass" if readiness["ok"] else "fail",
            **readiness,
        },
        "official_judged_accuracy": judged,
        "beam_hard_slice": beam,
        "state_freshness_decay": state,
        "production_cutover": {
            "status": "fail" if production_cutover_enabled else "pass",
            "enabled": production_cutover_enabled,
        },
    }
    complete = not blockers and not quality_gaps
    return {
        "ok": complete,
        "mode": "kontext-goal-scorecard",
        "runs_model_calls": False,
        "generated_at": utc_now(),
        "goal_complete_candidate": complete,
        "production_cutover_enabled": production_cutover_enabled,
        "source_of_truth": "mem0",
        "summary": {
            "pass_count": sum(1 for gate in gates.values() if gate.get("status") == "pass"),
            "fail_count": sum(1 for gate in gates.values() if gate.get("status") == "fail"),
            "weak_count": sum(1 for gate in gates.values() if gate.get("status") == "weak"),
            "blocker_count": len(blockers),
            "quality_gap_count": len(quality_gaps),
        },
        "blockers": blockers,
        "quality_gaps": quality_gaps,
        "gates": gates,
        "notes": [
            "This scorecard does not call models.",
            "It reads sanitized reports only and reports aggregate status, not raw memory or benchmark text.",
            "Kontext remains shadow/canary-only unless the user explicitly approves cutover.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a sanitized Kontext V2 goal scorecard from current gate reports.")
    parser.add_argument("--cutover-readiness", required=True)
    parser.add_argument("--judged-runbook", required=True)
    parser.add_argument("--beam-cross-gate", required=True)
    parser.add_argument("--state-behavior")
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_scorecard(
        load_json(args.cutover_readiness),
        load_json(args.judged_runbook),
        load_json(args.beam_cross_gate),
        load_json(args.state_behavior) if args.state_behavior else None,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
