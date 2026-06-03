from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_CUTOFFS = [10, 20, 50, 200]
DEFAULT_ANSWER_INPUT_TOKENS = 4_000
DEFAULT_ANSWER_OUTPUT_TOKENS = 300
DEFAULT_JUDGE_INPUT_TOKENS = 1_500
DEFAULT_JUDGE_OUTPUT_TOKENS = 120


def parse_cutoffs(value: str | None) -> list[int]:
    if not value:
        return DEFAULT_CUTOFFS.copy()
    cutoffs = sorted({min(max(int(item.strip()), 1), 200) for item in value.split(',') if item.strip()})
    if not cutoffs:
        raise ValueError('at least one cutoff is required')
    return cutoffs


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'{path} must contain a JSON object')
    return payload


def question_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report.get('questions')
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    rows = report.get('question_rows')
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def row_matched(row: dict[str, Any]) -> bool:
    values = row.get('matched_by_top_k')
    if isinstance(values, dict):
        return any(bool(value) for value in values.values())
    return bool(row.get('matched'))


def count_questions(report: dict[str, Any], scope: str, fallback_count: int | None = None) -> dict[str, int]:
    rows = question_rows(report)
    if rows:
        total = len(rows)
        evaluable = sum(1 for row in rows if bool(row.get('retrieval_evaluable', True)))
        matched = sum(1 for row in rows if row_matched(row))
    else:
        summary = report.get('summary') if isinstance(report.get('summary'), dict) else report
        total = int(summary.get('total_questions') or fallback_count or 0)
        evaluable = int(summary.get('retrieval_evaluable_questions') or total)
        matched = int(summary.get('retrieval_matched_questions') or summary.get('matched_questions') or evaluable)
    selected = {'all': total, 'retrieval-evaluable': evaluable, 'matched-only': matched}[scope]
    return {'total': total, 'retrieval_evaluable': evaluable, 'matched': matched, 'selected': selected}


def dataset_from_report(report: dict[str, Any], fallback: str | None) -> str:
    return str(report.get('dataset') or fallback or 'unknown').strip() or 'unknown'


def default_judging_mode(dataset: str) -> str:
    lowered = dataset.lower()
    if 'beam' in lowered:
        return 'beam-rubric'
    return 'answerer-judge'


def llm_call_counts(question_count: int, cutoffs: list[int], mode: str, judge_units_per_question: float) -> dict[str, float]:
    cutoff_count = len(cutoffs)
    if mode == 'retrieval-judge':
        answer_calls = 0
        judge_calls = question_count * cutoff_count
    elif mode == 'beam-rubric':
        answer_calls = question_count * cutoff_count
        judge_calls = question_count * cutoff_count * judge_units_per_question
    else:
        answer_calls = question_count * cutoff_count
        judge_calls = question_count * cutoff_count
    return {'answer_calls': answer_calls, 'judge_calls': judge_calls, 'total_calls': answer_calls + judge_calls}


def token_estimate(counts: dict[str, float], args: argparse.Namespace) -> dict[str, float]:
    answer_input = counts['answer_calls'] * args.answer_input_tokens
    answer_output = counts['answer_calls'] * args.answer_output_tokens
    judge_input = counts['judge_calls'] * args.judge_input_tokens
    judge_output = counts['judge_calls'] * args.judge_output_tokens
    return {
        'answer_input_tokens': answer_input,
        'answer_output_tokens': answer_output,
        'judge_input_tokens': judge_input,
        'judge_output_tokens': judge_output,
        'total_input_tokens': answer_input + judge_input,
        'total_output_tokens': answer_output + judge_output,
        'total_tokens': answer_input + answer_output + judge_input + judge_output,
    }


def usd_cost(tokens: dict[str, float], args: argparse.Namespace) -> dict[str, float] | None:
    prices = [
        args.answer_input_usd_per_1m,
        args.answer_output_usd_per_1m,
        args.judge_input_usd_per_1m,
        args.judge_output_usd_per_1m,
    ]
    if any(value is None for value in prices):
        return None
    answer = tokens['answer_input_tokens'] * args.answer_input_usd_per_1m / 1_000_000
    answer += tokens['answer_output_tokens'] * args.answer_output_usd_per_1m / 1_000_000
    judge = tokens['judge_input_tokens'] * args.judge_input_usd_per_1m / 1_000_000
    judge += tokens['judge_output_tokens'] * args.judge_output_usd_per_1m / 1_000_000
    return {'answerer_usd': round(answer, 4), 'judge_usd': round(judge, 4), 'total_usd': round(answer + judge, 4)}


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    report = load_json(args.predict_report) if args.predict_report else {}
    dataset = dataset_from_report(report, args.dataset)
    mode = args.mode or default_judging_mode(dataset)
    cutoffs = parse_cutoffs(args.cutoffs)
    counts = count_questions(report, args.question_scope, args.question_count)
    selected_questions = min(counts['selected'], args.max_questions) if args.max_questions is not None else counts['selected']
    calls = llm_call_counts(selected_questions, cutoffs, mode, args.judge_units_per_question)
    tokens = token_estimate(calls, args)
    cost = usd_cost(tokens, args)
    return {
        'ok': True,
        'mode': 'judged-micro-slice-plan',
        'runs_model_calls': False,
        'dataset': dataset,
        'judging_mode': mode,
        'question_scope': args.question_scope,
        'question_counts': counts,
        'selected_questions': selected_questions,
        'top_k_cutoffs': cutoffs,
        'judge_units_per_question': args.judge_units_per_question,
        'estimated_llm_calls': calls,
        'estimated_tokens': tokens,
        'estimated_cost_usd': cost,
        'notes': [
            'This planner does not call answerer or judge models.',
            'Official Mem0 judged runs include answer generation and judge scoring; predict-only retrieval is not the same evidence.',
            'For BEAM, judge units are rubric nuggets per question when available; the default is a lower-bound estimate.',
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Plan a no-API judged benchmark micro-slice for Kontext V2.')
    parser.add_argument('--predict-report', help='Existing predict-only report JSON. No raw memories are printed.')
    parser.add_argument('--dataset', help='Dataset name when no report is supplied.')
    parser.add_argument('--question-count', type=int, default=None, help='Fallback question count when no report is supplied.')
    parser.add_argument('--question-scope', choices=['all', 'retrieval-evaluable', 'matched-only'], default='all')
    parser.add_argument('--max-questions', type=int, default=None, help='Cap the planned judged slice.')
    parser.add_argument('--cutoffs', default='10,20,50,200')
    parser.add_argument('--mode', choices=['answerer-judge', 'retrieval-judge', 'beam-rubric'])
    parser.add_argument('--judge-units-per-question', type=float, default=1.0)
    parser.add_argument('--answer-input-tokens', type=int, default=DEFAULT_ANSWER_INPUT_TOKENS)
    parser.add_argument('--answer-output-tokens', type=int, default=DEFAULT_ANSWER_OUTPUT_TOKENS)
    parser.add_argument('--judge-input-tokens', type=int, default=DEFAULT_JUDGE_INPUT_TOKENS)
    parser.add_argument('--judge-output-tokens', type=int, default=DEFAULT_JUDGE_OUTPUT_TOKENS)
    parser.add_argument('--answer-input-usd-per-1m', type=float)
    parser.add_argument('--answer-output-usd-per-1m', type=float)
    parser.add_argument('--judge-input-usd-per-1m', type=float)
    parser.add_argument('--judge-output-usd-per-1m', type=float)
    parser.add_argument('--output')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.predict_report and not args.question_count:
        raise SystemExit('--predict-report or --question-count is required')
    plan = build_plan(args)
    text = json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
