import os
from pathlib import Path

import psycopg

from kontext_v2.benchmarks.fixtures import load_longmemeval_real_fixture
from kontext_v2.benchmarks.longmemeval_predict import run_longmemeval_predict_only, run_longmemeval_predict_sweep
from kontext_v2.schema import apply_schema


REAL_FIXTURE = Path(__file__).parent / "fixtures" / "longmemeval_real_shape.json"


def test_load_longmemeval_real_fixture_normalizes_per_question_conversations():
    fixture = load_longmemeval_real_fixture(REAL_FIXTURE, max_questions=1)

    assert fixture["dataset"] == "longmemeval_s"
    assert len(fixture["conversations"]) == 1
    assert len(fixture["questions"]) == 1
    conversation = fixture["conversations"][0]
    question = fixture["questions"][0]

    assert conversation["conversation_id"] == "lme-q-1"
    assert conversation["user_id"] == question["user_id"]
    assert [session["session_id"] for session in conversation["sessions"]] == ["session_a", "session_b"]
    assert question["category"] == "single-session-user"
    assert question["evidence"] == ["session_b"]


def test_run_longmemeval_predict_only_real_shape_writes_sanitized_report(tmp_path: Path):
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)

    report = run_longmemeval_predict_only(
        database_url,
        REAL_FIXTURE,
        tmp_path,
        "unit-longmemeval",
        top_k=5,
        max_questions=1,
    )

    assert report["dataset"] == "longmemeval_s"
    assert report["total_questions"] == 1
    assert report["matched_questions"] == 1
    assert report["categories"]["single-session-user"]["matched"] == 1
    output_text = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    assert "Riley" not in output_text
    assert "blue notebook" not in output_text
    assert "cedar desk" not in output_text
    assert "Where did Riley" not in output_text


def test_run_longmemeval_predict_sweep_real_shape_writes_sanitized_report(tmp_path: Path):
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)

    report = run_longmemeval_predict_sweep(
        database_url,
        REAL_FIXTURE,
        tmp_path,
        "unit-longmemeval-sweep",
        top_k_values=[1, 5],
        max_questions=2,
    )

    assert report["dataset"] == "longmemeval_s"
    assert report["mode"] == "predict-only-sweep"
    assert report["top_k_values"] == [1, 5]
    assert report["total_questions"] == 2
    assert set(report["sweeps"]) == {"1", "5"}
    output_text = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    assert "Riley" not in output_text
    assert "blue notebook" not in output_text
    assert "jasmine tea" not in output_text


def test_run_longmemeval_predict_sweep_supports_legacy_mem0_offline_backend_without_db(tmp_path: Path):
    private_bundle = tmp_path / "private" / "longmemeval-mem0.json"

    report = run_longmemeval_predict_sweep(
        "postgresql://unused",
        REAL_FIXTURE,
        tmp_path,
        "unit-longmemeval-mem0",
        top_k_values=[1],
        max_questions=1,
        retrieval_backend="legacy-mem0-offline",
        mem0_retrieval_module=Path("tools/mem0-remote-mcp/retrieval.py"),
        judged_bundle_output=private_bundle,
    )

    assert report["retrieval_backend"] == "legacy-mem0-offline"
    assert private_bundle.exists()
    assert '"retrieval_backend": "legacy-mem0-offline"' in private_bundle.read_text(encoding="utf-8")
