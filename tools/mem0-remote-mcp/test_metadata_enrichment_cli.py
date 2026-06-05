import json
import subprocess
import sys
from pathlib import Path


def test_metadata_enrichment_cli_writes_safe_dry_run_plan(tmp_path):
    memories = tmp_path / 'memories.json'
    output = tmp_path / 'plan.json'
    memories.write_text(
        json.dumps(
            {
                'results': [
                    {
                        'id': 'mother',
                        'text': 'Mother psychiatric ward and delusional escalation details must not print.',
                        'metadata': {'domains': ['relationships', 'psychology', 'family'], 'memory_type': 'person'},
                    }
                ]
            }
        ),
        encoding='utf-8',
    )

    completed = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/metadata_enrichment_cli.py',
            '--memories-json',
            str(memories),
            '--output',
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=True,
    )

    assert 'delusional escalation details' not in completed.stdout
    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['summary'] == {'update_type': 1}
    assert payload['actions'][0]['memory_type'] == 'clinical_context'
    assert 'delusional escalation details' not in json.dumps(payload)


def test_metadata_enrichment_cli_requires_max_updates_for_execute(tmp_path):
    memories = tmp_path / 'memories.json'
    output = tmp_path / 'plan.json'
    memories.write_text(
        json.dumps({'results': [{'id': 'm1', 'text': 'psychiatric ward', 'metadata': {'domains': ['psychology'], 'memory_type': 'note'}}]}),
        encoding='utf-8',
    )

    completed = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/metadata_enrichment_cli.py',
            '--memories-json',
            str(memories),
            '--output',
            str(output),
            '--execute',
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert '--max-updates is required' in completed.stderr
