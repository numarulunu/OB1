import json
from pathlib import Path

from kontext_v2.benchmarks.beam_predict import (
    _cross_encoder_config_for_flag,
    _fixture_for_run,
    _mem0_parity_config_for_flag,
    _semantic_config_for_flag,
    run_beam_predict_sweep,
)
from kontext_v2.benchmarks.fixtures import load_beam_real_fixture


REAL_FIXTURE = Path(__file__).parent / "fixtures" / "beam_real_shape.json"


def test_load_beam_real_fixture_normalizes_hf_rows_shape():
    fixture = load_beam_real_fixture(
        REAL_FIXTURE,
        beam_size="1M",
        max_questions=2,
        question_types=["information_extraction", "knowledge_update"],
    )

    assert fixture["dataset"] == "beam_1M"
    assert len(fixture["conversations"]) == 1
    assert len(fixture["questions"]) == 2

    conversation = fixture["conversations"][0]
    assert conversation["conversation_id"] == "beam-sample-1"
    assert conversation["user_id"] == "benchmark-beam_1M-beam-sample-1"
    assert [session["session_id"] for session in conversation["sessions"]] == ["batch_0", "batch_1"]
    assert conversation["sessions"][0]["date"] == "2025-01-03"
    assert conversation["sessions"][0]["messages"][0]["source_id"] == "101"

    assert fixture["questions"][0]["category"] == "information_extraction"
    assert fixture["questions"][0]["evidence"] == ["101"]
    assert fixture["questions"][0]["rubric"] == [{"description": "Mentions the amber notebook."}]
    assert fixture["questions"][1]["category"] == "knowledge_update"
    assert fixture["questions"][1]["evidence"] == ["101", "201"]


def test_load_beam_real_fixture_filters_conversations_and_question_types():
    fixture = load_beam_real_fixture(
        REAL_FIXTURE,
        beam_size="1M",
        conversation_indices=[0],
        question_types="knowledge_update",
    )

    assert len(fixture["conversations"]) == 1
    assert [question["category"] for question in fixture["questions"]] == ["knowledge_update"]
    assert fixture["questions"][0]["answer"] == "Obsidian vault"


def test_load_beam_real_fixture_preserves_zero_source_chat_id(tmp_path):
    fixture_path = tmp_path / "beam_zero_id.json"
    fixture_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "row": {
                            "conversation_id": "beam-zero-id",
                            "chat": [
                                [
                                    {
                                        "content": "Avery put the release note in the slate binder.",
                                        "id": 0,
                                        "index": "fallback-turn-id",
                                        "role": "user",
                                    }
                                ]
                            ],
                            "probing_questions": "{'information_extraction': [{'question': 'Where is the release note?', 'answer': 'slate binder', 'source_chat_ids': [0]}]}",
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    fixture = load_beam_real_fixture(fixture_path, beam_size="1M")

    session = fixture["conversations"][0]["sessions"][0]
    assert session["source_ids"] == ["0"]
    assert session["messages"][0]["source_id"] == "0"
    assert fixture["questions"][0]["evidence"] == ["0"]


def test_beam_fixture_for_run_uses_fixture_without_downloading():
    fixture = _fixture_for_run(
        REAL_FIXTURE,
        dataset_path=None,
        beam_size="1M",
        offset=0,
        length=1,
        conversations=None,
        max_questions=1,
        question_types="information_extraction",
    )

    assert fixture["dataset"] == "beam_1M"
    assert [question["category"] for question in fixture["questions"]] == ["information_extraction"]


def test_semantic_config_for_flag_reads_env_values():
    config = _semantic_config_for_flag(
        True,
        {
            "KONTEXT_BENCHMARK_SEMANTIC_LOCKED_HEAD": "12",
            "KONTEXT_BENCHMARK_SEMANTIC_WINDOW": "150",
            "KONTEXT_BENCHMARK_SEMANTIC_WEIGHT": "33.5",
            "KONTEXT_BENCHMARK_SEMANTIC_RRF_K": "7",
        },
    )

    assert config is not None
    assert config.enabled is True
    assert config.locked_head_size == 12
    assert config.candidate_window == 150
    assert config.score_weight == 33.5
    assert config.fusion_rrf_k == 7
    assert _semantic_config_for_flag(False, {}) is None


def test_cross_encoder_config_for_flag_reads_env_values():
    config = _cross_encoder_config_for_flag(
        True,
        {
            "KONTEXT_BENCHMARK_CROSS_ENCODER_LOCKED_HEAD": "3",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_WINDOW": "70",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_WEIGHT": "2.5",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_RRF_K": "0",
        },
    )

    assert config is not None
    assert config.enabled is True
    assert config.locked_head_size == 3
    assert config.candidate_window == 70
    assert config.score_weight == 2.5
    assert config.fusion_rrf_k == 0
    assert _cross_encoder_config_for_flag(False, {}) is None


def test_mem0_parity_config_for_flag_reads_env_values():
    config = _mem0_parity_config_for_flag(
        True,
        {
            "KONTEXT_BENCHMARK_MEM0_PARITY_WINDOW": "77",
            "KONTEXT_BENCHMARK_MEM0_PARITY_RRF_K": "12",
            "KONTEXT_BENCHMARK_MEM0_PARITY_LOCKED_HEAD": "2",
        },
    )

    assert config is not None
    assert config.enabled is True
    assert config.candidate_window == 77
    assert config.rrf_k == 12
    assert config.locked_head_size == 2
    assert _mem0_parity_config_for_flag(False, {}) is None


def test_run_beam_predict_sweep_supports_legacy_mem0_offline_backend_without_db(tmp_path: Path):
    private_bundle = tmp_path / "private" / "beam-mem0.json"

    report = run_beam_predict_sweep(
        "postgresql://unused",
        REAL_FIXTURE,
        tmp_path,
        "unit-beam-mem0",
        top_k_values=[1],
        max_questions=1,
        question_types="information_extraction",
        retrieval_backend="legacy-mem0-offline",
        mem0_retrieval_module=Path("tools/mem0-remote-mcp/retrieval.py"),
        judged_bundle_output=private_bundle,
    )

    assert report["retrieval_backend"] == "legacy-mem0-offline"
    assert private_bundle.exists()
    assert '"retrieval_backend": "legacy-mem0-offline"' in private_bundle.read_text(encoding="utf-8")


def test_run_beam_predict_sweep_rejects_mem0_parity_for_legacy_backend(tmp_path: Path):
    try:
        run_beam_predict_sweep(
            "postgresql://unused",
            REAL_FIXTURE,
            tmp_path,
            "unit-beam-mem0-parity-rejected",
            top_k_values=[1],
            max_questions=1,
            question_types="information_extraction",
            retrieval_backend="legacy-mem0-offline",
            mem0_retrieval_module=Path("tools/mem0-remote-mcp/retrieval.py"),
            mem0_parity_rerank=True,
        )
    except ValueError as exc:
        assert "mem0-parity-rerank" in str(exc)
    else:
        raise AssertionError("expected legacy Mem0 backend to reject Mem0 parity rerank")
