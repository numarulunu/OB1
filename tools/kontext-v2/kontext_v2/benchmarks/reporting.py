from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kontext_v2.benchmarks.adapter import stable_hash


def _question_matched(
    search_results: list[dict[str, Any]],
    expected_terms: list[str] | None = None,
    evidence: list[str] | None = None,
) -> bool:
    evidence_ids = {str(value).strip() for value in (evidence or []) if str(value).strip()}
    if evidence_ids:
        for row in search_results:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            source_ids = {str(value).strip() for value in metadata.get("source_ids") or [] if str(value).strip()}
            if evidence_ids & source_ids:
                return True
        return False
    terms = [str(term).lower() for term in (expected_terms or [])]
    haystack = "\n".join(str(row.get("memory") or "").lower() for row in search_results)
    return all(term in haystack for term in terms)


def _first_hit_rank(
    search_results: list[dict[str, Any]],
    expected_terms: list[str] | None = None,
    evidence: list[str] | None = None,
) -> int | None:
    evidence_ids = {str(value).strip() for value in (evidence or []) if str(value).strip()}
    if evidence_ids:
        for index, row in enumerate(search_results, start=1):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            source_ids = {str(value).strip() for value in metadata.get("source_ids") or [] if str(value).strip()}
            if evidence_ids & source_ids:
                return index
        return None
    terms = [str(term).lower() for term in (expected_terms or [])]
    if not terms:
        return None
    for index in range(1, len(search_results) + 1):
        haystack = "\n".join(str(row.get("memory") or "").lower() for row in search_results[:index])
        if all(term in haystack for term in terms):
            return index
    return None


def _miss_reason(item: dict[str, Any], top_k: int, first_hit_top_k: int | None) -> str:
    if first_hit_top_k is not None and first_hit_top_k > top_k:
        if item.get("evidence"):
            return "evidence_below_cutoff"
        return "expected_terms_below_cutoff"
    if item.get("evidence"):
        return "evidence_not_retrieved"
    if item.get("expected_terms"):
        return "expected_terms_not_retrieved"
    return "no_match_rule"


def _summary(top_k: int, results: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "matched": 0})
    latencies = []
    for item in results:
        category = str(item.get("category") or "unknown")
        matched = bool(item.get("matched"))
        latency = round(float(item.get("search_latency_ms") or 0), 1)
        latencies.append(latency)
        categories[category]["total"] += 1
        categories[category]["matched"] += int(matched)
    total = len(results)
    matched_total = sum(1 for item in results if item.get("matched"))
    return {
        "top_k": int(top_k),
        "total_questions": total,
        "matched_questions": matched_total,
        "match_rate": round(matched_total / total, 4) if total else 0.0,
        "average_search_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "categories": dict(sorted(categories.items())),
    }


def build_predict_only_report(
    dataset: str,
    run_id: str,
    top_k: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _summary(top_k, results)
    questions = []
    for item in results:
        questions.append(
            {
                "question_id": item.get("question_id"),
                "category": str(item.get("category") or "unknown"),
                "matched": bool(item.get("matched")),
                "search_latency_ms": round(float(item.get("search_latency_ms") or 0), 1),
                "result_ids": [str(value) for value in item.get("result_ids") or []],
                "result_hashes": [
                    stable_hash(str(row.get("memory") or ""))[:16]
                    for row in item.get("search_results") or []
                ],
            }
        )
    return {
        "dataset": dataset,
        "run_id": run_id,
        "mode": "predict-only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **summary,
        "questions": questions,
    }


def build_predict_sweep_report(
    dataset: str,
    run_id: str,
    top_k_values: list[int],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_top_k = sorted({min(max(int(value), 1), 200) for value in top_k_values})
    if not normalized_top_k:
        raise ValueError("top_k_values must contain at least one value")

    sweeps: dict[str, dict[str, Any]] = {}
    miss_analysis: dict[str, dict[str, Any]] = {}
    question_rows: list[dict[str, Any]] = []

    for item in results:
        rows = list(item.get("search_results") or [])
        first_hit = _first_hit_rank(rows, item.get("expected_terms") or [], item.get("evidence") or [])
        matched_by_top_k: dict[str, bool] = {}
        miss_reason_by_top_k: dict[str, str] = {}
        for top_k in normalized_top_k:
            matched = _question_matched(rows[:top_k], item.get("expected_terms") or [], item.get("evidence") or [])
            matched_by_top_k[str(top_k)] = matched
            miss_reason_by_top_k[str(top_k)] = "matched" if matched else _miss_reason(item, top_k, first_hit)
        question_rows.append(
            {
                "question_id": item.get("question_id"),
                "category": str(item.get("category") or "unknown"),
                "first_hit_top_k": first_hit,
                "matched_by_top_k": matched_by_top_k,
                "miss_reason_by_top_k": miss_reason_by_top_k,
                "search_latency_ms": round(float(item.get("search_latency_ms") or 0), 1),
                "result_ids_at_max_top_k": [str(row.get("id") or "") for row in rows[: normalized_top_k[-1]]],
                "result_hashes_at_max_top_k": [stable_hash(str(row.get("memory") or ""))[:16] for row in rows[: normalized_top_k[-1]]],
            }
        )

    for top_k in normalized_top_k:
        keyed = str(top_k)
        sweep_results = []
        reasons: Counter[str] = Counter()
        categories: dict[str, dict[str, Any]] = defaultdict(lambda: {"total_misses": 0, "reasons": Counter()})
        for item, question in zip(results, question_rows, strict=True):
            matched = bool(question["matched_by_top_k"][keyed])
            category = str(item.get("category") or "unknown")
            sweep_results.append({**item, "matched": matched})
            if not matched:
                reason = str(question["miss_reason_by_top_k"][keyed])
                reasons[reason] += 1
                categories[category]["total_misses"] += 1
                categories[category]["reasons"][reason] += 1
        sweeps[keyed] = _summary(top_k, sweep_results)
        miss_analysis[keyed] = {
            "total_misses": sum(reasons.values()),
            "reasons": dict(sorted(reasons.items())),
            "categories": {
                category: {
                    "total_misses": values["total_misses"],
                    "reasons": dict(sorted(values["reasons"].items())),
                }
                for category, values in sorted(categories.items())
            },
        }

    return {
        "dataset": dataset,
        "run_id": run_id,
        "mode": "predict-only-sweep",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "top_k_values": normalized_top_k,
        "max_top_k": normalized_top_k[-1],
        "total_questions": len(results),
        "sweeps": sweeps,
        "miss_analysis": miss_analysis,
        "questions": question_rows,
    }


def write_report_files(report: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    mode = str(report.get("mode") or "predict-only")
    stem = f"{report['dataset']}-{report['run_id']}-{mode}"
    json_path = target / f"{stem}.json"
    markdown_path = target / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if mode == "predict-only-sweep":
        lines = [
            f"# {report['dataset']} Predict-Only Sweep Report",
            "",
            f"- Run ID: `{report['run_id']}`",
            f"- Total questions: {report['total_questions']}",
            f"- Top-k values: {', '.join(str(value) for value in report.get('top_k_values') or [])}",
            "",
            "## Sweeps",
            "",
        ]
        for top_k, values in sorted((report.get("sweeps") or {}).items(), key=lambda item: int(item[0])):
            lines.append(f"- `top_k={top_k}`: {values['matched_questions']}/{values['total_questions']} matched ({values['match_rate']})")
        lines.extend(["", "## Miss Reasons", ""])
        for top_k, values in sorted((report.get("miss_analysis") or {}).items(), key=lambda item: int(item[0])):
            reason_text = ", ".join(f"{reason}: {count}" for reason, count in (values.get("reasons") or {}).items()) or "none"
            lines.append(f"- `top_k={top_k}`: {reason_text}")
    else:
        lines = [
            f"# {report['dataset']} Predict-Only Report",
            "",
            f"- Run ID: `{report['run_id']}`",
            f"- Total questions: {report['total_questions']}",
            f"- Matched questions: {report['matched_questions']}",
            f"- Match rate: {report['match_rate']}",
            f"- Average search latency ms: {report['average_search_latency_ms']}",
            "",
            "## Categories",
            "",
        ]
        for category, values in sorted((report.get("categories") or {}).items()):
            lines.append(f"- `{category}`: {values['matched']}/{values['total']} matched")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}