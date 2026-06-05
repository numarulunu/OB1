import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def load_review_mem0_pipeline():
    module_path = Path(__file__).with_name('review_mem0_pipeline.py')
    spec = importlib.util.spec_from_file_location('review_mem0_pipeline', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_card(**overrides):
    card = {
        'content': 'Synthetic durable memory card.',
        'memory_type': 'pattern',
        'signal_strength': 8,
        'emotional_intensity': 'formative',
        'current_status': 'active',
        'evidence_count': 3,
        'domains': ['psychology'],
        'people': ['mentor'],
        'source_ids': ['row-1', 'row-2'],
        'provenance': {'node_ids': ['node-a'], 'source_summary': 'synthetic'},
        'timeline': [],
        'incidents': [],
        'competing_interpretations': [],
        'ai_guidance': ['adapt advice'],
        'review_reason': '',
    }
    card.update(overrides)
    return card


def test_card_to_mem0_request_preserves_metadata_and_avoids_reextraction():
    pipeline = load_review_mem0_pipeline()
    card = synthetic_card(memory_type='identity_shaping', domains=['psychology', 'relationships'])

    request = pipeline.card_to_mem0_request(card, index=1, user_id='ionut', agent_id='ob1-v2')

    assert request['method'] == 'POST'
    assert request['path'] == '/memories'
    body = request['body']
    assert body['user_id'] == 'ionut'
    assert body['agent_id'] == 'ob1-v2'
    assert body['infer'] is False
    assert body['version'] == 'v2'
    assert body['immutable'] is True
    assert body['messages'] == [{'role': 'user', 'content': 'Synthetic durable memory card.'}]
    metadata = body['metadata']
    assert metadata['policy_version'] == pipeline.POLICY_VERSION
    assert metadata['memory_type'] == 'identity_shaping'
    assert metadata['domains'] == ['psychology', 'relationships']
    assert metadata['source_ids'] == ['row-1', 'row-2']
    assert metadata['source_id_count'] == 2
    assert metadata['ob1_card_id'].startswith('ob1-v2-card-')


def test_select_pilot_cards_prioritizes_high_signal_human_memory_domains():
    pipeline = load_review_mem0_pipeline()
    cards = [
        synthetic_card(content='Workflow card', signal_strength=7, domains=['workflow'], memory_type='pattern'),
        synthetic_card(content='Low signal relationship card', signal_strength=3, domains=['relationships'], memory_type='person'),
        synthetic_card(content='Identity card', signal_strength=9, domains=['psychology', 'family_origin'], memory_type='identity_shaping'),
        synthetic_card(content='AI card', signal_strength=8, domains=['ai'], memory_type='ai_breakthrough'),
    ]

    pilot = pipeline.select_pilot_cards(cards, limit=2)

    assert [card['content'] for card in pilot] == ['Identity card', 'AI card']


def test_export_dashboard_writes_self_contained_files_and_prints_counts_only(tmp_path):
    module_path = Path(__file__).with_name('review_mem0_pipeline.py')
    cards_path = tmp_path / 'cards.jsonl'
    plan_path = tmp_path / 'plan.json'
    output_root = tmp_path / 'dashboard'
    private_phrase = 'Synthetic private relationship memory.'
    cards_path.write_text(json.dumps(synthetic_card(content=private_phrase)) + '\n', encoding='utf-8')
    plan_path.write_text(json.dumps({'counts': {'delete_candidate_ids': 2, 'held_review_ids': 1}, 'db_apply': False}), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'export-dashboard',
            '--cards',
            str(cards_path),
            '--dry-run-plan',
            str(plan_path),
            '--output-root',
            str(output_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary['card_count'] == 1
    assert summary['db_apply'] is False
    assert private_phrase not in result.stdout
    html = (output_root / 'review-dashboard.html').read_text(encoding='utf-8')
    assert private_phrase in html
    assert (output_root / 'review-decisions-template.jsonl').exists()
    assert (output_root / 'README.md').exists()


def test_export_mem0_writes_full_and_limited_jsonl_without_raw_stdout(tmp_path):
    module_path = Path(__file__).with_name('review_mem0_pipeline.py')
    cards_path = tmp_path / 'cards.jsonl'
    output_path = tmp_path / 'mem0.jsonl'
    cards = [
        synthetic_card(content='Synthetic private A', signal_strength=9, domains=['psychology']),
        synthetic_card(content='Synthetic private B', signal_strength=4, domains=['workflow']),
    ]
    cards_path.write_text(''.join(json.dumps(card) + '\n' for card in cards), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'export-mem0',
            '--cards',
            str(cards_path),
            '--output',
            str(output_path),
            '--user-id',
            'ionut',
            '--limit',
            '1',
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary['request_count'] == 1
    assert 'Synthetic private' not in result.stdout
    requests = [json.loads(line) for line in output_path.read_text(encoding='utf-8').splitlines()]
    assert len(requests) == 1
    assert requests[0]['body']['messages'][0]['content'] == 'Synthetic private A'


def test_import_mem0_defaults_to_dry_run_without_network(tmp_path):
    module_path = Path(__file__).with_name('review_mem0_pipeline.py')
    requests_path = tmp_path / 'requests.jsonl'
    requests_path.write_text(json.dumps({'method': 'POST', 'path': '/memories', 'body': {'messages': []}}) + '\n', encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'import-mem0',
            '--requests',
            str(requests_path),
            '--base-url',
            'https://mem0.example.test',
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary == {
        'base_url': 'https://mem0.example.test',
        'dry_run': True,
        'request_count': 1,
        'attempted': 0,
        'succeeded': 0,
        'failed': 0,
        'db_apply': False,
    }


def test_import_mem0_skip_and_limit_select_remaining_requests_without_network(tmp_path):
    module_path = Path(__file__).with_name('review_mem0_pipeline.py')
    requests_path = tmp_path / 'requests.jsonl'
    rows = [
        {'method': 'POST', 'path': '/memories', 'body': {'messages': [{'content': f'row-{index}'}]}}
        for index in range(1, 6)
    ]
    requests_path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            str(module_path),
            'import-mem0',
            '--requests',
            str(requests_path),
            '--base-url',
            'https://mem0.example.test',
            '--skip',
            '2',
            '--limit',
            '2',
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary['request_count'] == 2
    assert summary['skipped'] == 2
