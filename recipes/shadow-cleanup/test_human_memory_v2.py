import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def load_human_memory_v2():
    module_path = Path(__file__).with_name('human_memory_v2.py')
    spec = importlib.util.spec_from_file_location('human_memory_v2', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_policy_version_names_formative_dossier_policy():
    human_memory_v2 = load_human_memory_v2()

    assert human_memory_v2.POLICY_VERSION == 'human-memory-v2-formative-dossiers'


def test_psychology_card_schema_requires_core_fields():
    human_memory_v2 = load_human_memory_v2()
    schema = human_memory_v2.psychology_card_schema()

    required_fields = {
        'content',
        'memory_type',
        'signal_strength',
        'emotional_intensity',
        'current_status',
        'evidence_count',
        'domains',
        'people',
        'source_ids',
        'provenance',
    }
    assert required_fields <= set(schema['required'])
    assert required_fields <= set(schema['properties'])


def test_psychology_card_schema_allows_required_memory_types():
    human_memory_v2 = load_human_memory_v2()
    schema = human_memory_v2.psychology_card_schema()
    memory_types = set(schema['properties']['memory_type']['enum'])

    assert {
        'pattern',
        'person',
        'event',
        'lesson',
        'trigger',
        'shadow_motive',
        'identity_shaping',
        'ai_breakthrough',
    } <= memory_types


def test_psychology_card_schema_allows_required_current_statuses():
    human_memory_v2 = load_human_memory_v2()
    schema = human_memory_v2.psychology_card_schema()
    current_statuses = set(schema['properties']['current_status']['enum'])

    assert {'active', 'dormant', 'resolved', 'superseded', 'unknown'} <= current_statuses


def test_psychology_card_schema_allows_required_emotional_intensities():
    human_memory_v2 = load_human_memory_v2()
    schema = human_memory_v2.psychology_card_schema()
    emotional_intensities = set(schema['properties']['emotional_intensity']['enum'])

    assert {'mild', 'strong', 'formative', 'identity_shaping'} <= emotional_intensities


def test_psychology_card_schema_bounds_signal_strength_from_one_to_ten():
    human_memory_v2 = load_human_memory_v2()
    schema = human_memory_v2.psychology_card_schema()
    signal_strength = schema['properties']['signal_strength']

    assert signal_strength['minimum'] == 1
    assert signal_strength['maximum'] == 10


def test_signal_strength_schema_minimum_matches_score_lower_bound():
    human_memory_v2 = load_human_memory_v2()
    schema = human_memory_v2.psychology_card_schema()

    assert schema['properties']['signal_strength']['minimum'] == human_memory_v2.clamp_score(0)


def test_psychology_signal_score_overweights_identity_emotion_and_future_advice():
    human_memory_v2 = load_human_memory_v2()

    score = human_memory_v2.psychology_signal_score(
        identity_impact=10,
        emotional_intensity=10,
        future_advice_value=10,
        recurrence=0,
        source_clarity=0,
    )

    assert score >= 8


def test_psychology_signal_score_returns_rounded_integer_formula_score():
    human_memory_v2 = load_human_memory_v2()

    score = human_memory_v2.psychology_signal_score(
        identity_impact=10,
        emotional_intensity=8,
        future_advice_value=6,
        recurrence=4,
        source_clarity=2,
    )

    assert score == 7
    assert isinstance(score, int)


def test_psychology_signal_score_stays_within_signal_strength_bounds():
    human_memory_v2 = load_human_memory_v2()

    low_score = human_memory_v2.psychology_signal_score(
        identity_impact=-10,
        emotional_intensity=-10,
        future_advice_value=-10,
        recurrence=-10,
        source_clarity=-10,
    )
    high_score = human_memory_v2.psychology_signal_score(
        identity_impact=99,
        emotional_intensity=99,
        future_advice_value=99,
        recurrence=99,
        source_clarity=99,
    )

    assert low_score == 1
    assert high_score == 10


def test_business_signal_score_prioritizes_usefulness_project_and_actionability():
    human_memory_v2 = load_human_memory_v2()

    work_relevant_score = human_memory_v2.business_signal_score(
        usefulness_currentness=8,
        project_relevance=8,
        actionability=8,
        recurrence=0,
        emotional_intensity=0,
    )
    emotionally_intense_score = human_memory_v2.business_signal_score(
        usefulness_currentness=0,
        project_relevance=0,
        actionability=0,
        recurrence=0,
        emotional_intensity=10,
    )

    assert work_relevant_score > emotionally_intense_score


def test_business_signal_score_returns_rounded_integer_formula_score():
    human_memory_v2 = load_human_memory_v2()

    score = human_memory_v2.business_signal_score(
        usefulness_currentness=10,
        project_relevance=8,
        actionability=6,
        recurrence=4,
        emotional_intensity=2,
    )

    assert score == 8
    assert isinstance(score, int)


def test_business_signal_score_stays_within_signal_strength_bounds():
    human_memory_v2 = load_human_memory_v2()

    low_score = human_memory_v2.business_signal_score(
        usefulness_currentness=-10,
        project_relevance=-10,
        actionability=-10,
        recurrence=-10,
        emotional_intensity=-10,
    )
    high_score = human_memory_v2.business_signal_score(
        usefulness_currentness=99,
        project_relevance=99,
        actionability=99,
        recurrence=99,
        emotional_intensity=99,
    )

    assert low_score == 1
    assert high_score == 10


def test_family_origin_relationship_content_routes_to_maximum_rescue_candidate():
    human_memory_v2 = load_human_memory_v2()

    classification = human_memory_v2.classify_candidate(
        {
            'id': 'row-family-1',
            'content': 'A formative childhood family pattern with my mother shaped how I handle partners.',
        },
        first_layer_decision='drop',
    )

    assert classification == {
        'id': 'row-family-1',
        'route': 'rescue_candidate',
        'domains': ['family_origin', 'relationships'],
        'protection_level': 'maximum',
        'first_layer_decision': 'drop',
    }


def test_ai_second_brain_philosophy_content_routes_to_ai_rescue_candidate():
    human_memory_v2 = load_human_memory_v2()

    classification = human_memory_v2.classify_candidate(
        {
            'id': 'row-ai-1',
            'content': 'AI second brain philosophy for memory agents and future decision support.',
        },
        first_layer_decision='drop',
    )

    assert classification['route'] == 'rescue_candidate'
    assert 'ai' in classification['domains']
    assert classification['protection_level'] == 'high'


def test_detect_domains_does_not_match_ai_inside_other_words():
    human_memory_v2 = load_human_memory_v2()

    assert 'ai' not in human_memory_v2.detect_domains('He said the invoice was paid.')


def test_metadata_topics_can_route_generic_content_to_rescue_candidate():
    human_memory_v2 = load_human_memory_v2()

    classification = human_memory_v2.classify_candidate(
        {
            'id': 'row-metadata-ai-1',
            'content': 'Generic note with no domain words.',
            'metadata': {'topics': ['ai', 'relationships']},
        },
        first_layer_decision='drop',
    )

    assert classification['route'] == 'rescue_candidate'
    assert 'ai' in classification['domains']
    assert 'relationships' in classification['domains']
    assert classification['protection_level'] == 'high'


def test_metadata_type_ai_breakthrough_routes_generic_content_to_rescue_candidate():
    human_memory_v2 = load_human_memory_v2()

    classification = human_memory_v2.classify_candidate(
        {
            'id': 'row-metadata-type-ai-1',
            'content': 'Generic note with no domain words.',
            'metadata': {'type': 'ai_breakthrough'},
        },
        first_layer_decision='drop',
    )

    assert classification['route'] == 'rescue_candidate'
    assert 'ai' in classification['domains']
    assert classification['protection_level'] == 'high'


def test_metadata_type_protected_domain_blocks_transcript_process_drop():
    human_memory_v2 = load_human_memory_v2()

    for memory_type, expected_domain in [
        ('identity_shaping', 'psychology'),
        ('shadow_motive', 'shadow_motives'),
    ]:
        classification = human_memory_v2.classify_candidate(
            {
                'id': f'row-metadata-type-{memory_type}',
                'content': 'Speaker diarization transcript process cleanup note.',
                'metadata': {'type': memory_type},
            },
            first_layer_decision='drop',
        )

        assert classification['route'] == 'rescue_candidate'
        assert classification['protection_level'] == 'high'
        assert expected_domain in classification['domains']
        assert 'drop_reason' not in classification


def test_detect_domains_treats_underscores_and_hyphens_as_token_separators():
    human_memory_v2 = load_human_memory_v2()

    domains = human_memory_v2.detect_domains('ai_breakthrough shadow-motive identity_shaping')

    assert 'ai' in domains
    assert 'shadow_motives' in domains
    assert 'psychology' in domains


def test_speaker_diarization_transcript_process_clutter_is_deterministic_drop():
    human_memory_v2 = load_human_memory_v2()

    classification = human_memory_v2.classify_candidate(
        {
            'id': 'row-transcript-1',
            'content': 'Speaker diarization transcript segment labels need cleanup before import.',
        },
        first_layer_decision='keep',
    )

    assert classification == {
        'id': 'row-transcript-1',
        'route': 'deterministic_drop',
        'domains': ['workflow'],
        'protection_level': 'none',
        'first_layer_decision': 'keep',
        'drop_reason': 'transcript_process_clutter',
    }


def test_transcript_process_metadata_type_is_deterministic_drop():
    human_memory_v2 = load_human_memory_v2()

    classification = human_memory_v2.classify_candidate(
        {
            'id': 'row-transcript-metadata-underscore-1',
            'content': 'Generic note with no protected domain words.',
            'metadata': {'type': 'transcript_process'},
        },
        first_layer_decision='drop',
    )

    assert classification['route'] == 'deterministic_drop'
    assert classification['drop_reason'] == 'transcript_process_clutter'


def test_transcript_process_hyphen_metadata_type_is_deterministic_drop():
    human_memory_v2 = load_human_memory_v2()

    classification = human_memory_v2.classify_candidate(
        {
            'id': 'row-transcript-metadata-hyphen-1',
            'content': 'Generic note with no protected domain words.',
            'metadata': {'type': 'transcript-process'},
        },
        first_layer_decision='drop',
    )

    assert classification['route'] == 'deterministic_drop'
    assert classification['drop_reason'] == 'transcript_process_clutter'


def test_protected_content_with_transcript_process_terms_routes_to_rescue_candidate():
    human_memory_v2 = load_human_memory_v2()

    classification = human_memory_v2.classify_candidate(
        {
            'id': 'row-protected-transcript-1',
            'content': 'Family origin relationship pattern mentioned during transcript process diarization notes.',
        },
        first_layer_decision='drop',
    )

    assert classification['route'] == 'rescue_candidate'
    assert classification['protection_level'] == 'maximum'
    assert 'drop_reason' not in classification


def test_detect_domains_covers_task_two_domain_set():
    human_memory_v2 = load_human_memory_v2()

    domains = human_memory_v2.detect_domains(
        'Family origin psychology, relationship shadow motives, AI opera vocality, '
        'money execution, business workflow.'
    )

    assert set(domains) == {
        'family_origin',
        'relationships',
        'psychology',
        'shadow_motives',
        'ai',
        'opera',
        'vocality',
        'money_execution',
        'business',
        'workflow',
    }


def test_keep_escalate_and_canonical_first_layer_decisions_route_to_rescue_candidate():
    human_memory_v2 = load_human_memory_v2()

    for decision in ['keep', 'escalate', 'canonical']:
        classification = human_memory_v2.classify_candidate(
            {'id': f'row-{decision}', 'content': 'Generic status note.'},
            first_layer_decision=decision,
        )

        assert classification['route'] == 'rescue_candidate'
        assert classification['first_layer_decision'] == decision


def test_jsonl_helpers_write_and_read_records(tmp_path):
    human_memory_v2 = load_human_memory_v2()
    path = tmp_path / 'records.jsonl'
    records = [{'id': 'row-1', 'value': 1}, {'id': 'row-2', 'value': 2}]

    count = human_memory_v2.write_jsonl(path, records)

    assert count == 2
    assert human_memory_v2.read_jsonl(path) == records


def test_load_cache_decisions_stores_only_row_id_to_decision(tmp_path):
    human_memory_v2 = load_human_memory_v2()
    cache_path = tmp_path / 'cache.jsonl'
    human_memory_v2.write_jsonl(
        cache_path,
        [
            {
                'row_id': 'row-1',
                'decision': 'keep',
                'content': 'raw text must not be retained',
                'raw_text': 'another raw field must not be retained',
            },
            {'row_id': 'row-2', 'decision': 'drop', 'text': 'also raw'},
        ],
    )

    decisions = human_memory_v2.load_cache_decisions([cache_path])

    assert decisions == {'row-1': 'keep', 'row-2': 'drop'}
    assert all(isinstance(value, str) for value in decisions.values())


def test_build_candidate_records_preserves_routed_candidate_fields():
    human_memory_v2 = load_human_memory_v2()
    rows = [
        {
            'id': 'row-ai-1',
            'content': 'AI second brain pattern worth rescue.',
            'metadata': {'topics': ['ai']},
        }
    ]

    records = human_memory_v2.build_candidate_records(rows, {'row-ai-1': 'drop'})

    assert records == [
        {
            'id': 'row-ai-1',
            'content': 'AI second brain pattern worth rescue.',
            'metadata': {'topics': ['ai']},
            'classification': {
                'id': 'row-ai-1',
                'route': 'rescue_candidate',
                'domains': ['ai'],
                'protection_level': 'high',
                'first_layer_decision': 'drop',
            },
        }
    ]


def test_build_candidate_records_excludes_deterministic_drop_rows():
    human_memory_v2 = load_human_memory_v2()
    rows = [
        {'id': 'row-drop-1', 'content': 'Speaker diarization transcript segment labels cleanup.'},
        {'id': 'row-keep-1', 'content': 'Family relationship memory.'},
    ]

    records = human_memory_v2.build_candidate_records(
        rows,
        {'row-drop-1': 'keep', 'row-keep-1': 'drop'},
    )

    assert [record['id'] for record in records] == ['row-keep-1']


def test_build_candidates_cli_writes_candidates_report_and_counts_only(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    snapshot_path = tmp_path / 'snapshot.jsonl'
    cache_path = tmp_path / 'cache.jsonl'
    output_path = tmp_path / 'candidates.jsonl'
    report_path = tmp_path / 'report.json'
    snapshot_rows = [
        {'id': 'row-ai-1', 'content': 'AI second brain pattern.', 'metadata': {'topics': ['ai']}},
        {'id': 'row-drop-1', 'content': 'Speaker diarization transcript segment labels cleanup.'},
    ]
    cache_rows = [
        {'row_id': 'row-ai-1', 'decision': 'drop', 'content': 'must not be printed'},
        {'row_id': 'row-drop-1', 'decision': 'keep', 'content': 'must not be printed'},
    ]
    snapshot_path.write_text('\n'.join(json.dumps(row) for row in snapshot_rows) + '\n', encoding='utf-8')
    cache_path.write_text('\n'.join(json.dumps(row) for row in cache_rows) + '\n', encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'build-candidates',
            '--snapshot',
            str(snapshot_path),
            '--cache-files',
            str(cache_path),
            '--output',
            str(output_path),
            '--report',
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))

    assert terminal_counts == {
        'snapshot_rows': 2,
        'candidate_rows': 1,
        'route_counts': {'deterministic_drop': 1, 'rescue_candidate': 1},
        'domain_counts': {'ai': 1, 'workflow': 1},
        'db_apply': False,
    }
    assert report == terminal_counts
    assert 'second brain' not in result.stdout
    assert 'diarization' not in result.stdout
    assert json.loads(output_path.read_text(encoding='utf-8').splitlines()[0])['id'] == 'row-ai-1'


def test_node_key_groups_by_protected_domain_and_first_topic():
    human_memory_v2 = load_human_memory_v2()
    candidate = {
        'id': 'row-family-trust-1',
        'content': 'Synthetic candidate content.',
        'metadata': {'topics': ['Trust', 'Boundaries']},
        'classification': {
            'domains': ['business', 'family_origin', 'relationships'],
        },
    }

    assert human_memory_v2.node_key(candidate) == 'domain:family_origin|topic:trust'


def test_build_node_packs_honors_max_rows_per_pack_for_same_node():
    human_memory_v2 = load_human_memory_v2()
    candidates = [
        {
            'id': f'row-{index:02d}',
            'content': f'Synthetic candidate {index}',
            'metadata': {'topics': ['trust']},
            'classification': {'domains': ['family_origin']},
        }
        for index in range(45)
    ]

    packs = human_memory_v2.build_node_packs(candidates, max_rows_per_pack=20)

    assert [pack['row_count'] for pack in packs] == [20, 20, 5]


def test_build_node_packs_sorts_same_node_candidates_before_chunking():
    human_memory_v2 = load_human_memory_v2()
    candidates = [
        {
            'id': f'row-{index:02d}',
            'content': f'Synthetic candidate {index}',
            'metadata': {'topics': ['trust']},
            'classification': {'domains': ['family_origin']},
        }
        for index in range(5)
    ]

    original_packs = human_memory_v2.build_node_packs(candidates, max_rows_per_pack=2)
    reversed_packs = human_memory_v2.build_node_packs(list(reversed(candidates)), max_rows_per_pack=2)

    assert [pack['pack_id'] for pack in reversed_packs] == [pack['pack_id'] for pack in original_packs]
    assert [
        [item['id'] for item in pack['items']]
        for pack in reversed_packs
    ] == [
        [item['id'] for item in pack['items']]
        for pack in original_packs
    ]


def test_stable_hash_handles_dict_key_order_stably():
    human_memory_v2 = load_human_memory_v2()

    assert human_memory_v2.stable_hash({'a': 1, 'b': 2}) == human_memory_v2.stable_hash({'b': 2, 'a': 1})


def test_build_node_packs_rejects_zero_max_rows_per_pack():
    human_memory_v2 = load_human_memory_v2()

    try:
        human_memory_v2.build_node_packs([], max_rows_per_pack=0)
        raised = False
    except ValueError:
        raised = True

    assert raised


def test_build_node_packs_rejects_negative_max_rows_per_pack():
    human_memory_v2 = load_human_memory_v2()

    try:
        human_memory_v2.build_node_packs([], max_rows_per_pack=-1)
        raised = False
    except ValueError:
        raised = True

    assert raised


def test_build_node_packs_include_required_pack_fields():
    human_memory_v2 = load_human_memory_v2()
    candidates = [
        {
            'id': 'row-ai-1',
            'content': 'Synthetic AI candidate.',
            'metadata': {'topics': ['agents']},
            'classification': {'domains': ['ai']},
        }
    ]

    packs = human_memory_v2.build_node_packs(candidates, max_rows_per_pack=20)

    assert len(packs) == 1
    assert packs[0] == {
        'pack_id': packs[0]['pack_id'],
        'policy_version': human_memory_v2.POLICY_VERSION,
        'stage': 'node_classification',
        'node_key': 'domain:ai|topic:agents',
        'row_count': 1,
        'items': candidates,
    }
    assert packs[0]['pack_id'].startswith('node-pack-')


def test_build_node_packs_cli_writes_jsonl_report_and_counts_only(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    candidates_path = tmp_path / 'candidates.jsonl'
    output_path = tmp_path / 'node-packs.jsonl'
    report_path = tmp_path / 'node-pack-report.json'
    candidates = [
        {
            'id': f'row-{index:02d}',
            'content': f'Synthetic private candidate {index}',
            'metadata': {'topics': ['trust']},
            'classification': {'domains': ['family_origin']},
        }
        for index in range(3)
    ]
    candidates_path.write_text('\n'.join(json.dumps(row) for row in candidates) + '\n', encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'build-node-packs',
            '--candidates',
            str(candidates_path),
            '--max-rows-per-pack',
            '2',
            '--output',
            str(output_path),
            '--report',
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    output_packs = [json.loads(line) for line in output_path.read_text(encoding='utf-8').splitlines()]

    assert terminal_counts == {
        'candidate_rows': 3,
        'pack_count': 2,
        'db_apply': False,
    }
    assert report == terminal_counts
    assert [pack['row_count'] for pack in output_packs] == [2, 1]
    assert 'Synthetic private candidate' not in result.stdout
    assert 'row-00' not in result.stdout


def tiny_node_pack():
    return {
        'pack_id': 'node-pack-test',
        'policy_version': 'human-memory-v2-formative-dossiers',
        'stage': 'node_classification',
        'node_key': 'domain:ai|topic:agents',
        'row_count': 3,
        'items': [
            {'id': 'row-1', 'content': 'Synthetic private candidate 1.'},
            {'id': 'row-2', 'content': 'Synthetic private candidate 2.'},
            {'id': 'row-3', 'content': 'Synthetic private candidate 3.'},
        ],
    }


def test_node_policy_prompt_includes_v2_rules_and_strict_json_schema():
    human_memory_v2 = load_human_memory_v2()

    prompt = human_memory_v2.node_policy_prompt(tiny_node_pack())

    assert human_memory_v2.POLICY_VERSION in prompt
    assert 'high recall' in prompt.lower()
    assert 'psychology/relationships/family-origin/shadow motives/identity-shaping/AI philosophy' in prompt
    assert 'drop only clear transcript/process/technical clutter' in prompt.lower()
    assert 'strict JSON' in prompt
    for field in [
        'pack_id',
        'policy_version',
        'nodes',
        'drop_source_ids',
        'review_source_ids',
        'review_questions',
    ]:
        assert field in prompt
    assert 'exactly once' in prompt


def test_validate_node_output_passes_when_ids_assigned_once_across_node_drop_and_review():
    human_memory_v2 = load_human_memory_v2()
    output = {
        'pack_id': 'node-pack-test',
        'policy_version': human_memory_v2.POLICY_VERSION,
        'nodes': [{'title': 'Synthetic node', 'source_ids': ['row-1']}],
        'drop_source_ids': ['row-2'],
        'review_source_ids': ['row-3'],
        'review_questions': [],
    }

    result = human_memory_v2.validate_node_output(tiny_node_pack(), output)

    assert result['status'] == 'pass'
    assert result['missing_ids'] == []
    assert result['extra_ids'] == []
    assert result['duplicate_ids'] == []
    assert result['counts'] == {
        'input_ids': 3,
        'node_source_ids': 1,
        'drop_source_ids': 1,
        'review_source_ids': 1,
        'assigned_ids': 3,
        'unique_assigned_ids': 3,
    }


def test_validate_node_output_fails_when_input_row_is_not_assigned():
    human_memory_v2 = load_human_memory_v2()
    output = {
        'nodes': [{'source_ids': ['row-1']}],
        'drop_source_ids': ['row-2'],
        'review_source_ids': [],
    }

    result = human_memory_v2.validate_node_output(tiny_node_pack(), output)

    assert result['status'] == 'fail'
    assert result['missing_ids'] == ['row-3']
    assert result['extra_ids'] == []
    assert result['duplicate_ids'] == []


def test_validate_node_output_fails_on_extra_unknown_ids():
    human_memory_v2 = load_human_memory_v2()
    output = {
        'nodes': [{'source_ids': ['row-1', 'row-unknown']}],
        'drop_source_ids': ['row-2'],
        'review_source_ids': ['row-3'],
    }

    result = human_memory_v2.validate_node_output(tiny_node_pack(), output)

    assert result['status'] == 'fail'
    assert result['missing_ids'] == []
    assert result['extra_ids'] == ['row-unknown']
    assert result['duplicate_ids'] == []


def test_validate_node_output_fails_on_duplicate_ids_assigned_twice():
    human_memory_v2 = load_human_memory_v2()
    output = {
        'nodes': [{'source_ids': ['row-1', 'row-2']}],
        'drop_source_ids': ['row-2'],
        'review_source_ids': ['row-3'],
    }

    result = human_memory_v2.validate_node_output(tiny_node_pack(), output)

    assert result['status'] == 'fail'
    assert result['missing_ids'] == []
    assert result['extra_ids'] == []
    assert result['duplicate_ids'] == ['row-2']


def test_validate_node_output_fails_when_drop_source_ids_is_string_not_list():
    human_memory_v2 = load_human_memory_v2()
    output = {
        'nodes': [{'source_ids': ['row-1']}],
        'drop_source_ids': 'row-2',
        'review_source_ids': ['row-3'],
    }

    result = human_memory_v2.validate_node_output(tiny_node_pack(), output)

    assert result['status'] == 'fail'
    assert result['type_errors'] == [{'field': 'drop_source_ids', 'expected': 'list'}]
    assert result['extra_ids'] == []
    assert 'r' not in result['extra_ids']


def test_validate_node_output_fails_when_nodes_or_source_ids_have_wrong_type():
    human_memory_v2 = load_human_memory_v2()

    nodes_string_result = human_memory_v2.validate_node_output(
        tiny_node_pack(),
        {
            'nodes': 'not-a-list',
            'drop_source_ids': ['row-2'],
            'review_source_ids': ['row-3'],
        },
    )
    source_ids_string_result = human_memory_v2.validate_node_output(
        tiny_node_pack(),
        {
            'nodes': [{'source_ids': 'row-1'}],
            'drop_source_ids': ['row-2'],
            'review_source_ids': ['row-3'],
        },
    )

    assert nodes_string_result['status'] == 'fail'
    assert nodes_string_result['type_errors'] == [{'field': 'nodes', 'expected': 'list'}]
    assert source_ids_string_result['status'] == 'fail'
    assert source_ids_string_result['type_errors'] == [{'field': 'nodes[0].source_ids', 'expected': 'list'}]
    assert 'r' not in source_ids_string_result['extra_ids']


def test_validate_nodes_cli_writes_report_and_prints_counts_only(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    packs_path = tmp_path / 'node-packs.jsonl'
    outputs_dir = tmp_path / 'node-proposals'
    outputs_dir.mkdir()
    output_path = outputs_dir / 'node-pack-test.json'
    report_path = tmp_path / 'validate-nodes-report.json'
    pack = tiny_node_pack()
    output = {
        'pack_id': 'node-pack-test',
        'policy_version': 'human-memory-v2-formative-dossiers',
        'nodes': [{'title': 'Synthetic node', 'source_ids': ['row-1']}],
        'drop_source_ids': ['row-2'],
        'review_source_ids': ['row-3'],
        'review_questions': [],
    }
    packs_path.write_text(json.dumps(pack) + '\n', encoding='utf-8')
    output_path.write_text(json.dumps(output), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'validate-nodes',
            '--packs',
            str(packs_path),
            '--outputs',
            str(outputs_dir / '*.json'),
            '--report',
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))

    assert terminal_counts == {
        'pack_count': 1,
        'output_count': 1,
        'pass_count': 1,
        'fail_count': 0,
        'missing_output_count': 0,
        'extra_output_count': 0,
        'duplicate_output_count': 0,
        'unknown_output_count': 0,
        'malformed_output_count': 0,
        'status': 'pass',
        'db_apply': False,
    }
    assert report['summary'] == terminal_counts
    assert report['results'][0]['status'] == 'pass'
    assert 'Synthetic private candidate' not in result.stdout
    assert 'row-1' not in result.stdout


def test_validate_nodes_cli_reports_malformed_json_without_raw_content(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    packs_path = tmp_path / 'node-packs.jsonl'
    output_path = tmp_path / 'malformed.json'
    report_path = tmp_path / 'validate-nodes-report.json'
    packs_path.write_text(json.dumps(tiny_node_pack()) + '\n', encoding='utf-8')
    output_path.write_text('{"pack_id":"node-pack-test","secret_raw_content":', encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'validate-nodes',
            '--packs',
            str(packs_path),
            '--outputs',
            str(output_path),
            '--report',
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))

    assert result.returncode == 1
    assert terminal_counts['status'] == 'fail'
    assert terminal_counts['malformed_output_count'] == 1
    assert report['issues'] == [
        {
            'path': str(output_path),
            'pack_id': None,
            'error': 'malformed_json',
        }
    ]
    assert 'secret_raw_content' not in result.stdout
    assert 'secret_raw_content' not in json.dumps(report)


def test_validate_nodes_cli_reports_json_list_output_as_non_object(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    packs_path = tmp_path / 'node-packs.jsonl'
    output_path = tmp_path / 'list-output.json'
    report_path = tmp_path / 'validate-nodes-report.json'
    packs_path.write_text(json.dumps(tiny_node_pack()) + '\n', encoding='utf-8')
    output_path.write_text(json.dumps([{'pack_id': 'node-pack-test', 'content': 'must not leak'}]), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'validate-nodes',
            '--packs',
            str(packs_path),
            '--outputs',
            str(output_path),
            '--report',
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))

    assert result.returncode == 1
    assert terminal_counts['malformed_output_count'] == 1
    assert report['issues'] == [
        {
            'path': str(output_path),
            'pack_id': None,
            'error': 'json_not_object',
        }
    ]
    assert 'must not leak' not in result.stdout
    assert 'must not leak' not in json.dumps(report)


def test_validate_nodes_cli_reports_duplicate_and_unknown_output_pack_ids(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    packs_path = tmp_path / 'node-packs.jsonl'
    outputs_dir = tmp_path / 'node-proposals'
    outputs_dir.mkdir()
    first_output_path = outputs_dir / 'a-first.json'
    duplicate_output_path = outputs_dir / 'b-duplicate.json'
    unknown_output_path = outputs_dir / 'c-unknown.json'
    report_path = tmp_path / 'validate-nodes-report.json'
    valid_output = {
        'pack_id': 'node-pack-test',
        'policy_version': 'human-memory-v2-formative-dossiers',
        'nodes': [{'source_ids': ['row-1']}],
        'drop_source_ids': ['row-2'],
        'review_source_ids': ['row-3'],
        'review_questions': [],
    }
    packs_path.write_text(json.dumps(tiny_node_pack()) + '\n', encoding='utf-8')
    first_output_path.write_text(json.dumps(valid_output), encoding='utf-8')
    duplicate_output_path.write_text(json.dumps(valid_output), encoding='utf-8')
    unknown_output_path.write_text(json.dumps({**valid_output, 'pack_id': 'node-pack-unknown'}), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'validate-nodes',
            '--packs',
            str(packs_path),
            '--outputs',
            str(outputs_dir / '*.json'),
            '--report',
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))

    assert result.returncode == 1
    assert terminal_counts['status'] == 'fail'
    assert terminal_counts['extra_output_count'] == 2
    assert terminal_counts['duplicate_output_count'] == 1
    assert terminal_counts['unknown_output_count'] == 1
    assert {
        'path': str(duplicate_output_path),
        'pack_id': 'node-pack-test',
        'error': 'duplicate_pack_id',
    } in report['issues']
    assert {
        'path': str(unknown_output_path),
        'pack_id': 'node-pack-unknown',
        'error': 'unknown_pack_id',
    } in report['issues']


def tiny_validated_node_output():
    return {
        'pack_id': 'node-pack-test',
        'policy_version': 'human-memory-v2-formative-dossiers',
        'nodes': [
            {
                'node_title': 'AI workstyle pattern',
                'node_type': 'ai_breakthrough',
                'domains': ['ai'],
                'source_ids': ['row-1', 'row-2'],
                'rationale': 'Synthetic grouping rationale.',
            }
        ],
        'drop_source_ids': ['row-3'],
        'review_source_ids': [],
        'review_questions': [],
    }


def tiny_card_pack():
    return {
        'pack_id': 'card-pack-test',
        'policy_version': 'human-memory-v2-formative-dossiers',
        'stage': 'card_synthesis',
        'node_count': 1,
        'nodes': [
            {
                'node_id': 'node-test',
                'node_title': 'AI workstyle pattern',
                'node_type': 'ai_breakthrough',
                'domains': ['ai'],
                'source_ids': ['row-1', 'row-2'],
                'rationale': 'Synthetic grouping rationale.',
                'source_items': [
                    {'id': 'row-1', 'content': 'Synthetic private candidate 1.'},
                    {'id': 'row-2', 'content': 'Synthetic private candidate 2.'},
                ],
            }
        ],
    }


def tiny_valid_card_output():
    return {
        'pack_id': 'card-pack-test',
        'policy_version': 'human-memory-v2-formative-dossiers',
        'cards': [
            {
                'content': 'Synthetic compact memory card.',
                'memory_type': 'ai_breakthrough',
                'signal_strength': 8,
                'emotional_intensity': 'strong',
                'current_status': 'active',
                'evidence_count': 2,
                'domains': ['ai'],
                'people': [],
                'source_ids': ['row-1', 'row-2'],
                'provenance': {'node_ids': ['node-test']},
                'timeline': [],
                'incidents': [],
                'competing_interpretations': [],
                'ai_guidance': ['Use this to adapt future AI work advice.'],
                'review_reason': '',
            }
        ],
        'held_for_review': [],
    }


def test_build_card_packs_expands_validated_nodes_with_source_items():
    human_memory_v2 = load_human_memory_v2()

    packs = human_memory_v2.build_card_packs([tiny_node_pack()], [tiny_validated_node_output()], max_nodes_per_pack=5)

    assert len(packs) == 1
    assert packs[0]['stage'] == 'card_synthesis'
    assert packs[0]['node_count'] == 1
    assert packs[0]['nodes'][0]['node_id'].startswith('node-')
    assert packs[0]['nodes'][0]['source_ids'] == ['row-1', 'row-2']
    assert [item['id'] for item in packs[0]['nodes'][0]['source_items']] == ['row-1', 'row-2']


def test_card_synthesis_prompt_includes_fixed_schema_and_human_memory_rules():
    human_memory_v2 = load_human_memory_v2()

    prompt = human_memory_v2.card_synthesis_prompt(tiny_card_pack())

    assert human_memory_v2.POLICY_VERSION in prompt
    assert 'strict JSON' in prompt
    assert 'human-memory card' in prompt.lower()
    for field in [
        'timeline',
        'incidents',
        'competing_interpretations',
        'ai_guidance',
        'domains',
        'people',
        'provenance',
        'review_reason',
    ]:
        assert field in prompt
    assert 'do not flatten psychology' in prompt.lower()


def test_validate_card_output_passes_when_cards_have_required_schema_fields():
    human_memory_v2 = load_human_memory_v2()

    result = human_memory_v2.validate_card_output(tiny_valid_card_output(), tiny_card_pack())

    assert result['status'] == 'pass'
    assert result['missing_required_fields'] == []
    assert result['type_errors'] == []
    assert result['missing_source_ids'] == []
    assert result['extra_source_ids'] == []


def test_validate_card_output_fails_when_card_missing_required_fields():
    human_memory_v2 = load_human_memory_v2()
    output = tiny_valid_card_output()
    del output['cards'][0]['content']
    del output['cards'][0]['source_ids']

    result = human_memory_v2.validate_card_output(output, tiny_card_pack())

    assert result['status'] == 'fail'
    assert {'card_index': 0, 'field': 'content'} in result['missing_required_fields']
    assert {'card_index': 0, 'field': 'source_ids'} in result['missing_required_fields']


def test_validate_card_output_fails_on_bad_signal_strength_and_empty_source_ids():
    human_memory_v2 = load_human_memory_v2()
    output = tiny_valid_card_output()
    output['cards'][0]['signal_strength'] = 12
    output['cards'][0]['source_ids'] = []

    result = human_memory_v2.validate_card_output(output, tiny_card_pack())

    assert result['status'] == 'fail'
    assert {'card_index': 0, 'field': 'signal_strength', 'expected': 'number 1..10'} in result['type_errors']
    assert {'card_index': 0, 'field': 'source_ids', 'expected': 'non-empty list'} in result['type_errors']
    assert result['missing_source_ids'] == ['row-1', 'row-2']


def test_build_card_packs_cli_writes_jsonl_report_and_counts_only(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    node_packs_path = tmp_path / 'node-packs.jsonl'
    node_outputs_dir = tmp_path / 'node-proposals'
    node_outputs_dir.mkdir()
    node_output_path = node_outputs_dir / 'node-pack-test.json'
    card_packs_path = tmp_path / 'card-packs.jsonl'
    report_path = tmp_path / 'card-pack-report.json'
    node_packs_path.write_text(json.dumps(tiny_node_pack()) + '\n', encoding='utf-8')
    node_output_path.write_text(json.dumps(tiny_validated_node_output()), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'build-card-packs',
            '--node-packs',
            str(node_packs_path),
            '--node-outputs',
            str(node_outputs_dir / '*.json'),
            '--output',
            str(card_packs_path),
            '--report',
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    output_packs = [json.loads(line) for line in card_packs_path.read_text(encoding='utf-8').splitlines()]

    assert terminal_counts == {
        'node_pack_count': 1,
        'node_output_count': 1,
        'card_pack_count': 1,
        'node_count': 1,
        'db_apply': False,
    }
    assert report == terminal_counts
    assert output_packs[0]['stage'] == 'card_synthesis'
    assert 'Synthetic private candidate' not in result.stdout
    assert 'row-1' not in result.stdout


def test_validate_cards_cli_writes_report_and_counts_only(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    card_packs_path = tmp_path / 'card-packs.jsonl'
    outputs_dir = tmp_path / 'card-proposals'
    outputs_dir.mkdir()
    output_path = outputs_dir / 'card-pack-test.json'
    report_path = tmp_path / 'validate-cards-report.json'
    card_packs_path.write_text(json.dumps(tiny_card_pack()) + '\n', encoding='utf-8')
    output_path.write_text(json.dumps(tiny_valid_card_output()), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'validate-cards',
            '--packs',
            str(card_packs_path),
            '--outputs',
            str(outputs_dir / '*.json'),
            '--report',
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))

    assert terminal_counts == {
        'pack_count': 1,
        'output_count': 1,
        'pass_count': 1,
        'fail_count': 0,
        'missing_output_count': 0,
        'extra_output_count': 0,
        'duplicate_output_count': 0,
        'unknown_output_count': 0,
        'malformed_output_count': 0,
        'card_count': 1,
        'status': 'pass',
        'db_apply': False,
    }
    assert report['summary'] == terminal_counts
    assert report['results'][0]['status'] == 'pass'
    assert 'Synthetic compact memory card' not in result.stdout
    assert 'row-1' not in result.stdout


def test_validate_cards_cli_reports_malformed_json_without_raw_content(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    card_packs_path = tmp_path / 'card-packs.jsonl'
    output_path = tmp_path / 'malformed-card.json'
    report_path = tmp_path / 'validate-cards-report.json'
    card_packs_path.write_text(json.dumps(tiny_card_pack()) + '\n', encoding='utf-8')
    output_path.write_text('{"pack_id":"card-pack-test","secret_raw_content":', encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'validate-cards',
            '--packs',
            str(card_packs_path),
            '--outputs',
            str(output_path),
            '--report',
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))

    assert result.returncode == 1
    assert terminal_counts['status'] == 'fail'
    assert terminal_counts['malformed_output_count'] == 1
    assert report['issues'] == [
        {
            'path': str(output_path),
            'pack_id': None,
            'error': 'malformed_json',
        }
    ]
    assert 'secret_raw_content' not in result.stdout
    assert 'secret_raw_content' not in json.dumps(report)

def test_build_v2_dry_run_plan_removes_v2_source_ids_from_v1_delete_set():
    human_memory_v2 = load_human_memory_v2()
    cards = [
        {
            'content': 'Synthetic compact rescued card.',
            'memory_type': 'pattern',
            'signal_strength': 8,
            'source_ids': ['row-1', 'row-3'],
        }
    ]

    plan = human_memory_v2.build_v2_dry_run_plan(
        v1_delete_ids=['row-1', 'row-2', 'row-3'],
        held_ids=['row-held'],
        v2_cards=cards,
    )

    assert plan['policy_version'] == human_memory_v2.POLICY_VERSION
    assert plan['db_apply'] is False
    assert plan['rescued_source_ids'] == ['row-1', 'row-3']
    assert plan['delete_candidate_ids'] == ['row-2']
    assert plan['held_review_ids'] == ['row-held']
    assert plan['insert_candidates'] == cards
    assert plan['counts'] == {
        'v1_delete_ids': 3,
        'v1_held_ids': 1,
        'v2_cards': 1,
        'rescued_source_ids': 2,
        'delete_candidate_ids': 1,
        'held_review_ids': 1,
    }


def test_dry_run_plan_cli_writes_artifacts_and_counts_only(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    delete_path = tmp_path / 'v1-delete.jsonl'
    held_path = tmp_path / 'v1-held.jsonl'
    proposal_dir = tmp_path / 'card-proposals'
    proposal_dir.mkdir()
    proposal_path = proposal_dir / 'card-pack-a.json'
    output_root = tmp_path / 'dry-run'
    delete_path.write_text(
        '\n'.join(json.dumps(row) for row in [
            {'id': 'row-1', 'category': 'delete'},
            {'id': 'row-3', 'category': 'delete'},
        ]) + '\n',
        encoding='utf-8',
    )
    held_path.write_text(json.dumps({'id': 'row-held', 'category': 'hold'}) + '\n', encoding='utf-8')
    proposal_path.write_text(json.dumps(tiny_valid_card_output()), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'dry-run-plan',
            '--v1-delete-candidates',
            str(delete_path),
            '--v1-held-ids',
            str(held_path),
            '--v2-card-proposals',
            str(proposal_dir / '*.json'),
            '--output-root',
            str(output_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    plan = json.loads((output_root / 'v2-dry-run-plan.json').read_text(encoding='utf-8'))
    inserts = [json.loads(line) for line in (output_root / 'v2-insert-candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    deletes = [json.loads(line) for line in (output_root / 'v2-delete-candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    holds = [json.loads(line) for line in (output_root / 'v2-held-review-ids.jsonl').read_text(encoding='utf-8').splitlines()]

    assert terminal_counts == {
        'v1_delete_ids': 2,
        'v1_held_ids': 1,
        'v2_cards': 1,
        'rescued_source_ids': 2,
        'delete_candidate_ids': 1,
        'held_review_ids': 1,
        'db_apply': False,
    }
    assert plan['counts']['delete_candidate_ids'] == 1
    assert inserts[0]['content'] == 'Synthetic compact memory card.'
    assert deletes == [{'id': 'row-3', 'category': 'v2_delete_candidate'}]
    assert holds == [{'id': 'row-held', 'category': 'v2_hold_review'}]
    assert 'Synthetic compact memory card' not in result.stdout
    assert 'row-1' not in result.stdout

def test_build_review_markdown_contains_card_headings_and_decision_checkboxes():
    human_memory_v2 = load_human_memory_v2()
    cards = [tiny_valid_card_output()['cards'][0]]

    markdown = human_memory_v2.build_review_markdown(cards, {'card_count': 1, 'delete_candidate_ids': 2})

    assert '# OB1 Claude Human Memory V2 Review' in markdown
    assert '## Card 1: ai_breakthrough' in markdown
    assert '- [ ] Keep' in markdown
    assert '- [ ] Edit' in markdown
    assert '- [ ] Drop' in markdown
    assert 'Signal strength: 8' in markdown


def test_export_review_cli_writes_review_package_and_counts_cards(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    dry_run_plan_path = tmp_path / 'v2-dry-run-plan.json'
    cards_path = tmp_path / 'v2-insert-candidates.jsonl'
    output_root = tmp_path / 'review-export'
    card = tiny_valid_card_output()['cards'][0]
    dry_run_plan_path.write_text(
        json.dumps(
            {
                'policy_version': 'human-memory-v2-formative-dossiers',
                'counts': {'v2_cards': 1, 'delete_candidate_ids': 3, 'held_review_ids': 1},
                'db_apply': False,
            }
        ),
        encoding='utf-8',
    )
    cards_path.write_text(json.dumps(card) + '\n', encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'export-review',
            '--dry-run-plan',
            str(dry_run_plan_path),
            '--cards',
            str(cards_path),
            '--output-root',
            str(output_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    markdown = (output_root / 'memory-cards-review.md').read_text(encoding='utf-8')
    exact_cards = (output_root / 'memory-cards.jsonl').read_text(encoding='utf-8').splitlines()

    assert terminal_counts == {
        'card_count': 1,
        'jsonl_card_count': 1,
        'db_apply': False,
        'export_path': str(output_root),
    }
    assert (output_root / 'README.md').exists()
    assert (output_root / 'memory-cards.csv').exists()
    assert (output_root / 'dry-run-plan.json').exists()
    assert len(exact_cards) == 1
    assert '## Card 1: ai_breakthrough' in markdown
    assert 'Synthetic compact memory card' not in result.stdout
    assert 'row-1' not in result.stdout

def test_build_candidates_cli_expands_cache_file_globs(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    snapshot_path = tmp_path / 'snapshot.jsonl'
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    cache_path = cache_dir / 'row-cache-one.jsonl'
    output_path = tmp_path / 'candidates.jsonl'
    report_path = tmp_path / 'report.json'
    snapshot_path.write_text(
        json.dumps({'id': 'row-ai-1', 'content': 'AI second brain pattern.', 'metadata': {'topics': ['ai']}}) + '\n',
        encoding='utf-8',
    )
    cache_path.write_text(json.dumps({'row_id': 'row-ai-1', 'decision': 'drop'}) + '\n', encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'build-candidates',
            '--snapshot',
            str(snapshot_path),
            '--cache-files',
            str(cache_dir / 'row-cache-*.jsonl'),
            '--output',
            str(output_path),
            '--report',
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)

    assert terminal_counts['candidate_rows'] == 1
    assert json.loads(output_path.read_text(encoding='utf-8').splitlines()[0])['classification']['first_layer_decision'] == 'drop'

def test_build_node_workloads_coalesces_many_tiny_packs_without_changing_pack_contract():
    human_memory_v2 = load_human_memory_v2()
    node_packs = [
        {
            'pack_id': f'node-pack-{index:02d}',
            'policy_version': human_memory_v2.POLICY_VERSION,
            'stage': 'node_classification',
            'node_key': 'domain:ai|topic:tiny',
            'row_count': 1,
            'items': [{'id': f'row-{index:02d}', 'content': 'Synthetic private candidate.'}],
        }
        for index in range(60)
    ]

    workloads = human_memory_v2.build_node_workloads(
        node_packs,
        max_packs_per_workload=25,
        max_rows_per_workload=100,
    )

    assert [workload['pack_count'] for workload in workloads] == [25, 25, 10]
    assert [workload['row_count'] for workload in workloads] == [25, 25, 10]
    assert workloads[0]['stage'] == 'node_classification_workload'
    assert workloads[0]['packs'][0]['pack_id'] == 'node-pack-00'
    assert workloads[0]['packs'][0]['stage'] == 'node_classification'


def test_build_node_workloads_honors_row_limit_for_large_packs():
    human_memory_v2 = load_human_memory_v2()
    node_packs = [
        {
            'pack_id': f'node-pack-{index}',
            'policy_version': human_memory_v2.POLICY_VERSION,
            'stage': 'node_classification',
            'node_key': 'domain:vocality|topic:large',
            'row_count': 30,
            'items': [{'id': f'row-{index}', 'content': 'Synthetic private candidate.'}],
        }
        for index in range(3)
    ]

    workloads = human_memory_v2.build_node_workloads(
        node_packs,
        max_packs_per_workload=10,
        max_rows_per_workload=60,
    )

    assert [workload['pack_count'] for workload in workloads] == [2, 1]
    assert [workload['row_count'] for workload in workloads] == [60, 30]


def test_build_node_workloads_cli_writes_jsonl_report_and_counts_only(tmp_path):
    module_path = Path(__file__).with_name('human_memory_v2.py')
    node_packs_path = tmp_path / 'node-packs.jsonl'
    output_path = tmp_path / 'node-workloads.jsonl'
    report_path = tmp_path / 'node-workloads-report.json'
    node_packs = [
        {
            'pack_id': f'node-pack-{index:02d}',
            'policy_version': 'human-memory-v2-formative-dossiers',
            'stage': 'node_classification',
            'node_key': 'domain:ai|topic:tiny',
            'row_count': 1,
            'items': [{'id': f'row-{index:02d}', 'content': 'Synthetic private candidate.'}],
        }
        for index in range(3)
    ]
    node_packs_path.write_text('\n'.join(json.dumps(row) for row in node_packs) + '\n', encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'build-node-workloads',
            '--node-packs',
            str(node_packs_path),
            '--max-packs-per-workload',
            '2',
            '--max-rows-per-workload',
            '100',
            '--output',
            str(output_path),
            '--report',
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    terminal_counts = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    workloads = [json.loads(line) for line in output_path.read_text(encoding='utf-8').splitlines()]

    assert terminal_counts == {
        'node_pack_count': 3,
        'node_row_count': 3,
        'workload_count': 2,
        'max_packs_per_workload': 2,
        'max_rows_per_workload': 100,
        'db_apply': False,
    }
    assert report == terminal_counts
    assert [workload['pack_count'] for workload in workloads] == [2, 1]
    assert 'Synthetic private candidate' not in result.stdout
    assert 'row-00' not in result.stdout

def test_read_json_accepts_utf8_bom_files(tmp_path):
    human_memory_v2 = load_human_memory_v2()
    path = tmp_path / 'bom.json'
    path.write_text('\ufeff{"pack_id":"node-pack-test"}', encoding='utf-8')

    assert human_memory_v2.read_json(path) == {'pack_id': 'node-pack-test'}


def test_read_jsonl_accepts_utf8_bom_first_line(tmp_path):
    human_memory_v2 = load_human_memory_v2()
    path = tmp_path / 'bom.jsonl'
    path.write_text('\ufeff{"id":"row-1"}\n{"id":"row-2"}\n', encoding='utf-8')

    assert human_memory_v2.read_jsonl(path) == [{'id': 'row-1'}, {'id': 'row-2'}]