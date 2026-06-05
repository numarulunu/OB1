from __future__ import annotations

import json
import os

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from kontext_v2.http_api import build_app
from kontext_v2.schema import apply_schema, list_tables


def _rpc(client: TestClient, token: str, method: str, params: dict | None = None, request_id: int = 1):
    return client.post(
        f"/mcp/{token}",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def _tool_payload(response):
    assert response.status_code == 200
    return json.loads(response.json()["result"]["content"][0]["text"])


def _reset_tables():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memory_intake_audit WHERE origin = %s", ("kontext-v2-codex",))
            cur.execute("DELETE FROM memory_flags WHERE origin = %s", ("kontext-v2-codex",))
        conn.commit()


def test_schema_creates_intake_audit_and_flags_tables():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        tables = set(list_tables(conn))

    assert {"memory_intake_audit", "memory_flags"} <= tables


def test_ingest_exchange_dry_run_records_sanitized_intake_audit_row():
    _reset_tables()
    token = "audit-write-token"
    secret = "secret-token-456"
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            write_enabled=True,
            mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token},
        )
    )

    payload = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {
                "name": "ingest_exchange",
                "arguments": {"messages": [{"role": "user", "content": f"Kontext V2 should mirror Mem0. TOKEN={secret}"}]},
            },
        )
    )

    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"], row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT source_hash, origin, action, status, metadata
            FROM memory_intake_audit
            WHERE origin = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            ("kontext-v2-codex",),
        ).fetchone()

    assert row is not None
    assert row["source_hash"] == payload["source_hash"]
    assert row["origin"] == "kontext-v2-codex"
    assert row["action"] == "ingest_exchange"
    assert row["status"] == "dry_run"
    assert row["metadata"]["proposal_count"] == len(payload["proposals"])
    rendered = json.dumps(dict(row), default=str, sort_keys=True)
    assert secret not in rendered
    assert "Kontext V2 should mirror Mem0" not in rendered


def test_flag_memory_dry_run_records_sanitized_flag_row_and_status_counts():
    _reset_tables()
    token = "flag-write-token"
    secret = "secret-token-789"
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            write_enabled=True,
            mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token},
        )
    )

    payload = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {
                "name": "flag_memory",
                "arguments": {"id": "mem-audit-1", "flag_type": "stale", "reason": f"bad TOKEN={secret}", "confidence": 0.7},
            },
        )
    )
    status = _tool_payload(
        _rpc(client, token, "tools/call", {"name": "ingestion_status", "arguments": {"recent_limit": 5}}, request_id=2)
    )

    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"], row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT external_mem0_id, flag_type, reason_hash, confidence, origin, status, metadata
            FROM memory_flags
            WHERE origin = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            ("kontext-v2-codex",),
        ).fetchone()

    assert payload["flag"]["reason_hash"] == row["reason_hash"]
    assert row["external_mem0_id"] == "mem-audit-1"
    assert row["flag_type"] == "stale"
    assert row["confidence"] == 0.7
    assert row["status"] == "dry_run"
    assert status["intake"]["flags"] >= 1
    rendered = json.dumps(dict(row), default=str, sort_keys=True)
    assert secret not in rendered