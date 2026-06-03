from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


FORBIDDEN_RAW_KEYS = {
    "question",
    "ground_truth_answer",
    "retrieved_memories_by_top_k",
    "memory",
    "messages",
    "content",
    "conversation",
}
FORBIDDEN_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[a-fA-F0-9]{64,}\b"),
]
DEFAULT_MIN_QUESTIONS = 30
DEFAULT_MIN_ACCURACY = 0.80


def load_report(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("judged report must be a JSON object")
    return payload


def cutoff_summary(report: dict[str, Any], cutoff: int) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    row = summary.get(str(cutoff)) if isinstance(summary.get(str(cutoff)), dict) else {}
    total = int(row.get("total") or 0)
    passed = int(row.get("passed") or 0)
    if "accuracy" in row:
        accuracy = float(row.get("accuracy") or 0.0)
    else:
        accuracy = passed / total if total else 0.0
    return {
        "cutoff": cutoff,
        "total": total,
        "passed": passed,
        "accuracy": round(accuracy, 4),
        "avg_score": round(float(row.get("avg_score") or 0.0), 4),
    }


def actual_cost_usd(report: dict[str, Any]) -> float | None:
    estimated = report.get("estimated_cost_usd")
    if not isinstance(estimated, dict):
        return None
    try:
        return float(estimated.get("total_usd"))
    except (TypeError, ValueError):
        return None


def total_usage_tokens(report: dict[str, Any]) -> int:
    usage = report.get("actual_usage") if isinstance(report.get("actual_usage"), dict) else {}
    try:
        return int(usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def completed_model_calls(report: dict[str, Any]) -> int:
    try:
        return int(report.get("completed_calls") or 0)
    except (TypeError, ValueError):
        return 0


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
            if any(pattern.search(node) for pattern in FORBIDDEN_VALUE_PATTERNS):
                hits.append(path)

    walk(value, "")
    return hits


def pass_gate(ok: bool, **details: Any) -> dict[str, Any]:
    return {"ok": bool(ok), **details}


def build_verification(report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary = cutoff_summary(report, args.cutoff)
    cost = actual_cost_usd(report)
    usage_tokens = total_usage_tokens(report)
    raw_hits = raw_payload_hits(report)
    runs_model_calls = report.get("runs_model_calls") is True
    completed_calls = completed_model_calls(report)
    provider = str(report.get("provider") or "")
    answerer_model = str(report.get("answerer_model") or "")
    judge_model = str(report.get("judge_model") or "")
    selected_questions = int(report.get("selected_questions") or summary["total"] or 0)
    expected_questions = getattr(args, "expected_questions", None)

    gates = {
        "model_calls": pass_gate(
            bool(args.require_model_calls) and runs_model_calls and completed_calls > 0,
            required=True,
            flag_present=bool(args.require_model_calls),
            actual=runs_model_calls,
            completed_calls=completed_calls,
        ),
        "usage": pass_gate(bool(args.require_usage) and usage_tokens > 0, required=True, flag_present=bool(args.require_usage), total_tokens=usage_tokens),
        "provider": pass_gate(bool(args.expected_provider) and provider == args.expected_provider, expected=args.expected_provider, actual=provider or None),
        "answerer_model": pass_gate(bool(getattr(args, "expected_answerer_model", None)) and answerer_model == args.expected_answerer_model, expected=getattr(args, "expected_answerer_model", None), actual=answerer_model or None),
        "judge_model": pass_gate(bool(getattr(args, "expected_judge_model", None)) and judge_model == args.expected_judge_model, expected=getattr(args, "expected_judge_model", None), actual=judge_model or None),
        "question_count": pass_gate(
            expected_questions is not None and selected_questions == expected_questions and selected_questions >= args.min_questions,
            min_questions=args.min_questions,
            expected_questions=expected_questions,
            selected_questions=selected_questions,
        ),
        "accuracy": pass_gate(summary["accuracy"] >= args.min_accuracy, min_accuracy=args.min_accuracy, actual=summary["accuracy"]),
        "raw_payload": pass_gate(len(raw_hits) == 0, hit_count=len(raw_hits), hit_paths=raw_hits[:20]),
        "cost": pass_gate(args.max_cost_usd is not None and cost is not None and cost <= args.max_cost_usd, max_cost_usd=args.max_cost_usd, actual_cost_usd=cost),
    }
    target = args.mem0_target_accuracy
    delta = round(summary["accuracy"] - target, 4) if target is not None else None
    summary["mem0_target_accuracy"] = target
    summary["accuracy_delta_vs_mem0_target"] = delta
    gates["mem0_target"] = pass_gate(
        target is not None and summary["accuracy"] >= target,
        target_accuracy=target,
        actual=summary["accuracy"],
        delta=delta,
    )

    return {
        "ok": all(gate["ok"] for gate in gates.values()),
        "mode": "judged-benchmark-verification",
        "source_mode": report.get("mode"),
        "provider": provider or None,
        "runs_model_calls": runs_model_calls,
        "completed_calls": completed_calls,
        "dataset": report.get("dataset"),
        "run_id": report.get("run_id"),
        "retrieval_backend": report.get("retrieval_backend") or "kontext",
        "answerer_model": answerer_model or None,
        "judge_model": judge_model or None,
        "summary": summary,
        "estimated_cost_usd": {"total_usd": cost} if cost is not None else None,
        "actual_usage": {"total_tokens": usage_tokens},
        "gates": gates,
        "notes": [
            "This verifier checks whether a judged report is acceptable evidence.",
            "It does not call answerer or judge models.",
            "It intentionally reports raw payload key paths only, never raw payload values.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a sanitized judged benchmark report against evidence gates.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--cutoff", type=int, default=50)
    parser.add_argument("--min-accuracy", type=float, default=DEFAULT_MIN_ACCURACY)
    parser.add_argument("--mem0-target-accuracy", type=float)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--min-questions", type=int, default=DEFAULT_MIN_QUESTIONS)
    parser.add_argument("--require-model-calls", action="store_true")
    parser.add_argument("--require-usage", action="store_true")
    parser.add_argument("--expected-provider")
    parser.add_argument("--expected-answerer-model")
    parser.add_argument("--expected-judge-model")
    parser.add_argument("--expected-questions", type=int)
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    verification = build_verification(load_report(args.report), args)
    text = json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if verification.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
