from __future__ import annotations

import json
import os

import psycopg

from kontext_v2.mirror_sync import run_live_snapshot_sync
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


class FakeSnapshotClient:
    def get_all(self):
        return {
            "results": [
                {
                    "id": "sync-memory-1",
                    "memory": "sync raw memory one",
                    "metadata": {
                        "domains": ["ai", "systems"],
                        "memory_type": "project_state",
                        "current_status": "test_fixture",
                        "memory_tier": "active",
                    },
                },
                {
                    "id": "sync-memory-2",
                    "memory": "sync raw memory two",
                    "metadata": {
                        "domains": ["workflow"],
                        "memory_type": "preference",
                        "current_status": "test_fixture",
                        "memory_tier": "active",
                    },
                },
            ]
        }


def _repo():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    conn = psycopg.connect(database_url)
    apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memories WHERE external_mem0_id = ANY(%s)", (["sync-memory-1", "sync-memory-2"],))
        cur.execute("DELETE FROM mirror_sync_runs WHERE source = %s", ("test_mem0",))
    conn.commit()
    return conn, KontextRepository(conn)


def test_live_snapshot_sync_dry_run_records_aggregate_status_without_writes():
    conn, repo = _repo()
    try:
        payload = run_live_snapshot_sync(repo, FakeSnapshotClient(), cap=1, dry_run=True, source="test_mem0")

        assert payload["ok"] is True
        assert payload["dry_run"] is True
        assert payload["processed_rows"] == 1
        assert payload["source_rows_seen"] == 2
        assert payload["report"] == {"created": 1, "updated": 0, "unchanged": 0, "skipped": 0}
        assert repo.fetch_by_external_id("sync-memory-1") is None

        latest = repo.latest_mirror_sync_run(source="test_mem0")
        assert latest is not None
        assert latest["status"] == "ok"
        assert latest["mode"] == "live_snapshot"
        assert latest["dry_run"] is True
        assert latest["rows_seen"] == 1
        assert latest["metadata"]["cap"] == 1
        assert "sync raw memory" not in json.dumps(latest, default=str)
    finally:
        conn.close()


def test_live_snapshot_sync_apply_imports_capped_rows_and_status_mentions_latest_sync():
    conn, repo = _repo()
    try:
        payload = run_live_snapshot_sync(repo, FakeSnapshotClient(), cap=2, dry_run=False, source="test_mem0")

        assert payload["ok"] is True
        assert payload["dry_run"] is False
        assert payload["processed_rows"] == 2
        assert payload["report"] == {"created": 2, "updated": 0, "unchanged": 0, "skipped": 0}
        assert repo.fetch_by_external_id("sync-memory-1") is not None
        assert repo.fetch_by_external_id("sync-memory-2") is not None

        latest = repo.latest_mirror_sync_run(source="test_mem0")
        assert latest is not None
        assert latest["dry_run"] is False
        assert latest["created"] == 2
        assert latest["updated"] == 0
    finally:
        conn.close()