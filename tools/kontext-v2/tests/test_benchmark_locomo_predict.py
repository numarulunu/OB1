import os
from pathlib import Path

import psycopg

from kontext_v2.benchmarks.locomo_predict import (
    _add_conversations,
    run_locomo_predict_only,
    run_locomo_predict_sweep,
)
from kontext_v2.benchmarks.fixtures import load_locomo_real_fixture
from kontext_v2.benchmarks.reporting import build_predict_only_report, build_predict_sweep_report, write_report_files
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "locomo_tiny.json"
REAL_FIXTURE = Path(__file__).parent / "fixtures" / "locomo_real_shape.json"


def test_load_locomo_real_fixture_preserves_answers_for_private_judged_bundle():
    fixture = load_locomo_real_fixture(REAL_FIXTURE, conversation_indices="0", max_questions=1)

    assert fixture["questions"][0]["answer"] == "May 7"


def test_report_uses_hashes_not_raw_memory(tmp_path: Path):
    report = build_predict_only_report(
        "locomo_tiny",
        "unit-report",
        5,
        [
            {
                "question_id": "tiny-q-1",
                "category": "single-hop",
                "matched": True,
                "expected_terms": ["Berlin trip"],
                "search_latency_ms": 12.5,
                "result_ids": ["benchmark:abc"],
                "search_results": [{"memory": "Alice: Berlin trip in June."}],
            }
        ],
    )

    assert report["matched_questions"] == 1
    assert report["categories"]["single-hop"]["matched"] == 1
    assert report["questions"][0]["result_hashes"]
    assert "Alice: Berlin trip" not in str(report)
    paths = write_report_files(report, tmp_path)
    assert paths["json"].exists()
    assert paths["markdown"].exists()


def test_run_locomo_predict_only_writes_sanitized_report(tmp_path: Path):
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)

    report = run_locomo_predict_only(database_url, FIXTURE, tmp_path, "unit-runner", top_k=5)

    assert report["dataset"] == "locomo_tiny"
    assert report["total_questions"] == 3
    assert report["matched_questions"] == 3
    output_text = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    assert "Alice: Berlin" not in output_text
    assert "Mira: My cello" not in output_text


def test_run_locomo_predict_only_real_shape_uses_evidence_and_sanitizes_report(tmp_path: Path):
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)

    report = run_locomo_predict_only(
        database_url,
        FIXTURE,
        tmp_path,
        "unit-real-runner",
        top_k=5,
        dataset_path=REAL_FIXTURE,
        conversations="0",
        max_questions=1,
    )

    assert report["dataset"] == "locomo10"
    assert report["total_questions"] == 1
    assert report["matched_questions"] == 1
    assert report["categories"]["temporal"]["matched"] == 1
    output_text = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    assert "Avery" not in output_text
    assert "support group" not in output_text
    assert "May 7" not in output_text
    assert "When did Avery" not in output_text


def test_load_locomo_real_fixture_ignores_unreachable_evidence_ids(tmp_path: Path):
    fixture_path = tmp_path / "locomo_missing_evidence.json"
    fixture_path.write_text(
        """
[
  {
    "sample_id": "sample-missing-evidence",
    "conversation": {
      "speaker_a": "Avery",
      "speaker_b": "Blake",
      "session_1": [
        {"speaker": "Avery", "dia_id": "D1:1", "text": "The useful source is present."}
      ]
    },
    "qa": [
      {"question": "Which source exists?", "evidence": ["D1:1", "D9:9"], "category": 4},
      {"question": "Which source is missing?", "evidence": ["D9:9"], "category": 4}
    ]
  }
]
""".strip(),
        encoding="utf-8",
    )

    fixture = load_locomo_real_fixture(fixture_path)

    assert fixture["questions"][0]["evidence"] == ["D1:1"]
    assert fixture["questions"][1]["evidence"] == []

def test_add_conversations_keeps_turn_rows_and_session_context_row_for_sourced_messages():
    class RecordingAdapter:
        def __init__(self) -> None:
            self.calls = []

        def add(
            self,
            messages,
            user_id,
            conversation_id,
            session_id,
            timestamp=None,
            source_ids=None,
            observation_kind="turn",
        ):
            self.calls.append(
                {
                    "messages": messages,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "session_id": session_id,
                    "timestamp": timestamp,
                    "source_ids": source_ids,
                    "observation_kind": observation_kind,
                }
            )

    adapter = RecordingAdapter()

    _add_conversations(
        adapter,
        [
            {
                "user_id": "benchmark-user",
                "conversation_id": "conv-1",
                "sessions": [
                    {
                        "session_id": "session_1",
                        "date": "2024-05-07",
                        "messages": [
                            {"role": "user", "content": "First sourced turn", "source_id": "D1:1"},
                            {"role": "assistant", "content": "Second sourced turn", "source_id": "D1:2"},
                        ],
                        "source_ids": ["D1:1", "D1:2"],
                    }
                ],
            }
        ],
    )

    assert [call["source_ids"] for call in adapter.calls] == [
        ["D1:1"],
        ["D1:2"],
        ["D1:1", "D1:2"],
    ]
    assert [call["observation_kind"] for call in adapter.calls] == ["turn", "turn", "session"]
    assert len(adapter.calls[-1]["messages"]) == 2


def test_add_conversations_session_only_writes_one_compact_session_row():
    class RecordingAdapter:
        def __init__(self) -> None:
            self.calls = []

        def add(
            self,
            messages,
            user_id,
            conversation_id,
            session_id,
            timestamp=None,
            source_ids=None,
            observation_kind="turn",
        ):
            self.calls.append(
                {
                    "messages": messages,
                    "source_ids": source_ids,
                    "observation_kind": observation_kind,
                }
            )

    adapter = RecordingAdapter()

    _add_conversations(
        adapter,
        [
            {
                "user_id": "benchmark-user",
                "conversation_id": "conv-1",
                "sessions": [
                    {
                        "session_id": "session_1",
                        "date": "2024-05-07",
                        "messages": [
                            {"role": "user", "content": "First sourced turn", "source_id": "D1:1"},
                            {"role": "assistant", "content": "Second sourced turn", "source_id": "D1:2"},
                        ],
                        "source_ids": ["D1:1", "D1:2"],
                    }
                ],
            }
        ],
        session_only=True,
    )

    assert len(adapter.calls) == 1
    assert adapter.calls[0]["source_ids"] == ["D1:1", "D1:2"]
    assert adapter.calls[0]["observation_kind"] == "session"
    assert len(adapter.calls[0]["messages"]) == 2


def test_sweep_report_tracks_cutoff_misses_without_raw_memory(tmp_path: Path):
    report = build_predict_sweep_report(
        "locomo10",
        "unit-sweep-report",
        [1, 2],
        [
            {
                "question_id": "sample-0-q-1",
                "category": "temporal",
                "search_latency_ms": 10.0,
                "evidence": ["D1:2"],
                "expected_terms": [],
                "search_results": [
                    {"id": "benchmark:one", "memory": "first raw memory text", "metadata": {"source_ids": ["D1:1"]}},
                    {"id": "benchmark:two", "memory": "second raw memory text", "metadata": {"source_ids": ["D1:2"]}},
                ],
            }
        ],
    )

    assert report["mode"] == "predict-only-sweep"
    assert report["sweeps"]["1"]["matched_questions"] == 0
    assert report["sweeps"]["2"]["matched_questions"] == 1
    assert report["miss_analysis"]["1"]["reasons"] == {"evidence_below_cutoff": 1}
    assert report["questions"][0]["first_hit_top_k"] == 2


def test_run_locomo_predict_sweep_supports_legacy_mem0_offline_backend_without_db(tmp_path: Path):
    private_bundle = tmp_path / "private" / "locomo-mem0.json"

    report = run_locomo_predict_sweep(
        "postgresql://unused",
        FIXTURE,
        tmp_path,
        "unit-mem0-offline",
        top_k_values=[1, 5],
        retrieval_backend="legacy-mem0-offline",
        mem0_retrieval_module=Path("tools/mem0-remote-mcp/retrieval.py"),
        judged_bundle_output=private_bundle,
    )

    assert report["retrieval_backend"] == "legacy-mem0-offline"
    assert report["mode"] == "predict-only-sweep"
    assert report["top_k_values"] == [1, 5]
    assert private_bundle.exists()
    bundle_text = private_bundle.read_text(encoding="utf-8")
    assert '"retrieval_backend": "legacy-mem0-offline"' in bundle_text
    assert "raw memory text" not in str(report)
    paths = write_report_files(report, tmp_path)
    assert paths["json"].name.endswith("predict-only-sweep.json")


def test_run_locomo_predict_sweep_real_shape_writes_sanitized_report(tmp_path: Path):
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)

    report = run_locomo_predict_sweep(
        database_url,
        FIXTURE,
        tmp_path,
        "unit-real-sweep",
        top_k_values=[1, 5],
        dataset_path=REAL_FIXTURE,
        conversations="0",
        max_questions=2,
    )

    assert report["dataset"] == "locomo10"
    assert report["mode"] == "predict-only-sweep"
    assert report["top_k_values"] == [1, 5]
    assert report["total_questions"] == 2
    assert set(report["sweeps"]) == {"1", "5"}
    output_text = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    assert "Avery" not in output_text
    assert "support group" not in output_text
    assert "May 7" not in output_text
    assert "When did Avery" not in output_text
