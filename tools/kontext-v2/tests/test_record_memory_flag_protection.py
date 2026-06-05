from __future__ import annotations

from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository


def _protected_memory() -> MemoryRecord:
    return MemoryRecord(
        external_mem0_id="protected-family-history-1",
        title="Protected family history",
        text="Protected autobiographical history.",
        metadata={"domains": ["family_origin", "psychology"], "memory_type": "family_origin"},
        memory_type="family_origin",
        current_status="active",
        memory_tier="historical",
        signal_strength=9.0,
        source_hash="protected-family-history-hash",
    )


class _FakeCursor:
    def __init__(self) -> None:
        self.executed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, _sql, params):
        self.executed = True
        return self

    def fetchone(self) -> dict:
        return {
            "id": 1,
            "external_mem0_id": "protected-family-history-1",
            "flag_type": "delete_candidate",
            "reason_hash": "reason-hash",
            "confidence": 0.9,
            "origin": "test",
            "status": "pending",
            "metadata": {},
            "created_at": "2026-05-26T00:00:00+00:00",
        }


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()
        self.commit_count = 0

    def cursor(self, **_kwargs):
        return self.cursor_obj

    def commit(self) -> None:
        self.commit_count += 1


def test_record_memory_flag_blocks_cleanup_flags_for_protected_history():
    conn = _FakeConn()
    repo = KontextRepository(conn)
    repo.fetch_by_external_id = lambda _external_id: _protected_memory()

    result = repo.record_memory_flag(
        external_mem0_id="protected-family-history-1",
        flag_type="delete_candidate",
        reason_hash="reason-hash",
        confidence=0.9,
        origin="test",
        status="pending",
    )

    assert result == {
        "external_mem0_id": "protected-family-history-1",
        "flag_type": "delete_candidate",
        "status": "blocked_protected",
        "protected": True,
    }
    assert conn.cursor_obj.executed is False
    assert conn.commit_count == 0


def test_record_memory_flag_can_explicitly_bypass_protection_check():
    conn = _FakeConn()
    repo = KontextRepository(conn)
    repo.fetch_by_external_id = lambda _external_id: _protected_memory()

    result = repo.record_memory_flag(
        external_mem0_id="protected-family-history-1",
        flag_type="delete_candidate",
        reason_hash="reason-hash",
        confidence=0.9,
        origin="test",
        status="pending",
        protection_check=False,
    )

    assert result["status"] == "pending"
    assert result["flag_type"] == "delete_candidate"
    assert conn.cursor_obj.executed is True
    assert conn.commit_count == 1
