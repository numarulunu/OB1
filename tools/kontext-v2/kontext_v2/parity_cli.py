
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import psycopg

from kontext_v2.live_mem0 import Mem0ApiClient, import_eval_seed, import_live_snapshot, safe_import_report
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import search_memories
from kontext_v2.schema import apply_schema

DEFAULT_CASES = "tools/mem0-remote-mcp/retrieval_eval_cases.v1.13.json"
DEFAULT_MEM0_BASE_URL = "https://mem0-api.ionutrosu.xyz"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kontext V2 parity eval")
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--kontext-database-url", required=False)
    parser.add_argument("--mem0-profile-url", required=False, help="Reserved for future MCP-profile parity; not printed.")
    parser.add_argument("--mem0-base-url", default=os.environ.get("MEM0_BASE_URL", DEFAULT_MEM0_BASE_URL))
    parser.add_argument("--mem0-api-key-env", default="MEM0_API_KEY")
    parser.add_argument("--mem0-user-id", default=os.environ.get("MEM0_USER_ID", "ionut"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-fetches", type=int, default=100)
    parser.add_argument("--output", required=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live-import", action="store_true")
    parser.add_argument("--seed-from-cases", action="store_true")
    parser.add_argument("--run-eval", action="store_true")
    return parser


def load_eval_cases(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("eval cases must be a list or an object with a cases list")
    cases = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each eval case must be an object")
        query = str(row.get("query") or "").strip()
        if not query:
            raise ValueError("each eval case requires query")
        cases.append({
            "name": str(row.get("name") or "").strip(),
            "query": query,
            "expected_ids": [str(v).strip() for v in row.get("expected_ids") or [] if str(v).strip()],
            "expected_domains": [str(v).strip() for v in row.get("expected_domains") or [] if str(v).strip()],
            "expected_memory_types": [str(v).strip() for v in row.get("expected_memory_types") or [] if str(v).strip()],
            "min_results": max(int(row.get("min_results") or 1), 0),
        })
    return cases


def _normalize_values(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


def _row_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("external_mem0_id") or "").strip()


def _row_domains(row: dict[str, Any]) -> set[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return _normalize_values(metadata.get("domains") or row.get("domains"))


def _row_memory_type(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(metadata.get("memory_type") or row.get("memory_type") or "").strip().lower()


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def evaluate_cases(search_fn: Callable[..., Any], cases: list[dict[str, Any]], top_k: int = 5) -> dict[str, Any]:
    case_reports = []
    for index, case in enumerate(cases, start=1):
        rows = _rows(search_fn(case["query"], top_k=top_k))
        result_ids = [_row_id(row) for row in rows if _row_id(row)]
        expected_ids = set(case.get("expected_ids") or [])
        expected_domains = _normalize_values(case.get("expected_domains"))
        expected_types = _normalize_values(case.get("expected_memory_types"))
        first_expected_rank = None
        for rank, memory_id in enumerate(result_ids, start=1):
            if memory_id in expected_ids:
                first_expected_rank = rank
                break
        expected_id_hit = first_expected_rank is not None if expected_ids else None
        domain_hit = bool(expected_domains and any(_row_domains(row) & expected_domains for row in rows)) if expected_domains else None
        memory_type_hit = bool(expected_types and any(_row_memory_type(row) in expected_types for row in rows)) if expected_types else None
        passed = len(rows) >= int(case.get("min_results") or 1)
        if expected_id_hit is not None:
            passed = passed and expected_id_hit
        if domain_hit is not None:
            passed = passed and domain_hit
        if memory_type_hit is not None:
            passed = passed and memory_type_hit
        case_reports.append({
            "name": case.get("name") or f"case-{index}",
            "query_hash": _query_hash(case["query"]),
            "result_count": len(rows),
            "expected_id_hit": expected_id_hit,
            "domain_hit": domain_hit,
            "memory_type_hit": memory_type_hit,
            "first_expected_rank": first_expected_rank,
            "reciprocal_rank": round(1 / first_expected_rank, 4) if first_expected_rank else 0.0,
            "passed": bool(passed),
            "top_ids": result_ids[:10],
        })
    expected_id_cases = [row for row, case in zip(case_reports, cases) if case.get("expected_ids")]
    mrr_values = [row["reciprocal_rank"] for row in expected_id_cases]
    passed_count = sum(1 for row in case_reports if row["passed"])
    total = len(case_reports)
    return {
        "summary": {
            "cases": total,
            "passed": passed_count,
            "failed": total - passed_count,
            "pass_rate": round(passed_count / total, 4) if total else 0.0,
            "expected_id_hits": sum(1 for row in case_reports if row["expected_id_hit"] is True),
            "domain_hits": sum(1 for row in case_reports if row["domain_hit"] is True),
            "memory_type_hits": sum(1 for row in case_reports if row["memory_type_hit"] is True),
            "mean_reciprocal_rank": round(sum(mrr_values) / len(mrr_values), 4) if mrr_values else 0.0,
        },
        "cases": case_reports,
        "failed_cases": [row["name"] for row in case_reports if not row["passed"]],
    }


def compare_eval_reports(mem0_report: dict[str, Any], kontext_report: dict[str, Any]) -> dict[str, Any]:
    mem0_cases = mem0_report.get("cases") or []
    kontext_cases = kontext_report.get("cases") or []
    shared_any = 0
    shared_first = 0
    mismatches = []
    for mem0_case, kontext_case in zip(mem0_cases, kontext_cases):
        mem0_ids = [str(value) for value in mem0_case.get("top_ids") or []]
        kontext_ids = [str(value) for value in kontext_case.get("top_ids") or []]
        has_shared = bool(set(mem0_ids) & set(kontext_ids))
        if has_shared:
            shared_any += 1
        if mem0_ids and kontext_ids and mem0_ids[0] == kontext_ids[0]:
            shared_first += 1
        if not has_shared:
            mismatches.append(mem0_case.get("name") or mem0_case.get("query_hash"))
    total = min(len(mem0_cases), len(kontext_cases))
    return {
        "cases": total,
        "shared_any_cases": shared_any,
        "shared_first_cases": shared_first,
        "shared_any_rate": round(shared_any / total, 4) if total else 0.0,
        "shared_first_rate": round(shared_first / total, 4) if total else 0.0,
        "mismatch_cases": mismatches,
    }


def require_local_staging_database(database_url: str) -> None:
    parsed = urlparse(database_url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Kontext parity requires the local staging database")
    if parsed.port != 55434 or parsed.username != "kontext_v2" or parsed.path != "/kontext_v2":
        raise RuntimeError("Kontext parity requires kontext_v2@localhost:55434/kontext_v2")


def _make_mem0_client(args: argparse.Namespace) -> Mem0ApiClient:
    api_key = os.environ.get(args.mem0_api_key_env, "").strip()
    if not api_key:
        raise RuntimeError("Mem0 API key environment variable is missing")
    return Mem0ApiClient(base_url=args.mem0_base_url, api_key=api_key, user_id=args.mem0_user_id)


def _write_or_print(payload: dict[str, Any], output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(text + "\n", encoding="utf-8")
    print(text)


def run_configured(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_eval_cases(args.cases)
    if not args.kontext_database_url:
        raise RuntimeError("--kontext-database-url is required outside --dry-run")
    require_local_staging_database(args.kontext_database_url)
    client = _make_mem0_client(args)
    top_k = min(max(int(args.top_k or 5), 1), 50)
    payload: dict[str, Any] = {"ok": True, "mode": "live_parity", "top_k": top_k, "case_count": len(cases), "imports": []}
    with psycopg.connect(args.kontext_database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        if args.live_import:
            report, rows_seen = import_live_snapshot(repo, client)
            payload["imports"].append(safe_import_report(report, "live_snapshot", rows_seen))
        if args.seed_from_cases:
            report, rows_seen = import_eval_seed(repo, client, cases, top_k=top_k, max_fetches=max(int(args.max_fetches or 0), 0))
            payload["imports"].append(safe_import_report(report, "eval_seed", rows_seen))
        if args.run_eval:
            mem0_report = evaluate_cases(client.search, cases, top_k=top_k)
            kontext_report = evaluate_cases(
                lambda query, top_k=5: {
                    "results": search_memories(
                        repo,
                        query=query,
                        top_k=top_k,
                        domains=[],
                        memory_types=[],
                        memory_tiers=[],
                        current_statuses=[],
                    )
                },
                cases,
                top_k=top_k,
            )
            payload["mem0_eval"] = mem0_report
            payload["kontext_eval"] = kontext_report
            payload["parity"] = compare_eval_reports(mem0_report, kontext_report)
        payload["kontext_mirror"] = {"memories": repo.count_memories()}
    return payload


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.dry_run:
        payload = {
            "ok": True,
            "mode": "dry_run",
            "cases": args.cases,
            "top_k": min(max(int(args.top_k or 5), 1), 50),
            "actions": {
                "live_import": bool(args.live_import),
                "seed_from_cases": bool(args.seed_from_cases),
                "run_eval": bool(args.run_eval),
            },
        }
        _write_or_print(payload, args.output)
        return 0
    payload = run_configured(args)
    _write_or_print(payload, args.output)
    return 0
