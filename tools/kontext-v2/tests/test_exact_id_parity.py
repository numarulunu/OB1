from __future__ import annotations

import json
import os

import psycopg

from kontext_v2.models import MemoryRecord
from kontext_v2.parity import compare_exact_id_freshness
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


class FakeMem0FetchClient:
    def __init__(self):
        self.rows = {
            "fresh-memory-1": {
                "id": "fresh-memory-1",
                "memory": "fresh raw memory text",
                "metadata": {
                    "domains": ["ai"],
                    "memory_type": "project_state",
                    "current_status": "test_fixture",
                    "memory_tier": "active",
                },
                "agent_id": "volatile-agent-id",
                "score": 0.123,
                "updated_at": "volatile timestamp",
            },
            "stale-memory-1": {
                "id": "stale-memory-1",
                "memory": "new raw memory text",
                "metadata": {
                    "domains": ["systems"],
                    "memory_type": "system_state",
                    "current_status": "test_fixture",
                    "memory_tier": "active",
                },
            },
        }

    def fetch(self, memory_id: str):
        return self.rows.get(memory_id, {"ok": False, "error": "not_found", "id": memory_id})


def test_exact_id_freshness_compares_source_hashes_without_raw_memory_text():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    client = FakeMem0FetchClient()
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memories WHERE external_mem0_id = ANY(%s)", (["fresh-memory-1", "stale-memory-1"],))
        conn.commit()
        repo = KontextRepository(conn)
        fresh_row = client.fetch("fresh-memory-1")
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="fresh-memory-1",
                title="Fresh memory",
                text=fresh_row["memory"],
                metadata=fresh_row["metadata"],
                memory_type="project_state",
                current_status="test_fixture",
                memory_tier="active",
                signal_strength=8,
                source_hash="legacy-import-shape-hash",
            )
        )
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="stale-memory-1",
                title="Stale memory",
                text="old raw memory text",
                metadata={"domains": ["systems"], "memory_type": "system_state"},
                memory_type="system_state",
                current_status="test_fixture",
                memory_tier="active",
                signal_strength=7,
                source_hash="old-source-hash",
            )
        )

        report = compare_exact_id_freshness(
            repo,
            client.fetch,
            ["fresh-memory-1", "stale-memory-1", "missing-memory-1"],
        )

    rows = {row["id"]: row for row in report["results"]}
    assert report["checked"] == 3
    assert rows["fresh-memory-1"]["fresh"] is True
    assert rows["fresh-memory-1"]["source_hash_match"] is False
    assert rows["fresh-memory-1"]["canonical_hash_match"] is True
    assert rows["stale-memory-1"]["fresh"] is False
    assert rows["stale-memory-1"]["canonical_hash_match"] is False
    assert rows["missing-memory-1"]["mem0_found"] is False
    assert rows["missing-memory-1"]["kontext_found"] is False

    serialized = json.dumps(report, sort_keys=True)
    assert "fresh raw memory text" not in serialized
    assert "new raw memory text" not in serialized
    assert "old raw memory text" not in serialized