from llm_client import LLMConfig, build_chat_body, extract_with_cache


def test_build_chat_body_requests_json_object():
    body = build_chat_body("Extract this", "test-model")

    assert body["model"] == "test-model"
    assert body["messages"] == [{"role": "user", "content": "Extract this"}]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0


def test_extract_with_cache_reuses_cached_response(tmp_path):
    cache = tmp_path / "cache.jsonl"
    calls = []

    def fake_post(config, prompt):
        calls.append((config.model, prompt))
        return '{"proposals":[]}'

    config = LLMConfig(base_url="https://example.test/v1", api_key="secret", model="test-model")

    first = extract_with_cache("prompt", config, cache, post_fn=fake_post)
    second = extract_with_cache("prompt", config, cache, post_fn=fake_post)

    assert first == '{"proposals":[]}'
    assert second == '{"proposals":[]}'
    assert calls == [("test-model", "prompt")]


def test_extract_with_cache_separates_model_cache_keys(tmp_path):
    cache = tmp_path / "cache.jsonl"
    calls = []

    def fake_post(config, prompt):
        calls.append(config.model)
        return '{"model":"' + config.model + '","proposals":[]}'

    extract_with_cache("prompt", LLMConfig("https://example.test/v1", "secret", "model-a"), cache, post_fn=fake_post)
    extract_with_cache("prompt", LLMConfig("https://example.test/v1", "secret", "model-b"), cache, post_fn=fake_post)

    assert calls == ["model-a", "model-b"]
