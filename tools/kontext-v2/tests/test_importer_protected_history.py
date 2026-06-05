from kontext_v2.importer import import_mem0_export, preview_mem0_export
from kontext_v2.models import MemoryRecord


class FakeRepo:
    def __init__(self, existing):
        self.existing = existing
        self.upserts = []

    def fetch_by_external_id(self, memory_id):
        if self.existing is not None and memory_id == self.existing.external_mem0_id:
            return self.existing
        return None

    def upsert_memory(self, memory):
        self.upserts.append(memory)


def protected_existing_memory() -> MemoryRecord:
    return MemoryRecord(
        external_mem0_id="protected-history-1",
        title="Protected history",
        text="Protected autobiographical history original.",
        metadata={
            "domains": ["psychology", "relationships"],
            "memory_type": "relationship_context",
            "current_status": "active",
            "memory_tier": "historical",
        },
        memory_type="relationship_context",
        current_status="active",
        memory_tier="historical",
        signal_strength=9,
        source_hash="old-source-hash",
    )


def changed_export_row(**metadata_overrides):
    metadata = {
        "domains": ["psychology", "relationships"],
        "memory_type": "relationship_context",
        "current_status": "active",
        "memory_tier": "historical",
        **metadata_overrides,
    }
    return {
        "id": "protected-history-1",
        "memory": "Protected autobiographical history replacement.",
        "metadata": metadata,
    }


def test_import_skips_updates_to_protected_autobiographical_history_without_override():
    repo = FakeRepo(protected_existing_memory())

    report = import_mem0_export(repo, {"results": [changed_export_row()]})

    assert report.created == 0
    assert report.updated == 0
    assert report.unchanged == 0
    assert report.skipped == 1
    assert repo.upserts == []


def test_preview_counts_protected_autobiographical_update_as_skipped_without_override():
    repo = FakeRepo(protected_existing_memory())

    report = preview_mem0_export(repo, {"results": [changed_export_row()]})

    assert report.updated == 0
    assert report.skipped == 1


def test_import_allows_explicit_protected_history_override():
    repo = FakeRepo(protected_existing_memory())

    report = import_mem0_export(
        repo,
        {"results": [changed_export_row(protected_history_import_override=True)]},
    )

    assert report.updated == 1
    assert report.skipped == 0
    assert len(repo.upserts) == 1
