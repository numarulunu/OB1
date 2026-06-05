from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from kontext_v2.intake import IngestionProposal, apply_intake_proposals, normalize_proposals
from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


TEST_IDS = [
    "apply-existing-1",
    "apply-duplicate-1",
    "kontext-local-apply-new-1",
    "apply-protected-history-1",
]


def _proposal(**overrides) -> IngestionProposal:
    defaults = {
        "action": "save",
        "content": "Kontext V2 should keep a local apply path behind an explicit apply flag.",
        "domains": ["ai", "systems"],
        "memory_type": "decision",
        "signal_strength": 8,
        "current_status": "active",
        "memory_tier": "active",
        "confidence": 0.9,
        "reason": "test proposal",
        "existing_id": "",
        "flag_type": "",
    }
    defaults.update(overrides)
    return IngestionProposal(**defaults)


def _reset(conn: psycopg.Connection) -> None:
    apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memory_flags WHERE origin = %s", ("kontext-v2-apply-test",))
        cur.execute("DELETE FROM memory_intake_audit WHERE origin = %s", ("kontext-v2-apply-test",))
        cur.execute("DELETE FROM memories WHERE external_mem0_id = ANY(%s)", (TEST_IDS,))
        cur.execute("DELETE FROM memories WHERE external_mem0_id LIKE 'kontext-local-%'")
    conn.commit()


def _repo():
    conn = psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"], row_factory=dict_row)
    _reset(conn)
    return conn, KontextRepository(conn)


def _insert(repo: KontextRepository, *, external_id: str, text: str, source_hash: str = "seed-hash") -> None:
    repo.upsert_memory(
        MemoryRecord(
            external_mem0_id=external_id,
            title="Seed",
            text=text,
            metadata={"domains": ["ai", "systems"], "memory_type": "decision"},
            memory_type="decision",
            current_status="active",
            memory_tier="active",
            signal_strength=7.0,
            source_hash=source_hash,
        )
    )


def _insert_protected_history(repo: KontextRepository) -> None:
    repo.upsert_memory(
        MemoryRecord(
            external_mem0_id="apply-protected-history-1",
            title="Protected history",
            text="Historical relationship and psychology context retained permanently.",
            metadata={"domains": ["relationships", "psychology"], "memory_type": "relationship_pattern"},
            memory_type="relationship_pattern",
            current_status="active",
            memory_tier="historical",
            signal_strength=9.0,
            source_hash="protected-history-hash",
        )
    )


class _FakeIntakeRepo:
    def __init__(self) -> None:
        self.flags: list[dict] = []
        self.upserts: list[MemoryRecord] = []
        self.memory = MemoryRecord(
            external_mem0_id="apply-protected-history-1",
            title="Protected history",
            text="Historical relationship and psychology context retained permanently.",
            metadata={"domains": ["relationships", "psychology"], "memory_type": "relationship_pattern"},
            memory_type="relationship_pattern",
            current_status="active",
            memory_tier="historical",
            signal_strength=9.0,
            source_hash="protected-history-hash",
        )

    def find_exact_text_id(self, text: str) -> str:
        return "apply-protected-history-1" if "relationship and psychology" in str(text) else ""

    def fetch_by_external_id(self, external_id: str) -> MemoryRecord | None:
        return self.memory if external_id == "apply-protected-history-1" else None

    def record_memory_flag(self, **kwargs):
        self.flags.append(kwargs)
        return kwargs

    def upsert_memory(self, memory: MemoryRecord, version_source: str = "kontext_ingestion"):
        self.upserts.append(memory)
        self.memory = memory
        return memory


def test_apply_false_is_default_and_does_not_write_memories():
    conn, repo = _repo()
    try:
        before = repo.count_memories()

        result = apply_intake_proposals(
            repo,
            proposals=[_proposal()],
            source_hash="apply-source-1",
            origin="kontext-v2-apply-test",
        )

        assert result["mode"] == "dry_run"
        assert result["writes_applied"] == 0
        assert result["counts"]["saved"] == 1
        assert repo.count_memories() == before
    finally:
        conn.close()


def test_apply_true_saves_only_high_confidence_new_memory():
    conn, repo = _repo()
    try:
        result = apply_intake_proposals(
            repo,
            proposals=[_proposal(existing_id="kontext-local-apply-new-1")],
            source_hash="apply-source-2",
            origin="kontext-v2-apply-test",
            apply=True,
        )
        saved = repo.fetch_by_external_id("kontext-local-apply-new-1")

        assert result["mode"] == "apply"
        assert result["writes_applied"] == 1
        assert result["counts"]["saved"] == 1
        assert saved is not None
        assert saved.memory_type == "decision"
    finally:
        conn.close()


def test_apply_true_skips_low_confidence_new_memory():
    conn, repo = _repo()
    try:
        before = repo.count_memories()

        result = apply_intake_proposals(
            repo,
            proposals=[_proposal(confidence=0.4)],
            source_hash="apply-source-3",
            origin="kontext-v2-apply-test",
            apply=True,
        )

        assert result["writes_applied"] == 0
        assert result["counts"]["skipped"] == 1
        assert repo.count_memories() == before
    finally:
        conn.close()


def test_apply_true_skips_exact_duplicate_save():
    conn, repo = _repo()
    try:
        duplicate_text = "Kontext V2 already stores this exact durable memory."
        _insert(repo, external_id="apply-duplicate-1", text=duplicate_text)
        before = repo.count_memories()

        result = apply_intake_proposals(
            repo,
            proposals=[_proposal(content=duplicate_text)],
            source_hash="apply-source-4",
            origin="kontext-v2-apply-test",
            apply=True,
        )

        assert result["writes_applied"] == 0
        assert result["counts"]["skipped"] == 1
        assert result["results"][0]["existing_id"] == "apply-duplicate-1"
        assert repo.count_memories() == before
    finally:
        conn.close()


def test_apply_true_updates_exact_id_and_preserves_versions():
    conn, repo = _repo()
    try:
        _insert(repo, external_id="apply-existing-1", text="Old Kontext apply state.")
        before_versions = repo.count_memory_versions("apply-existing-1")

        result = apply_intake_proposals(
            repo,
            proposals=[_proposal(action="update", content="Updated Kontext apply state.", existing_id="apply-existing-1")],
            source_hash="apply-source-5",
            origin="kontext-v2-apply-test",
            apply=True,
        )
        updated = repo.fetch_by_external_id("apply-existing-1")
        after_versions = repo.count_memory_versions("apply-existing-1")

        assert result["writes_applied"] == 1
        assert result["counts"]["updated"] == 1
        assert updated is not None
        assert updated.text == "Updated Kontext apply state."
        assert after_versions == before_versions + 1
    finally:
        conn.close()


def test_destructive_proposal_records_flag_without_deleting_memory():
    conn, repo = _repo()
    try:
        _insert(repo, external_id="apply-existing-1", text="Keep this memory while flagging it.")
        proposal = normalize_proposals(
            {
                "proposals": [
                    {
                        "action": "delete",
                        "content": "Keep this memory while flagging it.",
                        "existing_id": "apply-existing-1",
                    }
                ]
            }
        )[0]

        result = apply_intake_proposals(
            repo,
            proposals=[proposal],
            source_hash="apply-source-6",
            origin="kontext-v2-apply-test",
            apply=True,
        )

        assert result["writes_applied"] == 1
        assert result["counts"]["flagged"] == 1
        assert repo.fetch_by_external_id("apply-existing-1") is not None
        assert repo.count_memory_flags() >= 1
    finally:
        conn.close()


def test_destructive_proposal_does_not_flag_protected_autobiographical_history():
    repo = _FakeIntakeRepo()
    proposal = normalize_proposals(
        {
            "proposals": [
                {
                    "action": "delete",
                    "content": "Historical relationship and psychology context retained permanently.",
                    "existing_id": "apply-protected-history-1",
                }
            ]
        }
    )[0]

    result = apply_intake_proposals(
        repo,
        proposals=[proposal],
        source_hash="apply-source-protected-history",
        origin="kontext-v2-apply-test",
        apply=True,
    )

    assert result["writes_applied"] == 0
    assert result["counts"]["flagged"] == 0
    assert result["counts"]["skipped"] == 1
    assert result["results"][0]["action"] == "protected"
    assert result["results"][0]["protected"] is True
    assert repo.flags == []


def test_update_proposal_does_not_overwrite_protected_autobiographical_history():
    repo = _FakeIntakeRepo()
    proposal = _proposal(
        action="update",
        content="Rewrite the protected personal history as disposable.",
        existing_id="apply-protected-history-1",
        domains=["relationships"],
        memory_type="relationship_pattern",
        memory_tier="historical",
    )

    result = apply_intake_proposals(
        repo,
        proposals=[proposal],
        source_hash="apply-source-protected-update",
        origin="kontext-v2-apply-test",
        apply=True,
    )

    assert result["writes_applied"] == 0
    assert result["counts"]["updated"] == 0
    assert result["counts"]["skipped"] == 1
    assert result["results"][0]["action"] == "skipped_protected"
    assert result["results"][0]["reason"] == "protected_autobiographical_history"
    assert repo.upserts == []


def test_update_proposal_can_override_protected_history_only_explicitly():
    repo = _FakeIntakeRepo()
    proposal = _proposal(
        action="update",
        content="Corrected protected history retained as historical context.",
        existing_id="apply-protected-history-1",
        domains=["relationships"],
        memory_type="relationship_pattern",
        memory_tier="historical",
        protected_override=True,
    )

    result = apply_intake_proposals(
        repo,
        proposals=[proposal],
        source_hash="apply-source-protected-update-override",
        origin="kontext-v2-apply-test",
        apply=True,
    )

    assert result["writes_applied"] == 1
    assert result["counts"]["updated"] == 1
    assert result["results"][0]["action"] == "update"
    assert repo.upserts[-1].external_mem0_id == "apply-protected-history-1"


def test_save_proposal_with_existing_protected_id_does_not_overwrite_history():
    repo = _FakeIntakeRepo()
    proposal = _proposal(
        action="save",
        content="New content should not overwrite protected autobiographical history.",
        existing_id="apply-protected-history-1",
        domains=["relationships"],
        memory_type="relationship_pattern",
        memory_tier="historical",
    )

    result = apply_intake_proposals(
        repo,
        proposals=[proposal],
        source_hash="apply-source-protected-save",
        origin="kontext-v2-apply-test",
        apply=True,
    )

    assert result["writes_applied"] == 0
    assert result["counts"]["saved"] == 0
    assert result["counts"]["skipped"] == 1
    assert result["results"][0]["action"] == "skipped_protected"
    assert repo.upserts == []
