
import json
import subprocess
import sys

from core import Mem0Client, Mem0Config
from memory_snapshot import safe_snapshot_memory, snapshot_memories


def test_get_all_http_fallback_uses_self_hosted_get_endpoint_and_sanitizes_rows():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            return {
                'results': [
                    {
                        'id': 'mem-1',
                        'memory': 'Private memory text.',
                        'metadata': {'domains': ['workflow'], 'memory_tier': 'active'},
                    }
                ]
            }

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.get_all(page_size=50, max_pages=3)

    assert len(result['results']) == 1
    assert result['results'][0]['id'] == 'mem-1'
    assert result['results'][0]['text'] == 'Private memory text.'
    assert calls == [('GET', '/memories?user_id=ionut', None)]


def test_get_all_uses_postgres_snapshot_when_database_url_is_configured():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            raise AssertionError('HTTP fallback should not be used when Postgres is configured')

        def postgres_get_all(self, limit=10000):
            return [
                {
                    'id': 'mem-1',
                    'memory': 'Private memory text.',
                    'metadata': {'domains': ['workflow'], 'memory_tier': 'active'},
                }
            ]

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret', lexical_database_url='postgresql://example'))

    result = client.get_all(page_size=50, max_pages=3)

    assert len(result['results']) == 1
    assert result['results'][0]['id'] == 'mem-1'
    assert result['results'][0]['text'] == 'Private memory text.'
    assert calls == []


def test_safe_snapshot_memory_excludes_text_by_default():
    row = {
        'id': 'mem-1',
        'title': 'Private memory text.',
        'text': 'Private memory text.',
        'metadata': {'domains': ['workflow'], 'memory_type': 'pattern', 'memory_tier': 'active'},
    }

    result = safe_snapshot_memory(row)
    rendered = json.dumps(result, ensure_ascii=False)

    assert result == {
        'id': 'mem-1',
        'metadata': {'domains': ['workflow'], 'memory_type': 'pattern', 'memory_tier': 'active'},
    }
    assert 'Private memory text' not in rendered


def test_snapshot_memories_writes_safe_snapshot_and_prints_no_memory_text(tmp_path):
    output_path = tmp_path / 'snapshot.json'

    class FakeClient(Mem0Client):
        def get_all(self, page_size=100, max_pages=100):
            return {
                'results': [
                    {
                        'id': 'mem-1',
                        'title': 'Private memory text.',
                        'text': 'Private memory text.',
                        'metadata': {'domains': ['workflow'], 'signal_strength': 2, 'memory_tier': 'active'},
                    }
                ]
            }

    summary = snapshot_memories(FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret')), output_path)

    payload = json.loads(output_path.read_text(encoding='utf-8'))
    rendered = json.dumps(payload, ensure_ascii=False)
    assert summary == {'output': str(output_path), 'count': 1, 'include_text': False}
    assert payload['results'] == [{'id': 'mem-1', 'metadata': {'domains': ['workflow'], 'signal_strength': 2, 'memory_tier': 'active'}}]
    assert 'Private memory text' not in rendered


def test_memory_snapshot_cli_requires_api_key(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/memory_snapshot_cli.py',
            '--output',
            str(tmp_path / 'snapshot.json'),
        ],
        cwd='.',
        text=True,
        capture_output=True,
        env={},
    )

    assert result.returncode == 1
    assert 'MEM0_API_KEY' in result.stderr
