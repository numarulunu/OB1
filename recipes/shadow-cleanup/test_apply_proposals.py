import importlib.util
import json
from pathlib import Path


def load_applicator():
    module_path = Path(__file__).with_name("apply_proposals.py")
    spec = importlib.util.spec_from_file_location("apply_proposals", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def proposal_row(pack_id="pack1"):
    return {
        "pack_id": pack_id,
        "pack_kind": "topic_cluster",
        "cluster_key": "project:ob1|topic:memory",
        "model": "opus",
        "proposal": {
            "canonical_memories": [
                {
                    "content": "Use OB1 as the durable memory baseline.",
                    "source_ids": ["a", "b"],
                    "confidence": 0.92,
                    "reason": "Consolidates repeated architecture decisions.",
                    "metadata_patch": {"type": "decision", "topics": ["ob1", "memory"]},
                }
            ],
            "archive_candidates": [{"id": "a", "reason": "Superseded by canonical memory."}],
            "risk_notes": [],
        },
    }


def test_load_valid_proposals_skips_errors_and_duplicate_pack_ids(tmp_path):
    applicator = load_applicator()
    path = tmp_path / "proposals.jsonl"
    path.write_text(
        json.dumps(proposal_row("pack1"))
        + "\n"
        + json.dumps({"pack_id": "bad", "error": "failed"})
        + "\n"
        + json.dumps(proposal_row("pack1"))
        + "\n",
        encoding="utf-8",
    )

    proposals = applicator.load_valid_proposals([path])

    assert len(proposals) == 1
    assert proposals[0]["pack_id"] == "pack1"


def test_summarize_proposals_counts_without_raw_content():
    applicator = load_applicator()

    summary = applicator.summarize_proposals([proposal_row()])

    assert summary == {
        "proposal_rows": 1,
        "canonical_memories": 1,
        "archive_candidates": 1,
        "source_ids": 2,
    }


def test_build_canonical_metadata_merges_patch_and_cleanup_fields():
    applicator = load_applicator()
    row = proposal_row()
    memory = row["proposal"]["canonical_memories"][0]

    metadata = applicator.build_canonical_metadata(row, memory, "batch1")

    assert metadata["source"] == "shadow_cleanup"
    assert metadata["type"] == "decision"
    assert metadata["topics"] == ["ob1", "memory"]
    assert metadata["shadow_cleanup_pack_id"] == "pack1"
    assert metadata["shadow_cleanup_source_ids"] == ["a", "b"]
    assert metadata["import_mode"] == "shadow_cleanup_apply_v1"


def test_merge_archive_metadata_is_non_destructive():
    applicator = load_applicator()
    existing = {"source": "claude", "topics": ["memory"]}

    merged = applicator.merge_archive_metadata(
        existing,
        pack_id="pack1",
        reason="Superseded.",
        canonical_ids=["new1"],
        applied_at="2026-05-03T00:00:00+00:00",
        batch="batch1",
    )

    assert merged["source"] == "claude"
    assert merged["topics"] == ["memory"]
    assert merged["shadow_cleanup"]["status"] == "archive_candidate"
    assert merged["shadow_cleanup"]["canonical_ids"] == ["new1"]
    assert merged["shadow_cleanup"]["batch"] == "batch1"


def test_is_valid_thought_id_accepts_uuid_only():
    applicator = load_applicator()

    assert applicator.is_valid_thought_id("123e4567-e89b-12d3-a456-426614174000")
    assert not applicator.is_valid_thought_id("ad3c0fa76f3bcb81f-...")
    assert not applicator.is_valid_thought_id("")


def test_read_sync_log_tracks_processed_pack_ids(tmp_path):
    applicator = load_applicator()
    path = tmp_path / "sync.json"
    path.write_text(json.dumps({"packs": {"pack1": {"ok": True}, "pack2": {"ok": False}}}), encoding="utf-8")

    sync = applicator.read_sync_log(path)

    assert applicator.processed_pack_ids(sync) == {"pack1"}
