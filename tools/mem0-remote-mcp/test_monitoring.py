import json

from monitoring import build_audit_stats, build_status_payload


def write_jsonl(path, rows):
    lines = []
    for row in rows:
        if row == 'malformed':
            lines.append('{bad json')
        else:
            lines.append(json.dumps(row, ensure_ascii=False))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_build_audit_stats_counts_actions_origins_flags_and_errors(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            {'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'save', 'memory_id': 'm1'},
            {'ts': '2026-05-09T00:01:00Z', 'origin': 'claude', 'action': 'update', 'memory_id': 'm2'},
            {'ts': '2026-05-09T00:02:00Z', 'origin': 'chatgpt', 'action': 'flag', 'flag_type': 'delete_candidate'},
            {'ts': '2026-05-09T00:03:00Z', 'origin': 'codex', 'action': 'skip'},
            {'ts': '2026-05-09T00:04:00Z', 'origin': 'codex', 'action': 'extract', 'errors': ['timeout while calling provider']},
            'malformed',
        ],
    )

    stats = build_audit_stats(audit_path)

    assert stats.rows_seen == 5
    assert stats.malformed_rows == 1
    assert stats.actions == {'save': 1, 'update': 1, 'flag': 1, 'skip': 1, 'extract': 1}
    assert stats.origins == {'codex': 3, 'claude': 1, 'chatgpt': 1}
    assert stats.flag_types == {'delete_candidate': 1}
    assert stats.error_count == 1
    assert stats.error_types == {'timeout': 1}
    assert stats.last_success_by_origin == {'codex': '2026-05-09T00:00:00Z', 'claude': '2026-05-09T00:01:00Z'}
    assert stats.last_error['origin'] == 'codex'
    assert stats.last_error['message'] == 'timeout while calling provider'


def test_build_audit_stats_limits_recent_rows(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            {'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'save'},
            {'ts': '2026-05-09T00:01:00Z', 'origin': 'claude', 'action': 'flag', 'flag_type': 'stale_candidate'},
        ],
    )

    stats = build_audit_stats(audit_path, recent_limit=1)

    assert stats.rows_seen == 1
    assert stats.actions == {'flag': 1}
    assert stats.flag_types == {'stale_candidate': 1}


def test_build_status_payload_is_safe_and_compact(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            {
                'ts': '2026-05-09T00:00:00Z',
                'origin': 'codex',
                'action': 'save',
                'preview': 'user: private raw words should not appear',
                'memory_id': 'm1',
                'llm': {'model': 'qwen/qwen3.6-flash', 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}},
            },
            {'ts': '2026-05-09T00:01:00Z', 'origin': 'claude', 'action': 'flag', 'flag_type': 'merge_candidate'},
        ],
    )

    payload = build_status_payload(
        audit_path,
        ingestion_enabled=True,
        ingestion_model='qwen/qwen3.6-flash',
        profiles=['chatgpt', 'codex'],
        recent_limit=100,
    )

    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload['ok'] is True
    assert payload['ingestion']['enabled'] is True
    assert payload['ingestion']['model'] == 'qwen/qwen3.6-flash'
    assert payload['audit']['actions']['save'] == 1
    assert payload['audit']['pending_flags'] == 1
    assert payload['usage']['prompt_tokens'] == 100
    assert payload['usage']['completion_tokens'] == 20
    assert payload['usage']['cost_status'] == 'usage_tokens_only'
    assert 'private raw words' not in rendered
    assert 'profile-token' not in rendered


def test_build_status_payload_marks_maintenance_due_from_pending_flags(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            {
                'ts': '2026-05-09T00:00:00Z',
                'origin': 'codex',
                'action': 'flag',
                'flag_type': 'stale_candidate',
                'preview': 'private raw words',
            },
            {'ts': '2026-05-09T00:01:00Z', 'origin': 'claude', 'action': 'flag', 'flag_type': 'delete_candidate'},
            {'ts': '2026-05-09T00:02:00Z', 'origin': 'codex', 'action': 'flag', 'flag_type': 'merge_candidate'},
        ],
    )

    payload = build_status_payload(
        audit_path,
        ingestion_enabled=False,
        ingestion_model='',
        maintenance_flag_threshold=3,
    )

    maintenance = payload['maintenance']
    rendered = json.dumps(payload, ensure_ascii=False)
    assert maintenance['due'] is True
    assert maintenance['pending_flags'] == 3
    assert 'pending_flags_threshold' in maintenance['reasons']
    assert maintenance['requires_approval'] is True
    assert 'dream_maintenance_cli.py' in maintenance['dry_run_command']
    assert 'private raw words' not in rendered


def test_build_status_payload_excludes_already_maintained_flag_groups(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    maintenance_path = tmp_path / 'maintenance.jsonl'
    write_jsonl(
        audit_path,
        [
            {'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'flag', 'flag_type': 'conflict_candidate', 'memory_id': 'mem-1'},
            {'ts': '2026-05-09T00:01:00Z', 'origin': 'codex', 'action': 'flag', 'flag_type': 'stale', 'memory_id': 'mem-2'},
        ],
    )
    write_jsonl(
        maintenance_path,
        [
            {'ts': '2026-05-09T01:00:00Z', 'action': 'ask_user', 'group_key': 'id:mem-1'},
            {'ts': '2026-05-09T01:01:00Z', 'action': 'downgrade_tier', 'group_key': 'id:mem-2'},
        ],
    )

    payload = build_status_payload(
        audit_path,
        ingestion_enabled=False,
        ingestion_model='',
        maintenance_log=maintenance_path,
    )

    assert payload['audit']['pending_flags'] == 0
    assert payload['maintenance']['due'] is False
    assert payload['maintenance']['reminder'] == ''


def test_build_status_payload_keeps_maintenance_quiet_below_threshold(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(audit_path, [{'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'flag', 'flag_type': 'stale_candidate'}])

    payload = build_status_payload(audit_path, ingestion_enabled=False, ingestion_model='', maintenance_flag_threshold=5)

    assert payload['maintenance']['due'] is False
    assert payload['maintenance']['reminder'] == ''


def test_build_status_payload_reports_usage_unavailable(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(audit_path, [{'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'skip'}])

    payload = build_status_payload(audit_path, ingestion_enabled=True, ingestion_model='qwen/qwen3.6-flash')

    assert payload['usage']['cost_status'] == 'usage_unavailable'
