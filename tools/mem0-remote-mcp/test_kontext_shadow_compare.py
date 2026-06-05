from __future__ import annotations

import json
from pathlib import Path

from kontext_shadow_compare import (
    KontextShadowConfig,
    build_comparison_row,
    endpoint_url,
    kontext_rpc_search,
    log_kontext_shadow_search,
    parse_kontext_results,
)


def test_build_comparison_row_is_sanitized_and_counts_overlap():
    query = "private relationship query should not be logged"
    mem0 = [{"id": "mem-a", "title": "private mem0 title"}, {"id": "mem-b"}]
    kontext = [{"id": "mem-b", "title": "private kontext title"}, {"id": "mem-c"}]

    row = build_comparison_row(
        origin="codex",
        query=query,
        top_k=2,
        filters={"domains": ["ai"], "secret": "must not render"},
        mem0_results=mem0,
        mem0_latency_ms=12.3,
        kontext_results=kontext,
        kontext_latency_ms=8.7,
        kontext_error="",
    )
    rendered = json.dumps(row, sort_keys=True)

    assert row["event"] == "kontext_shadow_compare"
    assert row["origin"] == "codex"
    assert row["query_hash"]
    assert "query_preview" not in row
    assert row["filters"] == {"domains": ["ai"]}
    assert row["mem0_count"] == 2
    assert row["kontext_count"] == 2
    assert row["shared_count"] == 1
    assert row["shared_any"] is True
    assert row["shared_first"] is False
    assert row["mem0_top_ids"] == ["mem-a", "mem-b"]
    assert row["kontext_top_ids"] == ["mem-b", "mem-c"]
    assert query not in rendered
    assert "private mem0 title" not in rendered
    assert "private kontext title" not in rendered
    assert "must not render" not in rendered


def test_parse_kontext_results_from_jsonrpc_text_payload():
    response = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"results": [{"id": "a"}, {"external_mem0_id": "b"}], "count": 2}),
                }
            ]
        }
    }

    rows = parse_kontext_results(response)

    assert [row.get("id") or row.get("external_mem0_id") for row in rows] == ["a", "b"]


def test_endpoint_url_keeps_direct_search_url():
    config = KontextShadowConfig(
        enabled=True,
        mcp_url="http://kontext:8080/search",
        token="token",
        log_path="/tmp/shadow.jsonl",
    )

    assert endpoint_url(config) == "http://kontext:8080/search"


def test_kontext_search_endpoint_payload_parses_rows(monkeypatch):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({"results": [{"id": "from-search"}], "count": 1}).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setattr("kontext_shadow_compare.urllib.request.urlopen", fake_urlopen)

    rows, latency_ms, error = kontext_rpc_search(
        KontextShadowConfig(
            enabled=True,
            mcp_url="http://kontext:8080/search",
            token="token",
            log_path="/tmp/shadow.jsonl",
        ),
        "side by side",
        5,
        {"domains": ["ai"], "secret": "drop"},
    )

    assert rows == [{"id": "from-search"}]
    assert latency_ms >= 0
    assert error == ""
    assert calls == [{"query": "side by side", "top_k": 5, "domains": ["ai"]}]


def test_log_kontext_shadow_search_writes_safe_row_with_fake_rpc(tmp_path):
    log_path = tmp_path / "shadow.jsonl"

    def fake_rpc(config, query, top_k, filters):
        assert query == "private live query"
        return [{"id": "same"}, {"id": "kontext-only"}], 4.2, ""

    ok = log_kontext_shadow_search(
        KontextShadowConfig(
            enabled=True,
            mcp_url="https://kontext.example/mcp/{token}",
            token="secret-token",
            log_path=str(log_path),
            timeout=1.0,
        ),
        origin="codex",
        query="private live query",
        top_k=2,
        filters={"domains": ["systems"]},
        mem0_results=[{"id": "same"}, {"id": "mem0-only"}],
        mem0_latency_ms=9.9,
        rpc_func=fake_rpc,
    )

    assert ok is True
    row = json.loads(log_path.read_text(encoding="utf-8"))
    rendered = json.dumps(row, sort_keys=True)
    assert row["shared_count"] == 1
    assert row["kontext_error"] == ""
    assert "private live query" not in rendered
    assert "secret-token" not in rendered


def test_dockerfile_packages_shadow_compare_module():
    dockerfile = Path("tools/mem0-remote-mcp/Dockerfile").read_text(encoding="utf-8")

    assert "kontext_shadow_compare.py" in dockerfile


def test_compose_attaches_memory_mcp_to_kontext_network():
    compose = Path("tools/mem0-remote-mcp/docker-compose.yaml").read_text(encoding="utf-8")

    assert "kontext_network" in compose
    assert "KONTEXT_DOCKER_NETWORK" in compose


def test_compose_enables_sanitized_kontext_write_audit_without_embedding_tokens():
    compose = Path("tools/mem0-remote-mcp/docker-compose.yaml").read_text(encoding="utf-8")

    assert "KONTEXT_WRITE_AUDIT_ENABLED" in compose
    assert "http://kontext:8080/api/v2/mcp/{token}" in compose
    assert "KONTEXT_WRITE_AUDIT_LOG" in compose
    assert "KONTEXT_WRITE_AUDIT_MCP_TOKEN" not in compose
