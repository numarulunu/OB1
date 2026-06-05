from __future__ import annotations

import hashlib
import json
import os

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from kontext_v2.http_api import build_app
from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


def _rpc(client: TestClient, token: str, method: str, params: dict | None = None, request_id: int = 1):
    return client.post(
        f"/mcp/{token}",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def test_mcp_search_records_sanitized_shadow_trace():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    query = "private mcp trace query"
    raw_text = "raw mcp trace memory text private mcp trace query should not be logged"
    token = "codex-shadow-token"

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memories WHERE external_mem0_id = %s", ("shadow-memory-1",))
            cur.execute("DELETE FROM retrieval_queries WHERE profile = %s", ("codex",))
        conn.commit()
        KontextRepository(conn).upsert_memory(
            MemoryRecord(
                external_mem0_id="shadow-memory-1",
                title="Kontext MCP trace",
                text=raw_text,
                metadata={
                    "domains": ["ai", "systems"],
                    "memory_type": "project_state",
                    "current_status": "test_fixture",
                    "memory_tier": "active",
                },
                memory_type="project_state",
                current_status="test_fixture",
                memory_tier="active",
                signal_strength=9,
                source_hash="shadow-memory-1-hash",
            )
        )

    app = build_app(database_url=database_url, mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token})
    client = TestClient(app)
    response = _rpc(
        client,
        token,
        "tools/call",
        {"name": "search", "arguments": {"query": query, "top_k": 3, "domains": ["ai"]}},
    )
    assert response.status_code == 200

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT query_hash, origin, service, profile, filters, result_external_ids, latency_ms
            FROM retrieval_queries
            WHERE profile = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            ("codex",),
        ).fetchone()

    assert row is not None
    assert row["query_hash"] == hashlib.sha256(query.encode("utf-8")).hexdigest()
    assert row["origin"] == "mcp"
    assert row["service"] == "kontext"
    assert row["profile"] == "codex"
    assert row["filters"]["domains"] == ["ai"]
    assert row["filters"]["top_k"] == 3
    assert row["filters"]["query_features"]["char_len"] == len(query)
    assert "private" not in json.dumps(row["filters"], sort_keys=True).lower()
    assert row["result_external_ids"][0] == "shadow-memory-1"
    assert row["latency_ms"] >= 0

    serialized = json.dumps(dict(row), default=str, sort_keys=True)
    assert query not in serialized
    assert raw_text not in serialized
