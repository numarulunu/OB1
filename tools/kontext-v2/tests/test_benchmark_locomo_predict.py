import os
from pathlib import Path

import psycopg

from kontext_v2.benchmarks.locomo_predict import run_locomo_predict_only
from kontext_v2.benchmarks.reporting import build_predict_only_report, write_report_files
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "locomo_tiny.json"
REAL_FIXTURE = Path(__file__).parent / "fixtures" / "locomo_real_shape.json"


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
