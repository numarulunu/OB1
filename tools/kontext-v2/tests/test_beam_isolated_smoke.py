from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "beam_isolated_smoke.py"
SPEC = importlib.util.spec_from_file_location("beam_isolated_smoke", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
beam_isolated_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(beam_isolated_smoke)


def test_database_url_for_schema_adds_search_path_option():
    url = beam_isolated_smoke.database_url_for_schema("postgresql://user:pass@host/db", "beam_test")

    assert "options=-csearch_path%3Dbeam_test%2Cpublic" in url


def test_sweep_summary_returns_sanitized_rank_metrics():
    report = {
        "sweeps": {
            "50": {
                "retrieval_matched_questions": 2,
                "retrieval_evaluable_questions": 3,
                "mrr": 0.5,
                "median_first_hit_rank": 7,
                "p90_first_hit_rank": 51,
                "max_first_hit_rank": 88,
                "average_search_latency_ms": 123.4,
            }
        }
    }

    assert beam_isolated_smoke.sweep_summary(report) == {
        "50": {
            "retrieval": "2/3",
            "mrr": 0.5,
            "median_first_hit_rank": 7,
            "p90_first_hit_rank": 51,
            "max_first_hit_rank": 88,
            "avg_ms": 123.4,
        }
    }
