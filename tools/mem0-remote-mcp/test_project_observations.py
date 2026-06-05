import json
import subprocess
import sys

from project_observations import ProjectObservationStore, normalize_path


def test_append_redacts_secrets_and_dedupes(tmp_path):
    path = tmp_path / 'observations.jsonl'
    store = ProjectObservationStore(path)
    first = store.append({
        'origin': 'codex',
        'event_type': 'deploy',
        'title': 'Deploy V1.14',
        'summary': 'Used API_KEY=abc123 and bearer raw-token',
        'tool_name': 'shell_command',
        'command_category': 'deploy',
        'status': 'passed',
        'touched_paths': ['tools\\mem0-remote-mcp\\server.py'],
        'outcome': 'Remote restart succeeded TOKEN=private-token',
        'commands': ['Authorization: Bearer secret-token'],
        'source_hash': 'same-source',
    })
    second = store.append({'origin': 'codex', 'title': 'Duplicate', 'source_hash': 'same-source'})
    rendered = path.read_text(encoding='utf-8')
    row = json.loads(rendered)
    assert first['appended'] is True
    assert second['appended'] is False
    assert row['id'].startswith('obs_')
    assert row['timestamp'].endswith('Z')
    assert row['tool_name'] == 'shell_command'
    assert row['command_category'] == 'deploy'
    assert row['status'] == 'passed'
    assert row['touched_paths'] == ['tools/mem0-remote-mcp/server.py']
    assert 'abc123' not in rendered
    assert 'raw-token' not in rendered
    assert 'secret-token' not in rendered
    assert '<redacted>' in rendered


def test_recent_stats_and_by_file(tmp_path):
    path = tmp_path / 'observations.jsonl'
    path.write_text('{bad json\n', encoding='utf-8')
    store = ProjectObservationStore(path)
    store.append({'origin': 'codex', 'event_type': 'deploy', 'title': 'Deploy', 'source_hash': 'one'})
    store.append({
        'origin': 'claude',
        'event_type': 'implementation',
        'title': 'Add observation store',
        'files_read': ['tools\\mem0-remote-mcp\\README.md'],
        'files_modified': ['tools/mem0-remote-mcp/project_observations.py'],
        'source_hash': 'two',
    })
    assert [row['title'] for row in store.recent(limit=10, event_type='deploy')] == ['Deploy']
    assert [row['title'] for row in store.by_file('tools/mem0-remote-mcp/project_observations.py')] == ['Add observation store']
    assert normalize_path('tools\\mem0-remote-mcp\\README.md') == 'tools/mem0-remote-mcp/README.md'
    assert store.stats(limit=10)['malformed_rows'] == 1


def test_project_observations_cli_append_recent_and_by_file(tmp_path):
    log_path = tmp_path / 'observations.jsonl'
    script = 'tools/mem0-remote-mcp/project_observations_cli.py'
    subprocess.run([
        sys.executable, script, 'append', '--log', str(log_path), '--origin', 'codex',
        '--event-type', 'implementation', '--title', 'CLI observation',
        '--summary', 'No secret TOKEN=private-token should remain',
        '--file-modified', 'tools/mem0-remote-mcp/project_observations_cli.py',
        '--source-hash', 'cli-source',
    ], check=True, capture_output=True, text=True)
    recent = subprocess.run([sys.executable, script, 'recent', '--log', str(log_path), '--limit', '5'], check=True, capture_output=True, text=True)
    by_file = subprocess.run([sys.executable, script, 'by-file', '--log', str(log_path), 'tools/mem0-remote-mcp/project_observations_cli.py'], check=True, capture_output=True, text=True)
    assert json.loads(recent.stdout)['rows'][0]['title'] == 'CLI observation'
    assert json.loads(by_file.stdout)['rows'][0]['title'] == 'CLI observation'
    assert 'private-token' not in log_path.read_text(encoding='utf-8')
