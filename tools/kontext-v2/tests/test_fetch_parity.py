import json
import os
from pathlib import Path

import psycopg

from kontext_v2.importer import import_mem0_export
from kontext_v2.mcp_server import fetch_tool, ingestion_status_tool
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "mem0_sanitized_export.json"


def test_fetch_and_status_are_mem0_compatible():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-ai-architecture-1", "mem-vocality-1"],),
            )
        conn.commit()
        repo = KontextRepository(conn)
        import_mem0_export(repo, payload)
        fetched = fetch_tool(repo, "mem-ai-architecture-1")
        status = ingestion_status_tool(repo, recent_limit=5)

    assert fetched["id"] == "mem-ai-architecture-1"
    assert fetched["metadata"]["memory_type"] == "project_state"
    assert status["ok"] is True
    assert status["service"] == "kontext-v2"
    assert status["mirror"]["memories"] >= 2
