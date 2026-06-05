from kontext_v2.benchmarks.reporting import (
    build_private_judged_input_bundle,
    build_predict_only_report,
    build_predict_sweep_report,
    format_predict_cli_summary,
    format_sweep_cli_summary,
)


def test_predict_only_report_skips_questions_without_a_match_rule():
    report = build_predict_only_report(
        'unit_dataset',
        'unit-run',
        5,
        [
            {'question_id': 'no-rule', 'category': 'abstention', 'matched': True, 'expected_terms': [], 'evidence': [], 'search_latency_ms': 1.0, 'search_results': [{'memory': 'irrelevant result'}]},
            {'question_id': 'with-evidence', 'category': 'fact', 'matched': True, 'expected_terms': [], 'evidence': ['turn-1'], 'search_latency_ms': 2.0, 'search_results': [{'memory': 'match', 'metadata': {'source_ids': ['turn-1']}}]},
        ],
    )

    assert report['total_questions'] == 2
    assert report['matched_questions'] == 1
    assert report['retrieval_evaluable_questions'] == 1
    assert report['retrieval_matched_questions'] == 1
    assert report['skipped_no_match_rule'] == 1
    assert report['retrieval_match_rate'] == 1.0
    assert report['mrr'] == 1.0
    assert report['first_hit_found_questions'] == 1
    assert report['median_first_hit_rank'] == 1
    assert report['questions'][0]['matched'] is False
    assert report['questions'][0]['retrieval_evaluable'] is False
    assert report['questions'][1]['first_hit_rank'] == 1
    assert report['categories']['abstention']['skipped_no_match_rule'] == 1
    assert format_predict_cli_summary(report) == 'matched=1/2 retrieval=1/1 mrr=1.0 skipped=1 avg_ms=1.5'


def test_sweep_report_counts_only_retrieval_evaluable_questions():
    report = build_predict_sweep_report(
        'unit_dataset',
        'unit-sweep',
        [1, 2],
        [
            {'question_id': 'no-rule', 'category': 'abstention', 'expected_terms': [], 'evidence': [], 'search_latency_ms': 1.0, 'search_results': [{'id': 'a', 'memory': 'result without official match rule', 'metadata': {}}]},
            {'question_id': 'with-evidence', 'category': 'fact', 'expected_terms': [], 'evidence': ['turn-2'], 'search_latency_ms': 2.0, 'search_results': [{'id': 'b', 'memory': 'distractor', 'metadata': {'source_ids': ['turn-1']}}, {'id': 'c', 'memory': 'evidence', 'metadata': {'source_ids': ['turn-2']}}]},
        ],
    )

    assert report['retrieval_evaluable_questions'] == 1
    assert report['skipped_no_match_rule'] == 1
    assert report['questions'][0]['retrieval_evaluable'] is False
    assert report['questions'][0]['matched_by_top_k'] == {'1': False, '2': False}
    assert report['miss_analysis']['1']['reasons'] == {'evidence_below_cutoff': 1, 'no_match_rule': 1}
    assert report['sweeps']['1']['retrieval_evaluable_questions'] == 1
    assert report['sweeps']['1']['retrieval_matched_questions'] == 0
    assert report['sweeps']['1']['retrieval_match_rate'] == 0.0
    assert report['sweeps']['1']['mrr'] == 0.0
    assert report['sweeps']['2']['retrieval_matched_questions'] == 1
    assert report['sweeps']['2']['retrieval_match_rate'] == 1.0
    assert report['sweeps']['2']['mrr'] == 0.5
    assert format_sweep_cli_summary(report) == (
        'top_k=1:retrieval=0/1,mrr=0.0,matched=0/2,skipped=1 '
        'top_k=2:retrieval=1/1,mrr=0.5,matched=1/2,skipped=1'
    )


def test_private_judged_bundle_contains_raw_benchmark_inputs_only_when_requested():
    bundle = build_private_judged_input_bundle(
        'unit_dataset',
        'judged-slice',
        [1, 2],
        [
            {
                'question_id': 'q1',
                'category': 'fact',
                'question': 'Where is the private benchmark answer?',
                'answer': 'inside the private benchmark memory',
                'question_date': '2026-05-21',
                'search_results': [
                    {
                        'id': 'benchmark:one',
                        'memory': 'raw benchmark memory text',
                        'metadata': {
                            'source': 'benchmark',
                            'is_live_memory': False,
                            'memory_type': 'benchmark_observation',
                            'source_ids': ['turn-1'],
                            'session_id': 'session_1',
                            'conversation_id': 'conv-1',
                            'timestamp': '2024-05-07',
                        },
                    },
                    {
                        'id': 'benchmark:two',
                        'memory': 'second raw benchmark memory text',
                        'metadata': {
                            'source': 'benchmark',
                            'is_live_memory': False,
                            'memory_type': 'benchmark_observation',
                            'source_ids': ['turn-2'],
                        },
                    },
                ],
            }
        ],
    )

    assert bundle['mode'] == 'private-judged-input-bundle'
    assert bundle['runs_model_calls'] is False
    assert bundle['contains_raw_benchmark_text'] is True
    assert bundle['contains_live_user_memory'] is False
    assert bundle['top_k_values'] == [1, 2]
    question = bundle['questions'][0]
    assert question['question'] == 'Where is the private benchmark answer?'
    assert question['ground_truth_answer'] == 'inside the private benchmark memory'
    assert [row['memory'] for row in question['retrieved_memories_by_top_k']['1']] == ['raw benchmark memory text']
    assert [row['memory'] for row in question['retrieved_memories_by_top_k']['2']] == [
        'raw benchmark memory text',
        'second raw benchmark memory text',
    ]
    first = question['retrieved_memories_by_top_k']['1'][0]
    assert first['metadata']['timestamp'] == '2024-05-07'
    assert first['metadata']['session_id'] == 'session_1'
    assert first['metadata']['conversation_id'] == 'conv-1'


def test_private_judged_bundle_accepts_adapter_rows_with_benchmark_ids():
    bundle = build_private_judged_input_bundle(
        'unit_dataset',
        'adapter-slice',
        [1],
        [
            {
                'question_id': 'q1',
                'question': 'What did the adapter return?',
                'answer': 'benchmark row',
                'search_results': [
                    {
                        'id': 'benchmark:unit:adapter:one',
                        'memory': 'adapter benchmark memory text',
                        'metadata': {'source_ids': ['turn-1']},
                    }
                ],
            }
        ],
    )

    assert bundle['questions'][0]['retrieved_memories_by_top_k']['1'][0]['memory'] == 'adapter benchmark memory text'


def test_private_judged_bundle_records_retrieval_backend_when_supplied():
    bundle = build_private_judged_input_bundle(
        'unit_dataset',
        'adapter-slice',
        [1],
        [
            {
                'question_id': 'q1',
                'question': 'What did the adapter return?',
                'answer': 'benchmark row',
                'search_results': [
                    {
                        'id': 'benchmark:unit:adapter:one',
                        'memory': 'adapter benchmark memory text',
                        'metadata': {'source_ids': ['turn-1']},
                    }
                ],
            }
        ],
        retrieval_backend='legacy-mem0-offline',
    )

    assert bundle['retrieval_backend'] == 'legacy-mem0-offline'


def test_private_judged_bundle_refuses_live_memory_rows():
    try:
        build_private_judged_input_bundle(
            'unit_dataset',
            'bad-slice',
            [1],
            [
                {
                    'question_id': 'q1',
                    'question': 'Should not matter',
                    'answer': 'Should not matter',
                    'search_results': [
                        {
                            'id': 'live:one',
                            'memory': 'live user memory text',
                            'metadata': {
                                'source': 'mem0',
                                'is_live_memory': True,
                                'memory_type': 'project_state',
                            },
                        }
                    ],
                }
            ],
        )
    except ValueError as exc:
        assert 'live/user memory' in str(exc)
    else:
        raise AssertionError('expected live memory rows to be rejected')
