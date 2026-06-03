from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from kontext_v2.repository import KontextRepository
from kontext_v2.retention import is_protected_autobiographical_history
from kontext_v2.retrieval import normalized_memory_type, row_domains, search_memories


DEFAULT_CASES = "tools/kontext-v2/eval/ob1_retrieval_quality_cases.json"


def normalize_values(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def hash_text(value: Any, length: int = 16) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:length]


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("cases must be a list or object with cases")
    return [normalize_case(row) for row in rows]


def normalize_case(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("each case must be an object")
    query = str(row.get("query") or "").strip()
    name = str(row.get("name") or "").strip()
    if not query or not name:
        raise ValueError("each case requires name and query")
    case = dict(row)
    case["group"] = str(case.get("group") or "general").strip() or "general"
    case["expected_domains"] = sorted(normalize_values(case.get("expected_domains")))
    case["expected_memory_types"] = sorted(normalize_values(case.get("expected_memory_types")))
    case["max_rank"] = max(int(case.get("max_rank") or 10), 1)
    case["query_hash"] = hash_text(query)
    return case


def normalize_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for row in rows:
        cases.append(normalize_case(row))
    return cases


def row_id_hash(row: dict[str, Any]) -> str:
    return hash_text(row.get("external_mem0_id") or row.get("id") or "")


def row_matches_case(row: dict[str, Any], case: dict[str, Any]) -> bool:
    expected_domains = set(case.get("expected_domains") or [])
    expected_types = set(case.get("expected_memory_types") or [])
    domain_ok = bool(row_domains(row) & expected_domains) if expected_domains else True
    type_ok = normalized_memory_type(row) in expected_types if expected_types else True
    if not domain_ok or not type_ok:
        return False
    if case.get("require_protected_history") and not is_protected_autobiographical_history(row):
        return False
    return True


def failed_reasons(rows: list[dict[str, Any]], case: dict[str, Any], rank: int | None) -> list[str]:
    if rank is None:
        if case.get("require_protected_history"):
            protected_candidates = [row for row in rows if is_protected_autobiographical_history(row)]
            if not protected_candidates:
                return ["protected_history_not_found"]
        return ["expected_domain_type_not_found"]
    if rank > int(case.get("max_rank") or 10):
        return ["rank_above_threshold"]
    return []


def evaluate_case(repo: Any, case: dict[str, Any], top_k: int) -> dict[str, Any]:
    rows = search_memories(
        repo,
        query=str(case.get("query") or ""),
        top_k=top_k,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )
    rank = next((index for index, row in enumerate(rows, start=1) if row_matches_case(row, case)), None)
    reasons = failed_reasons(rows, case, rank)
    return {
        "name": case["name"],
        "group": case["group"],
        "query_hash": case["query_hash"],
        "passed": not reasons,
        "first_satisfying_rank": rank,
        "max_rank": case["max_rank"],
        "result_count": len(rows),
        "top_result_hashes": [row_id_hash(row) for row in rows[:5] if row_id_hash(row)],
        "failed_reasons": reasons,
    }


def summarize_cases(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(case_reports)
    passed = sum(1 for case in case_reports if case.get("passed") is True)
    return {
        "cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
    }


def group_summaries(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in case_reports:
        grouped[str(case.get("group") or "general")].append(case)
    return {group: summarize_cases(rows) for group, rows in sorted(grouped.items())}


def evaluate_cases(repo: Any, cases: list[dict[str, Any]], top_k: int = 10) -> dict[str, Any]:
    safe_top_k = min(max(int(top_k or 10), 1), 50)
    normalized_cases = normalize_cases(cases)
    case_reports = [evaluate_case(repo, case, safe_top_k) for case in normalized_cases]
    summary = summarize_cases(case_reports)
    return {
        "ok": summary["failed"] == 0,
        "mode": "ob1-retrieval-quality-gate",
        "runs_model_calls": False,
        "top_k": safe_top_k,
        "summary": summary,
        "groups": group_summaries(case_reports),
        "cases": case_reports,
        "notes": [
            "This gate does not call answerer or judge models.",
            "Reports include query hashes and result ID hashes only, not raw queries, memory text, or raw result IDs.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a sanitized OB1-specific Kontext retrieval quality gate.")
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--database-url")
    parser.add_argument("--database-url-env", default="KONTEXT_V2_DATABASE_URL")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = load_cases(args.cases)
    database_url = args.database_url
    if not database_url:
        import os

        database_url = os.environ.get(args.database_url_env, "")
    if not database_url:
        raise SystemExit("database URL is required")

    import psycopg

    with psycopg.connect(database_url) as conn:
        report = evaluate_cases(KontextRepository(conn), cases, top_k=args.top_k)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
