from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = 60


@dataclass(frozen=True)
class LLMResult:
    content: str
    model: str
    usage: dict[str, int]


def build_chat_body(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        'model': model,
        'messages': messages,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
    }


def normalize_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage: dict[str, int] = {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        try:
            number = int(value.get(key) or 0)
        except (TypeError, ValueError):
            number = 0
        if number:
            usage[key] = number
    if usage and 'total_tokens' not in usage:
        total = usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)
        if total:
            usage['total_tokens'] = total
    return usage


def call_llm_with_metadata(config: LLMConfig, messages: list[dict[str, str]]) -> LLMResult:
    if not config.api_key:
        raise RuntimeError('ingestion LLM API key is missing')
    body = build_chat_body(config.model, messages)
    request = urllib.request.Request(
        config.base_url.rstrip('/') + '/chat/completions',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        method='POST',
        headers={
            'Authorization': f'Bearer {config.api_key}',
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:500]
        raise RuntimeError(f'ingestion LLM HTTP {exc.code}: {detail}') from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError('ingestion LLM returned malformed JSON') from exc
    try:
        content = payload['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('ingestion LLM response missing message content') from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('ingestion LLM response content is empty')
    return LLMResult(content=content, model=str(payload.get('model') or config.model), usage=normalize_usage(payload.get('usage')))


def call_llm(config: LLMConfig, messages: list[dict[str, str]]) -> str:
    return call_llm_with_metadata(config, messages).content
