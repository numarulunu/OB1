from __future__ import annotations

from argparse import Namespace

from kontext_v2 import sync_cli
from kontext_v2 import mirror_sync
from kontext_v2.importer import ImportReport
from kontext_v2.models import MemoryRecord


def test_live_snapshot_sync_can_refresh_existing_ids_missing_from_public_snapshot(monkeypatch):
    captured = {}

    class FakeClient:
        def get_all(self):
            return {"results": [{"id": "recent-id", "memory": "recent"}]}

        def fetch(self, memory_id):
            captured.setdefault("fetched", []).append(memory_id)
            return {"id": memory_id, "memory": "refreshed"}

    class FakeRepo:
        def list_memory_rows(self, limit):
            captured["refresh_limit"] = limit
            return [{"external_mem0_id": "stale-id"}]

        def fetch_by_external_id(self, external_id):
            return MemoryRecord(
                external_mem0_id=external_id,
                title="",
                text="old",
                metadata={},
                memory_type="",
                current_status="active",
                memory_tier="active",
                signal_strength=None,
                source_hash="old-source",
            )

        def record_mirror_sync_run(self, **kwargs):
            captured["sync_metadata"] = kwargs["metadata"]
            return {"id": 1}

    def fake_preview(repo, payload):
        captured["payload_ids"] = [row["id"] for row in payload["results"]]
        return ImportReport(created=0, updated=1, unchanged=1, skipped=0)

    monkeypatch.setattr(mirror_sync, "preview_mem0_export", fake_preview)

    payload = mirror_sync.run_live_snapshot_sync(
        FakeRepo(),
        FakeClient(),
        cap=25,
        dry_run=True,
        refresh_existing_limit=1,
    )

    assert captured["refresh_limit"] == 1
    assert captured["fetched"] == ["stale-id"]
    assert captured["payload_ids"] == ["recent-id", "stale-id"]
    assert captured["sync_metadata"]["refresh_existing_limit"] == 1
    assert captured["sync_metadata"]["refreshed_existing_rows"] == 1
    assert captured["sync_metadata"]["refreshed_stale_rows"] == 1
    assert payload["processed_rows"] == 2


def test_live_snapshot_sync_uses_refresh_existing_offset(monkeypatch):
    captured = {}

    class FakeClient:
        def get_all(self):
            return {"results": []}

        def fetch(self, memory_id):
            return {"id": memory_id, "memory": "refreshed"}

    class FakeRepo:
        def list_memory_rows(self, limit, offset=0):
            captured["refresh_limit"] = limit
            captured["refresh_offset"] = offset
            return [{"external_mem0_id": "window-id"}]

        def fetch_by_external_id(self, external_id):
            return MemoryRecord(
                external_mem0_id=external_id,
                title="",
                text="old",
                metadata={},
                memory_type="",
                current_status="active",
                memory_tier="active",
                signal_strength=None,
                source_hash="old-source",
            )

        def record_mirror_sync_run(self, **kwargs):
            captured["sync_metadata"] = kwargs["metadata"]
            return {"id": 1}

    monkeypatch.setattr(mirror_sync, "preview_mem0_export", lambda repo, payload: ImportReport(created=0, updated=1, unchanged=0, skipped=0))

    mirror_sync.run_live_snapshot_sync(
        FakeRepo(),
        FakeClient(),
        dry_run=True,
        refresh_existing_limit=1,
        refresh_existing_offset=7,
    )

    assert captured["refresh_limit"] == 1
    assert captured["refresh_offset"] == 7
    assert captured["sync_metadata"]["refresh_existing_offset"] == 7


def test_live_snapshot_sync_skips_exact_refresh_rows_that_are_canonically_fresh(monkeypatch):
    captured = {}

    class FakeClient:
        def get_all(self):
            return {"results": []}

        def fetch(self, memory_id):
            if memory_id == "fresh-id":
                return {
                    "id": "fresh-id",
                    "memory": "same text",
                    "metadata": {"memory_type": "project_state", "memory_tier": "active", "audit_only": "changed"},
                }
            return {
                "id": "stale-id",
                "memory": "new text",
                "metadata": {"memory_type": "project_state", "memory_tier": "active"},
            }

    class FakeRepo:
        def list_memory_rows(self, limit):
            return [{"external_mem0_id": "fresh-id"}, {"external_mem0_id": "stale-id"}]

        def fetch_by_external_id(self, external_id):
            text = "same text" if external_id == "fresh-id" else "old text"
            return MemoryRecord(
                external_mem0_id=external_id,
                title="",
                text=text,
                metadata={"memory_type": "project_state", "memory_tier": "active"},
                memory_type="project_state",
                current_status="active",
                memory_tier="active",
                signal_strength=None,
                source_hash="old-source",
            )

        def record_mirror_sync_run(self, **kwargs):
            captured["sync_metadata"] = kwargs["metadata"]
            return {"id": 1}

    def fake_preview(repo, payload):
        captured["payload_ids"] = [row["id"] for row in payload["results"]]
        return ImportReport(created=0, updated=1, unchanged=0, skipped=0)

    monkeypatch.setattr(mirror_sync, "preview_mem0_export", fake_preview)

    mirror_sync.run_live_snapshot_sync(
        FakeRepo(),
        FakeClient(),
        dry_run=True,
        refresh_existing_limit=2,
    )

    assert captured["payload_ids"] == ["stale-id"]
    assert captured["sync_metadata"]["refreshed_existing_rows"] == 2
    assert captured["sync_metadata"]["refreshed_stale_rows"] == 1


def test_sync_cli_passes_mem0_lexical_database_url_to_client(monkeypatch):
    created = {}

    class FakeClient:
        def __init__(self, *, base_url, api_key, user_id, lexical_database_url):
            created["client"] = {
                "base_url": base_url,
                "api_key": api_key,
                "user_id": user_id,
                "lexical_database_url": lexical_database_url,
            }

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(sync_cli, "Mem0ApiClient", FakeClient)
    monkeypatch.setattr(sync_cli.psycopg, "connect", lambda url: FakeConnection())
    monkeypatch.setattr(sync_cli, "apply_schema", lambda conn: None)
    monkeypatch.setattr(sync_cli, "KontextRepository", lambda conn: "repo")
    monkeypatch.setattr(
        sync_cli,
        "run_live_snapshot_sync",
        lambda repo, client, cap, dry_run, source, refresh_existing_limit, refresh_existing_offset, refresh_ids: {
            "repo": repo,
            "cap": cap,
            "dry_run": dry_run,
            "source": source,
            "refresh_existing_limit": refresh_existing_limit,
            "refresh_existing_offset": refresh_existing_offset,
            "refresh_ids": refresh_ids,
        },
    )

    payload = sync_cli.run(
        Namespace(
            database_url="postgres://kontext-db",
            mem0_base_url="http://mem0",
            mem0_api_key="secret-key",
            mem0_user_id="ionut",
            mem0_lexical_database_url="postgres://mem0-db",
            cap=500,
            refresh_existing_limit=250,
            refresh_existing_offset=50,
            refresh_id_file="",
            apply=False,
            source="mem0",
        )
    )

    assert payload == {
        "repo": "repo",
        "cap": 500,
        "dry_run": True,
        "source": "mem0",
        "refresh_existing_limit": 250,
        "refresh_existing_offset": 50,
        "refresh_ids": [],
    }
    assert created["client"] == {
        "base_url": "http://mem0",
        "api_key": "secret-key",
        "user_id": "ionut",
        "lexical_database_url": "postgres://mem0-db",
    }



def test_live_snapshot_sync_fetches_explicit_refresh_ids(monkeypatch):
    captured = {}

    class FakeClient:
        def get_all(self):
            return {"results": []}

        def fetch(self, memory_id):
            captured.setdefault("fetched", []).append(memory_id)
            return {"id": memory_id, "memory": "exact"}

    class FakeRepo:
        def record_mirror_sync_run(self, **kwargs):
            captured["sync_metadata"] = kwargs["metadata"]
            return {"id": 1}

    def fake_preview(repo, payload):
        captured["payload_ids"] = [row["id"] for row in payload["results"]]
        return ImportReport(created=1, updated=0, unchanged=1, skipped=0)

    monkeypatch.setattr(mirror_sync, "preview_mem0_export", fake_preview)

    payload = mirror_sync.run_live_snapshot_sync(
        FakeRepo(),
        FakeClient(),
        dry_run=True,
        refresh_ids=["missing-id", "missing-id", "other-id"],
    )

    assert captured["fetched"] == ["missing-id", "other-id"]
    assert captured["payload_ids"] == ["missing-id", "other-id"]
    assert captured["sync_metadata"]["refresh_id_count"] == 2
    assert captured["sync_metadata"]["refreshed_id_rows"] == 2
    assert payload["processed_rows"] == 2


def test_sync_cli_reads_refresh_id_file(tmp_path):
    path = tmp_path / "ids.txt"
    path.write_text("a\n\nb\na\n", encoding="utf-8")

    assert sync_cli.read_refresh_id_file(str(path)) == ["a", "b"]

    json_path = tmp_path / "ids.json"
    json_path.write_text('{"ids": ["x", "y", "x"]}', encoding="utf-8")

    assert sync_cli.read_refresh_id_file(str(json_path)) == ["x", "y"]
