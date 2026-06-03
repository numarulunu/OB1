from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


FORBIDDEN_PUBLIC_KEYS = {
    "answer",
    "answers",
    "generated_answer",
    "ground_truth_answer",
    "judge_response",
    "judge_responses",
    "memory",
    "memories",
    "messages",
    "prompt",
    "prompts",
    "question",
    "raw",
    "retrieved_memories",
    "retrieved_memories_by_top_k",
}
SECRET_PATTERN = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|profile[_-]?url|api[_-]?key|bearer\s+[A-Za-z0-9._-]+)", re.IGNORECASE)


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _raw_payload_hits(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in FORBIDDEN_PUBLIC_KEYS:
                hits.append(f"{path}.{key_text}")
            hits.extend(_raw_payload_hits(child, f"{path}.{key_text}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(_raw_payload_hits(child, f"{path}[{index}]"))
    elif isinstance(value, str) and SECRET_PATTERN.search(value):
        hits.append(path)
    return hits


def _validate_report(report: dict[str, Any], label: str) -> None:
    if report.get("mode") != "predict-only-sweep":
        raise ValueError(f"{label} report must be mode=predict-only-sweep")
    if not isinstance(report.get("questions"), list):
        raise ValueError(f"{label} report missing questions list")
    hits = _raw_payload_hits(report)
    if hits:
        raise ValueError(f"{label} report contains forbidden raw payload fields: {', '.join(hits[:5])}")


def _top_k_values(report: dict[str, Any]) -> list[int]:
    return [int(value) for value in report.get("top_k_values") or []]


def _check_compatible(kontext: dict[str, Any], mem0: dict[str, Any], top_k: int, allow_mixed: bool) -> None:
    if allow_mixed:
        return
    if str(kontext.get("dataset") or "") != str(mem0.get("dataset") or ""):
        raise ValueError("mixed dataset reports require --allow-mixed")
    if top_k not in _top_k_values(kontext) or top_k not in _top_k_values(mem0):
        raise ValueError("mixed top-k reports require --allow-mixed")


def _questions_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in report.get("questions") or []:
        question_id = str(row.get("question_id") or "")
        if question_id:
            rows[question_id] = row
    return rows


def _matched(row: dict[str, Any], top_k: int) -> bool:
    return bool((row.get("matched_by_top_k") or {}).get(str(top_k)))


def _first_hit_bucket(row: dict[str, Any], top_k: int) -> str:
    value = row.get("first_hit_top_k")
    if value is None:
        return "none"
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return "none"
    if rank <= 5:
        return "1-5"
    if rank <= 10:
        return "6-10"
    if rank <= top_k:
        return f"11-{top_k}"
    return f">{top_k}"


def _overlap(left: dict[str, Any], right: dict[str, Any]) -> dict[str, int]:
    left_ids = {str(value) for value in left.get("result_ids_at_max_top_k") or [] if str(value)}
    right_ids = {str(value) for value in right.get("result_ids_at_max_top_k") or [] if str(value)}
    left_hashes = {str(value) for value in left.get("result_hashes_at_max_top_k") or [] if str(value)}
    right_hashes = {str(value) for value in right.get("result_hashes_at_max_top_k") or [] if str(value)}
    return {
        "id_overlap": len(left_ids & right_ids),
        "hash_overlap": len(left_hashes & right_hashes),
    }


def _cause_bucket(k_hit: bool, m_hit: bool, k_row: dict[str, Any], m_row: dict[str, Any], top_k: int) -> str:
    if k_hit and m_hit:
        return "both_hit"
    if k_hit and not m_hit:
        return "kontext_only"
    if m_hit and not k_hit:
        first_hit = k_row.get("first_hit_top_k")
        try:
            first_hit_rank = int(first_hit) if first_hit is not None else None
        except (TypeError, ValueError):
            first_hit_rank = None
        if first_hit_rank and first_hit_rank > top_k:
            return "kontext_evidence_below_cutoff"
        return "mem0_only"
    if _overlap(k_row, m_row)["hash_overlap"] > 0:
        return "both_miss_with_overlap"
    return "both_miss"


def build_beam_mem0_parity_audit(
    kontext_report: dict[str, Any],
    mem0_report: dict[str, Any],
    *,
    top_k: int = 20,
    allow_mixed: bool = False,
) -> dict[str, Any]:
    _validate_report(kontext_report, "kontext")
    _validate_report(mem0_report, "mem0")
    _check_compatible(kontext_report, mem0_report, top_k, allow_mixed)

    kontext_questions = _questions_by_id(kontext_report)
    mem0_questions = _questions_by_id(mem0_report)
    shared_ids = sorted(set(kontext_questions) & set(mem0_questions))
    if not shared_ids:
        raise ValueError("reports have no shared question IDs")

    summary = Counter()
    categories: dict[str, Counter[str]] = defaultdict(Counter)
    first_hit_buckets = {"kontext": Counter(), "legacy-mem0-offline": Counter()}
    overlap_totals = Counter()
    cause_buckets = Counter()

    for question_id in shared_ids:
        k_row = kontext_questions[question_id]
        m_row = mem0_questions[question_id]
        category = str(k_row.get("category") or m_row.get("category") or "unknown")
        k_hit = _matched(k_row, top_k)
        m_hit = _matched(m_row, top_k)
        bucket = "both_hit" if k_hit and m_hit else "kontext_only" if k_hit else "mem0_only" if m_hit else "both_miss"
        summary[bucket] += 1
        categories[category][bucket] += 1
        first_hit_buckets["kontext"][_first_hit_bucket(k_row, top_k)] += 1
        first_hit_buckets["legacy-mem0-offline"][_first_hit_bucket(m_row, top_k)] += 1
        overlap = _overlap(k_row, m_row)
        overlap_totals["id_overlap"] += overlap["id_overlap"]
        overlap_totals["hash_overlap"] += overlap["hash_overlap"]
        cause_buckets[_cause_bucket(k_hit, m_hit, k_row, m_row, top_k)] += 1

    audit = {
        "ok": True,
        "mode": "beam-mem0-parity-audit",
        "dataset": str(kontext_report.get("dataset") or ""),
        "top_k": top_k,
        "reports": {
            "kontext": {
                "run_id": str(kontext_report.get("run_id") or ""),
                "retrieval_backend": str(kontext_report.get("retrieval_backend") or ""),
                "benchmark_rerank": str(kontext_report.get("benchmark_rerank") or "none"),
            },
            "legacy-mem0-offline": {
                "run_id": str(mem0_report.get("run_id") or ""),
                "retrieval_backend": str(mem0_report.get("retrieval_backend") or ""),
                "benchmark_rerank": str(mem0_report.get("benchmark_rerank") or "none"),
            },
        },
        "summary": {
            "questions": len(shared_ids),
            "both_hit": summary["both_hit"],
            "kontext_only": summary["kontext_only"],
            "mem0_only": summary["mem0_only"],
            "both_miss": summary["both_miss"],
        },
        "categories": {category: dict(counts) for category, counts in sorted(categories.items())},
        "first_hit_rank_buckets": {
            backend: dict(sorted(counts.items())) for backend, counts in first_hit_buckets.items()
        },
        "overlap": dict(overlap_totals),
        "likely_causes": dict(sorted(cause_buckets.items())),
        "raw_payload_scan": {"ok": True, "hit_count": 0},
    }
    hits = _raw_payload_hits(audit)
    if hits:
        raise ValueError(f"audit output contains forbidden raw payload fields: {', '.join(hits[:5])}")
    return audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sanitized BEAM Kontext vs legacy Mem0 parity audit")
    parser.add_argument("--kontext-report", required=True)
    parser.add_argument("--mem0-report", required=True)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-mixed", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    audit = build_beam_mem0_parity_audit(
        _load_json(args.kontext_report),
        _load_json(args.mem0_report),
        top_k=args.top_k,
        allow_mixed=args.allow_mixed,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "ok=true "
        f"questions={audit['summary']['questions']} "
        f"both_hit={audit['summary']['both_hit']} "
        f"kontext_only={audit['summary']['kontext_only']} "
        f"mem0_only={audit['summary']['mem0_only']} "
        f"both_miss={audit['summary']['both_miss']} "
        f"raw_payload_hits={audit['raw_payload_scan']['hit_count']} "
        f"output={output}"
    )


if __name__ == "__main__":
    main()
