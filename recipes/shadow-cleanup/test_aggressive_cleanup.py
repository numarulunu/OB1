import importlib.util
import json
from pathlib import Path


UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"


def load_aggressive():
    module_path = Path(__file__).with_name("aggressive_cleanup.py")
    spec = importlib.util.spec_from_file_location("aggressive_cleanup", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def thought(record_id, content, metadata=None):
    return {"id": record_id, "content": content, "metadata": metadata or {}}


def test_noise_score_flags_process_trivia_over_durable_memory():
    aggressive = load_aggressive()

    noisy = thought(
        "a",
        "The speaker diarization was successfully inferred from conversational cues.",
        {"source": "claude_history", "type": "decision"},
    )
    durable = thought(
        "b",
        "User prefers aggressive second-brain cleanup that keeps project-useful memory only.",
        {"source": "claude_history", "type": "preference"},
    )

    assert aggressive.noise_score(noisy) > aggressive.noise_score(durable)


def test_build_candidate_packs_prefers_claude_noise_and_keeps_raw_content_internal():
    aggressive = load_aggressive()
    records = [
        thought("a", "Speaker identification must resolve aliases to canonical names.", {"source": "claude_history", "type": "decision", "cwd": "tmp"}),
        thought("b", "The import sync log advanced to chunk 4.", {"source": "claude_history", "type": "context", "cwd": "tmp"}),
        thought("c", "User wants OB1 to be the durable memory baseline.", {"source": "kontext", "type": "decision"}),
    ]

    packs = aggressive.build_candidate_packs(records, source="claude_history", limit_rows=2, max_items=2)

    assert len(packs) == 2
    assert sum(pack["record_count"] for pack in packs) == 2
    assert sorted(item["id"] for pack in packs for item in pack["items"]) == ["a", "b"]
    assert "content" in packs[0]["items"][0]


def test_build_candidate_packs_can_filter_project_key():
    aggressive = load_aggressive()
    records = [
        thought("a", "Speaker diarization status.", {"source": "claude_history", "type": "context", "cwd": "C:/tmp/job"}),
        thought("b", "Parser status.", {"source": "claude_history", "type": "context", "cwd": "C:/Work/OB1"}),
    ]

    packs = aggressive.build_candidate_packs(
        records,
        source="claude_history",
        limit_rows=10,
        max_items=10,
        project_filter="temp-working-dir",
    )

    assert sum(pack["record_count"] for pack in packs) == 1
    assert packs[0]["items"][0]["id"] == "a"


def test_parse_deepseek_json_accepts_fenced_json_with_v2_label():
    aggressive = load_aggressive()
    payload = {
        "pack_id": "pack1",
        "decisions": [{"id": UUID_A, "label": "DROP_NOISE", "confidence": 0.95, "reason": "Process trivia."}],
        "compressed_memories": [],
        "risk_notes": [],
    }

    parsed = aggressive.parse_deepseek_json("```json\n" + json.dumps(payload) + "\n```")

    assert parsed["pack_id"] == "pack1"
    assert parsed["decisions"][0]["label"] == "DROP_NOISE"


def test_summarize_proposals_counts_v2_labels():
    aggressive = load_aggressive()
    rows = [
        {"proposal": {"decisions": [{"label": "DROP_NOISE"}, {"label": "KEEP_CORE"}], "compressed_memories": [{"content": "x"}]}},
        {"proposal": {"decisions": [{"label": "COMPRESS"}, {"action": "delete"}], "compressed_memories": []}},
    ]

    summary = aggressive.summarize_proposals(rows)

    assert summary["proposal_rows"] == 2
    assert summary["DROP_NOISE"] == 2
    assert summary["KEEP_CORE"] == 1
    assert summary["COMPRESS"] == 1
    assert summary["compressed_memories"] == 1


def test_drop_decision_map_keeps_highest_confidence_drop_only():
    aggressive = load_aggressive()
    rows = [
        {"pack_id": "p1", "proposal": {"decisions": [{"id": UUID_A, "label": "DROP_NOISE", "confidence": 0.93}]}},
        {"pack_id": "p2", "proposal": {"decisions": [{"id": UUID_A, "label": "DROP_STALE", "confidence": 0.98}]}},
        {"pack_id": "p3", "proposal": {"decisions": [{"id": UUID_B, "label": "DROP_NOISE", "confidence": 0.5}]}},
        {"pack_id": "p4", "proposal": {"decisions": [{"id": UUID_C, "label": "KEEP_CORE", "confidence": 0.99}]}},
    ]

    decisions = aggressive.drop_decision_map(rows, min_confidence=0.92)

    assert sorted(decisions) == [UUID_A]
    assert decisions[UUID_A]["label"] == "DROP_STALE"
    assert decisions[UUID_A]["confidence"] == 0.98


def test_split_delete_rows_protects_goldlist_terms_by_default():
    aggressive = load_aggressive()
    rows = [
        thought(UUID_A, "Parser status and sync log details."),
        thought(UUID_B, "Current Vocality positioning should remain protected."),
    ]

    to_delete, protected = aggressive.split_delete_rows(rows, allow_goldlist_delete=False)

    assert [row["id"] for row in to_delete] == [UUID_A]
    assert [row["id"] for row in protected] == [UUID_B]


def test_compressed_memory_candidates_only_delete_compress_decisions():
    aggressive = load_aggressive()
    rows = [
        {
            "pack_id": "p1",
            "cluster_key": "project:test",
            "model": "deepseek/test",
            "proposal": {
                "decisions": [
                    {"id": UUID_A, "label": "COMPRESS", "confidence": 0.9},
                    {"id": UUID_B, "label": "KEEP_CORE", "confidence": 0.99},
                    {"id": UUID_C, "label": "COMPRESS", "confidence": 0.84},
                ],
                "compressed_memories": [
                    {
                        "content": "Durable compressed memory.",
                        "source_ids": [UUID_A, UUID_B, UUID_C],
                        "type": "pattern",
                        "topics": ["x"],
                        "confidence": 0.9,
                        "reason": "Useful summary.",
                    }
                ],
            },
        }
    ]

    candidates = aggressive.compressed_memory_candidates(
        rows,
        min_confidence=0.85,
        min_decision_confidence=0.85,
        min_source_ids=1,
    )

    assert len(candidates) == 1
    assert candidates[0]["source_ids"] == [UUID_A, UUID_B, UUID_C]
    assert candidates[0]["delete_source_ids"] == [UUID_A]


def test_compressed_memory_candidates_skip_low_confidence_and_tiny_groups():
    aggressive = load_aggressive()
    rows = [
        {
            "pack_id": "p1",
            "proposal": {
                "decisions": [{"id": UUID_A, "label": "COMPRESS", "confidence": 0.95}],
                "compressed_memories": [
                    {"content": "Too small.", "source_ids": [UUID_A], "confidence": 0.95},
                    {"content": "Too weak.", "source_ids": [UUID_A, UUID_B], "confidence": 0.7},
                ],
            },
        }
    ]

    candidates = aggressive.compressed_memory_candidates(
        rows,
        min_confidence=0.85,
        min_decision_confidence=0.85,
        min_source_ids=2,
    )

    assert candidates == []
