from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_CASES = "tools/mem0-remote-mcp/retrieval_eval_cases.v1.13.json"
DEFAULT_SERVICES = {
    "kontext": "http://127.0.0.1:8200/api/v2/mcp/{token}",
    "mem0": "https://memory-mcp.ionutrosu.xyz/mcp/{token}",
}
DEFAULT_PROFILES = {
    "codex": "MCP_CODEX_TOKEN",
    "claude": "MCP_CLAUDE_TOKEN",
}


def normalize_values(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("cases must be a list or object with cases")
    cases: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        query = str(row.get("query") or "").strip()
        if not query:
            continue
        cases.append({
            "name": str(row.get("name") or "").strip() or hashlib.sha256(query.encode()).hexdigest()[:12],
            "query": query,
            "query_hash": hashlib.sha256(query.encode()).hexdigest()[:16],
            "expected_domains": sorted(normalize_values(row.get("expected_domains"))),
            "expected_memory_types": sorted(normalize_values(row.get("expected_memory_types"))),
            "min_results": max(int(row.get("min_results") or 1), 0),
        })
    return cases


def parse_tool_text_payload(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result") if isinstance(response, dict) else None
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list) or not content:
        return {}
    text = content[0].get("text") if isinstance(content[0], dict) else ""
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def row_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("external_mem0_id") or "").strip()


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return metadata


def row_domains(row: dict[str, Any]) -> set[str]:
    metadata = row_metadata(row)
    return normalize_values(metadata.get("domains") or row.get("domains"))


def row_memory_type(row: dict[str, Any]) -> str:
    metadata = row_metadata(row)
    return str(metadata.get("memory_type") or row.get("memory_type") or "").strip().lower()


def safe_result_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row_id(row),
        "title": str(row.get("title") or ""),
        "domains": sorted(row_domains(row)),
        "memory_type": row_memory_type(row),
    }


def safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [safe_result_row(row) for row in rows]


def evaluate_case_reports(
    cases: list[dict[str, Any]],
    service_results: dict[str, list[dict[str, Any]]],
    include_top_rows: bool = False,
) -> dict[str, Any]:
    case_reports: list[dict[str, Any]] = []
    for case in cases:
        rows = service_results.get(case["name"], [])
        expected_domains = normalize_values(case.get("expected_domains"))
        expected_types = normalize_values(case.get("expected_memory_types"))
        domain_hit = bool(expected_domains and any(row_domains(row) & expected_domains for row in rows)) if expected_domains else None
        memory_type_hit = bool(expected_types and any(row_memory_type(row) in expected_types for row in rows)) if expected_types else None
        passed = len(rows) >= int(case.get("min_results") or 1)
        if domain_hit is not None:
            passed = passed and domain_hit
        if memory_type_hit is not None:
            passed = passed and memory_type_hit
        report = {
            "name": case["name"],
            "query_hash": case.get("query_hash"),
            "result_count": len(rows),
            "domain_hit": domain_hit,
            "memory_type_hit": memory_type_hit,
            "passed": bool(passed),
            "top_ids": [row_id(row) for row in rows if row_id(row)][:10],
        }
        if include_top_rows:
            report["top_rows"] = safe_rows(rows[:5])
        case_reports.append(report)
    passed_count = sum(1 for report in case_reports if report["passed"])
    total = len(case_reports)
    return {
        "summary": {
            "cases": total,
            "passed": passed_count,
            "failed": total - passed_count,
            "pass_rate": round(passed_count / total, 4) if total else 0.0,
            "domain_hits": sum(1 for report in case_reports if report["domain_hit"] is True),
            "memory_type_hits": sum(1 for report in case_reports if report["memory_type_hit"] is True),
        },
        "cases": case_reports,
        "failed_cases": [report["name"] for report in case_reports if not report["passed"]],
    }


def rpc(url: str, method: str, params: dict[str, Any] | None = None, request_id: int = 1, timeout: int = 45) -> tuple[dict[str, Any], float]:
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload, (time.perf_counter() - started) * 1000


def call_search(url: str, query: str, top_k: int) -> tuple[list[dict[str, Any]], float, str]:
    response, latency_ms = rpc(
        url,
        "tools/call",
        {"name": "search", "arguments": {"query": query, "top_k": top_k}},
        request_id=3,
    )
    if response.get("error"):
        return [], latency_ms, str(response["error"].get("message") or "jsonrpc_error")
    payload = parse_tool_text_payload(response)
    rows = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    return [row for row in rows if isinstance(row, dict)], latency_ms, ""


def summarize_latency(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "avg_ms": 0.0, "p50_ms": 0.0, "max_ms": 0.0}
    return {
        "count": len(values),
        "avg_ms": round(sum(values) / len(values), 2),
        "p50_ms": round(statistics.median(values), 2),
        "max_ms": round(max(values), 2),
    }


def evaluate_service(url: str, cases: list[dict[str, Any]], top_k: int, include_top_rows: bool = False) -> dict[str, Any]:
    init_ok = False
    tools: list[str] = []
    errors: list[str] = []
    latencies: list[float] = []
    try:
        init, init_latency = rpc(url, "initialize", {"protocolVersion": "2024-11-05"}, request_id=1)
        init_ok = bool(init.get("result"))
        latencies.append(init_latency)
        tool_response, tool_latency = rpc(url, "tools/list", {}, request_id=2)
        latencies.append(tool_latency)
        tools = sorted(str(tool.get("name") or "") for tool in tool_response.get("result", {}).get("tools", []) if isinstance(tool, dict))
    except urllib.error.HTTPError as exc:
        errors.append(f"http_{exc.code}")
    except Exception as exc:  # noqa: BLE001
        errors.append(type(exc).__name__)

    results_by_case: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        if errors:
            results_by_case[case["name"]] = []
            continue
        try:
            rows, latency_ms, error = call_search(url, case["query"], top_k=top_k)
            latencies.append(latency_ms)
            if error:
                errors.append(f"{case['name']}:{error}")
            results_by_case[case["name"]] = rows
        except urllib.error.HTTPError as exc:
            errors.append(f"{case['name']}:http_{exc.code}")
            results_by_case[case["name"]] = []
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{case['name']}:{type(exc).__name__}")
            results_by_case[case["name"]] = []

    report = evaluate_case_reports(cases, results_by_case, include_top_rows=include_top_rows)
    return {
        "init_ok": init_ok,
        "tools": tools,
        "latency": summarize_latency(latencies),
        "errors": errors[:20],
        "eval": report,
    }


def compare_services(service_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    names = list(service_reports)
    if len(names) < 2:
        return {"services": names}
    first, second = names[0], names[1]
    first_cases = service_reports[first].get("eval", {}).get("cases", [])
    second_cases = service_reports[second].get("eval", {}).get("cases", [])
    shared_any = 0
    shared_first = 0
    no_overlap: list[str] = []
    for left, right in zip(first_cases, second_cases):
        left_ids = [str(value) for value in left.get("top_ids") or []]
        right_ids = [str(value) for value in right.get("top_ids") or []]
        overlap = set(left_ids) & set(right_ids)
        if overlap:
            shared_any += 1
        else:
            no_overlap.append(str(left.get("name") or right.get("name")))
        if left_ids and right_ids and left_ids[0] == right_ids[0]:
            shared_first += 1
    total = min(len(first_cases), len(second_cases))
    return {
        "pair": [first, second],
        "cases": total,
        "shared_any_cases": shared_any,
        "shared_first_cases": shared_first,
        "shared_any_rate": round(shared_any / total, 4) if total else 0.0,
        "shared_first_rate": round(shared_first / total, 4) if total else 0.0,
        "no_overlap_cases": no_overlap,
    }


def run_reliability_eval(
    cases_path: str,
    profiles: dict[str, str],
    services: dict[str, str],
    top_k: int = 5,
    include_top_rows: bool = False,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    output: dict[str, Any] = {"ok": True, "case_count": len(cases), "top_k": top_k, "profiles": {}}
    for profile_name, token_env in profiles.items():
        token = os.environ.get(token_env, "").strip()
        if not token:
            output["profiles"][profile_name] = {"ok": False, "error": "missing_token"}
            continue
        service_reports: dict[str, dict[str, Any]] = {}
        for service_name, template in services.items():
            service_reports[service_name] = evaluate_service(
                template.format(token=token),
                cases=cases,
                top_k=top_k,
                include_top_rows=include_top_rows,
            )
        output["profiles"][profile_name] = {
            "ok": all(not report.get("errors") for report in service_reports.values()),
            "services": service_reports,
            "comparison": compare_services(service_reports),
        }
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe live MCP reliability comparison for Kontext V2 and Mem0.")
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output")
    parser.add_argument("--profiles", default="codex,claude")
    parser.add_argument("--include-top-rows", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    selected_profiles = {
        name: DEFAULT_PROFILES[name]
        for name in [item.strip() for item in args.profiles.split(",") if item.strip()]
        if name in DEFAULT_PROFILES
    }
    payload = run_reliability_eval(
        cases_path=args.cases,
        profiles=selected_profiles,
        services=DEFAULT_SERVICES,
        top_k=min(max(int(args.top_k or 5), 1), 20),
        include_top_rows=bool(args.include_top_rows),
    )
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
