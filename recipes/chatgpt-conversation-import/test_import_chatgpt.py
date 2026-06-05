import importlib.util
import json
from pathlib import Path


def load_importer():
    module_path = Path(__file__).with_name("import-chatgpt.py")
    spec = importlib.util.spec_from_file_location("import_chatgpt", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_extraction_response_caps_thoughts_at_five():
    importer = load_importer()
    payload = {
        "thoughts": [
            {
                "content": f"Memory {index}",
                "type": "context",
                "topics": ["test"],
                "people": [],
                "confidence": "firm",
            }
            for index in range(7)
        ],
        "conversation_type": "test",
    }

    parsed = importer._parse_extraction_response(json.dumps(payload))

    assert len(parsed["thoughts"]) == 5
    assert [thought["content"] for thought in parsed["thoughts"]] == [
        "Memory 0",
        "Memory 1",
        "Memory 2",
        "Memory 3",
        "Memory 4",
    ]


def test_summarize_openrouter_uses_fallback_after_primary_failure(monkeypatch):
    importer = load_importer()
    importer.OPENROUTER_API_KEY = "test-key"
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    def fake_post(url, headers, body):
        calls.append(body["model"])
        if len(calls) == 1:
            return FakeResponse(429, text="rate limited")
        return FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "thoughts": [
                                        {
                                            "content": "Fallback memory",
                                            "type": "context",
                                            "topics": [],
                                            "people": [],
                                            "confidence": "firm",
                                        }
                                    ],
                                    "conversation_type": "test",
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(importer, "http_post_with_retry", fake_post)

    parsed = importer.summarize_openrouter(
        "title",
        "2026-04-29",
        "User: hello\nAssistant: hi",
        2,
        "gpt-test",
        openrouter_model="qwen/test",
        fallback_openrouter_model="openai/gpt-4o-mini",
    )

    assert calls == ["qwen/test", "openai/gpt-4o-mini"]
    assert parsed["thoughts"][0]["content"] == "Fallback memory"


def test_normalize_supabase_url_accepts_data_api_url():
    importer = load_importer()

    assert importer.normalize_supabase_url("https://example.supabase.co/rest/v1/") == "https://example.supabase.co"


def test_generate_embedding_returns_vector(monkeypatch):
    importer = load_importer()
    importer.OPENROUTER_API_KEY = "test-key"

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr(importer, "http_post_with_retry", lambda url, headers, body: FakeResponse())

    assert importer.generate_embedding("hello") == [0.1, 0.2, 0.3]


def test_http_post_with_retry_retries_rate_limit(monkeypatch):
    importer = load_importer()
    calls = []

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    def fake_post(url, headers, json, timeout):
        calls.append(url)
        return FakeResponse(429 if len(calls) == 1 else 200)

    monkeypatch.setattr(importer.requests, "post", fake_post)
    monkeypatch.setattr(importer.time, "sleep", lambda seconds: None)

    response = importer.http_post_with_retry("https://example.test", {}, {}, retries=2)

    assert response.status_code == 200
    assert len(calls) == 2


def test_generate_embedding_retries_missing_data_response(monkeypatch):
    importer = load_importer()
    importer.OPENROUTER_API_KEY = "test-key"
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            calls.append(1)
            if len(calls) == 1:
                return {"error": {"message": "rate limited"}}
            return {"data": [{"embedding": [0.4, 0.5, 0.6]}]}

    monkeypatch.setattr(importer, "http_post_with_retry", lambda url, headers, body: FakeResponse())
    monkeypatch.setattr(importer.time, "sleep", lambda seconds: None)

    assert importer.generate_embedding("hello") == [0.4, 0.5, 0.6]
    assert len(calls) == 2
