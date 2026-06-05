#!/usr/bin/env python3
"""Small stdio MCP bridge from Codex to self-hosted Mem0.

The server intentionally exposes only distilled-memory operations. It does not
ask Mem0 to infer from raw chat unless a future caller explicitly extends it.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = '2024-11-05'
DEFAULT_BASE_URL = 'https://mem0-api.ionutrosu.xyz'
DEFAULT_USER_ID = 'ionut'
DEFAULT_AGENT_ID = 'codex-live-memory'


@dataclass
class Config:
    base_url: str
    api_key: str
    user_id: str = DEFAULT_USER_ID
    agent_id: str = DEFAULT_AGENT_ID


def load_config() -> Config:
    api_key = os.environ.get('MEM0_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('MEM0_API_KEY is required for the Mem0 Codex MCP server.')
    return Config(
        base_url=os.environ.get('MEM0_BASE_URL', DEFAULT_BASE_URL).strip().rstrip('/'),
        api_key=api_key,
        user_id=os.environ.get('MEM0_USER_ID', DEFAULT_USER_ID).strip() or DEFAULT_USER_ID,
        agent_id=os.environ.get('MEM0_AGENT_ID', DEFAULT_AGENT_ID).strip() or DEFAULT_AGENT_ID,
    )


def json_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def json_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


def text_content(value: Any) -> dict[str, Any]:
    return {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False, sort_keys=True)}]}


def mem0_request(method: str, path: str, body: dict[str, Any] | None, config: Config) -> Any:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        config.base_url + path,
        data=data,
        method=method,
        headers={'X-API-Key': config.api_key, 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read().decode('utf-8')
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:500]
        raise RuntimeError(f'Mem0 HTTP {exc.code}: {detail}') from exc


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            'name': 'memory_search',
            'description': 'Search the user\'s curated Mem0 second-brain memories. Use this before answering questions that may depend on personal preferences, projects, workflows, relationships, psychology, opera, business, or AI-system context.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Search query.'},
                    'top_k': {'type': 'integer', 'description': 'Maximum number of results.', 'default': 5, 'minimum': 1, 'maximum': 20},
                    'domains': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Optional domain filters for local post-filtering by metadata domains.'},
                },
                'required': ['query'],
                'additionalProperties': False,
            },
        },
        {
            'name': 'memory_save',
            'description': 'Save a distilled durable memory to Mem0. Do not save raw transcript text, secrets, temporary logs, one-off implementation noise, or low-confidence guesses.',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'content': {'type': 'string', 'description': 'A compact, durable memory statement.'},
                    'domains': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Relevant domains such as ai, workflow, business, opera, vocality, psychology, relationships.'},
                    'memory_type': {'type': 'string', 'description': 'Type such as preference, project, workflow, identity_shaping, relationship, lesson, decision.'},
                    'signal_strength': {'type': 'number', 'description': 'Signal score from 1 to 10.', 'minimum': 1, 'maximum': 10},
                },
                'required': ['content'],
                'additionalProperties': False,
            },
        },
    ]


def sanitize_result(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get('metadata') or {}
    return {
        'id': row.get('id'),
        'memory': row.get('memory'),
        'score': row.get('score') or row.get('similarity'),
        'metadata': {
            'domains': metadata.get('domains') or [],
            'memory_type': metadata.get('memory_type') or '',
            'signal_strength': metadata.get('signal_strength'),
        },
    }


def call_memory_search(arguments: dict[str, Any], config: Config) -> dict[str, Any]:
    query = str(arguments.get('query') or '').strip()
    if not query:
        raise ValueError('query is required')
    top_k = int(arguments.get('top_k') or 5)
    top_k = min(max(top_k, 1), 20)
    data = mem0_request(
        'POST',
        '/search',
        {'query': query, 'filters': {'user_id': config.user_id}, 'top_k': top_k},
        config,
    )
    results = data.get('results') if isinstance(data, dict) else data
    results = [sanitize_result(row) for row in (results or []) if isinstance(row, dict)]
    requested_domains = {str(item).lower() for item in (arguments.get('domains') or [])}
    if requested_domains:
        results = [
            row for row in results
            if requested_domains & {str(domain).lower() for domain in row['metadata'].get('domains', [])}
        ]
    return text_content({'query': query, 'results': results})


def call_memory_save(arguments: dict[str, Any], config: Config) -> dict[str, Any]:
    content = str(arguments.get('content') or '').strip()
    if not content:
        raise ValueError('content is required')
    domains = [str(item).strip() for item in (arguments.get('domains') or []) if str(item).strip()]
    metadata = {
        'source': 'codex-mcp',
        'domains': domains,
        'memory_type': str(arguments.get('memory_type') or 'note'),
        'signal_strength': arguments.get('signal_strength'),
    }
    data = mem0_request(
        'POST',
        '/memories',
        {
            'messages': [{'role': 'user', 'content': content}],
            'user_id': config.user_id,
            'agent_id': config.agent_id,
            'metadata': metadata,
            'infer': False,
            'version': 'v2',
            'immutable': False,
        },
        config,
    )
    return text_content({'saved': True, 'response': data})


def handle_request(request: dict[str, Any], config: Config | None) -> dict[str, Any] | None:
    method = request.get('method')
    request_id = request.get('id')
    if method == 'notifications/initialized':
        return None
    if method == 'initialize':
        return json_response(
            request_id,
            {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'mem0-codex-mcp', 'version': '0.1.0'},
            },
        )
    if method == 'tools/list':
        return json_response(request_id, {'tools': tool_definitions()})
    if method == 'tools/call':
        if config is None:
            raise RuntimeError('Server config is not loaded.')
        params = request.get('params') or {}
        name = params.get('name')
        arguments = params.get('arguments') or {}
        if name == 'memory_search':
            return json_response(request_id, call_memory_search(arguments, config))
        if name == 'memory_save':
            return json_response(request_id, call_memory_save(arguments, config))
        return json_error(request_id, -32602, f'Unknown tool: {name}')
    return json_error(request_id, -32601, f'Unknown method: {method}')


def main() -> int:
    try:
        config = load_config()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(request, config)
        except Exception as exc:
            request_id = None
            if 'request' in locals() and isinstance(request, dict):
                request_id = request.get('id')
            response = json_error(request_id, -32000, str(exc))
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(',', ':')), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
