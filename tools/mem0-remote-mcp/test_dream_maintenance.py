import json

import pytest

from core import Mem0Client, Mem0Config
from dream_maintenance import (
    MaintenanceAction,
    build_llm_pack,
    build_maintenance_plan,
    execute_maintenance_plan,
    load_maintained_group_keys,
    parse_audit_flags,
    parse_llm_actions,
)


def write_jsonl(path, rows):
    lines = []
    for row in rows:
        if row == 'malformed':
            lines.append('{bad json')
        else:
            lines.append(json.dumps(row, ensure_ascii=False))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def flag_row(**kwargs):
    row = {
        'ts': kwargs.pop('ts', '2026-05-09T00:00:00Z'),
        'origin': kwargs.pop('origin', 'codex'),
        'action': 'flag',
        'flag_type': kwargs.pop('flag_type', 'delete_candidate'),
        'confidence': kwargs.pop('confidence', 0.9),
        'reason': kwargs.pop('reason', 'duplicate junk'),
    }
    row.update(kwargs)
    return row


def test_maintenance_action_requires_exact_ids_for_mutating_actions():
    with pytest.raises(ValueError, match='exact memory id'):
        MaintenanceAction(action='delete', memory_ids=[], reason='remove junk')

    with pytest.raises(ValueError, match='at least two'):
        MaintenanceAction(action='merge', memory_ids=['mem-1'], content='merged', reason='merge duplicate')

    action = MaintenanceAction(action='keep', memory_ids=[], reason='weak signal')
    assert action.action == 'keep'


def test_parse_audit_flags_groups_by_memory_id_and_counts_skipped_rows(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            flag_row(memory_id='mem-1', origin='codex'),
            {'action': 'save', 'origin': 'codex'},
            'malformed',
            flag_row(memory_id='mem-1', origin='claude', confidence=0.8, reason='same duplicate'),
        ],
    )

    groups, skipped = parse_audit_flags(audit_path)

    assert skipped == 1
    assert len(groups) == 1
    assert groups[0].key == 'id:mem-1'
    assert groups[0].memory_ids == ['mem-1']
    assert [candidate.origin for candidate in groups[0].candidates] == ['codex', 'claude']


def test_parse_audit_flags_groups_idless_rows_by_proposal_content(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            flag_row(proposal={'content': 'Duplicate workflow memory.'}, origin='codex'),
            flag_row(proposal={'content': ' duplicate   workflow memory. '}, origin='claude'),
        ],
    )

    groups, skipped = parse_audit_flags(audit_path)

    assert skipped == 0
    assert len(groups) == 1
    assert groups[0].key == 'content:duplicate workflow memory.'
    assert groups[0].memory_ids == []


def test_dry_run_planner_deletes_only_high_confidence_exact_id_candidates(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            flag_row(memory_id='mem-delete', confidence=0.92, flag_type='delete_candidate'),
            flag_row(memory_id='mem-weak', confidence=0.4, flag_type='delete_candidate'),
        ],
    )

    groups, _ = parse_audit_flags(audit_path)
    plan = build_maintenance_plan(groups)

    by_id = {action.memory_ids[0]: action.action for action in plan.actions if action.memory_ids}
    assert by_id['mem-delete'] == 'delete'
    assert by_id['mem-weak'] == 'keep'


def test_dry_run_planner_handles_stale_merge_and_conflict_candidates(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            flag_row(memory_id='mem-stale', flag_type='stale_candidate', confidence=0.9),
            flag_row(memory_ids=['mem-a', 'mem-b'], flag_type='merge_candidate', confidence=0.9),
            flag_row(memory_id='mem-conflict', flag_type='conflict_candidate', confidence=0.9),
        ],
    )

    groups, _ = parse_audit_flags(audit_path)
    plan = build_maintenance_plan(groups)

    actions = {(tuple(action.memory_ids), action.action) for action in plan.actions}
    assert (('mem-stale',), 'downgrade_tier') in actions
    assert (('mem-a', 'mem-b'), 'merge') in actions
    assert (('mem-conflict',), 'ask_user') in actions


def test_dry_run_planner_treats_bare_stale_flags_as_stale_candidates(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(audit_path, [flag_row(memory_id='mem-stale', flag_type='stale', confidence=0.9)])

    groups, _ = parse_audit_flags(audit_path)
    plan = build_maintenance_plan(groups)

    action = plan.actions[0]
    assert action.action == 'downgrade_tier'
    assert action.memory_ids == ['mem-stale']
    assert action.memory_tier == 'historical'
    assert action.current_status == 'unknown'


def test_dry_run_planner_updates_exact_possible_correction_with_content(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    corrected_content = 'Patricia Neway is the correct name for this contact.'
    write_jsonl(
        audit_path,
        [
            flag_row(
                memory_id='mem-correction',
                flag_type='possible_correction',
                reason='name spelling correction',
                proposal={'content': corrected_content},
            )
        ],
    )

    groups, _ = parse_audit_flags(audit_path)
    plan = build_maintenance_plan(groups)

    action = plan.actions[0]
    assert action.action == 'update'
    assert action.memory_ids == ['mem-correction']
    assert action.content == corrected_content
    assert 'possible correction' in action.reason


def test_dry_run_planner_asks_for_possible_correction_without_exact_content(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(audit_path, [flag_row(memory_id='mem-correction', flag_type='possible_correction')])

    groups, _ = parse_audit_flags(audit_path)
    plan = build_maintenance_plan(groups)

    action = plan.actions[0]
    assert action.action == 'ask_user'
    assert action.memory_ids == ['mem-correction']


def test_build_llm_pack_contains_only_current_memory_and_flag_reasons():
    pack = build_llm_pack(
        groups=[
            parse_audit_flags_from_rows([flag_row(memory_id='mem-1', reason='duplicate old item')])[0][0]
        ],
        memories={'mem-1': {'id': 'mem-1', 'text': 'Current compact memory', 'metadata': {'memory_tier': 'active'}}},
    )

    rendered = json.dumps(pack, ensure_ascii=False)
    assert 'mem-1' in rendered
    assert 'Current compact memory' in rendered
    assert 'duplicate old item' in rendered
    assert 'preview' not in rendered
    assert 'raw chat' not in rendered.lower()


def parse_audit_flags_from_rows(rows):
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'audit.jsonl'
        write_jsonl(path, rows)
        return parse_audit_flags(path)


def test_parse_llm_actions_validates_exact_ids_and_falls_back_to_ask_user():
    groups, _ = parse_audit_flags_from_rows([flag_row(memory_id='mem-1', flag_type='conflict_candidate')])
    raw = json.dumps(
        {
            'actions': [
                {'action': 'update', 'memory_ids': ['mem-1'], 'content': 'Resolved interpretation.', 'reason': 'clear current truth'},
                {'action': 'delete', 'memory_ids': ['unknown'], 'reason': 'bad id'},
            ]
        }
    )

    actions = parse_llm_actions(raw, groups)

    assert actions[0].action == 'update'
    assert actions[0].memory_ids == ['mem-1']
    assert actions[1].action == 'ask_user'
    assert 'invalid LLM action' in actions[1].reason


def test_execute_maintenance_plan_writes_rollback_and_separate_maintenance_ledger(tmp_path):
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if method == 'GET':
                return {'id': path.rsplit('/', 1)[-1], 'memory': 'Before memory', 'metadata': {'memory_tier': 'active'}}
            if method == 'PUT':
                return {'updated': True}
            if method == 'DELETE':
                return {'deleted': True}
            raise AssertionError(f'unexpected request: {method} {path}')

    plan = build_maintenance_plan(
        parse_audit_flags_from_rows(
            [
                flag_row(memory_id='mem-stale', flag_type='stale_candidate', confidence=0.9),
                flag_row(memory_id='mem-delete', flag_type='delete_candidate', confidence=0.95),
            ]
        )[0]
    )

    result = execute_maintenance_plan(
        plan,
        FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret')),
        rollback_log=tmp_path / 'rollback.jsonl',
        maintenance_log=tmp_path / 'maintenance.jsonl',
        max_deletes=1,
    )

    assert result['counts']['downgraded'] == 1
    assert result['counts']['deleted'] == 1
    rollback_lines = (tmp_path / 'rollback.jsonl').read_text(encoding='utf-8').splitlines()
    maintenance_lines = (tmp_path / 'maintenance.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(rollback_lines) == 2
    assert len(maintenance_lines) == 2
    assert any(call[0:2] == ('DELETE', '/memories/mem-delete') for call in calls)
    put_body = next(call[2] for call in calls if call[0:2] == ('PUT', '/memories/mem-stale'))
    metadata = put_body['metadata']
    assert metadata['memory_tier'] == 'historical'
    assert metadata['current_status'] == 'unknown'
    assert metadata['downgraded_by'] == 'dream_maintenance'
    assert metadata['previous_memory_tier'] == 'active'
    assert metadata['stale_reason'] == 'stale candidate: duplicate junk'
    assert metadata['downgraded_at']
    maintenance_rows = [json.loads(line) for line in maintenance_lines]
    assert {row['group_key'] for row in maintenance_rows} == {'id:mem-stale', 'id:mem-delete'}


def test_load_maintained_group_keys_reads_direct_and_nested_keys(tmp_path):
    maintenance_log = tmp_path / 'maintenance.jsonl'
    write_jsonl(
        maintenance_log,
        [
            {'action': 'ask_user', 'group_key': 'id:review-me'},
            {'action': 'downgrade_tier', 'result': {'after': {'metadata': {'maintenance_group_key': 'id:old-row'}}}},
        ],
    )

    assert load_maintained_group_keys(maintenance_log) == {'id:review-me', 'id:old-row'}


def test_execute_maintenance_plan_refuses_deletes_without_cap(tmp_path):
    plan = build_maintenance_plan(parse_audit_flags_from_rows([flag_row(memory_id='mem-delete', confidence=0.95)])[0])
    client = Mem0Client(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    with pytest.raises(ValueError, match='max_deletes'):
        execute_maintenance_plan(plan, client, rollback_log=tmp_path / 'rollback.jsonl', maintenance_log=tmp_path / 'maintenance.jsonl')
