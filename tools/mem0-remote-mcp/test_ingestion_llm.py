import json

import ingestion_llm
from ingestion_llm import LLMConfig, call_llm, call_llm_with_metadata


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                'choices': [{'message': {'content': '{"proposals": []}'}}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 4, 'total_tokens': 16},
            }
        ).encode('utf-8')


def test_call_llm_with_metadata_returns_content_model_and_usage(monkeypatch):
    monkeypatch.setattr(ingestion_llm.urllib.request, 'urlopen', lambda request, timeout: FakeResponse())

    result = call_llm_with_metadata(
        LLMConfig(base_url='https://openrouter.example.test/api/v1', api_key='secret', model='qwen/test', timeout=20),
        [{'role': 'user', 'content': 'extract'}],
    )

    assert result.content == '{"proposals": []}'
    assert result.model == 'qwen/test'
    assert result.usage == {'prompt_tokens': 12, 'completion_tokens': 4, 'total_tokens': 16}


def test_call_llm_keeps_existing_string_contract(monkeypatch):
    monkeypatch.setattr(ingestion_llm.urllib.request, 'urlopen', lambda request, timeout: FakeResponse())

    content = call_llm(
        LLMConfig(base_url='https://openrouter.example.test/api/v1', api_key='secret', model='qwen/test'),
        [{'role': 'user', 'content': 'extract'}],
    )

    assert content == '{"proposals": []}'
