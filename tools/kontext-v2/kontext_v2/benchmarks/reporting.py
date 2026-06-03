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
    if not terms:
        return False
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


def _question_evaluable(expected_terms: list[str] | None = None, evidence: list[str] | None = None) -> bool:
    evidence_ids = {str(value).strip() for value in (evidence or []) if str(value).strip()}
    terms = [str(term).strip() for term in (expected_terms or []) if str(term).strip()]
    return bool(evidence_ids or terms)


def _result_evaluable(item: dict[str, Any]) -> bool:
    return _question_evaluable(item.get("expected_terms") or [], item.get("evidence") or [])


def _result_matched(item: dict[str, Any]) -> bool:
    return _result_evaluable(item) and bool(item.get("matched"))


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


def _rank_metrics(results: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    evaluable = 0
    found_within_cutoff = 0
    reciprocal_rank_total = 0.0
    first_hit_ranks: list[int] = []
    for item in results:
        if not _result_evaluable(item):
            continue
        evaluable += 1
        first_hit = _first_hit_rank(item.get("search_results") or [], item.get("expected_terms") or [], item.get("evidence") or [])
        if first_hit is None:
            continue
        first_hit_ranks.append(first_hit)
        if first_hit <= top_k:
            found_within_cutoff += 1
            reciprocal_rank_total += 1.0 / first_hit
    sorted_ranks = sorted(first_hit_ranks)
    median_index = len(sorted_ranks) // 2
    p90_index = min(max(int(len(sorted_ranks) * 0.9 + 0.999999) - 1, 0), len(sorted_ranks) - 1) if sorted_ranks else 0
    return {
        "mrr": round(reciprocal_rank_total / evaluable, 4) if evaluable else 0.0,
        "first_hit_found_questions": len(first_hit_ranks),
        "first_hit_within_cutoff_questions": found_within_cutoff,
        "median_first_hit_rank": sorted_ranks[median_index] if sorted_ranks else None,
        "p90_first_hit_rank": sorted_ranks[p90_index] if sorted_ranks else None,
        "max_first_hit_rank": sorted_ranks[-1] if sorted_ranks else None,
    }


def _summary(top_k: int, results: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "matched": 0, "retrieval_evaluable": 0, "retrieval_matched": 0, "skipped_no_match_rule": 0}
    )
    latencies = []
    for item in results:
        category = str(item.get("category") or "unknown")
        evaluable = _result_evaluable(item)
        matched = evaluable and bool(item.get("matched"))
        latency = round(float(item.get("search_latency_ms") or 0), 1)
        latencies.append(latency)
        categories[category]["total"] += 1
        categories[category]["matched"] += int(matched)
        categories[category]["retrieval_evaluable"] += int(evaluable)
        categories[category]["retrieval_matched"] += int(matched and evaluable)
        categories[category]["skipped_no_match_rule"] += int(not evaluable)
    total = len(results)
    matched_total = sum(1 for item in results if _result_matched(item))
    evaluable_total = sum(1 for item in results if _result_evaluable(item))
    retrieval_matched_total = matched_total
    return {
        "top_k": int(top_k),
        "total_questions": total,
        "matched_questions": matched_total,
        "match_rate": round(matched_total / total, 4) if total else 0.0,
        "retrieval_evaluable_questions": evaluable_total,
        "retrieval_matched_questions": retrieval_matched_total,
        "retrieval_match_rate": round(retrieval_matched_total / evaluable_total, 4) if evaluable_total else 0.0,
        "skipped_no_match_rule": total - evaluable_total,
        "average_search_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "categories": dict(sorted(categories.items())),
        **_rank_metrics(results, int(top_k)),
    }


def format_predict_cli_summary(report: dict[str, Any]) -> str:
    return (
        f"matched={report['matched_questions']}/{report['total_questions']} "
        f"retrieval={report.get('retrieval_matched_questions', 0)}/{report.get('retrieval_evaluable_questions', 0)} "
        f"mrr={report.get('mrr', 0.0)} "
        f"skipped={report.get('skipped_no_match_rule', 0)} "
        f"avg_ms={report.get('average_search_latency_ms', 0.0)}"
    )


def format_sweep_cli_summary(report: dict[str, Any]) -> str:
    parts = []
    for top_k, values in sorted((report.get("sweeps") or {}).items(), key=lambda item: int(item[0])):
        parts.append(
            f"top_k={top_k}:retrieval="
            f"{values.get('retrieval_matched_questions', 0)}/{values.get('retrieval_evaluable_questions', 0)}"
            f",mrr={values.get('mrr', 0.0)}"
            f",matched={values['matched_questions']}/{values['total_questions']}"
            f",skipped={values.get('skipped_no_match_rule', 0)}"
        )
    return " ".join(parts)


def build_predict_only_report(
    dataset: str,
    run_id: str,
    top_k: int,
    results: list[dict[str, Any]],
    retrieval_backend: str = "kontext",
) -> dict[str, Any]:
    summary = _summary(top_k, results)
    questions = []
    for item in results:
        evaluable = _result_evaluable(item)
        matched = evaluable and bool(item.get("matched"))
        questions.append(
            {
                "question_id": item.get("question_id"),
                "category": str(item.get("category") or "unknown"),
                "matched": matched,
                "retrieval_evaluable": evaluable,
                "first_hit_rank": _first_hit_rank(item.get("search_results") or [], item.get("expected_terms") or [], item.get("evidence") or []),
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
        "retrieval_backend": retrieval_backend,
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
    retrieval_backend: str = "kontext",
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
        evaluable = _question_evaluable(item.get("expected_terms") or [], item.get("evidence") or [])
        matched_by_top_k: dict[str, bool] = {}
        miss_reason_by_top_k: dict[str, str] = {}
        for top_k in normalized_top_k:
            matched = evaluable and _question_matched(rows[:top_k], item.get("expected_terms") or [], item.get("evidence") or [])
            matched_by_top_k[str(top_k)] = matched
            miss_reason_by_top_k[str(top_k)] = "matched" if matched else _miss_reason(item, top_k, first_hit)
        question_rows.append(
            {
                "question_id": item.get("question_id"),
                "category": str(item.get("category") or "unknown"),
                "retrieval_evaluable": evaluable,
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

    retrieval_evaluable_total = sum(1 for row in question_rows if row["retrieval_evaluable"])
    sweep_totals = {
        "retrieval_evaluable_questions": retrieval_evaluable_total,
        "skipped_no_match_rule": len(results) - retrieval_evaluable_total,
    }

    return {
        **sweep_totals,
        "dataset": dataset,
        "run_id": run_id,
        "retrieval_backend": retrieval_backend,
        "mode": "predict-only-sweep",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "top_k_values": normalized_top_k,
        "max_top_k": normalized_top_k[-1],
        "total_questions": len(results),
        "sweeps": sweeps,
        "miss_analysis": miss_analysis,
        "questions": question_rows,
    }


def _normalized_top_k_values(top_k_values: list[int] | int) -> list[int]:
    if isinstance(top_k_values, int):
        values = [top_k_values]
    else:
        values = list(top_k_values)
    normalized = sorted({min(max(int(value), 1), 200) for value in values})
    if not normalized:
        raise ValueError("top_k_values must contain at least one value")
    return normalized


def _private_bundle_memory(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    source = str(metadata.get("source") or "").strip()
    memory_type = str(metadata.get("memory_type") or row.get("memory_type") or "").strip()
    is_live = metadata.get("is_live_memory")
    is_benchmark = source == "benchmark" or memory_type == "benchmark_observation" or str(row.get("id") or "").startswith("benchmark:")
    if is_live is True or not is_benchmark:
        raise ValueError("private judged bundles may only contain benchmark rows, not live/user memory rows")
    return {
        "id": str(row.get("id") or ""),
        "memory": str(row.get("memory") or ""),
        "memory_hash": stable_hash(str(row.get("memory") or ""))[:16],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "metadata": {
            "source_ids": [str(value) for value in metadata.get("source_ids") or []],
            "session_id": metadata.get("session_id"),
            "conversation_id": metadata.get("conversation_id"),
            "timestamp": metadata.get("timestamp"),
        },
    }


def build_private_judged_input_bundle(
    dataset: str,
    run_id: str,
    top_k_values: list[int] | int,
    results: list[dict[str, Any]],
    retrieval_backend: str = "kontext",
) -> dict[str, Any]:
    normalized_top_k = _normalized_top_k_values(top_k_values)
    questions = []
    for item in results:
        rows = list(item.get("search_results") or [])
        retrieved_by_top_k = {
            str(top_k): [_private_bundle_memory(row) for row in rows[:top_k]]
            for top_k in normalized_top_k
        }
        questions.append(
            {
                "question_id": item.get("question_id"),
                "category": str(item.get("category") or "unknown"),
                "question": str(item.get("question") or ""),
                "ground_truth_answer": str(item.get("answer") or item.get("ground_truth_answer") or ""),
                "question_date": item.get("question_date"),
                "rubric": item.get("rubric") if isinstance(item.get("rubric"), list) else [],
                "retrieval_evaluable": _result_evaluable(item),
                "first_hit_top_k": _first_hit_rank(rows, item.get("expected_terms") or [], item.get("evidence") or []),
                "retrieved_memories_by_top_k": retrieved_by_top_k,
            }
        )
    return {
        "dataset": dataset,
        "run_id": run_id,
        "retrieval_backend": retrieval_backend,
        "mode": "private-judged-input-bundle",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_model_calls": False,
        "contains_raw_benchmark_text": True,
        "contains_live_user_memory": False,
        "private_do_not_log": True,
        "top_k_values": normalized_top_k,
        "total_questions": len(questions),
        "questions": questions,
        "notes": [
            "Private input for explicitly approved judged benchmark runs.",
            "Contains raw public benchmark questions, answers, and retrieved benchmark memories.",
            "Do not commit, print, paste, or save this bundle to long-term memory.",
        ],
    }


def write_private_judged_input_bundle(bundle: dict[str, Any], output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


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
            f"- Retrieval-evaluable questions: {report.get('retrieval_evaluable_questions', 0)}",
            f"- Top-k values: {', '.join(str(value) for value in report.get('top_k_values') or [])}",
            "",
            "## Sweeps",
            "",
        ]
        for top_k, values in sorted((report.get("sweeps") or {}).items(), key=lambda item: int(item[0])):
            lines.append(
                f"- `top_k={top_k}`: {values['matched_questions']}/{values['total_questions']} matched ({values['match_rate']}), "
                f"retrieval {values.get('retrieval_matched_questions', 0)}/{values.get('retrieval_evaluable_questions', 0)} "
                f"({values.get('retrieval_match_rate', 0.0)}), MRR {values.get('mrr', 0.0)}"
            )
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
            f"- Retrieval-evaluable questions: {report.get('retrieval_evaluable_questions', 0)}",
            f"- Retrieval matched questions: {report.get('retrieval_matched_questions', 0)}",
            f"- Match rate: {report['match_rate']}",
            f"- Retrieval match rate: {report.get('retrieval_match_rate', 0.0)}",
            f"- MRR: {report.get('mrr', 0.0)}",
            f"- First-hit ranks: median {report.get('median_first_hit_rank')}, p90 {report.get('p90_first_hit_rank')}, max {report.get('max_first_hit_rank')}",
            f"- Average search latency ms: {report['average_search_latency_ms']}",
            "",
            "## Categories",
            "",
        ]
        for category, values in sorted((report.get("categories") or {}).items()):
            lines.append(f"- `{category}`: {values['matched']}/{values['total']} matched")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
