
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class EvalCase:
    query: str
    name: str = ''
    expected_ids: list[str] = field(default_factory=list)
    expected_domains: list[str] = field(default_factory=list)
    expected_memory_types: list[str] = field(default_factory=list)
    min_results: int = 1


def normalize_values(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def load_eval_cases(path: str | Path) -> list[EvalCase]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    rows = data.get('cases') if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError('eval cases must be a list or an object with a cases list')
    cases = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('each eval case must be an object')
        query = str(row.get('query') or '').strip()
        if not query:
            raise ValueError('each eval case requires query')
        cases.append(
            EvalCase(
                name=str(row.get('name') or '').strip(),
                query=query,
                expected_ids=[str(value).strip() for value in row.get('expected_ids') or [] if str(value).strip()],
                expected_domains=[str(value).strip() for value in row.get('expected_domains') or [] if str(value).strip()],
                expected_memory_types=[str(value).strip() for value in row.get('expected_memory_types') or [] if str(value).strip()],
                min_results=max(int(row.get('min_results') or 1), 0),
            )
        )
    return cases


def row_domains(row: dict[str, Any]) -> set[str]:
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    return normalize_values(metadata.get('domains') or row.get('domains'))


def row_memory_type(row: dict[str, Any]) -> str:
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    return str(metadata.get('memory_type') or row.get('memory_type') or '').strip().lower()


def normalize_results(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get('results') if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


def evaluate_case(case: EvalCase, results: list[dict[str, Any]]) -> dict[str, Any]:
    result_ids = [str(row.get('id') or '') for row in results]
    expected_ids = [str(value) for value in case.expected_ids]
    expected_id_set = set(expected_ids)
    first_expected_rank = None
    for index, memory_id in enumerate(result_ids, start=1):
        if memory_id in expected_id_set:
            first_expected_rank = index
            break
    expected_id_hit = first_expected_rank is not None if expected_ids else None
    expected_domains = normalize_values(case.expected_domains)
    expected_types = normalize_values(case.expected_memory_types)
    domain_hit = bool(expected_domains and any(row_domains(row) & expected_domains for row in results)) if expected_domains else None
    memory_type_hit = bool(expected_types and any(row_memory_type(row) in expected_types for row in results)) if expected_types else None
    enough_results = len(results) >= case.min_results
    passed = enough_results
    if expected_id_hit is not None:
        passed = passed and expected_id_hit
    if domain_hit is not None:
        passed = passed and domain_hit
    if memory_type_hit is not None:
        passed = passed and memory_type_hit
    return {
        'name': case.name,
        'query': case.query,
        'result_count': len(results),
        'expected_id_hit': expected_id_hit,
        'domain_hit': domain_hit,
        'memory_type_hit': memory_type_hit,
        'first_expected_rank': first_expected_rank,
        'reciprocal_rank': round(1 / first_expected_rank, 4) if first_expected_rank else 0.0,
        'passed': bool(passed),
        'top_ids': result_ids[:10],
    }


def evaluate_retrieval_cases(search_fn: Callable[..., Any], cases: list[EvalCase], top_k: int = 5) -> dict[str, Any]:
    case_reports = []
    for case in cases:
        payload = search_fn(case.query, top_k=top_k)
        case_reports.append(evaluate_case(case, normalize_results(payload)))

    expected_id_cases = [row for row, case in zip(case_reports, cases) if case.expected_ids]
    mrr_values = [row['reciprocal_rank'] for row in expected_id_cases]
    passed = sum(1 for row in case_reports if row['passed'])
    total = len(case_reports)
    summary = {
        'cases': total,
        'passed': passed,
        'failed': total - passed,
        'pass_rate': round(passed / total, 4) if total else 0.0,
        'expected_id_hits': sum(1 for row in case_reports if row['expected_id_hit'] is True),
        'domain_hits': sum(1 for row in case_reports if row['domain_hit'] is True),
        'memory_type_hits': sum(1 for row in case_reports if row['memory_type_hit'] is True),
        'mean_reciprocal_rank': round(sum(mrr_values) / len(mrr_values), 4) if mrr_values else 0.0,
    }
    return {'summary': summary, 'cases': case_reports}


def failed_case_names(report: dict[str, Any]) -> list[str]:
    names = []
    for index, row in enumerate(report.get('cases') or [], start=1):
        if row.get('passed'):
            continue
        name = str(row.get('name') or '').strip() or f'case-{index}'
        names.append(name)
    return names


def report_meets_thresholds(report: dict[str, Any], min_pass_rate: float = 1.0, min_cases: int = 1) -> bool:
    summary = report.get('summary') or {}
    cases = int(summary.get('cases') or 0)
    try:
        pass_rate = float(summary.get('pass_rate') or 0.0)
    except (TypeError, ValueError):
        pass_rate = 0.0
    return cases >= max(int(min_cases or 0), 0) and pass_rate >= max(float(min_pass_rate or 0.0), 0.0)


def format_retrieval_eval_report(report: dict[str, Any]) -> str:
    summary = report.get('summary') or {}
    lines = [
        'Mem0 retrieval eval',
        f"cases={summary.get('cases', 0)} passed={summary.get('passed', 0)} pass_rate={summary.get('pass_rate', 0.0)} mrr={summary.get('mean_reciprocal_rank', 0.0)}",
    ]
    failed = failed_case_names(report)
    if failed:
        lines.append('failed=' + ','.join(failed))
    return '\n'.join(lines)


def make_live_search():
    from core import Mem0Client, Mem0Config

    api_key = os.environ.get('MEM0_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('MEM0_API_KEY is required for live retrieval eval')
    client = Mem0Client(
        Mem0Config(
            base_url=os.environ.get('MEM0_BASE_URL', 'http://127.0.0.1:18888'),
            api_key=api_key,
            user_id=os.environ.get('MEM0_USER_ID', 'ionut'),
            client_name='retrieval-eval',
            lexical_database_url=os.environ.get('MEM0_LEXICAL_DATABASE_URL', ''),
            retrieval_telemetry_log='',
        )
    )
    return client.search


def main() -> int:
    parser = argparse.ArgumentParser(description='Run safe retrieval quality eval cases against Mem0 search.')
    parser.add_argument('--cases', required=True, help='Path to retrieval eval cases JSON')
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--min-pass-rate', type=float, default=1.0, help='Minimum passing case ratio required for exit 0')
    parser.add_argument('--min-cases', type=int, default=1, help='Minimum loaded case count required for exit 0')
    parser.add_argument('--output', default='', help='Optional path for the full JSON report')
    parser.add_argument('--json', action='store_true', help='Print full JSON report, including case queries')
    args = parser.parse_args()

    report = evaluate_retrieval_cases(make_live_search(), load_eval_cases(args.cases), top_k=args.top_k)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_retrieval_eval_report(report))
    return 0 if report_meets_thresholds(report, min_pass_rate=args.min_pass_rate, min_cases=args.min_cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
