from __future__ import annotations

import json
import os

import psycopg
from fastapi.testclient import TestClient

from kontext_v2.http_api import build_app
from kontext_v2.mcp_bridge import build_mcp_profiles, profile_names
from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


def _seed_memory() -> None:
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mcp-bridge-memory-1"],),
            )
        conn.commit()
        KontextRepository(conn).upsert_memory(
            MemoryRecord(
                external_mem0_id="mcp-bridge-memory-1",
                title="Private Kontext MCP bridge",
                text="Private raw memory text for the authenticated MCP fetch path.",
                metadata={
                    "domains": ["ai", "systems", "infrastructure"],
                    "memory_type": "project_state",
                    "current_status": "test_fixture",
                    "memory_tier": "active",
                    "signal_strength": 9,
                },
                memory_type="project_state",
                current_status="test_fixture",
                memory_tier="active",
                signal_strength=9,
                source_hash="mcp-bridge-memory-1-hash",
            )
        )


def _rpc(client: TestClient, token: str, method: str, params: dict | None = None, request_id: int = 1):
    return client.post(
        f"/mcp/{token}",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def _rpc_header(client: TestClient, token: str, method: str, params: dict | None = None, request_id: int = 1):
    return client.post(
        "/mcp",
        headers={"authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def test_mcp_profiles_use_existing_tokens_without_exposing_them():
    profiles = build_mcp_profiles(
        {
            "MCP_CODEX_TOKEN": "codex-private-token",
            "MCP_CLAUDE_TOKEN": "claude-private-token",
        }
    )

    assert profile_names(profiles) == ["claude", "codex"]
    rendered = json.dumps({"profiles": profile_names(profiles)})
    assert "private-token" not in rendered


def test_mcp_profile_tokens_must_be_unique():
    try:
        build_mcp_profiles(
            {
                "MCP_CODEX_TOKEN": "same-token",
                "MCP_CLAUDE_TOKEN": "same-token",
            }
        )
    except RuntimeError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("expected duplicate token failure")


def test_private_mcp_jsonrpc_requires_token_and_supports_tools():
    _seed_memory()
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            mcp_env={"MCP_CODEX_TOKEN": "codex-token"},
        )
    )

    assert client.post("/mcp/bad-token", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 404

    initialized = _rpc(client, "codex-token", "initialize", {"protocolVersion": "2024-11-05"})
    tools = _rpc(client, "codex-token", "tools/list")
    search = _rpc(
        client,
        "codex-token",
        "tools/call",
        {
            "name": "search",
            "arguments": {
                "query": "private authenticated Kontext MCP bridge",
                "top_k": 5,
                "current_statuses": ["test_fixture"],
            },
        },
    )

    assert initialized.status_code == 200
    assert initialized.json()["result"]["capabilities"] == {"tools": {}}
    assert tools.status_code == 200
    assert {tool["name"] for tool in tools.json()["result"]["tools"]} >= {"search", "fetch", "ingestion_status"}
    assert search.status_code == 200
    search_payload = json.loads(search.json()["result"]["content"][0]["text"])
    assert search_payload["results"][0]["id"] == "mcp-bridge-memory-1"
    assert "Private raw memory text" not in search.json()["result"]["content"][0]["text"]


def test_private_mcp_jsonrpc_accepts_authorization_header():
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            mcp_env={"MCP_CODEX_TOKEN": "codex-token"},
        )
    )

    missing = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    invalid = client.post(
        "/mcp",
        headers={"authorization": "Bearer bad-token"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    probe = client.get("/mcp", headers={"authorization": "Bearer codex-token"})
    event_stream_probe = client.get(
        "/mcp",
        headers={"authorization": "Bearer codex-token", "accept": "text/event-stream"},
    )
    tools = _rpc_header(client, "codex-token", "tools/list")

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert probe.status_code == 200
    assert event_stream_probe.status_code == 405
    assert tools.status_code == 200
    assert {tool["name"] for tool in tools.json()["result"]["tools"]} >= {"search", "fetch", "ingestion_status"}


def test_mcp_route_supports_claude_remote_connector_preflight_and_streamable_get_probe():
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            mcp_env={"MCP_CLAUDE_TOKEN": "claude-token"},
        )
    )

    preflight = client.options(
        "/mcp/claude-token",
        headers={
            "Origin": "https://claude.ai",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,accept",
        },
    )
    event_stream_get = client.get("/mcp/claude-token", headers={"Accept": "text/event-stream"})
    json_probe = client.get("/mcp/claude-token", headers={"Accept": "application/json"})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://claude.ai"
    assert event_stream_get.status_code == 405
    assert "POST" in event_stream_get.headers["allow"]
    assert json_probe.status_code == 200
    assert json_probe.json()["service"] == "kontext-v2"
