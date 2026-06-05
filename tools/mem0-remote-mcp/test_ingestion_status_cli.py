import json
import subprocess
import sys


def test_ingestion_status_cli_outputs_json_without_raw_previews(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    audit_path.write_text(
        json.dumps(
            {
                'ts': '2026-05-09T00:00:00Z',
                'origin': 'codex',
                'action': 'save',
                'preview': 'raw private preview should not print',
                'llm': {'model': 'qwen/test', 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}},
            }
        )
        + '\n',
        encoding='utf-8',
    )

    result = subprocess.run(
        [sys.executable, 'tools/mem0-remote-mcp/ingestion_status_cli.py', '--audit-log', str(audit_path), '--json'],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload['audit']['actions']['save'] == 1
    assert payload['usage']['prompt_tokens'] == 10
    assert 'raw private preview' not in result.stdout


def test_ingestion_status_cli_outputs_human_summary(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    audit_path.write_text(json.dumps({'ts': '2026-05-09T00:00:00Z', 'origin': 'claude', 'action': 'flag'}) + '\n', encoding='utf-8')

    result = subprocess.run(
        [sys.executable, 'tools/mem0-remote-mcp/ingestion_status_cli.py', '--audit-log', str(audit_path)],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    assert 'Mem0 ingestion status' in result.stdout
    assert 'pending flags: 1' in result.stdout
    assert 'claude' in result.stdout
