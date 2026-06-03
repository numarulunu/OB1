from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def verification_gate_status(report: dict[str, Any]) -> dict[str, Any]:
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
    raw_payload = gates.get("raw_payload") if isinstance(gates.get("raw_payload"), dict) else {}
    usage = gates.get("usage") if isinstance(gates.get("usage"), dict) else {}
    provider = gates.get("provider") if isinstance(gates.get("provider"), dict) else {}
    model_calls = gates.get("model_calls") if isinstance(gates.get("model_calls"), dict) else {}
    return {
        "raw_payload_ok": raw_payload.get("ok"),
        "usage_ok": usage.get("ok"),
        "provider_ok": provider.get("ok"),
        "model_calls_ok": model_calls.get("ok"),
        "failed_gates": [key for key, value in gates.items() if isinstance(value, dict) and not value.get("ok")],
    }


def public_source_row(path: str | Path, report: dict[str, Any], verification: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    cost = report.get("estimated_cost_usd") if isinstance(report.get("estimated_cost_usd"), dict) else {}
    verification_status = verification_gate_status(verification or {}) if verification else {}
    return {
        "path_hash": stable_hash(str(path)),
        "mode": str(report.get("mode") or ""),
        "ok": bool(report.get("ok")),
        "provider": str(report.get("provider") or ""),
        "runs_model_calls": bool(report.get("runs_model_calls")),
        "question_offset": safe_int(report.get("question_offset")),
        "planned_questions": safe_int(report.get("planned_questions")),
        "selected_questions": safe_int(report.get("selected_questions")),
        "completed_questions": safe_int(report.get("completed_questions") or len(report.get("questions") or [])),
        "completed_calls": safe_int(report.get("completed_calls")),
        "cost_usd": round(safe_float(cost.get("total_usd")), 6),
        "error_status": report.get("error_status"),
        "error_type": str(report.get("error_type") or ""),
        "summary": summary,
        "verification": verification_status,
    }


def question_key(row: dict[str, Any], source_index: int, row_index: int) -> str:
    question_hash = str(row.get("question_hash") or "").strip()
    if question_hash:
        return f"question_hash:{question_hash}"
    return f"source:{source_index}:row:{row_index}"


def build_combined_report(
    run_reports: list[str | Path],
    verification_reports: list[str | Path] | None = None,
    cutoff: str = "20",
    expected_questions: int | None = None,
    min_accuracy: float = 0.8,
    max_cost_usd: float | None = None,
) -> dict[str, Any]:
    verification_reports = verification_reports or []
    verifications = [load_json(path) for path in verification_reports]
    sources: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    blocked_by: list[str] = []
    score_sum = 0.0
    passed = 0
    total_cost = 0.0

    for source_index, path in enumerate(run_reports):
        report = load_json(path)
        verification = verifications[source_index] if source_index < len(verifications) else None
        source = public_source_row(path, report, verification)
        sources.append(source)
        total_cost += safe_float(source.get("cost_usd"))
        if verification:
            gates = source["verification"]
            if gates.get("raw_payload_ok") is not True:
                blocked_by.append(f"raw payload gate failed: source {source_index + 1}")
            if gates.get("usage_ok") is not True:
                blocked_by.append(f"usage gate failed: source {source_index + 1}")
            if gates.get("model_calls_ok") is False:
                blocked_by.append(f"model-call gate failed: source {source_index + 1}")
        for row_index, question in enumerate(report.get("questions") or []):
            if not isinstance(question, dict):
                continue
            result = (question.get("cutoff_results") or {}).get(str(cutoff))
            if not isinstance(result, dict):
                continue
            key = question_key(question, source_index, row_index)
            if key in seen:
                blocked_by.append(f"duplicate question: {stable_hash(key)}")
                continue
            seen.add(key)
            score = safe_float(result.get("score"))
            judgment = str(result.get("judgment") or "")
            judge_count = safe_int(result.get("judge_count"))
            judge_pass_count = safe_int(result.get("judge_pass_count"))
            row_passed = judgment == "PASS" or (judge_count > 0 and judge_pass_count >= max(1, (judge_count // 2) + 1))
            score_sum += score
            passed += int(row_passed)
            rows.append(
                {
                    "question_hash": str(question.get("question_hash") or ""),
                    "category": str(question.get("category") or "unknown"),
                    "score": round(score, 4),
                    "passed": row_passed,
                    "judge_count": judge_count,
                    "judge_pass_count": judge_pass_count,
                    "generated_answer_hash": str(result.get("generated_answer_hash") or ""),
                    "selector_status": str(result.get("beam_answer_selector_status") or ""),
                    "selected_candidate_index": safe_int(result.get("beam_answer_selected_candidate_index")),
                }
            )

    total = len(rows)
    accuracy = round(passed / total, 4) if total else 0.0
    avg_score = round(score_sum / total, 4) if total else 0.0
    if expected_questions is not None and total != expected_questions:
        blocked_by.append(f"expected {expected_questions} questions, got {total}")
    if accuracy < min_accuracy:
        blocked_by.append(f"accuracy {accuracy:.4f} below {min_accuracy:.4f}")
    if max_cost_usd is not None and total_cost > max_cost_usd:
        blocked_by.append(f"cost {total_cost:.6f} exceeds cap {max_cost_usd:.6f}")

    return {
        "ok": not blocked_by,
        "mode": "judged-chunk-combined-verification",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_model_calls": False,
        "cutoff": safe_int(cutoff),
        "expected_questions": expected_questions,
        "summary": {
            "passed": passed,
            "total": total,
            "accuracy": accuracy,
            "avg_score": avg_score,
            "min_accuracy": min_accuracy,
            "total_cost_usd": round(total_cost, 6),
            "max_cost_usd": max_cost_usd,
        },
        "blocked_by": blocked_by,
        "sources": sources,
        "questions": rows,
        "notes": [
            "This combines sanitized public judged-run rows only.",
            "Raw benchmark payloads, generated answers, prompts, judge text, memories, and secrets are not included.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Combine sanitized judged-run chunks into one public verification report.")
    parser.add_argument("--run-report", action="append", required=True)
    parser.add_argument("--verification-report", action="append")
    parser.add_argument("--cutoff", default="20")
    parser.add_argument("--expected-questions", type=int)
    parser.add_argument("--min-accuracy", type=float, default=0.8)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_combined_report(
        args.run_report,
        verification_reports=args.verification_report,
        cutoff=str(args.cutoff),
        expected_questions=args.expected_questions,
        min_accuracy=args.min_accuracy,
        max_cost_usd=args.max_cost_usd,
    )
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "output": args.output, "summary": report["summary"]}, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
