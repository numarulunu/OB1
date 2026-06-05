from __future__ import annotations

from kontext_v2 import exact_id_freshness_cli as cli


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_run_caps_exact_id_scan_before_fetching_mem0(monkeypatch):
    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *, base_url, api_key, user_id, timeout):
            seen["client"] = {
                "base_url": base_url,
                "api_key": api_key,
                "user_id": user_id,
                "timeout": timeout,
            }

        def fetch(self, memory_id: str):  # pragma: no cover - compare is stubbed here
            raise AssertionError(f"unexpected fetch for {memory_id}")

    class FakeRepo:
        def __init__(self, conn):
            seen["repo_conn"] = conn

        def list_memory_rows(self, limit=1000):
            seen["repo_limit"] = limit
            return [{"external_mem0_id": f"mem-{i}"} for i in range(10)]

    def fake_compare(repo, fetch, memory_ids):
        seen["memory_ids"] = list(memory_ids)
        return {
            "checked": len(memory_ids),
            "fresh": len(memory_ids),
            "stale": 0,
            "results": [{"id": memory_id, "fresh": True, "source_hash_match": True} for memory_id in memory_ids],
        }

    monkeypatch.setattr(cli.psycopg, "connect", lambda database_url: _FakeConnection())
    monkeypatch.setattr(cli, "apply_schema", lambda conn: None)
    monkeypatch.setattr(cli, "KontextRepository", FakeRepo)
    monkeypatch.setattr(cli, "Mem0ApiClient", FakeClient)
    monkeypatch.setattr(cli, "compare_exact_id_freshness", fake_compare)

    payload = cli.run(
        database_url="postgres://example",
        mem0_base_url="https://mem0.example",
        mem0_api_key="test-key",
        mem0_user_id="ionut",
        limit=3,
        fetch_timeout=11,
    )

    assert seen["repo_limit"] == 3
    assert seen["memory_ids"] == ["mem-0", "mem-1", "mem-2"]
    assert seen["client"]["timeout"] == 11
    assert payload["checked"] == 3
    assert payload["scan_limit"] == 3
    assert payload["scan_limit_reached"] is True


def test_run_uses_offset_and_reports_next_window(monkeypatch):
    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def fetch(self, memory_id: str):  # pragma: no cover - compare is stubbed here
            raise AssertionError(f"unexpected fetch for {memory_id}")

    class FakeRepo:
        def __init__(self, conn):
            pass

        def count_memories(self):
            return 10

        def list_memory_rows(self, limit=1000, offset=0):
            seen["repo_limit"] = limit
            seen["repo_offset"] = offset
            return [{"external_mem0_id": f"mem-{i}"} for i in range(offset, min(offset + limit, 10))]

    def fake_compare(repo, fetch, memory_ids):
        seen["memory_ids"] = list(memory_ids)
        return {
            "checked": len(memory_ids),
            "fresh": len(memory_ids),
            "stale": 0,
            "results": [{"id": memory_id, "fresh": True, "source_hash_match": True} for memory_id in memory_ids],
        }

    monkeypatch.setattr(cli.psycopg, "connect", lambda database_url: _FakeConnection())
    monkeypatch.setattr(cli, "apply_schema", lambda conn: None)
    monkeypatch.setattr(cli, "KontextRepository", FakeRepo)
    monkeypatch.setattr(cli, "Mem0ApiClient", FakeClient)
    monkeypatch.setattr(cli, "compare_exact_id_freshness", fake_compare)

    payload = cli.run(
        database_url="postgres://example",
        mem0_base_url="https://mem0.example",
        mem0_api_key="test-key",
        mem0_user_id="ionut",
        limit=3,
        offset=6,
    )

    assert seen["repo_limit"] == 3
    assert seen["repo_offset"] == 6
    assert seen["memory_ids"] == ["mem-6", "mem-7", "mem-8"]
    assert payload["total_rows"] == 10
    assert payload["requested_scan_offset"] == 6
    assert payload["scan_offset"] == 6
    assert payload["next_scan_offset"] == 9
    assert payload["scan_offset_wrapped"] is False


def test_run_wraps_stale_offset(monkeypatch):
    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def fetch(self, memory_id: str):  # pragma: no cover - compare is stubbed here
            raise AssertionError(f"unexpected fetch for {memory_id}")

    class FakeRepo:
        def __init__(self, conn):
            pass

        def count_memories(self):
            return 4

        def list_memory_rows(self, limit=1000, offset=0):
            seen["repo_offset"] = offset
            return [{"external_mem0_id": f"mem-{i}"} for i in range(offset, min(offset + limit, 4))]

    def fake_compare(repo, fetch, memory_ids):
        seen["memory_ids"] = list(memory_ids)
        return {
            "checked": len(memory_ids),
            "fresh": len(memory_ids),
            "stale": 0,
            "results": [{"id": memory_id, "fresh": True, "source_hash_match": True} for memory_id in memory_ids],
        }

    monkeypatch.setattr(cli.psycopg, "connect", lambda database_url: _FakeConnection())
    monkeypatch.setattr(cli, "apply_schema", lambda conn: None)
    monkeypatch.setattr(cli, "KontextRepository", FakeRepo)
    monkeypatch.setattr(cli, "Mem0ApiClient", FakeClient)
    monkeypatch.setattr(cli, "compare_exact_id_freshness", fake_compare)

    payload = cli.run(
        database_url="postgres://example",
        mem0_base_url="https://mem0.example",
        mem0_api_key="test-key",
        mem0_user_id="ionut",
        limit=2,
        offset=99,
    )

    assert seen["repo_offset"] == 0
    assert seen["memory_ids"] == ["mem-0", "mem-1"]
    assert payload["requested_scan_offset"] == 99
    assert payload["scan_offset"] == 0
    assert payload["next_scan_offset"] == 2
    assert payload["scan_offset_wrapped"] is True


def test_parser_reads_freshness_limit_and_timeout_from_env(monkeypatch):
    monkeypatch.setenv("KONTEXT_EXACT_ID_FRESHNESS_LIMIT", "123")
    monkeypatch.setenv("KONTEXT_EXACT_ID_FETCH_TIMEOUT", "17")
    monkeypatch.setenv("KONTEXT_EXACT_ID_FRESHNESS_OFFSET", "45")

    args = cli.build_parser().parse_args([])

    assert args.limit == 123
    assert args.fetch_timeout == 17
    assert args.offset == 45
