import importlib.util
import json
import sys
from pathlib import Path


def load_server():
    module_path = Path(__file__).with_name('mem0_mcp_server.py')
    spec = importlib.util.spec_from_file_location('mem0_mcp_server', module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tools_list_exposes_search_and_save_only():
    server = load_server()

    response = server.handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, None)

    tool_names = [tool['name'] for tool in response['result']['tools']]
    assert tool_names == ['memory_search', 'memory_save']


def test_search_uses_user_filter_and_sanitizes_response():
    server = load_server()
    calls = []

    def fake_request(method, path, body, config):
        calls.append((method, path, body))
        return {
            'results': [
                {
                    'id': 'mem-1',
                    'memory': 'Relevant durable memory.',
                    'score': 0.91,
                    'metadata': {
                        'domains': ['ai', 'workflow'],
                        'memory_type': 'pattern',
                        'signal_strength': 9,
                        'source_ids': ['raw-1'],
                    },
                }
            ]
        }

    server.mem0_request = fake_request
    config = server.Config(base_url='https://mem0.example.test', api_key='test-key')

    response = server.handle_request(
        {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {'name': 'memory_search', 'arguments': {'query': 'AI workflow', 'top_k': 3}},
        },
        config,
    )

    assert calls == [
        (
            'POST',
            '/search',
            {'query': 'AI workflow', 'filters': {'user_id': 'ionut'}, 'top_k': 3},
        )
    ]
    payload = json.loads(response['result']['content'][0]['text'])
    assert payload == {
        'query': 'AI workflow',
        'results': [
            {
                'id': 'mem-1',
                'memory': 'Relevant durable memory.',
                'score': 0.91,
                'metadata': {'domains': ['ai', 'workflow'], 'memory_type': 'pattern', 'signal_strength': 9},
            }
        ],
    }


def test_save_writes_distilled_memory_without_inference():
    server = load_server()
    calls = []

    def fake_request(method, path, body, config):
        calls.append((method, path, body))
        return {'id': 'created'}

    server.mem0_request = fake_request
    config = server.Config(base_url='https://mem0.example.test', api_key='test-key')

    response = server.handle_request(
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {
                'name': 'memory_save',
                'arguments': {
                    'content': 'User prefers compact project memory over raw transcripts.',
                    'domains': ['ai', 'workflow'],
                    'memory_type': 'preference',
                },
            },
        },
        config,
    )

    assert calls[0][0] == 'POST'
    assert calls[0][1] == '/memories'
    body = calls[0][2]
    assert body['messages'] == [{'role': 'user', 'content': 'User prefers compact project memory over raw transcripts.'}]
    assert body['user_id'] == 'ionut'
    assert body['agent_id'] == 'codex-live-memory'
    assert body['infer'] is False
    assert body['metadata']['source'] == 'codex-mcp'
    assert body['metadata']['domains'] == ['ai', 'workflow']
    payload = json.loads(response['result']['content'][0]['text'])
    assert payload['saved'] is True
