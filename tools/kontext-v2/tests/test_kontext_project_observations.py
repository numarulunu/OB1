from __future__ import annotations

import json
import os

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from kontext_v2.http_api import build_app
from kontext_v2.project_observations import build_observation
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema, list_tables


ORIGIN = "kontext-v2-project-test"
SECRET = "secret-token-555"


def _rpc(client: TestClient, token: str, method: str, params: dict | None = None, request_id: int = 1):
    return client.post(
        f"/mcp/{token}",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def _tool_payload(response):
    assert response.status_code == 200
    return json.loads(response.json()["result"]["content"][0]["text"])


def _reset(conn: psycopg.Connection) -> None:
    apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM project_observations WHERE origin = %s", (ORIGIN,))
    conn.commit()


def _repo():
    conn = psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"], row_factory=dict_row)
    _reset(conn)
    return conn, KontextRepository(conn)


def _payload(index: int, *, title: str, file_path: str = "tools/kontext-v2/kontext_v2/project_observations.py") -> dict:
    return {
        "origin": ORIGIN,
        "project_root": "C:/Tools/OB1",
        "cwd": "C:/Tools/OB1",
        "event_type": "implementation",
        "title": f"{title} TOKEN={SECRET}",
        "summary": f"Implemented project observation slice {index}. TOKEN={SECRET}",
        "files_modified": [file_path],
        "files_read": ["docs/superpowers/plans/2026-05-12-kontext-v21-mem0-compatible-architecture.md"],
        "commands": [f"pytest test_project_observations.py TOKEN={SECRET}"],
        "tests": ["python -B -m pytest tools\\kontext-v2\\tests\\test_project_observations.py -q"],
        "decisions": ["Project search returns compact rows only."],
        "next_steps": ["Run full regression."],
        "source_hash": f"project-observation-source-{index}",
        "timestamp": f"2026-05-13T10:0{index}:00Z",
    }


def test_schema_creates_project_observations_table():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        tables = set(list_tables(conn))

    assert "project_observations" in tables


def test_project_search_returns_compact_sanitized_rows_only():
    conn, repo = _repo()
    try:
        inserted = repo.record_project_observation(_payload(1, title="Kontext project continuity parity"))

        result = repo.project_search("continuity parity", limit=5)

        assert result["count"] == 1
        row = result["rows"][0]
        assert row["id"] == inserted["id"]
        assert set(row) == {"id", "date", "type", "title", "project", "files_count", "token_estimate"}
        assert row["project"] == "OB1"
        assert row["files_count"] == 2
        rendered = json.dumps(result, sort_keys=True)
        assert SECRET not in rendered
        assert "summary" not in rendered
        assert "commands" not in rendered
    finally:
        conn.close()


def test_project_fetch_returns_sanitized_details_by_exact_id():
    conn, repo = _repo()
    try:
        inserted = repo.record_project_observation(_payload(1, title="Fetch exact observation"))

        result = repo.project_fetch(inserted["id"])

        assert result["found"] is True
        row = result["row"]
        assert row["id"] == inserted["id"]
        assert row["summary"].startswith("Implemented project observation slice 1")
        assert row["files_modified"] == ["tools/kontext-v2/kontext_v2/project_observations.py"]
        assert SECRET not in json.dumps(result, sort_keys=True)
    finally:
        conn.close()


def test_project_observation_redacts_credential_markers_and_long_hex_values():
    long_hex = "a" * 64
    pem_marker = "-----BEGIN " + "PRIVATE KEY" + "-----"
    payload = _payload(9, title="Synthetic credential marker")
    payload["summary"] = (
        "Observed synthetic credential marker "
        f"api_key=placeholder-value {pem_marker} {long_hex} "
        + ("x" * 2500)
    )
    payload["commands"] = [f"tool --token placeholder-value --hash {long_hex}"]
    payload["next_steps"] = [f"Remove synthetic marker {pem_marker}"]

    row = build_observation(payload)
    rendered = json.dumps(row, sort_keys=True)

    assert row["summary"]
    assert len(row["summary"]) <= 2000
    assert "placeholder-value" not in rendered
    assert pem_marker not in rendered
    assert long_hex not in rendered
    assert "<redacted" in rendered.lower()


def test_project_timeline_returns_compact_chronological_context():
    conn, repo = _repo()
    try:
        first = repo.record_project_observation(_payload(1, title="First project event"))
        anchor = repo.record_project_observation(_payload(2, title="Anchor project event"))
        third = repo.record_project_observation(_payload(3, title="Third project event"))

        result = repo.project_timeline(anchor_id=anchor["id"], before=1, after=1)

        assert result["anchor_id"] == anchor["id"]
        assert [row["id"] for row in result["rows"]] == [first["id"], anchor["id"], third["id"]]
        assert all("summary" not in row for row in result["rows"])
        assert SECRET not in json.dumps(result, sort_keys=True)
    finally:
        conn.close()


def test_project_file_context_recommends_only_when_no_prior_observations():
    conn, repo = _repo()
    try:
        repo.record_project_observation(_payload(1, title="File context observation"))

        result = repo.project_file_context("tools\\kontext-v2\\kontext_v2\\project_observations.py", limit=10)
        missing = repo.project_file_context("tools/kontext-v2/kontext_v2/missing.py", limit=10)

        assert result["file_path"] == "tools/kontext-v2/kontext_v2/project_observations.py"
        assert result["recommend_full_file_read"] is False
        assert result["titles"]
        assert missing["recommend_full_file_read"] is True
    finally:
        conn.close()


def test_mcp_project_tools_are_repo_backed_and_sanitized():
    conn, repo = _repo()
    try:
        inserted = repo.record_project_observation(_payload(1, title="MCP backed project search"))
    finally:
        conn.close()

    token = "project-read-token"
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            write_enabled=False,
            mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token},
        )
    )

    search = _tool_payload(
        _rpc(client, token, "tools/call", {"name": "project_search", "arguments": {"query": "MCP backed", "limit": 5}})
    )
    fetch = _tool_payload(
        _rpc(client, token, "tools/call", {"name": "project_fetch", "arguments": {"id": inserted["id"]}}, request_id=2)
    )

    assert search["rows"][0]["id"] == inserted["id"]
    assert fetch["found"] is True
    assert SECRET not in json.dumps([search, fetch], sort_keys=True)
