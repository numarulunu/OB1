from __future__ import annotations

from types import SimpleNamespace

from kontext_v2.parity import compare_exact_id_freshness


class FakeRepo:
    def __init__(self, memories):
        self.memories = memories

    def fetch_by_external_id(self, external_id: str):
        return self.memories.get(external_id)


def test_exact_id_freshness_ignores_mem0_only_audit_metadata():
    mem0_rows = {
        "mem-1": {
            "id": "mem-1",
            "memory": "Stable project memory",
            "metadata": {
                "domains": ["systems", "ai"],
                "memory_type": "project_state",
                "current_status": "active",
                "memory_tier": "active",
                "signal_strength": "9.0",
                "source": "audit-log",
                "policy_version": "v2",
                "source_ids": ["opaque-source-id"],
            },
        }
    }
    repo = FakeRepo(
        {
            "mem-1": SimpleNamespace(
                text="Stable project memory",
                metadata={
                    "domains": ["ai", "systems"],
                    "memory_type": "project_state",
                    "current_status": "active",
                    "memory_tier": "active",
                    "signal_strength": 9,
                },
                memory_type="project_state",
                current_status="active",
                memory_tier="active",
                signal_strength=9,
                source_hash="legacy-source-hash",
            )
        }
    )

    report = compare_exact_id_freshness(repo, lambda memory_id: mem0_rows[memory_id], ["mem-1"])

    assert report["fresh"] == 1
    assert report["results"][0]["canonical_hash_match"] is True


def test_exact_id_freshness_still_fails_on_core_metadata_mismatch():
    mem0_rows = {
        "mem-1": {
            "id": "mem-1",
            "memory": "Stable project memory",
            "metadata": {
                "domains": ["ai"],
                "memory_type": "project_state",
                "current_status": "active",
                "memory_tier": "active",
            },
        }
    }
    repo = FakeRepo(
        {
            "mem-1": SimpleNamespace(
                text="Stable project memory",
                metadata={
                    "domains": ["ai"],
                    "memory_type": "preference",
                    "current_status": "active",
                    "memory_tier": "active",
                },
                memory_type="preference",
                current_status="active",
                memory_tier="active",
                signal_strength=None,
                source_hash="legacy-source-hash",
            )
        }
    )

    report = compare_exact_id_freshness(repo, lambda memory_id: mem0_rows[memory_id], ["mem-1"])

    assert report["stale"] == 1
    assert report["results"][0]["canonical_hash_match"] is False
