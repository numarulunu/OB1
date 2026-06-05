from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'judged_benchmark_plan.py'


def load_module():
    spec = importlib.util.spec_from_file_location('judged_benchmark_plan', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def args(**overrides):
    defaults = {
        'predict_report': None,
        'dataset': 'longmemeval',
        'question_count': 5,
        'question_scope': 'all',
        'max_questions': None,
        'cutoffs': '10,50',
        'mode': None,
        'judge_units_per_question': 1.0,
        'answer_input_tokens': 100,
        'answer_output_tokens': 10,
        'judge_input_tokens': 50,
        'judge_output_tokens': 5,
        'answer_input_usd_per_1m': None,
        'answer_output_usd_per_1m': None,
        'judge_input_usd_per_1m': None,
        'judge_output_usd_per_1m': None,
        'output': None,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def test_plan_counts_answerer_and_judge_calls_without_costs():
    module = load_module()
    plan = module.build_plan(args())

    assert plan['runs_model_calls'] is False
    assert plan['judging_mode'] == 'answerer-judge'
    assert plan['selected_questions'] == 5
    assert plan['top_k_cutoffs'] == [10, 50]
    assert plan['estimated_llm_calls'] == {'answer_calls': 10, 'judge_calls': 10, 'total_calls': 20}
    assert plan['estimated_tokens']['total_tokens'] == 1650
    assert plan['estimated_cost_usd'] is None


def test_plan_reads_predict_report_without_raw_question_text(tmp_path):
    module = load_module()
    report_path = tmp_path / 'predict.json'
    report_path.write_text(
        json.dumps(
            {
                'dataset': 'beam_10M',
                'questions': [
                    {'question_id': 'q1', 'question': 'private text', 'retrieval_evaluable': True, 'matched_by_top_k': {'50': True}},
                    {'question_id': 'q2', 'question': 'private text 2', 'retrieval_evaluable': False, 'matched_by_top_k': {'50': False}},
                ],
            }
        ),
        encoding='utf-8',
    )

    plan = module.build_plan(args(predict_report=str(report_path), question_scope='retrieval-evaluable', question_count=None, judge_units_per_question=3))
    rendered = json.dumps(plan)

    assert plan['dataset'] == 'beam_10M'
    assert plan['judging_mode'] == 'beam-rubric'
    assert plan['question_counts']['selected'] == 1
    assert plan['estimated_llm_calls']['answer_calls'] == 2
    assert plan['estimated_llm_calls']['judge_calls'] == 6
    assert 'private text' not in rendered


def test_cost_is_only_returned_when_all_prices_are_supplied():
    module = load_module()
    plan = module.build_plan(
        args(
            answer_input_usd_per_1m=1,
            answer_output_usd_per_1m=2,
            judge_input_usd_per_1m=3,
            judge_output_usd_per_1m=4,
        )
    )

    assert plan['estimated_cost_usd'] == {'answerer_usd': 0.0012, 'judge_usd': 0.0017, 'total_usd': 0.0029}


def test_cli_writes_output(tmp_path, capsys):
    module = load_module()
    output = tmp_path / 'plan.json'

    assert module.main(['--dataset', 'locomo', '--question-count', '2', '--max-questions', '1', '--cutoffs', '200', '--output', str(output)]) == 0
    payload = json.loads(output.read_text(encoding='utf-8'))

    assert payload['selected_questions'] == 1
    assert payload['estimated_llm_calls']['total_calls'] == 2
    assert json.loads(capsys.readouterr().out)['ok'] is True
