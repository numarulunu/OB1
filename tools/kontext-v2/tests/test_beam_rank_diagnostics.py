from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "beam_rank_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("beam_rank_diagnostics", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
beam_rank_diagnostics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(beam_rank_diagnostics)


def test_feature_map_keeps_only_sanitized_numeric_features():
    explanation = {
        "features": [
            {"name": "adjacent_own", "value": 3.33333, "reason": "raw reason ignored"},
            {"name": "unsafe_raw_text", "value": 99},
        ]
    }

    assert beam_rank_diagnostics.feature_map(explanation) == {"adjacent_own": 3.3333}


def test_database_url_for_schema_adds_search_path_option():
    url = beam_rank_diagnostics.database_url_for_schema("postgresql://user:pass@host/db", "beam_diag")

    assert "options=-csearch_path%3Dbeam_diag%2Cpublic" in url


def test_summarize_diagnostics_reports_top_k_and_mrr_without_raw_text():
    diagnostics = [
        {"question_id": "q1", "first_hit_rank": 1, "rows": [{"safe": "metadata"}]},
        {"question_id": "q2", "first_hit_rank": 49, "rows": []},
        {"question_id": "q3", "first_hit_rank": 87, "rows": []},
        {"question_id": "q4", "first_hit_rank": None, "rows": []},
    ]

    summary = beam_rank_diagnostics.summarize_diagnostics(diagnostics, cutoffs=[10, 50, 200])

    assert summary == {
        "question_count": 4,
        "evidence_found_count": 3,
        "missing_evidence_count": 1,
        "first_hit_ranks": [1, 49, 87, None],
        "max_first_hit_rank": 87,
        "hits_at_k": {
            "10": {"passed": 1, "total": 4, "rate": 0.25, "mrr": 0.25},
            "50": {"passed": 2, "total": 4, "rate": 0.5, "mrr": 0.2551},
            "200": {"passed": 3, "total": 4, "rate": 0.75, "mrr": 0.258},
        },
    }


def test_run_can_match_session_only_semantic_beam_path(monkeypatch):
    calls = {}

    class DummyCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return self

    class DummyConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return DummyCursor()

    def fake_connect(*_args, **_kwargs):
        return DummyConnection()

    class FakeAdapter:
        def __init__(self, _conn, dataset, run_id, semantic_rerank_config=None, cross_encoder_rerank_config=None):
            calls["dataset"] = dataset
            calls["run_id"] = run_id
            calls["semantic_enabled"] = bool(getattr(semantic_rerank_config, "enabled", False))
            calls["semantic_rrf_k"] = getattr(semantic_rerank_config, "fusion_rrf_k", None)
            calls["cross_encoder_rrf_k"] = getattr(cross_encoder_rerank_config, "fusion_rrf_k", None)
            calls["cross_encoder_enabled"] = bool(getattr(cross_encoder_rerank_config, "enabled", False))

    fixture = {
        "dataset": "beam_10M",
        "conversations": [{"conversation_id": "c1", "sessions": []}],
        "questions": [{"question_id": "q1", "user_id": "u1", "evidence": ["1"]}],
    }

    monkeypatch.setattr(beam_rank_diagnostics.psycopg, "connect", fake_connect)
    monkeypatch.setattr(beam_rank_diagnostics, "apply_schema", lambda _conn: None)
    def fake_fixture_for_run(_fixture_path, _dataset_path, _beam_size, _offset, _length, conversations, _max_questions, _question_types):
        calls["fixture_conversations"] = conversations
        return fixture

    monkeypatch.setattr(beam_rank_diagnostics, "_fixture_for_run", fake_fixture_for_run)
    monkeypatch.setattr(beam_rank_diagnostics, "KontextBenchmarkAdapter", FakeAdapter)

    def fake_add_conversations(_adapter, conversations, session_only=False):
        calls["conversation_count"] = len(conversations)
        calls["session_only"] = session_only

    monkeypatch.setattr(beam_rank_diagnostics, "_add_conversations", fake_add_conversations)
    monkeypatch.setattr(
        beam_rank_diagnostics,
        "diagnose_question",
        lambda _adapter, question, _window, semantic_rerank=False, cross_encoder_rerank=False: {
            "question_id": question["question_id"],
            "first_hit_rank": 1,
            "semantic_rerank": semantic_rerank,
            "cross_encoder_rerank": cross_encoder_rerank,
        },
    )

    summary = beam_rank_diagnostics.run(
        SimpleNamespace(
            schema="beam_diag_test",
            fixture_path="fixture.json",
            dataset_path="dataset.json",
            beam_size="10M",
            offset=7,
            length=1,
            conversations="1",
            max_questions=4,
            question_types="instruction_following,preference",
            environ={
                "KONTEXT_BENCHMARK_CROSS_ENCODER_RRF_K": "17",
            },
            question_id=None,
            window=50,
            session_only=True,
            semantic_rerank=True,
            cross_encoder_rerank=True,
        )
    )

    assert calls == {
        "dataset": "beam_10M",
        "run_id": "beam_diag_test",
        "fixture_conversations": "1",
        "semantic_enabled": True,
        "cross_encoder_enabled": True,
        "conversation_count": 1,
        "session_only": True,
        "semantic_rrf_k": 10,
        "cross_encoder_rrf_k": 17,
    }
    assert summary["diagnostics"] == [
        {
            "question_id": "q1",
            "first_hit_rank": 1,
            "semantic_rerank": True,
            "cross_encoder_rerank": True,
        }
    ]
    assert summary["summary"]["hits_at_k"]["50"]["passed"] == 1
