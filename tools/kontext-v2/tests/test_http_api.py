from __future__ import annotations

import json
import os
from contextlib import contextmanager

import psycopg
from fastapi.testclient import TestClient

import kontext_v2.http_api as http_api
from kontext_v2.http_api import build_app
from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


def test_http_api_exposes_safe_health_tools_and_search():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["http-api-memory-1"],),
            )
        conn.commit()
        KontextRepository(conn).upsert_memory(
            MemoryRecord(
                external_mem0_id="http-api-memory-1",
                title="Kontext V2 API bridge",
                text="Kontext V2 exposes Mem0-compatible mirror search through the existing Kontext app.",
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
                source_hash="http-api-memory-1-hash",
            )
        )

    client = TestClient(build_app(database_url))

    health = client.get("/health")
    tools = client.get("/tools")
    search = client.post(
        "/search",
        json={
            "query": "existing Kontext app Mem0-compatible mirror search",
            "top_k": 5,
            "current_statuses": ["test_fixture"],
        },
    )

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["service"] == "kontext-v2"
    assert "Kontext V2 exposes" not in health.text
    assert tools.status_code == 200
    assert {tool["name"] for tool in tools.json()["tools"]} >= {"search", "fetch", "ingestion_status"}
    assert search.status_code == 200
    assert search.json()["results"][0]["id"] == "http-api-memory-1"


def test_http_api_exposes_sanitized_dry_run_write_audit():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    secret = "secret-token-audit-999"
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        repo.record_intake_audit(
            source_hash="dry-run-audit-source-hash-1",
            origin="kontext-v2-codex",
            action="save",
            status="dry_run",
            metadata={"tool": "save", "proposal_count": 1, "content": f"TOKEN={secret}"},
        )

    client = TestClient(
        build_app(database_url, dry_run_write_enabled=True, mcp_env={"KONTEXT_MCP_CODEX_TOKEN": "read-token"})
    )
    response = client.get("/dry-run-writes", headers={"authorization": "Bearer read-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] >= 1
    assert payload["summary"]["by_action"]["save"] >= 1
    assert payload["items"][0]["action"] == "save"
    assert payload["items"][0]["status"] == "dry_run"
    assert payload["items"][0]["metadata"] == {"tool": "save", "proposal_count": 1}
    assert secret not in response.text


def test_http_api_can_expose_dry_run_write_tools_without_enabling_category_writes():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    client = TestClient(build_app(database_url, dry_run_write_enabled=True))

    tools = client.get("/tools")
    categories = client.get("/categories")

    names = {tool["name"] for tool in tools.json()["tools"]}
    assert tools.status_code == 200
    assert {"save", "update", "delete", "extract_memories", "ingest_exchange", "submit_memory_override", "flag_memory"} <= names
    assert {"upsert_category", "assign_category", "unassign_category", "delete_category"}.isdisjoint(names)
    assert categories.status_code == 200
    assert categories.json()["category_write_enabled"] is False


def test_http_api_dashboard_snapshot_requires_configured_mcp_token(monkeypatch):
    class FakeRepo:
        pass

    def fake_build_dashboard_snapshot(repo):
        assert isinstance(repo, FakeRepo)
        return {
            "data_source": "kontext_v2",
            "meta": {"data_source": "kontext_v2", "total": 11752, "loaded": 1},
            "entries": [{"id": "real-1", "title": "Real Kontext row"}],
            "categories": [{"slug": "systems", "count": 1}],
            "events": [],
            "activity": [],
        }

    @contextmanager
    def fake_repo(self):
        yield FakeRepo()

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    monkeypatch.setattr(http_api, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    client = TestClient(build_app("postgresql://unused", mcp_env={"KONTEXT_MCP_CODEX_TOKEN": "read-token"}))

    response = client.get("/dashboard/snapshot")

    assert response.status_code == 401

    response = client.get("/dashboard/snapshot", headers={"authorization": "Bearer read-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["meta"]["data_source"] == "kontext_v2"
    assert payload["meta"]["total"] == 11752
    assert payload["entries"][0]["id"] == "real-1"
    assert "Core identity" not in response.text
    assert "Mem0 synced" not in response.text


def test_http_api_dry_run_writes_requires_configured_mcp_token(monkeypatch):
    class FakeRepo:
        def list_dry_run_write_audit(self, limit=30):
            return {"count": 1, "summary": {"by_action": {"save": 1}}, "items": []}

    @contextmanager
    def fake_repo(self):
        yield FakeRepo()

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    client = TestClient(build_app("postgresql://unused", mcp_env={"KONTEXT_MCP_CODEX_TOKEN": "read-token"}))

    unauthenticated = client.get("/dry-run-writes")
    response = client.get("/dry-run-writes", headers={"authorization": "Bearer read-token"})

    assert unauthenticated.status_code == 401
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_http_api_sync_status_reports_unknown_with_checked_at(monkeypatch):
    class FakeRepo:
        def latest_mirror_sync_run(self):
            return None

    @contextmanager
    def fake_repo(self):
        yield FakeRepo()

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    client = TestClient(build_app("postgresql://unused"))

    response = client.get("/sync/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["checked_at"]
    assert payload["sync"]["latest"]["status"] == "unknown"
    assert "never_synced" not in response.text


def test_http_api_fetch_requires_configured_mcp_token(monkeypatch):
    class FakeRepo:
        pass

    def fake_fetch_tool(repo, memory_id):
        assert isinstance(repo, FakeRepo)
        return {
            "ok": True,
            "id": memory_id,
            "memory": "Private raw memory text only for authenticated callers.",
            "metadata": {"domains": ["ai"]},
        }

    @contextmanager
    def fake_repo(self):
        yield FakeRepo()

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    monkeypatch.setattr(http_api, "fetch_tool", fake_fetch_tool)
    client = TestClient(build_app("postgresql://unused", mcp_env={"KONTEXT_MCP_CODEX_TOKEN": "read-token"}))

    unauthenticated = client.get("/fetch/memory-1")
    authenticated = client.get("/fetch/memory-1", headers={"x-kontext-token": "read-token"})

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["id"] == "memory-1"


def test_http_api_memory_preview_requires_token_and_returns_raw_body_only_when_authenticated(monkeypatch):
    class FakeRepo:
        pass

    def fake_fetch_tool(repo, memory_id):
        assert isinstance(repo, FakeRepo)
        return {
            "ok": True,
            "id": memory_id,
            "memory": "Private preview body only for authenticated callers.",
            "metadata": {"domains": ["ai"]},
        }

    @contextmanager
    def fake_repo(self):
        yield FakeRepo()

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    monkeypatch.setattr(http_api, "fetch_tool", fake_fetch_tool)
    client = TestClient(build_app("postgresql://unused", mcp_env={"KONTEXT_MCP_CODEX_TOKEN": "read-token"}))

    unauthenticated = client.get("/memory/memory-1/preview")
    authenticated = client.get("/memory/memory-1/preview", headers={"authorization": "Bearer read-token"})

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    payload = authenticated.json()
    assert payload["id"] == "memory-1"
    assert payload["body"] == "Private preview body only for authenticated callers."
    assert payload["body_len"] == len(payload["body"])
    assert len(payload["body_hash"]) == 8


def test_http_api_accepts_project_observations_and_dedupes_source_hash():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    token = "http-project-token"
    payload = {
        "project_root": "C:/Tools/OB1",
        "cwd": "C:/Tools/OB1",
        "event_type": "implementation",
        "title": "Project observation endpoint TOKEN=secret-token-777",
        "summary": "Captured project observation safely TOKEN=secret-token-777",
        "files_modified": ["tools/kontext-v2/kontext_v2/http_api.py"],
        "source_hash": "http-project-observation-hash-1",
    }

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM project_observations WHERE source_hash = %s", (payload["source_hash"],))
        conn.commit()

    client = TestClient(build_app(database_url, mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token}))

    first = client.post(f"/project-observation/{token}", json=payload)
    second = client.post(f"/project-observation/{token}", json=payload)

    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert first.json()["appended"] is True
    assert second.status_code == 200
    assert second.json()["appended"] is False
    assert first.json()["id"] == second.json()["id"]
    assert "secret-token-777" not in first.text


def test_http_api_accepts_hook_heartbeats_and_exposes_maintenance_status(monkeypatch):
    token = "http-hook-token"

    class FakeRepo:
        def record_hook_heartbeat(self, payload):
            assert payload["origin"] == "codex"
            assert payload["hook_type"] == "session_start"
            assert "secret-token-777" not in json.dumps(payload)
            return {"ok": True, "id": 1, "origin": "codex", "hook_type": "session_start"}

        def maintenance_status(self):
            return {
                "due": False,
                "pending_flags": 1,
                "flag_types": {"stale_candidate": 1},
                "requires_approval": True,
                "reasons": [],
                "reminder": "",
            }

    @contextmanager
    def fake_repo(self):
        yield FakeRepo()

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    client = TestClient(build_app("postgresql://unused", mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token}))
    heartbeat = client.post(
        f"/hook-heartbeat/{token}",
        json={"hook_type": "session_start", "source": "safe-source", "marker": "safe-marker"},
    )
    maintenance = client.get(f"/maintenance-status/{token}")

    assert heartbeat.status_code == 200
    assert heartbeat.json()["ok"] is True
    assert heartbeat.json()["origin"] == "codex"
    assert heartbeat.json()["hook_type"] == "session_start"
    assert maintenance.status_code == 200
    assert maintenance.json()["ok"] is True
    assert maintenance.json()["maintenance"]["pending_flags"] >= 1
    assert maintenance.json()["maintenance"]["flag_types"]["stale_candidate"] >= 1


def test_http_api_search_explain_returns_sanitized_rank_breakdown(monkeypatch):
    private_text = "private Kontext raw memory text should not appear in explain responses"

    class FakeRepo:
        def list_memory_rows(self, limit=1000):
            return [
                {
                    "external_mem0_id": "http-explain-memory-1",
                    "title": "Kontext memory architecture",
                    "text": private_text,
                    "rank": 0.5,
                    "metadata": {
                        "domains": ["ai", "systems", "infrastructure"],
                        "memory_type": "project_state",
                        "current_status": "active",
                        "memory_tier": "active",
                        "signal_strength": 9,
                    },
                    "memory_type": "project_state",
                    "current_status": "active",
                    "memory_tier": "active",
                    "signal_strength": 9,
                    "updated_at": "2026-05-18T00:00:00Z",
                }
            ]

    @contextmanager
    def fake_repo(self):
        yield FakeRepo()

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    client = TestClient(build_app("postgresql://unused", mcp_env={"KONTEXT_MCP_CODEX_TOKEN": "read-token"}))

    unauthenticated = client.post("/search/explain", json={"query": "AI memory architecture", "top_k": 1})
    response = client.post(
        "/search/explain",
        json={"query": "AI memory architecture", "top_k": 1},
        headers={"authorization": "Bearer read-token"},
    )

    assert unauthenticated.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    result = payload["results"][0]
    assert result["id"] == "http-explain-memory-1"
    assert "memory" not in result
    assert "explanation" in result
    assert result["explanation"]["memory_type"] == "project_state"
    assert result["explanation"]["total"] > 0
    assert private_text not in json.dumps(payload)


def test_http_api_search_forwards_state_namespace_when_routing_enabled(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_NAMESPACE", "live")

    class FakeRepo:
        def __init__(self):
            self.state_namespaces: list[str] = []

        def search_current_state_facts(self, query, *, namespace="live", top_k=5):
            self.state_namespaces.append(namespace)
            return []

        def list_memory_rows(self, limit=1000):
            return [
                {
                    "external_mem0_id": "http-routing-memory-1",
                    "title": "Current OB1 status",
                    "text": "Fallback memory row.",
                    "metadata": {"domains": ["ai", "systems"], "current_status": "active"},
                    "memory_type": "project_state",
                    "current_status": "active",
                    "memory_tier": "active",
                    "signal_strength": 8,
                }
            ]

    fake_repo_instance = FakeRepo()

    @contextmanager
    def fake_repo(self):
        yield fake_repo_instance

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    client = TestClient(build_app("postgresql://unused"))

    response = client.post(
        "/search",
        json={"query": "What is the current OB1 status?", "top_k": 1, "namespace": "Benchmark:BEAM"},
    )

    assert response.status_code == 200
    assert fake_repo_instance.state_namespaces == ["benchmark:beam"]


def test_http_api_search_and_fetch_are_byte_equal_with_state_flags_off(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "0")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "0")
    token = "read-token"

    class FakeRepo:
        def search_current_state_facts(self, *_args, **_kwargs):
            raise AssertionError("routing-off search must not query state projection")

        def list_memory_rows(self, limit=1000):
            return [
                {
                    "external_mem0_id": "http-parity-memory-1",
                    "title": "Routing off parity",
                    "text": "Routing off parity row.",
                    "metadata": {"domains": ["ai", "systems"], "current_status": "active"},
                    "memory_type": "project_state",
                    "current_status": "active",
                    "memory_tier": "active",
                    "signal_strength": 8,
                }
            ]

        def fetch_by_external_id(self, memory_id):
            return MemoryRecord(
                external_mem0_id=memory_id,
                title="Routing off parity",
                text="Routing off parity row.",
                metadata={"domains": ["ai", "systems"]},
                memory_type="project_state",
                current_status="active",
                memory_tier="active",
                signal_strength=8,
                source_hash="http-parity-memory-1-hash",
            )

    @contextmanager
    def fake_repo(self):
        yield FakeRepo()

    monkeypatch.setattr("kontext_v2.http_api.KontextV2Api.repo", fake_repo)
    client = TestClient(build_app("postgresql://unused", mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token}))

    first_search = client.post("/search", json={"query": "current routing off parity", "top_k": 1})
    second_search = client.post("/search", json={"query": "current routing off parity", "top_k": 1})
    first_fetch = client.get("/fetch/http-parity-memory-1", headers={"authorization": f"Bearer {token}"})
    second_fetch = client.get("/fetch/http-parity-memory-1", headers={"authorization": f"Bearer {token}"})

    assert first_search.status_code == 200
    assert first_fetch.status_code == 200
    assert first_search.content == second_search.content
    assert first_fetch.content == second_fetch.content
