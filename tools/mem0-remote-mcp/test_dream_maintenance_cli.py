import json
import subprocess
import sys


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')


def test_dream_maintenance_cli_writes_dry_run_plan(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    output_path = tmp_path / 'plan.json'
    write_jsonl(
        audit_path,
        [
            {
                'ts': '2026-05-09T00:00:00Z',
                'origin': 'codex',
                'action': 'flag',
                'flag_type': 'delete_candidate',
                'confidence': 0.95,
                'memory_id': 'mem-delete',
                'reason': 'duplicate junk',
                'preview': 'raw private preview should not appear',
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/dream_maintenance_cli.py',
            '--audit-log',
            str(audit_path),
            '--output',
            str(output_path),
        ],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding='utf-8'))
    assert payload['dry_run'] is True
    assert payload['actions'][0]['action'] == 'delete'
    assert 'raw private preview' not in result.stdout
    assert 'raw private preview' not in json.dumps(payload)


def test_dream_maintenance_cli_refuses_execute_without_max_deletes(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    output_path = tmp_path / 'plan.json'
    write_jsonl(
        audit_path,
        [
            {
                'ts': '2026-05-09T00:00:00Z',
                'origin': 'codex',
                'action': 'flag',
                'flag_type': 'delete_candidate',
                'confidence': 0.95,
                'memory_id': 'mem-delete',
                'reason': 'duplicate junk',
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/dream_maintenance_cli.py',
            '--audit-log',
            str(audit_path),
            '--output',
            str(output_path),
            '--execute',
        ],
        cwd='.',
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert 'max-deletes' in result.stderr


def test_dream_maintenance_cli_limit_groups(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    output_path = tmp_path / 'plan.json'
    write_jsonl(
        audit_path,
        [
            {'action': 'flag', 'flag_type': 'delete_candidate', 'confidence': 0.95, 'memory_id': 'mem-1'},
            {'action': 'flag', 'flag_type': 'delete_candidate', 'confidence': 0.95, 'memory_id': 'mem-2'},
        ],
    )

    subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/dream_maintenance_cli.py',
            '--audit-log',
            str(audit_path),
            '--output',
            str(output_path),
            '--limit-groups',
            '1',
        ],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding='utf-8'))
    assert len(payload['actions']) == 1


def test_dream_maintenance_cli_skips_maintained_groups(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    maintenance_path = tmp_path / 'maintenance.jsonl'
    output_path = tmp_path / 'plan.json'
    write_jsonl(
        audit_path,
        [
            {'action': 'flag', 'flag_type': 'stale', 'memory_id': 'mem-1'},
            {'action': 'flag', 'flag_type': 'stale', 'memory_id': 'mem-2'},
        ],
    )
    write_jsonl(maintenance_path, [{'action': 'downgrade_tier', 'group_key': 'id:mem-1'}])

    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/dream_maintenance_cli.py',
            '--audit-log',
            str(audit_path),
            '--maintenance-log',
            str(maintenance_path),
            '--output',
            str(output_path),
        ],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding='utf-8'))
    assert json.loads(result.stdout)['actions'] == 1
    assert payload['actions'][0]['memory_ids'] == ['mem-2']
