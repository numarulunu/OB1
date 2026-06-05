
import json
import subprocess
import sys


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')


def telemetry_row(*ids):
    return {'event': 'search', 'results': [{'id': memory_id, 'title': memory_id} for memory_id in ids]}


def test_lifecycle_cli_writes_decay_dry_run_plan(tmp_path):
    memories_path = tmp_path / 'memories.json'
    telemetry_path = tmp_path / 'retrieval.jsonl'
    output_path = tmp_path / 'lifecycle-plan.json'
    write_json(
        memories_path,
        [
            {
                'id': 'low-use',
                'text': 'Low use workflow memory.',
                'metadata': {'domains': ['workflow'], 'signal_strength': 2, 'current_status': 'active', 'memory_tier': 'active'},
            }
        ],
    )
    write_jsonl(telemetry_path, [telemetry_row('other'), telemetry_row('other'), telemetry_row('other')])

    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/lifecycle_cli.py',
            '--job',
            'decay_low_use',
            '--memories-json',
            str(memories_path),
            '--telemetry-log',
            str(telemetry_path),
            '--active-to-historical-epochs',
            '2',
            '--output',
            str(output_path),
        ],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding='utf-8'))
    assert payload['job'] == 'decay_low_use'
    assert payload['dry_run'] is True
    assert payload['actions'][0]['action'] == 'downgrade_tier'
    assert 'Low use workflow memory' not in result.stdout


def test_lifecycle_cli_refuses_execute_without_max_updates(tmp_path):
    memories_path = tmp_path / 'memories.json'
    telemetry_path = tmp_path / 'retrieval.jsonl'
    output_path = tmp_path / 'lifecycle-plan.json'
    write_json(
        memories_path,
        [{'id': 'low-use', 'text': 'Low use memory.', 'metadata': {'domains': ['workflow'], 'signal_strength': 2, 'memory_tier': 'active'}}],
    )
    write_jsonl(telemetry_path, [telemetry_row('other'), telemetry_row('other'), telemetry_row('other')])

    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/lifecycle_cli.py',
            '--job',
            'decay_low_use',
            '--memories-json',
            str(memories_path),
            '--telemetry-log',
            str(telemetry_path),
            '--active-to-historical-epochs',
            '2',
            '--output',
            str(output_path),
            '--execute',
        ],
        cwd='.',
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert 'max-updates' in result.stderr



def test_lifecycle_cli_requires_memories_json_unless_live_snapshot(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/lifecycle_cli.py',
            '--job',
            'decay_low_use',
            '--telemetry-log',
            str(tmp_path / 'retrieval.jsonl'),
            '--output',
            str(tmp_path / 'plan.json'),
        ],
        cwd='.',
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert 'memories-json' in result.stderr


def test_lifecycle_cli_live_snapshot_requires_api_key(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'
    write_jsonl(telemetry_path, [telemetry_row('other')])

    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/lifecycle_cli.py',
            '--job',
            'decay_low_use',
            '--live-snapshot',
            '--snapshot-output',
            str(tmp_path / 'snapshot.json'),
            '--telemetry-log',
            str(telemetry_path),
            '--output',
            str(tmp_path / 'plan.json'),
        ],
        cwd='.',
        text=True,
        capture_output=True,
        env={},
    )

    assert result.returncode == 1
    assert 'MEM0_API_KEY' in result.stderr
