from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RAW_PAYLOAD_KEYS = {
    "question",
    "ground_truth_answer",
    "retrieved_memories_by_top_k",
    "memory",
    "messages",
    "content",
    "conversation",
    "generated_answer",
    "judge_response",
    "judge_responses",
    "structured_evidence",
    "resolved_state",
    "beam_state_ledger",
    "beam_state_verifier",
    "beam_state_verifier_response",
    "beam_deterministic_state",
}
TYPED_FAILURE_CLASSES = {
    "typed_object_missing",
    "typed_object_invalid_schema",
    "typed_object_status_mismatch",
    "typed_object_relation_mismatch",
    "typed_object_value_mismatch",
}

TERM_STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "into",
    "what",
    "when",
    "where",
    "which",
    "should",
    "would",
    "could",
    "about",
    "private",
}


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def typed_object_summary_enabled() -> bool:
    return _env_bool("KONTEXT_TYPED_OBJECT_SUMMARY")


def file_hash(path: str | Path | None) -> str | None:
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    return hashlib.sha256(target.read_bytes()).hexdigest()[:12]


def raw_payload_hits(value: Any) -> list[str]:
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = f"{path}/{key}" if path else str(key)
                if key in RAW_PAYLOAD_KEYS:
                    hits.append(child_path)
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(value, "")
    return hits


def cutoff_result(question: dict[str, Any], cutoff: int) -> dict[str, Any]:
    results = question.get("cutoff_results") if isinstance(question.get("cutoff_results"), dict) else {}
    result = results.get(str(cutoff))
    return result if isinstance(result, dict) else {}


def judgment_passed(result: dict[str, Any]) -> bool:
    value = result.get("judgment")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() == "PASS"


def bundle_questions_by_id(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = bundle.get("questions") if isinstance(bundle.get("questions"), list) else []
    return {str(row.get("question_id") or ""): row for row in rows if isinstance(row, dict)}


def debug_by_id(debug: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(debug, dict):
        return {}
    rows = debug.get("records") if isinstance(debug.get("records"), list) else []
    return {str(row.get("question_id") or ""): row for row in rows if isinstance(row, dict)}


def coverage_terms(value: str, limit: int = 80) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for term in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", str(value or "").lower()):
        if term in TERM_STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def overlap_stats(terms: list[str], value: str) -> tuple[int, float]:
    if not terms:
        return 0, 0.0
    lowered = str(value or "").lower()
    matched = sum(1 for term in terms if term in lowered)
    return matched, round(matched / len(terms), 4)


def retrieved_memory_text(bundle_question: dict[str, Any] | None, cutoff: int) -> str:
    if not isinstance(bundle_question, dict):
        return ""
    by_top_k = bundle_question.get("retrieved_memories_by_top_k")
    rows = by_top_k.get(str(cutoff)) if isinstance(by_top_k, dict) else []
    if not isinstance(rows, list):
        return ""
    parts: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            parts.append(str(row.get("memory") or row.get("text") or row.get("body") or ""))
    return "\n".join(parts)


def coverage_class(retrieved_ratio: float, structured_ratio: float, generated_ratio: float, term_count: int) -> str:
    if term_count <= 0:
        return "no_ground_terms"
    if retrieved_ratio < 0.35:
        return "evidence_gap"
    if structured_ratio < 0.35:
        return "structured_loss"
    if generated_ratio < 0.5:
        return "synthesis_loss"
    return "judge_or_format_mismatch"


def term_coverage(
    bundle_question: dict[str, Any] | None,
    debug_record: dict[str, Any] | None,
    cutoff: int,
) -> dict[str, Any]:
    ground_truth = str(bundle_question.get("ground_truth_answer") or "") if isinstance(bundle_question, dict) else ""
    terms = coverage_terms(ground_truth)
    retrieved_matches, retrieved_ratio = overlap_stats(terms, retrieved_memory_text(bundle_question, cutoff))
    structured_text = str(debug_record.get("structured_evidence") or "") if isinstance(debug_record, dict) else ""
    generated_text = str(debug_record.get("generated_answer") or "") if isinstance(debug_record, dict) else ""
    structured_matches, structured_ratio = overlap_stats(terms, structured_text)
    generated_matches, generated_ratio = overlap_stats(terms, generated_text)
    return {
        "ground_truth_term_count": len(terms),
        "retrieved_overlap_count": retrieved_matches,
        "retrieved_overlap_ratio": retrieved_ratio,
        "structured_overlap_count": structured_matches,
        "structured_overlap_ratio": structured_ratio,
        "generated_overlap_count": generated_matches,
        "generated_overlap_ratio": generated_ratio,
        "coverage_class": coverage_class(retrieved_ratio, structured_ratio, generated_ratio, len(terms)),
    }


def safe_candidate_summaries(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("beam_answer_candidate_summaries")
    if not isinstance(rows, list):
        return []
    summaries: list[dict[str, Any]] = []
    numeric_fields = [
        "question_overlap_terms",
        "question_term_count",
        "question_overlap_ratio",
        "specific_question_overlap_terms",
        "specific_question_term_count",
        "specific_question_overlap_ratio",
        "ground_truth_overlap_terms",
        "ground_truth_term_count",
        "ground_truth_overlap_ratio",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = str(row.get("id") or "")
        index_match = re.fullmatch(r"candidate_(\d+)", raw_id.strip())
        summary = {
            "id": raw_id[:40],
            "kind": str(row.get("kind") or "unknown")[:40],
            "answer_hash": str(row.get("answer_hash") or "")[:80],
            "answer_chars": safe_int(row.get("answer_chars")),
        }
        if index_match:
            summary["index"] = safe_int(index_match.group(1))
        for key in numeric_fields:
            if key in row:
                value = row.get(key)
                if key.endswith("_ratio"):
                    summary[key] = round(float(value or 0.0), 4)
                else:
                    summary[key] = safe_int(value)
        if "state_marker_present" in row:
            summary["state_marker_present"] = row.get("state_marker_present") is True
        summaries.append(summary)
    return summaries


def safe_list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def selected_candidate_kind(result: dict[str, Any], summaries: list[dict[str, Any]]) -> str:
    selected_index = safe_int(result.get("beam_answer_selected_candidate_index"))
    if selected_index < 1 or selected_index > len(summaries):
        return "unknown"
    for summary in summaries:
        summary["selected"] = safe_int(summary.get("index")) == selected_index
    return str(summaries[selected_index - 1].get("kind") or "unknown")


def selector_metric_loss_diagnostics(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    selected = next((row for row in summaries if row.get("selected") is True), None)
    eligible = [row for row in summaries if safe_int(row.get("answer_chars")) > 0]
    best = max(
        eligible,
        key=lambda row: (
            safe_int(row.get("specific_question_overlap_terms")),
            safe_int(row.get("question_overlap_terms")),
            row.get("state_marker_present") is True,
            safe_int(row.get("answer_chars")),
        ),
        default=selected,
    )
    selected_specific = safe_int(selected.get("specific_question_overlap_terms")) if isinstance(selected, dict) else 0
    selected_question = safe_int(selected.get("question_overlap_terms")) if isinstance(selected, dict) else 0
    best_specific = safe_int(best.get("specific_question_overlap_terms")) if isinstance(best, dict) else 0
    best_question = safe_int(best.get("question_overlap_terms")) if isinstance(best, dict) else 0
    loss = bool(
        selected
        and best
        and best is not selected
        and (best_specific > selected_specific or (best_specific == selected_specific and best_question > selected_question))
    )
    return {
        "selector_metric_loss": loss,
        "selected_specific_question_overlap_terms": selected_specific,
        "selected_question_overlap_terms": selected_question,
        "best_specific_question_overlap_terms": best_specific,
        "best_question_overlap_terms": best_question,
        "best_specific_question_overlap_kind": str(best.get("kind") or "unknown")[:40] if isinstance(best, dict) else "unknown",
        "best_specific_question_overlap_index": safe_int(best.get("index")) if isinstance(best, dict) else 0,
    }


def class_for_failure(
    question: dict[str, Any],
    bundle_question: dict[str, Any] | None,
    result: dict[str, Any],
    debug_record: dict[str, Any] | None,
    cutoff: int,
) -> str:
    if result.get("beam_direct_span_candidate") and int(result.get("beam_answer_candidate_count") or 0) > 0:
        if result.get("beam_direct_span_candidate_used"):
            return "beam_direct_span_candidate_wrong"
        return "beam_direct_span_candidate_not_selected"

    if result.get("beam_deterministic_state_resolver"):
        status = str(result.get("beam_state_resolver_status") or "").strip()
        if status == "resolved" and result.get("beam_direct_answer_used"):
            return "deterministic_resolver_wrong"
        if status == "resolved":
            return "deterministic_resolver_not_used"
        if status == "ambiguous":
            return "deterministic_resolver_ambiguous"
        if status == "no_support":
            return "deterministic_resolver_no_support"
        return "deterministic_resolver_not_used"

    if result.get("beam_retrieved_excerpt_direct_bypass"):
        if result.get("beam_retrieved_excerpt_direct_bypass_used"):
            return "beam_retrieved_excerpt_direct_bypass_wrong"
        return "beam_retrieved_excerpt_direct_bypass_not_used"

    if result.get("beam_typed_projection_candidate") and int(result.get("beam_answer_candidate_count") or 0) > 0:
        if result.get("beam_typed_projection_candidate_used"):
            return "beam_typed_projection_candidate_wrong"
        return "beam_typed_projection_candidate_not_selected"

    if result.get("beam_ranked_state_memory_candidate") and int(result.get("beam_answer_candidate_count") or 0) > 0:
        if result.get("beam_ranked_state_memory_candidate_used"):
            return "beam_ranked_state_memory_candidate_wrong"
        return "beam_ranked_state_memory_candidate_not_selected"

    if result.get("beam_state_direct_candidate") and int(result.get("beam_answer_candidate_count") or 0) > 0:
        if result.get("beam_state_direct_candidate_used"):
            return "beam_state_direct_candidate_wrong"
        return "beam_state_direct_candidate_not_selected"

    if result.get("beam_memory_atomizer"):
        return "beam_memory_atomizer_wrong"

    if result.get("beam_state_verifier"):
        reason = str(result.get("beam_direct_answer_bypass_reason") or "").strip()
        if reason == "verifier_valid_support_mismatch":
            return "state_verifier_support_mismatch"
        status = str(result.get("beam_state_verifier_status") or "").strip()
        if status == "corrected" and result.get("beam_direct_answer_used"):
            return "state_verifier_corrected_wrong"
        if status == "rejected":
            return "state_verifier_rejected"
        if status in {"uncertain", "empty", "invalid_json", "missing"} or status.startswith("verifier_"):
            return "state_verifier_uncertain"

    if result.get("beam_extractive_candidate") and int(result.get("beam_answer_candidate_count") or 0) > 0:
        if int(result.get("beam_answer_selected_candidate_index") or 0) == 3:
            return "beam_extractive_candidate_wrong"
        return "beam_extractive_candidate_not_selected"

    if result.get("beam_direct_answer_bypass"):
        if result.get("beam_direct_answer_used"):
            return "direct_answer_bypass_wrong"
        if result.get("beam_focused_state_answer"):
            return "focused_state_answer_wrong"
        return "direct_answer_bypass_not_used"

    if result.get("beam_state_reducer"):
        resolved_state = debug_record.get("resolved_state") if isinstance(debug_record, dict) else None
        status = str(result.get("beam_state_parser_status") or "").strip()
        if isinstance(resolved_state, dict) and resolved_state.get("parser_status"):
            status = str(resolved_state.get("parser_status") or "").strip()
        if status == "empty":
            return "state_reducer_empty"
        if status == "invalid_json":
            return "state_reducer_invalid_json"
        direct_answer = ""
        if isinstance(resolved_state, dict):
            direct_answer = str(resolved_state.get("direct_answer") or "").strip()
        if status == "ok" and not direct_answer:
            return "state_reducer_no_direct_answer"
        if status == "ok" and direct_answer:
            return "answer_synthesis_wrong_after_state"

    judge_count = int(result.get("judge_count") or 0)
    judge_pass_count = int(result.get("judge_pass_count") or 0)
    if judge_count > 1 and 0 < judge_pass_count < judge_count:
        return "judge_instability"

    memories_evaluated = int(result.get("memories_evaluated") or 0)
    first_hit = bundle_question.get("first_hit_top_k") if isinstance(bundle_question, dict) else None
    if memories_evaluated == 0 or first_hit is None or (isinstance(first_hit, int) and first_hit > cutoff):
        return "evidence_missing_top20"

    text = ""
    if isinstance(debug_record, dict):
        text = str(debug_record.get("generated_answer") or "")
        if not str(debug_record.get("structured_evidence") or "").strip() and result.get("beam_structured_evidence"):
            return "structured_evidence_missed"

    if "Evidence:" in text or "Uncertainty:" in text:
        return "answer_format_or_task_mismatch"

    if result.get("beam_evidence_windows") and not result.get("beam_structured_evidence"):
        return "window_selector_missed"
    return "answer_synthesis_wrong"


def typed_object_failure_class(result: dict[str, Any]) -> str | None:
    if not typed_object_summary_enabled():
        return None
    expected = result.get("expected_typed_state_v2")
    if not isinstance(expected, dict):
        expected = {}
    typed = result.get("typed_state_v2")
    required = bool(result.get("typed_state_required") or expected)
    if not isinstance(typed, dict):
        return "typed_object_missing" if required else None
    if typed.get("schema_version") != "typed-state-v2":
        return "typed_object_invalid_schema"
    expected_status = str(expected.get("status") or "").strip()
    if expected_status and str(typed.get("status") or "").strip() != expected_status:
        return "typed_object_status_mismatch"
    expected_relation = str(expected.get("event_relation") or "").strip()
    if expected_relation and str(typed.get("event_relation") or "").strip() != expected_relation:
        return "typed_object_relation_mismatch"
    expected_value_hash = str(expected.get("value_hash") or "").strip()
    if expected_value_hash and str(typed.get("value_hash") or "").strip() != expected_value_hash:
        return "typed_object_value_mismatch"
    if result.get("typed_value_match") is False:
        return "typed_object_value_mismatch"
    return None


def class_for_failure_v2(
    question: dict[str, Any],
    bundle_question: dict[str, Any] | None,
    result: dict[str, Any],
    debug_record: dict[str, Any] | None,
    cutoff: int,
) -> str:
    typed_class = typed_object_failure_class(result)
    if typed_class:
        return typed_class
    return class_for_failure(question, bundle_question, result, debug_record, cutoff)


def increment(bucket: dict[str, int], key: str, amount: int = 1) -> None:
    bucket[key] = int(bucket.get(key) or 0) + amount


def build_audit_report(
    bundle: dict[str, Any],
    run: dict[str, Any],
    verification: dict[str, Any] | None = None,
    *,
    debug: dict[str, Any] | None = None,
    cutoff: int = 20,
    bundle_path: str | Path | None = None,
    run_path: str | Path | None = None,
    verification_path: str | Path | None = None,
    debug_path: str | Path | None = None,
) -> dict[str, Any]:
    by_bundle_id = bundle_questions_by_id(bundle)
    by_debug_id = debug_by_id(debug)
    run_questions = run.get("questions") if isinstance(run.get("questions"), list) else []
    failure_classes = {
        "evidence_missing_top20": 0,
        "window_selector_missed": 0,
        "structured_evidence_missed": 0,
        "state_reducer_empty": 0,
        "state_reducer_invalid_json": 0,
        "state_reducer_no_direct_answer": 0,
        "answer_synthesis_wrong_after_state": 0,
        "direct_answer_bypass_wrong": 0,
        "direct_answer_bypass_not_used": 0,
        "focused_state_answer_wrong": 0,
        "state_verifier_corrected_wrong": 0,
        "state_verifier_rejected": 0,
        "state_verifier_uncertain": 0,
        "state_verifier_support_mismatch": 0,
        "deterministic_resolver_wrong": 0,
        "deterministic_resolver_ambiguous": 0,
        "deterministic_resolver_no_support": 0,
        "deterministic_resolver_not_used": 0,
        "beam_extractive_candidate_wrong": 0,
        "beam_extractive_candidate_not_selected": 0,
        "beam_direct_span_candidate_wrong": 0,
        "beam_direct_span_candidate_not_selected": 0,
        "beam_ranked_state_memory_candidate_wrong": 0,
        "beam_ranked_state_memory_candidate_not_selected": 0,
        "beam_typed_projection_candidate_wrong": 0,
        "beam_typed_projection_candidate_not_selected": 0,
        "beam_retrieved_excerpt_direct_bypass_wrong": 0,
        "beam_retrieved_excerpt_direct_bypass_not_used": 0,
        "beam_state_direct_candidate_wrong": 0,
        "beam_state_direct_candidate_not_selected": 0,
        "beam_memory_atomizer_wrong": 0,
        "answer_synthesis_wrong": 0,
        "answer_format_or_task_mismatch": 0,
        "judge_instability": 0,
    }
    if typed_object_summary_enabled():
        failure_classes.update({key: 0 for key in sorted(TYPED_FAILURE_CLASSES)})
    category_breakdown: dict[str, dict[str, int]] = {}
    failed_question_hashes: list[dict[str, Any]] = []
    judge_agreement = {"unanimous_fail": 0, "split": 0, "unanimous_pass_but_failed": 0}
    coverage_summary = {
        "evidence_gap": 0,
        "structured_loss": 0,
        "synthesis_loss": 0,
        "judge_or_format_mismatch": 0,
        "no_ground_terms": 0,
    }
    selector_metric_loss_count = 0

    for question in run_questions:
        if not isinstance(question, dict):
            continue
        category = str(question.get("category") or "unknown")
        bucket = category_breakdown.setdefault(category, {"total": 0, "failed": 0})
        bucket["total"] += 1
        result = cutoff_result(question, cutoff)
        if not result or judgment_passed(result):
            continue
        bucket["failed"] += 1
        question_id = str(question.get("question_id") or "")
        bundle_question = by_bundle_id.get(question_id)
        debug_record = by_debug_id.get(question_id)
        failure_class = class_for_failure_v2(question, bundle_question, result, debug_record, cutoff)
        increment(failure_classes, failure_class)
        coverage = term_coverage(bundle_question, debug_record, cutoff)
        candidate_summaries = safe_candidate_summaries(result)
        selected_kind = selected_candidate_kind(result, candidate_summaries)
        selector_metric_loss = selector_metric_loss_diagnostics(candidate_summaries)
        if selector_metric_loss["selector_metric_loss"]:
            selector_metric_loss_count += 1
        increment(coverage_summary, str(coverage.get("coverage_class") or "no_ground_terms"))
        judge_count = int(result.get("judge_count") or 0)
        judge_pass_count = int(result.get("judge_pass_count") or 0)
        if judge_count > 1 and 0 < judge_pass_count < judge_count:
            increment(judge_agreement, "split")
        elif judge_count > 0 and judge_pass_count == 0:
            increment(judge_agreement, "unanimous_fail")
        elif judge_count > 0 and judge_pass_count == judge_count:
            increment(judge_agreement, "unanimous_pass_but_failed")
        failed_question_hashes.append(
            {
                "question_id_hash": stable_hash(question_id),
                "question_hash": question.get("question_hash") or stable_hash(question_id),
                "category": category,
                "failure_class": failure_class,
                "score": round(float(result.get("score") or 0.0), 4),
                "judge_count": judge_count,
                "judge_pass_count": judge_pass_count,
                "term_coverage": coverage,
                "selected_candidate_index": safe_int(result.get("beam_answer_selected_candidate_index")),
                "selected_candidate_kind": selected_kind,
                **selector_metric_loss,
                "candidate_summaries": candidate_summaries,
                "generated_answer_hash": str(result.get("generated_answer_hash") or "")[:80],
                "beam_direct_span_candidate_fused": result.get("beam_direct_span_candidate_fused") is True,
                "beam_state_verifier_status": str(result.get("beam_state_verifier_status") or "")[:80],
                "beam_direct_answer_bypass_reason": str(result.get("beam_direct_answer_bypass_reason") or "")[:120],
                "beam_state_resolver_status": str(result.get("beam_state_resolver_status") or "")[:80],
                "beam_state_resolution_rule": str(result.get("beam_state_resolution_rule") or "")[:80],
                "supporting_event_hash_count": safe_list_count(result.get("supporting_event_hashes")),
                "beam_state_resolver_supporting_event_hash_count": safe_list_count(
                    result.get("beam_state_resolver_supporting_event_hashes")
                ),
                "beam_state_verifier_supporting_event_hash_count": safe_list_count(
                    result.get("beam_state_verifier_supporting_event_hashes")
                ),
            }
        )

    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    cutoff_summary = summary.get(str(cutoff)) if isinstance(summary.get(str(cutoff)), dict) else {}
    if not cutoff_summary and isinstance(verification, dict):
        cutoff_summary = verification.get("summary") if isinstance(verification.get("summary"), dict) else {}
    report = {
        "ok": True,
        "mode": "beam-failure-audit",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_model_calls": False,
        "dataset": str(run.get("dataset") or bundle.get("dataset") or "unknown"),
        "run_id": str(run.get("run_id") or bundle.get("run_id") or "unknown"),
        "cutoff": cutoff,
        "summary": {
            "passed": int(cutoff_summary.get("passed") or 0),
            "total": int(cutoff_summary.get("total") or 0),
            "accuracy": round(float(cutoff_summary.get("accuracy") or 0.0), 4),
            "avg_score": round(float(cutoff_summary.get("avg_score") or 0.0), 4),
        },
        "failures_total": sum(failure_classes.values()),
        "failure_classes": failure_classes,
        "category_breakdown": dict(sorted(category_breakdown.items())),
        "judge_agreement": judge_agreement,
        "coverage_summary": coverage_summary,
        "selector_metric_loss_summary": {"count": selector_metric_loss_count},
        "failed_questions": failed_question_hashes,
        "source_hashes": {
            "bundle": file_hash(bundle_path),
            "run": file_hash(run_path),
            "verification": file_hash(verification_path),
            "debug": file_hash(debug_path),
        },
        "gates": {"raw_payload": {"hit_count": 0, "hits": []}},
    }
    hits = raw_payload_hits(report)
    report["gates"]["raw_payload"] = {"hit_count": len(hits), "hits": hits}
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a sanitized BEAM failure audit from private judged artifacts.")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--verification")
    parser.add_argument("--debug")
    parser.add_argument("--cutoff", type=int, default=20)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle = load_json(args.bundle)
    run = load_json(args.run)
    verification = load_json(args.verification) if args.verification else None
    debug = load_json(args.debug) if args.debug else None
    report = build_audit_report(
        bundle,
        run,
        verification,
        debug=debug,
        cutoff=args.cutoff,
        bundle_path=args.bundle,
        run_path=args.run,
        verification_path=args.verification,
        debug_path=args.debug,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
