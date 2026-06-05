
import json
from pathlib import Path

from retrieval_eval import (
    EvalCase,
    evaluate_retrieval_cases,
    format_retrieval_eval_report,
    load_eval_cases,
    report_meets_thresholds,
)


def test_retrieval_eval_scores_expected_ids_domains_and_mrr():
    def fake_search(query, top_k=5, **filters):
        if 'memory layer' in query:
            return {
                'results': [
                    {'id': 'wrong', 'metadata': {'domains': ['workflow'], 'memory_type': 'note'}},
                    {'id': 'architecture', 'metadata': {'domains': ['ai', 'systems'], 'memory_type': 'architecture_decision'}},
                ]
            }
        return {'results': [{'id': 'relationship', 'metadata': {'domains': ['relationships', 'psychology'], 'memory_type': 'relationship_pattern'}}]}

    report = evaluate_retrieval_cases(
        fake_search,
        [
            EvalCase(query='memory layer architecture', expected_ids=['architecture'], expected_domains=['ai'], expected_memory_types=['architecture_decision']),
            EvalCase(query='relationship pattern', expected_domains=['psychology'], expected_memory_types=['relationship_pattern']),
        ],
        top_k=5,
    )

    assert report['summary']['cases'] == 2
    assert report['summary']['passed'] == 2
    assert report['summary']['expected_id_hits'] == 1
    assert report['summary']['domain_hits'] == 2
    assert report['summary']['memory_type_hits'] == 2
    assert report['summary']['mean_reciprocal_rank'] == 0.5
    assert report['cases'][0]['first_expected_rank'] == 2


def test_load_eval_cases_from_json(tmp_path):
    path = tmp_path / 'cases.json'
    path.write_text(
        json.dumps(
            [
                {
                    'name': 'ai systems',
                    'query': 'mem0 mcp architecture',
                    'expected_domains': ['ai', 'systems'],
                    'expected_memory_types': ['architecture_decision'],
                    'min_results': 1,
                }
            ]
        ),
        encoding='utf-8',
    )

    cases = load_eval_cases(path)

    assert cases == [
        EvalCase(
            name='ai systems',
            query='mem0 mcp architecture',
            expected_domains=['ai', 'systems'],
            expected_memory_types=['architecture_decision'],
            min_results=1,
        )
    ]

def test_retrieval_eval_thresholds_and_safe_default_report():
    def fake_search(query, top_k=5, **filters):
        if 'private mother query' in query:
            return {'results': [{'id': 'wrong', 'metadata': {'domains': ['ai'], 'memory_type': 'note'}}]}
        return {'results': [{'id': 'ok', 'metadata': {'domains': ['systems'], 'memory_type': 'project_state'}}]}

    report = evaluate_retrieval_cases(
        fake_search,
        [
            EvalCase(name='systems', query='systems query', expected_domains=['systems'], expected_memory_types=['project_state']),
            EvalCase(name='private-case', query='private mother query', expected_domains=['relationships'], expected_memory_types=['relationship_pattern']),
        ],
        top_k=5,
    )

    assert report['summary']['pass_rate'] == 0.5
    assert report_meets_thresholds(report, min_pass_rate=0.5, min_cases=2) is True
    assert report_meets_thresholds(report, min_pass_rate=0.75, min_cases=2) is False
    assert report_meets_thresholds(report, min_pass_rate=0.5, min_cases=3) is False

    rendered = format_retrieval_eval_report(report)
    assert 'cases=2 passed=1 pass_rate=0.5' in rendered
    assert 'failed=private-case' in rendered
    assert 'private mother query' not in rendered

def test_v113_eval_case_pack_is_present_and_domain_based():
    cases = load_eval_cases(Path(__file__).with_name('retrieval_eval_cases.v1.13.json'))

    assert len(cases) >= 10
    assert all(case.name for case in cases)
    assert all(case.expected_domains for case in cases)
    assert sum(1 for case in cases if {'relationships', 'psychology'} & set(case.expected_domains)) >= 3
    assert sum(1 for case in cases if {'ai', 'systems'} & set(case.expected_domains)) >= 3
