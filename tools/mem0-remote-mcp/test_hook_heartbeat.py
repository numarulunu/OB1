import json

from hook_heartbeat import HookHeartbeatLog, build_hook_stats


def test_hook_heartbeat_writes_content_free_rows(tmp_path):
    path = tmp_path / 'hooks.jsonl'
    log = HookHeartbeatLog(path)

    row = log.append(origin='codex', hook_type='user_prompt', source='compact', marker='private prompt text')

    rendered = path.read_text(encoding='utf-8')
    payload = json.loads(rendered)
    assert row['origin'] == 'codex'
    assert payload['event'] == 'hook_heartbeat'
    assert payload['hook_type'] == 'user_prompt'
    assert payload['source'] == 'compact'
    assert payload['marker_hash']
    assert 'private prompt text' not in rendered


def test_hook_stats_count_origins_types_and_malformed_rows(tmp_path):
    path = tmp_path / 'hooks.jsonl'
    path.write_text(
        '\n'.join(
            [
                json.dumps({'ts': '2026-05-10T00:00:00Z', 'event': 'hook_heartbeat', 'origin': 'codex', 'hook_type': 'session_start'}),
                '{bad json',
                json.dumps({'ts': '2026-05-10T00:01:00Z', 'event': 'hook_heartbeat', 'origin': 'claude', 'hook_type': 'user_prompt'}),
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    stats = build_hook_stats(path, recent_limit=10).to_dict()

    assert stats['rows_seen'] == 2
    assert stats['malformed_rows'] == 1
    assert stats['origins'] == {'codex': 1, 'claude': 1}
    assert stats['hook_types'] == {'session_start': 1, 'user_prompt': 1}
    assert stats['last_seen_by_origin'] == {'codex': '2026-05-10T00:00:00Z', 'claude': '2026-05-10T00:01:00Z'}
