from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {
    "question",
    "answer",
    "generated_answer",
    "ground_truth_answer",
    "memory",
    "memories",
    "retrieved_memories",
    "retrieved_memories_by_top_k",
    "content",
    "text",
    "judge_response",
    "prompt",
    "messages",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def score_bucket(score: float) -> str:
    if score >= 0.8:
        return "0.80-1.00"
    if score >= 0.5:
        return "0.50-0.79"
    if score > 0:
        return "0.01-0.49"
    return "0.00"


def raw_payload_hits(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if key in SENSITIVE_KEYS:
                hits.append(nested_path)
                continue
            hits.extend(raw_payload_hits(nested, nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            hits.extend(raw_payload_hits(nested, f"{path}[{index}]"))
    return hits


def cutoff_score(row: dict[str, Any], cutoff: str) -> float:
    cutoff_results = row.get("cutoff_results") if isinstance(row.get("cutoff_results"), dict) else {}
    result = cutoff_results.get(cutoff) if isinstance(cutoff_results.get(cutoff), dict) else {}
    return safe_float(result.get("score"))


def cutoff_pass(row: dict[str, Any], cutoff: str) -> bool:
    return cutoff_score(row, cutoff) >= 0.5


def cutoff_memories(row: dict[str, Any], cutoff: str) -> int:
    cutoff_results = row.get("cutoff_results") if isinstance(row.get("cutoff_results"), dict) else {}
    result = cutoff_results.get(cutoff) if isinstance(cutoff_results.get(cutoff), dict) else {}
    return safe_int(result.get("memories_evaluated"))


def transition_label(row: dict[str, Any], cutoffs: list[str]) -> str:
    bits = [f"k{cutoff}:{'pass' if cutoff_pass(row, cutoff) else 'fail'}" for cutoff in cutoffs]
    return "|".join(bits)


def best_cutoff(row: dict[str, Any], cutoffs: list[str]) -> str:
    scores = [(cutoff_score(row, cutoff), cutoff) for cutoff in cutoffs]
    scores.sort(key=lambda item: (item[0], -safe_int(item[1])), reverse=True)
    return scores[0][1] if scores else "none"


def id_hash(row: dict[str, Any]) -> str:
    value = row.get("question_hash") or row.get("question_id") or row.get("ground_truth_hash") or "unknown"
    return str(value)[:16]


def build_audit(report: dict[str, Any], verification: dict[str, Any] | None = None) -> dict[str, Any]:
    questions = report.get("questions") if isinstance(report.get("questions"), list) else []
    top_k_values = [str(item) for item in report.get("top_k_values") or []]
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    verification_summary = verification.get("summary") if isinstance(verification, dict) and isinstance(verification.get("summary"), dict) else {}
    raw_hits = raw_payload_hits(report)

    transition_counts: Counter[str] = Counter()
    best_cutoff_counts: Counter[str] = Counter()
    oracle_passes = 0
    category_counts: Counter[str] = Counter()
    category_passes_by_cutoff: dict[str, Counter[str]] = {cutoff: Counter() for cutoff in top_k_values}
    score_buckets_by_cutoff: dict[str, Counter[str]] = {cutoff: Counter() for cutoff in top_k_values}
    memory_counts_by_cutoff: dict[str, Counter[str]] = {cutoff: Counter() for cutoff in top_k_values}
    failure_ids_by_cutoff: dict[str, list[str]] = {cutoff: [] for cutoff in top_k_values}

    for row in questions:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "unknown")
        category_counts[category] += 1
        transition_counts[transition_label(row, top_k_values)] += 1
        best_cutoff_counts[best_cutoff(row, top_k_values)] += 1
        if any(cutoff_pass(row, cutoff) for cutoff in top_k_values):
            oracle_passes += 1
        for cutoff in top_k_values:
            score = cutoff_score(row, cutoff)
            score_buckets_by_cutoff[cutoff][score_bucket(score)] += 1
            memory_count = cutoff_memories(row, cutoff)
            memory_counts_by_cutoff[cutoff][str(memory_count)] += 1
            if cutoff_pass(row, cutoff):
                category_passes_by_cutoff[cutoff][category] += 1
            else:
                failure_ids_by_cutoff[cutoff].append(id_hash(row))

    cutoff_summaries = {}
    for cutoff in top_k_values:
        cutoff_summary = summary.get(cutoff) if isinstance(summary.get(cutoff), dict) else {}
        category_rates = {}
        for category, total in category_counts.items():
            passed = category_passes_by_cutoff[cutoff][category]
            category_rates[category] = {
                "passed": passed,
                "total": total,
                "accuracy": round(passed / total, 4) if total else 0.0,
            }
        cutoff_summaries[cutoff] = {
            "passed": safe_int(cutoff_summary.get("passed")),
            "total": safe_int(cutoff_summary.get("total")),
            "accuracy": safe_float(cutoff_summary.get("accuracy")),
            "avg_score": safe_float(cutoff_summary.get("avg_score")),
            "score_buckets": dict(sorted(score_buckets_by_cutoff[cutoff].items())),
            "memories_evaluated_counts": dict(sorted(memory_counts_by_cutoff[cutoff].items(), key=lambda item: safe_int(item[0]))),
            "category_rates": category_rates,
            "failed_question_hashes": sorted(failure_ids_by_cutoff[cutoff])[:50],
        }

    best_cutoff_name = "none"
    if cutoff_summaries:
        best_cutoff_name = max(cutoff_summaries, key=lambda cutoff: (cutoff_summaries[cutoff]["accuracy"], cutoff_summaries[cutoff]["avg_score"]))

    likely_causes: list[str] = []
    if best_cutoff_name != str(max((safe_int(cutoff) for cutoff in top_k_values), default=0)):
        likely_causes.append("larger top_k did not improve accuracy; too much context or answer/judge behavior is plausible")
    if cutoff_summaries.get(best_cutoff_name, {}).get("accuracy", 0.0) < 0.8:
        likely_causes.append("best observed cutoff is still below target; retrieval/prompt quality is insufficient for this gate")
    if raw_hits:
        likely_causes.append("report contains raw-payload key paths; public audit must remain sanitized")

    return {
        "ok": False,
        "mode": "judged-failure-audit",
        "created_at": utc_now(),
        "runs_model_calls": False,
        "source": {
            "run_id": report.get("run_id"),
            "dataset": report.get("dataset"),
            "provider": report.get("provider"),
            "answerer_model": report.get("answerer_model"),
            "judge_model": report.get("judge_model"),
            "selected_questions": safe_int(report.get("selected_questions")),
            "top_k_values": [safe_int(item) for item in top_k_values],
        },
        "usage": {
            "actual_usage": report.get("actual_usage") if isinstance(report.get("actual_usage"), dict) else {},
            "estimated_cost_usd": report.get("estimated_cost_usd") if isinstance(report.get("estimated_cost_usd"), dict) else {},
        },
        "verification_summary": verification_summary,
        "cutoffs": cutoff_summaries,
        "transition_counts": dict(sorted(transition_counts.items())),
        "best_cutoff_counts": dict(sorted(best_cutoff_counts.items())),
        "best_cutoff_by_accuracy": best_cutoff_name,
        "oracle_best_per_question": {
            "passed": oracle_passes,
            "total": len(questions),
            "accuracy": round(oracle_passes / len(questions), 4) if questions else 0.0,
        },
        "raw_payload_key_hit_count": len(raw_hits),
        "raw_payload_key_hit_paths": raw_hits[:50],
        "likely_causes": likely_causes,
        "notes": [
            "No raw question text, generated answers, judge responses, or memory text are emitted.",
            "Question identifiers are truncated hashes only.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a sanitized judged benchmark failure audit.")
    parser.add_argument("--paid-run", required=True)
    parser.add_argument("--verification")
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    audit = build_audit(load_json(args.paid_run), load_json(args.verification) if args.verification else None)
    if args.output:
        write_json(args.output, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
