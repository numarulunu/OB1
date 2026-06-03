#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
from pathlib import Path
from typing import Any

import psycopg

from kontext_v2.mcp_reliability_eval import load_cases
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import (
    explain_score_row,
    filter_rows,
    normalize_domain_values,
    normalize_memory_tier,
    score_row,
    scoring_query_domains,
)
from kontext_v2.schema import apply_schema


DEFAULT_CASES = "tools/mem0-remote-mcp/retrieval_eval_cases.v1.14-expanded.json"


def id_hash(value: Any) -> str:
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else ""


def safe_features(explanation: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    features = explanation.get("features") if isinstance(explanation.get("features"), list) else []
    rows = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        try:
            value = round(float(feature.get("value") or 0.0), 4)
        except (TypeError, ValueError):
            value = 0.0
        rows.append({"name": str(feature.get("name") or ""), "value": value})
    rows.sort(key=lambda row: abs(row["value"]), reverse=True)
    return rows[:limit]


def safe_explanation(row: dict[str, Any], explanation: dict[str, Any], rank: int | None = None) -> dict[str, Any]:
    try:
        total = round(float(explanation.get("total") or 0.0), 4)
    except (TypeError, ValueError):
        total = 0.0
    return {
        "id_hash": id_hash(row.get("external_mem0_id") or row.get("id")),
        "rank": rank,
        "score_total": total,
        "domains": explanation.get("domains") or [],
        "query_domains": explanation.get("query_domains") or [],
        "memory_type": explanation.get("memory_type") or "",
        "tier": explanation.get("tier") or "",
        "status": explanation.get("status") or "",
        "features": safe_features(explanation),
    }


def profile_payload(eval_payload: dict[str, Any], profile: str) -> dict[str, Any]:
    profiles = eval_payload.get("profiles") if isinstance(eval_payload.get("profiles"), dict) else {}
    payload = profiles.get(profile) if isinstance(profiles.get(profile), dict) else {}
    return payload


def service_cases(profile_report: dict[str, Any], service: str) -> dict[str, dict[str, Any]]:
    services = profile_report.get("services") if isinstance(profile_report.get("services"), dict) else {}
    service_report = services.get(service) if isinstance(services.get(service), dict) else {}
    eval_report = service_report.get("eval") if isinstance(service_report.get("eval"), dict) else {}
    cases = eval_report.get("cases") if isinstance(eval_report.get("cases"), list) else []
    return {str(case.get("name") or ""): case for case in cases if isinstance(case, dict)}


def no_overlap_case_names(profile_report: dict[str, Any], left_service: str, right_service: str) -> list[str]:
    comparison = profile_report.get("comparison") if isinstance(profile_report.get("comparison"), dict) else {}
    names = comparison.get("no_overlap_cases") if isinstance(comparison.get("no_overlap_cases"), list) else []
    if names:
        return [str(name) for name in names]
    left_cases = service_cases(profile_report, left_service)
    right_cases = service_cases(profile_report, right_service)
    output = []
    for name, left in left_cases.items():
        right = right_cases.get(name) or {}
        left_ids = {str(value) for value in left.get("top_ids") or []}
        right_ids = {str(value) for value in right.get("top_ids") or []}
        if left_ids and right_ids and not (left_ids & right_ids):
            output.append(name)
    return output


def ranked_rows_for_case(case: dict[str, Any], source_rows: list[dict[str, Any]]) -> list[tuple[int, float, dict[str, Any]]]:
    query = str(case.get("query") or "")
    domains = list(case.get("domains") or [])
    memory_types = list(case.get("memory_types") or [])
    memory_tiers = list(case.get("memory_tiers") or [])
    current_statuses = list(case.get("current_statuses") or [])
    requested_domains = normalize_domain_values(domains)
    requested_tiers = {normalize_memory_tier(value) for value in memory_tiers if str(value).strip()}
    effective_domains = requested_domains or scoring_query_domains(query)
    rows = filter_rows([dict(row) for row in source_rows], domains, memory_types, memory_tiers, current_statuses)
    scored = [
        (index, score_row(query, row, requested_domains=effective_domains, requested_tiers=requested_tiers), row)
        for index, row in enumerate(rows)
    ]
    scored.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    return scored


def rank_bucket(rank: int | None) -> str:
    if rank is None:
        return "missing"
    if rank <= 10:
        return "top_10"
    if rank <= 20:
        return "top_20"
    if rank <= 50:
        return "top_50"
    if rank <= 100:
        return "top_100"
    return "over_100"


def diagnose_no_overlap(
    *,
    cases: list[dict[str, Any]],
    eval_payload: dict[str, Any],
    source_rows: list[dict[str, Any]],
    profile: str = "codex",
    left_service: str = "kontext",
    right_service: str = "mem0",
    top_mem0_ids: int = 5,
    rank_limit: int = 100,
) -> dict[str, Any]:
    profile_report = profile_payload(eval_payload, profile)
    left_cases = service_cases(profile_report, left_service)
    right_cases = service_cases(profile_report, right_service)
    cases_by_name = {str(case.get("name") or ""): case for case in cases}
    names = no_overlap_case_names(profile_report, left_service, right_service)
    rows_by_external_id = {str(row.get("external_mem0_id") or ""): row for row in source_rows if row.get("external_mem0_id")}
    bucket_counts = {"top_10": 0, "top_20": 0, "top_50": 0, "top_100": 0, "over_100": 0, "missing": 0}
    best_ranks: list[int] = []
    reports = []
    for name in names:
        case = cases_by_name.get(name)
        if not case:
            continue
        ranked = ranked_rows_for_case(case, source_rows)
        rank_by_id = {str(row.get("external_mem0_id") or ""): index + 1 for index, (_, _, row) in enumerate(ranked)}
        row_by_id = {str(row.get("external_mem0_id") or ""): row for _, _, row in ranked}
        right_case = right_cases.get(name) or {}
        left_case = left_cases.get(name) or {}
        mem0_ids = [str(value) for value in right_case.get("top_ids") or [] if str(value).strip()][:top_mem0_ids]
        candidate_ranks = [rank_by_id.get(memory_id) for memory_id in mem0_ids]
        present_ranks = [rank for rank in candidate_ranks if rank is not None]
        best_rank = min(present_ranks) if present_ranks else None
        bucket = rank_bucket(best_rank)
        bucket_counts[bucket] += 1
        if best_rank is not None:
            best_ranks.append(best_rank)
        query = str(case.get("query") or "")
        effective_domains = scoring_query_domains(query)
        top_row = ranked[0][2] if ranked else {}
        top_explanation = explain_score_row(query, dict(top_row), requested_domains=effective_domains, requested_tiers=set()) if top_row else {}
        best_mem0_payload = None
        if best_rank is not None:
            best_id = next((memory_id for memory_id in mem0_ids if rank_by_id.get(memory_id) == best_rank), "")
            best_row = row_by_id.get(best_id) or rows_by_external_id.get(best_id) or {}
            best_explanation = explain_score_row(query, dict(best_row), requested_domains=effective_domains, requested_tiers=set()) if best_row else {}
            best_mem0_payload = safe_explanation(best_row, best_explanation, rank=best_rank) if best_row else None
        reports.append({
            "name": name,
            "query_hash": case.get("query_hash"),
            "kontext_top_id_hashes": [id_hash(value) for value in (left_case.get("top_ids") or [])[:top_mem0_ids]],
            "mem0_top_id_hashes": [id_hash(value) for value in mem0_ids],
            "mem0_top_ids_checked": len(mem0_ids),
            "mem0_top_ids_mirrored": sum(1 for memory_id in mem0_ids if memory_id in rows_by_external_id),
            "best_mem0_rank_in_kontext": best_rank,
            "best_mem0_rank_bucket": bucket,
            "mem0_ids_within_rank_limit": sum(1 for rank in candidate_ranks if rank is not None and rank <= rank_limit),
            "kontext_top": safe_explanation(top_row, top_explanation, rank=1) if top_row else None,
            "best_mem0_candidate": best_mem0_payload,
        })
    return {
        "ok": True,
        "profile": profile,
        "pair": [left_service, right_service],
        "case_count": len(cases),
        "no_overlap_cases": len(reports),
        "top_mem0_ids_per_case": top_mem0_ids,
        "rank_limit": rank_limit,
        "rank_buckets": bucket_counts,
        "best_rank_median": statistics.median(best_ranks) if best_ranks else None,
        "best_rank_max": max(best_ranks) if best_ranks else None,
        "reports": reports,
    }



def collect_missing_mem0_ids(
    *,
    eval_payload: dict[str, Any],
    source_rows: list[dict[str, Any]],
    profile: str = "codex",
    left_service: str = "kontext",
    right_service: str = "mem0",
    top_mem0_ids: int = 5,
) -> list[str]:
    profile_report = profile_payload(eval_payload, profile)
    right_cases = service_cases(profile_report, right_service)
    names = no_overlap_case_names(profile_report, left_service, right_service)
    existing_ids = {str(row.get("external_mem0_id") or "") for row in source_rows if row.get("external_mem0_id")}
    output: list[str] = []
    seen: set[str] = set()
    for name in names:
        right_case = right_cases.get(name) or {}
        for value in (right_case.get("top_ids") or [])[:top_mem0_ids]:
            memory_id = str(value or "").strip()
            if memory_id and memory_id not in existing_ids and memory_id not in seen:
                seen.add(memory_id)
                output.append(memory_id)
    return output


def load_source_rows(database_url: str, limit: int) -> list[dict[str, Any]]:
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        return repo.list_memory_rows(limit=limit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sanitized MCP no-overlap rank diagnostics for Kontext vs Mem0.")
    parser.add_argument("--eval-json", required=True)
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--database-url", default=os.environ.get("KONTEXT_V2_DATABASE_URL", ""))
    parser.add_argument("--profile", default="codex")
    parser.add_argument("--left-service", default="kontext")
    parser.add_argument("--right-service", default="mem0")
    parser.add_argument("--top-mem0-ids", type=int, default=5)
    parser.add_argument("--rank-limit", type=int, default=100)
    parser.add_argument("--candidate-limit", type=int, default=10000)
    parser.add_argument("--missing-id-output", default="")
    parser.add_argument("--output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.database_url:
        raise RuntimeError("--database-url is required")
    cases = load_cases(args.cases)
    eval_payload = json.loads(Path(args.eval_json).read_text(encoding="utf-8"))
    source_rows = load_source_rows(args.database_url, max(int(args.candidate_limit or 10000), 1))
    payload = diagnose_no_overlap(
        cases=cases,
        eval_payload=eval_payload,
        source_rows=source_rows,
        profile=args.profile,
        left_service=args.left_service,
        right_service=args.right_service,
        top_mem0_ids=max(int(args.top_mem0_ids or 5), 1),
        rank_limit=max(int(args.rank_limit or 100), 1),
    )
    if args.missing_id_output:
        missing_ids = collect_missing_mem0_ids(
            eval_payload=eval_payload,
            source_rows=source_rows,
            profile=args.profile,
            left_service=args.left_service,
            right_service=args.right_service,
            top_mem0_ids=max(int(args.top_mem0_ids or 5), 1),
        )
        Path(args.missing_id_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.missing_id_output).write_text("\n".join(missing_ids) + ("\n" if missing_ids else ""), encoding="utf-8")
        payload["missing_mem0_ids_written"] = len(missing_ids)
    text = json.dumps(payload, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
