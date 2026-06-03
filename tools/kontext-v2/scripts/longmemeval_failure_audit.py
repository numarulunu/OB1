from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


COMMON_TERMS = {
    "about",
    "after",
    "answer",
    "before",
    "does",
    "from",
    "have",
    "longmemeval",
    "private",
    "question",
    "that",
    "their",
    "there",
    "these",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}

CLASSIFICATIONS = (
    "evidence_absent_top20",
    "evidence_present_truncated",
    "evidence_present_answer_wrong",
    "ambiguous_or_judge_mismatch",
)
RAW_PAYLOAD_KEYS = {
    "question",
    "ground_truth_answer",
    "retrieved_memories_by_top_k",
    "memory",
    "messages",
    "content",
    "generated_answer",
    "judge_response",
    "judge_responses",
    "prompt",
}
FORBIDDEN_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[a-fA-F0-9]{64,}\b"),
]


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def load_judged_runner():
    runner_path = Path(__file__).with_name("judged_benchmark_run.py")
    spec = importlib.util.spec_from_file_location("judged_benchmark_run", runner_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def content_terms(text: str) -> list[str]:
    terms: list[str] = []
    for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", str(text).lower()):
        if term not in COMMON_TERMS and term not in terms:
            terms.append(term)
    return terms[:80]


def term_coverage(terms: list[str], text: str) -> tuple[int, int]:
    lowered = str(text).lower()
    if not terms:
        return 0, 0
    covered = sum(1 for term in terms if term in lowered)
    return covered, len(terms)


def term_coverage_chunks(terms: list[str], chunks: list[str]) -> tuple[int, int, int]:
    if not terms:
        return 0, 0, 0
    found: set[str] = set()
    total_chars = 0
    for chunk in chunks:
        text = str(chunk)
        total_chars += len(text)
        lowered = text.lower()
        for term in terms:
            if term not in found and term in lowered:
                found.add(term)
    return len(found), len(terms), total_chars


def raw_payload_hits(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in RAW_PAYLOAD_KEYS:
                hits.append(child_path)
            hits.extend(raw_payload_hits(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(raw_payload_hits(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in FORBIDDEN_VALUE_PATTERNS):
            hits.append(path)
    return hits


def coverage_bucket(covered: int, total: int) -> str:
    if total <= 0:
        return "none"
    ratio = covered / total
    if ratio == 0:
        return "0"
    if ratio < 0.25:
        return "lt25"
    if ratio < 0.5:
        return "25_49"
    if ratio < 0.75:
        return "50_74"
    return "75_100"


def cutoff_result(row: dict[str, Any], cutoff: int) -> dict[str, Any]:
    results = row.get("cutoff_results") if isinstance(row.get("cutoff_results"), dict) else {}
    value = results.get(str(cutoff))
    return value if isinstance(value, dict) else {}


def bundle_question_map(private_bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = private_bundle.get("questions") if isinstance(private_bundle.get("questions"), list) else []
    return {str(row.get("question_id")): row for row in rows if isinstance(row, dict)}


def retrieved_rows(question: dict[str, Any], cutoff: int) -> list[dict[str, Any]]:
    by_top_k = question.get("retrieved_memories_by_top_k") if isinstance(question.get("retrieved_memories_by_top_k"), dict) else {}
    rows = by_top_k.get(str(cutoff))
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def retained_text(rows: list[dict[str, Any]], memory_max_chars: int, total_max_chars: int) -> str:
    chunks: list[str] = []
    remaining = max(total_max_chars, 0)
    for row in rows:
        text = str(row.get("memory") or "")
        chunk = text[: max(memory_max_chars, 0)]
        if remaining <= 0:
            break
        chunk = chunk[:remaining]
        remaining -= len(chunk)
        chunks.append(chunk)
    return "\n".join(chunks)


def classify_miss(
    private_question: dict[str, Any],
    cutoff: int,
    memory_max_chars: int,
    total_max_chars: int,
) -> tuple[str, dict[str, Any]]:
    rows = retrieved_rows(private_question, cutoff)
    retained = retained_text(rows, memory_max_chars, total_max_chars)
    terms = content_terms(str(private_question.get("ground_truth_answer") or ""))
    full_covered, full_total, full_chars = term_coverage_chunks(terms, [str(row.get("memory") or "") for row in rows])
    retained_covered, retained_total = term_coverage(terms, retained)
    if not rows or full_covered == 0:
        classification = "evidence_absent_top20"
    elif retained_covered == 0 or (full_chars > len(retained) and retained_covered < full_covered):
        classification = "evidence_present_truncated"
    elif retained_covered >= max(1, int(max(retained_total, 1) * 0.5)):
        classification = "evidence_present_answer_wrong"
    else:
        classification = "ambiguous_or_judge_mismatch"
    return classification, {
        "retrieved_count": len(rows),
        "full_coverage_bucket": coverage_bucket(full_covered, full_total),
        "retained_coverage_bucket": coverage_bucket(retained_covered, retained_total),
        "full_chars": full_chars,
        "retained_chars": len(retained),
    }


def build_audit(
    run_report: dict[str, Any],
    verification_report: dict[str, Any],
    private_bundle: dict[str, Any],
    cutoff: int = 20,
    memory_max_chars: int = 900,
    total_max_chars: int = 14_000,
) -> dict[str, Any]:
    bundle_by_id = bundle_question_map(private_bundle)
    run_questions = run_report.get("questions") if isinstance(run_report.get("questions"), list) else []
    classification_counts: Counter[str] = Counter({key: 0 for key in CLASSIFICATIONS})
    category_breakdown: dict[str, Counter[str]] = defaultdict(Counter)
    miss_rows: list[dict[str, Any]] = []
    miss_count = 0
    misses_with_retrieved = 0
    retrieved_total = 0
    full_chars_total = 0
    retained_chars_total = 0
    truncated_rows = 0
    selector_full_has = 0
    selector_has = 0
    selector_lost = 0
    selector_buckets: Counter[str] = Counter()
    runner = load_judged_runner()

    for row in run_questions:
        if not isinstance(row, dict):
            continue
        result = cutoff_result(row, cutoff)
        if str(result.get("judgment") or "").upper() == "PASS":
            continue
        miss_count += 1
        question_id = str(row.get("question_id") or "")
        private_question = bundle_by_id.get(question_id, {})
        classification, metrics = classify_miss(private_question, cutoff, memory_max_chars, total_max_chars)
        rows = retrieved_rows(private_question, cutoff)
        terms = content_terms(str(private_question.get("ground_truth_answer") or ""))
        full_covered, full_total, _full_chars = term_coverage_chunks(terms, [str(item.get("memory") or "") for item in rows])
        selector_covered = 0
        selector_total = len(terms)
        if runner is not None:
            selector_lines = runner.longmemeval_evidence_window_lines(
                private_question,
                rows,
                max_chars=max(0, int(total_max_chars * 0.75)),
            )
            selector_covered, selector_total = term_coverage(terms, "\n".join(selector_lines))
        selector_buckets[coverage_bucket(selector_covered, selector_total)] += 1
        full_ratio = full_covered / full_total if full_total else 0.0
        selector_ratio = selector_covered / selector_total if selector_total else 0.0
        if full_ratio >= 0.5:
            selector_full_has += 1
            if selector_ratio >= 0.5:
                selector_has += 1
            else:
                selector_lost += 1
        classification_counts[classification] += 1
        category = str(row.get("category") or private_question.get("category") or "unknown")
        category_breakdown[category][classification] += 1
        category_breakdown[category]["misses"] += 1
        if metrics["retrieved_count"]:
            misses_with_retrieved += 1
        retrieved_total += int(metrics["retrieved_count"])
        full_chars_total += int(metrics["full_chars"])
        retained_chars_total += int(metrics["retained_chars"])
        if int(metrics["retained_chars"]) < int(metrics["full_chars"]):
            truncated_rows += 1
        miss_rows.append(
            {
                "question_id_hash": stable_hash(question_id),
                "category": category,
                "classification": classification,
                "score": result.get("score"),
                "retrieved_count": metrics["retrieved_count"],
                "full_coverage_bucket": metrics["full_coverage_bucket"],
                "retained_coverage_bucket": metrics["retained_coverage_bucket"],
            }
        )

    usage = run_report.get("actual_usage") if isinstance(run_report.get("actual_usage"), dict) else {}
    cost = run_report.get("estimated_cost_usd") if isinstance(run_report.get("estimated_cost_usd"), dict) else {}
    report = {
        "ok": True,
        "mode": "longmemeval-failure-audit",
        "runs_model_calls": False,
        "dataset": str(run_report.get("dataset") or private_bundle.get("dataset") or "unknown"),
        "retrieval_backend": str(run_report.get("retrieval_backend") or private_bundle.get("retrieval_backend") or "unknown"),
        "cutoff": cutoff,
        "run_summary": run_report.get("summary"),
        "verification_ok": verification_report.get("ok"),
        "verification_summary": verification_report.get("summary"),
        "classification_counts": dict(classification_counts),
        "category_breakdown": {key: dict(value) for key, value in category_breakdown.items()},
        "retrieval_coverage": {
            "misses": miss_count,
            "misses_with_retrieved_top20": misses_with_retrieved,
            "avg_retrieved_per_miss": round(retrieved_total / miss_count, 4) if miss_count else 0.0,
        },
        "prompt_truncation": {
            "memory_max_chars": memory_max_chars,
            "total_max_chars": total_max_chars,
            "truncated_miss_rows": truncated_rows,
            "full_chars_total": full_chars_total,
            "retained_chars_total": retained_chars_total,
        },
        "selector_coverage": {
            "misses_full_has_answer_terms": selector_full_has,
            "misses_selector_has_answer_terms": selector_has,
            "misses_selector_lost_answer_terms": selector_lost,
            "selector_coverage_buckets": dict(selector_buckets),
        },
        "token_cost": {
            "total_tokens": usage.get("total_tokens"),
            "estimated_total_usd": cost.get("total_usd"),
        },
        "miss_rows": miss_rows,
    }
    hits = raw_payload_hits(report)
    report["gates"] = {"raw_payload": {"ok": len(hits) == 0, "hit_count": len(hits), "hit_paths": hits[:20]}}
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a sanitized LongMemEval failure audit from private judged artifacts.")
    parser.add_argument("--run-report", required=True)
    parser.add_argument("--verification-report", required=True)
    parser.add_argument("--private-bundle", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cutoff", type=int, default=20)
    parser.add_argument("--memory-max-chars", type=int, default=900)
    parser.add_argument("--total-max-chars", type=int, default=14_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_audit(
        load_json(args.run_report),
        load_json(args.verification_report),
        load_json(args.private_bundle),
        cutoff=args.cutoff,
        memory_max_chars=args.memory_max_chars,
        total_max_chars=args.total_max_chars,
    )
    hits = raw_payload_hits(report)
    report.setdefault("gates", {})["raw_payload"] = {"ok": len(hits) == 0, "hit_count": len(hits), "hit_paths": hits[:20]}
    if hits:
        blocked = {
            "ok": False,
            "mode": "longmemeval-failure-audit-blocked",
            "runs_model_calls": False,
            "reason": "raw payload detected before public write",
            "gates": {"raw_payload": {"ok": False, "hit_count": len(hits), "hit_paths": hits[:20]}},
            "notes": [
                "The unsafe audit report was not written.",
                "This report includes raw payload paths only, never raw values.",
            ],
        }
        print(json.dumps(blocked, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
