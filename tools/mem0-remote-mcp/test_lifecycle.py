import json

import pytest

from core import Mem0Client, Mem0Config
from lifecycle import (
    LifecyclePolicy,
    build_access_index,
    build_decay_plan,
    execute_lifecycle_plan,
)


def memory(memory_id, text='Memory text.', **metadata):
    return {
        'id': memory_id,
        'title': text[:90],
        'text': text,
        'metadata': metadata,
    }


def telemetry_row(*ids, ts='2026-05-10T00:00:00Z'):
    return {
        'ts': ts,
        'event': 'search',
        'origin': 'codex',
        'query_hash': 'hash',
        'result_count': len(ids),
        'results': [{'id': memory_id, 'title': f'Title {memory_id}'} for memory_id in ids],
        'latency_ms': 10,
    }


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')


def test_access_index_uses_retrieval_event_count_as_epoch(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'
    write_jsonl(
        telemetry_path,
        [
            telemetry_row('mem-a'),
            telemetry_row('mem-b'),
            telemetry_row('mem-a'),
            telemetry_row(),
        ],
    )

    index = build_access_index(telemetry_path)

    assert index.current_epoch == 4
    assert index.access_count('mem-a') == 2
    assert index.last_seen_epoch('mem-a') == 3
    assert index.epochs_since_access('mem-a') == 1
    assert index.epochs_since_access('never-seen') == 4


def test_decay_waits_for_enough_retrieval_epochs_not_wall_clock_time(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'
    write_jsonl(
        telemetry_path,
        [telemetry_row('other', ts='2020-01-01T00:00:00Z')],
    )
    policy = LifecyclePolicy(active_to_historical_epochs=3)

    plan = build_decay_plan(
        [memory('low-use', domains=['workflow'], signal_strength=2, current_status='active', memory_tier='active')],
        build_access_index(telemetry_path),
        policy,
    )

    assert plan.actions[0].action == 'keep'
    assert 'below epoch threshold' in plan.actions[0].reason


def test_decay_downgrades_low_signal_active_memory_after_enough_unseen_epochs(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'
    write_jsonl(telemetry_path, [telemetry_row('other') for _ in range(5)])
    policy = LifecyclePolicy(active_to_historical_epochs=3)

    plan = build_decay_plan(
        [memory('low-use', domains=['workflow'], signal_strength=2, current_status='active', memory_tier='active')],
        build_access_index(telemetry_path),
        policy,
    )

    action = plan.actions[0]
    assert action.action == 'downgrade_tier'
    assert action.memory_ids == ['low-use']
    assert action.memory_tier == 'historical'
    assert action.epochs_since_access == 5


def test_decay_protects_identity_relationship_and_family_memories(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'
    write_jsonl(telemetry_path, [telemetry_row('other') for _ in range(100)])

    plan = build_decay_plan(
        [
            memory(
                'formative',
                domains=['relationships', 'psychology'],
                memory_type='formative_event',
                signal_strength=2,
                current_status='active',
                memory_tier='active',
            )
        ],
        build_access_index(telemetry_path),
        LifecyclePolicy(active_to_historical_epochs=3),
    )

    assert plan.actions[0].action == 'keep'
    assert 'protected' in plan.actions[0].reason


def test_lifecycle_promotes_frequently_retrieved_historical_memory(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'
    write_jsonl(telemetry_path, [telemetry_row('mem-h'), telemetry_row('other'), telemetry_row('mem-h'), telemetry_row('mem-h')])
    policy = LifecyclePolicy(promote_after_access_count=3)

    plan = build_decay_plan(
        [memory('mem-h', domains=['systems'], signal_strength=5, current_status='unknown', memory_tier='historical')],
        build_access_index(telemetry_path),
        policy,
    )

    action = plan.actions[0]
    assert action.action == 'promote_tier'
    assert action.memory_tier == 'active'
    assert action.current_status == 'active'
    assert action.access_count == 3


def test_execute_lifecycle_plan_requires_update_cap_and_writes_rollback(tmp_path):
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if method == 'GET':
                return {'id': path.rsplit('/', 1)[-1], 'memory': 'Before memory.', 'metadata': {'memory_tier': 'active'}}
            if method == 'PUT':
                return {'updated': True}
            raise AssertionError(f'unexpected request: {method} {path}')

    telemetry_path = tmp_path / 'retrieval.jsonl'
    write_jsonl(telemetry_path, [telemetry_row('other') for _ in range(5)])
    plan = build_decay_plan(
        [memory('low-use', domains=['workflow'], signal_strength=2, current_status='active', memory_tier='active')],
        build_access_index(telemetry_path),
        LifecyclePolicy(active_to_historical_epochs=3),
    )

    with pytest.raises(ValueError, match='max_updates'):
        execute_lifecycle_plan(
            plan,
            FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret')),
            rollback_log=tmp_path / 'rollback.jsonl',
            maintenance_log=tmp_path / 'maintenance.jsonl',
        )

    result = execute_lifecycle_plan(
        plan,
        FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret')),
        rollback_log=tmp_path / 'rollback.jsonl',
        maintenance_log=tmp_path / 'maintenance.jsonl',
        max_updates=1,
    )

    assert result['counts']['downgraded'] == 1
    assert (tmp_path / 'rollback.jsonl').read_text(encoding='utf-8')
    assert (tmp_path / 'maintenance.jsonl').read_text(encoding='utf-8')
    assert any(call[0:2] == ('PUT', '/memories/low-use') for call in calls)
    put_body = next(call[2] for call in calls if call[0:2] == ('PUT', '/memories/low-use'))
    metadata = put_body['metadata']
    assert metadata['memory_tier'] == 'historical'
    assert metadata['lifecycle_action'] == 'downgrade_tier'
    assert metadata['previous_memory_tier'] == 'active'
    assert metadata['previous_current_status'] == 'unknown'
    assert metadata['retrieval_access_count'] == 0
    assert metadata['retrieval_epochs_since_access'] == 5
    assert metadata['lifecycle_updated_at']
