from __future__ import annotations

from datetime import UTC, datetime

from kontext_v2.repository import KontextRepository


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple | list | None = None):
        self.calls.append((sql, tuple(params or ())))
        return self

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows: list[dict]):
        self.cursor_obj = FakeCursor(rows)

    def cursor(self, row_factory=None):
        return self.cursor_obj


def _observation_row(title: str = "Kontext continuity") -> dict:
    return {
        "observation_id": "obs_1",
        "origin": "codex",
        "project": "OB1",
        "project_root": "C:/Tools/OB1",
        "cwd": "C:/Tools/OB1",
        "event_type": "implementation",
        "title": title,
        "summary": "Implemented project search pushdown.",
        "files": {"files_read": [], "files_modified": ["tools/kontext-v2/kontext_v2/repository.py"]},
        "metadata": {},
        "source_hash": "source-1",
        "created_at": datetime(2026, 5, 26, tzinfo=UTC),
    }


def _memory_row() -> dict:
    return {
        "external_mem0_id": "memory-1",
        "title": "Memory one",
        "text": "Private memory text",
        "metadata": {},
        "memory_type": "project_state",
        "current_status": "active",
        "memory_tier": "active",
        "signal_strength": 9.0,
        "created_at": datetime(2026, 5, 25, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 26, tzinfo=UTC),
        "imported_at": datetime(2026, 5, 26, tzinfo=UTC),
        "rank": 0.0,
    }


def test_project_search_uses_postgres_search_document_pushdown() -> None:
    conn = FakeConn([_observation_row()])
    repo = KontextRepository(conn)

    result = repo.project_search("Kontext continuity", limit=5)

    sql, params = conn.cursor_obj.calls[0]
    assert "search_document @@ websearch_to_tsquery('simple', %s)" in sql
    assert "ts_rank_cd(search_document, websearch_to_tsquery('simple', %s))" in sql
    assert params[:2] == ("Kontext continuity", "Kontext continuity")
    assert result["count"] == 1
    assert result["rows"][0]["id"] == "obs_1"


def test_project_search_can_scope_to_project_and_root() -> None:
    conn = FakeConn([_observation_row()])
    repo = KontextRepository(conn)

    result = repo.project_search("Kontext continuity", limit=5, project="OB1", project_root="C:/Tools/OB1")

    sql, params = conn.cursor_obj.calls[0]
    assert "LOWER(project) = LOWER(%s)" in sql
    assert "LOWER(project_root) = LOWER(%s)" in sql
    assert params[:4] == ("Kontext continuity", "Kontext continuity", "OB1", "C:/Tools/OB1")
    assert result["count"] == 1


def test_list_memory_rows_supports_filtered_keyset_query_without_offset() -> None:
    after = {
        "external_mem0_id": "memory-0",
        "signal_strength": 9.0,
        "updated_at": datetime(2026, 5, 26, tzinfo=UTC),
    }
    conn = FakeConn([_memory_row()])
    repo = KontextRepository(conn)

    rows = repo.list_memory_rows(
        limit=5000,
        current_status="active",
        memory_tier="active",
        after=after,
    )

    sql, params = conn.cursor_obj.calls[0]
    assert "current_status = %s" in sql
    assert "memory_tier = %s" in sql
    assert "COALESCE(signal_strength, '-Infinity'::float8)" in sql
    assert "OFFSET" not in sql
    assert params[0:2] == ("active", "active")
    assert params[-2] == "memory-0"
    assert params[-1] == 2500
    assert rows[0]["external_mem0_id"] == "memory-1"
