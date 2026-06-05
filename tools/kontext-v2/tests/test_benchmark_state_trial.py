from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kontext_v2.benchmarks.fixtures import load_beam_real_fixture
from kontext_v2.benchmarks.state_trial import (
    _projected_answer_value,
    _state_key,
    build_benchmark_state_events,
    build_judged_bundle_state_events,
    run_benchmark_state_trial,
    run_judged_bundle_state_trial,
    write_state_trial_report,
)
from kontext_v2.models import CurrentStateFact


FIXTURE = Path(__file__).parent / "fixtures" / "beam_real_shape.json"


class FakeStateTrialRepo:
    def __init__(self) -> None:
        self.staged: list[dict] = []
        self.accepted: list[str] = []
        self.edges: list[dict] = []
        self.rebuilt: list[str] = []
        self.state_searches: list[dict] = []

    def stage_state_event_candidate(self, **kwargs):
        self.staged.append(kwargs)
        return SimpleNamespace(id=f"candidate-{len(self.staged)}", status=kwargs["status"])

    def accept_state_event_candidate(self, candidate_id: str, *, namespace: str):
        self.accepted.append(candidate_id)
        index = int(candidate_id.rsplit("-", 1)[-1])
        staged = self.staged[index - 1]
        if namespace != staged["namespace"]:
            raise ValueError("namespace mismatch")
        return SimpleNamespace(
            id=f"event-{index}",
            candidate_id=candidate_id,
            namespace=staged["namespace"],
            state_key=staged["state_key"],
            event_type=staged["event_type"],
            value_hash=f"value-hash-{index}",
        )

    def insert_state_event_edge(self, **kwargs):
        self.edges.append(kwargs)
        return SimpleNamespace(id=len(self.edges), **kwargs)

    def rebuild_current_state_projection(self, *, namespace: str):
        self.rebuilt.append(namespace)
        return [SimpleNamespace(id="fact-1")]

    def search_current_state_facts(self, query: str, *, namespace: str, top_k: int):
        self.state_searches.append({"query": query, "namespace": namespace, "top_k": top_k})
        return [
            {
                "external_mem0_id": "state:fact-1",
                "text": "projected state row text should not leak",
                "metadata": {
                    "retrieval_path": "state_projection",
                    "namespace": namespace,
                    "active_event_id": "event-2",
                    "support_event_ids": ["event-2"],
                },
            }
        ]

    def list_memory_rows(self, limit: int = 1000, offset: int = 0, **_: object) -> list[dict]:
        return [
            {
                "external_mem0_id": "memory-stale-state",
                "title": "Stale benchmark memory",
                "text": "The current state projection should outrank this stale memory.",
                "metadata": {"domains": ["ai", "systems"], "current_status": "active"},
                "memory_type": "project_state",
                "current_status": "active",
                "memory_tier": "active",
                "signal_strength": 5.0,
            }
        ]

    def state_model_counts(self, *, namespace: str):
        return {
            "namespace": namespace,
            "candidates_by_status": {"accepted": len(self.accepted) or len(self.staged)},
            "events": len(self.accepted),
            "facts_by_status": {"active": len(self.rebuilt)},
        }


def test_build_benchmark_state_events_extracts_state_questions_only():
    fixture = load_beam_real_fixture(FIXTURE, beam_size="1M")

    plan = build_benchmark_state_events(
        fixture,
        namespace="Benchmark:BEAM Trial",
        source_hash="fixture-source",
        extractor_version="trial-v1",
    )

    assert len(plan.state_events) == 2
    assert {item["event_type"] for item in plan.state_events} == {"knowledge_update"}
    assert {item["metadata"]["source_id"] for item in plan.state_events} == {"101", "201"}
    assert {item["state_key"] for item in plan.state_events} == {plan.questions[0]["state_key"]}
    assert plan.questions[0]["category"] == "knowledge_update"
    assert plan.questions[0]["evidence_source_ids"] == ["101", "201"]


def test_state_key_uses_full_question_hash_suffix():
    question_hash = "0123456789abcdef"

    assert _state_key("preference_following", question_hash) == "benchmark.preference_following.0123456789abcdef"


def test_run_benchmark_state_trial_reports_sanitized_projection_impact(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    fixture = load_beam_real_fixture(FIXTURE, beam_size="1M")
    repo = FakeStateTrialRepo()

    report = run_benchmark_state_trial(
        repo,
        fixture,
        namespace="benchmark:beam-trial",
        source_hash="fixture-source",
        extractor_version="trial-v1",
        top_k=3,
        baseline_results=[
            {
                "question_id": "beam-sample-1-q-3-knowledge_update",
                "matched": False,
                "search_results": [],
            }
        ],
    )

    assert report["ok"] is True
    assert report["writes_applied"] == 5
    assert report["counts"]["state_events_planned"] == 2
    assert report["counts"]["candidates_staged"] == 2
    assert report["counts"]["events_accepted"] == 2
    assert report["counts"]["edges_inserted"] == 1
    assert report["counts"]["projection_queries"] == 1
    assert report["counts"]["projection_matched"] == 1
    assert repo.edges == [
        {
            "namespace": "benchmark:beam-trial",
            "source_event_id": "event-2",
            "target_event_id": "event-1",
            "edge_type": "supersedes",
            "state_key": report["questions"][0]["state_key"],
            "reason_hash": report["questions"][0]["supersession_reason_hash"],
        }
    ]
    serialized = json.dumps(report, sort_keys=True)
    assert "Obsidian vault" not in serialized
    assert "amber notebook" not in serialized
    assert "projected state row text" not in serialized
    assert report["questions"][0]["baseline_matched"] is False
    assert report["questions"][0]["projection_matched"] is True
    assert report["questions"][0]["route"] == "state_projection"


def test_run_benchmark_state_trial_routes_real_question_through_search_memories(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    fixture = load_beam_real_fixture(FIXTURE, beam_size="1M")
    repo = FakeStateTrialRepo()

    report = run_benchmark_state_trial(
        repo,
        fixture,
        namespace="benchmark:beam-trial",
        source_hash="fixture-source",
        extractor_version="trial-v1",
        top_k=3,
    )

    routed_searches = [
        search for search in repo.state_searches if "Where is Avery keeping the deploy checklist now?" in search["query"]
    ]
    assert routed_searches == [
        {
            "query": "Where is Avery keeping the deploy checklist now?",
            "namespace": "benchmark:beam-trial",
            "top_k": 3,
        }
    ]
    assert report["counts"]["routed_projection_queries"] == 1
    assert report["counts"]["routed_projection_matched"] == 1
    assert report["counts"]["beam_routed_projection_matched"] == 1
    assert report["questions"][0]["routed_projection_matched"] is True
    assert report["questions"][0]["routed_route"] == "state_projection"


def test_run_benchmark_state_trial_dry_run_projects_without_accepting(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    fixture = load_beam_real_fixture(FIXTURE, beam_size="1M")
    repo = FakeStateTrialRepo()

    report = run_benchmark_state_trial(
        repo,
        fixture,
        namespace="benchmark:beam-trial",
        source_hash="fixture-source",
        extractor_version="trial-v1",
        top_k=3,
        dry_run=True,
    )

    assert report["ok"] is True
    assert report["dry_run"] is True
    assert repo.accepted == []
    assert repo.edges == []
    assert repo.rebuilt == []
    assert report["writes_applied"] == 2
    assert report["counts"]["events_accepted"] == 0
    assert report["counts"]["events_would_accept"] == 2
    assert report["counts"]["edges_inserted"] == 0
    assert report["counts"]["edges_would_insert"] == 1
    assert report["counts"]["projection_facts"] == 1
    assert report["counts"]["projection_matched"] == 1
    assert report["questions"][0]["projection_matched"] is True
    assert report["questions"][0]["route"] == "state_projection_preview"


def test_run_judged_bundle_state_trial_routes_private_question_through_search_memories(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    repo = FakeStateTrialRepo()

    report = run_judged_bundle_state_trial(
        repo,
        _private_bundle_fixture(),
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
    )

    routed_searches = [search for search in repo.state_searches if "What interface does the user prefer?" in search["query"]]
    assert routed_searches == [
        {
            "query": "What interface does the user prefer?",
            "namespace": "benchmark:private-hard",
            "top_k": 20,
        }
    ]
    assert report["counts"]["routed_projection_queries"] == 1
    assert report["counts"]["routed_active_answer_overlap_questions"] == 1
    assert report["questions"][0]["routed_route"] == "state_projection"


def test_run_benchmark_state_trial_is_disabled_without_benchmark_flag(monkeypatch):
    monkeypatch.delenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", raising=False)
    fixture = load_beam_real_fixture(FIXTURE, beam_size="1M")
    repo = FakeStateTrialRepo()

    report = run_benchmark_state_trial(
        repo,
        fixture,
        namespace="benchmark:beam-trial",
        source_hash="fixture-source",
    )

    assert report["ok"] is False
    assert report["mode"] == "disabled"
    assert report["writes_applied"] == 0
    assert repo.staged == []


@pytest.mark.parametrize("namespace", ["", "   ", "live", "LIVE ", "project:trial"])
def test_run_benchmark_state_trial_rejects_unsafe_namespace_before_writes(monkeypatch, namespace):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    fixture = load_beam_real_fixture(FIXTURE, beam_size="1M")
    repo = FakeStateTrialRepo()

    with pytest.raises(ValueError, match="benchmark state trial namespace"):
        run_benchmark_state_trial(
            repo,
            fixture,
            namespace=namespace,
            source_hash="fixture-source",
        )

    assert repo.staged == []
    assert repo.accepted == []
    assert repo.rebuilt == []


def test_write_state_trial_report_is_sanitized(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    fixture = load_beam_real_fixture(FIXTURE, beam_size="1M")
    report = run_benchmark_state_trial(
        FakeStateTrialRepo(),
        fixture,
        namespace="benchmark:beam-trial",
        source_hash="fixture-source",
    )

    paths = write_state_trial_report(report, tmp_path)
    text = paths["json"].read_text(encoding="utf-8") + paths["markdown"].read_text(encoding="utf-8")

    assert "Obsidian vault" not in text
    assert "amber notebook" not in text
    assert "BEAM state trial" in paths["markdown"].read_text(encoding="utf-8")


def _private_bundle_fixture() -> dict:
    return {
        "dataset": "beam_1M",
        "run_id": "private-hard-slice",
        "contains_raw_benchmark_text": True,
        "private_do_not_log": True,
        "questions": [
            {
                "question_id": "private-pref-1",
                "category": "preference_following",
                "question": "What interface does the user prefer?",
                "ground_truth_answer": "Use the citadel interface.",
                "retrieval_evaluable": True,
                "retrieved_memories_by_top_k": {
                    "20": [
                        {
                            "id": "mem-old",
                            "memory_hash": "oldhash",
                            "memory": "User: I used to prefer the harbor interface.",
                            "metadata": {"timestamp": "2023-06-01", "source_ids": ["old"]},
                        },
                        {
                            "id": "mem-new",
                            "memory_hash": "newhash",
                            "memory": "User: I now prefer the citadel interface.",
                            "metadata": {"timestamp": "2023-06-02", "source_ids": ["new"]},
                        },
                    ]
                },
            },
            {
                "question_id": "private-info-1",
                "category": "information_extraction",
                "question": "What unrelated fact was mentioned?",
                "ground_truth_answer": "This should not create typed state.",
                "retrieval_evaluable": True,
                "retrieved_memories_by_top_k": {
                    "20": [
                        {
                            "id": "mem-info",
                            "memory_hash": "infohash",
                            "memory": "User: The raw private phrase is sandstone.",
                            "metadata": {"timestamp": "2023-06-03", "source_ids": ["info"]},
                        }
                    ]
                },
            },
        ],
    }


def test_build_judged_bundle_state_events_uses_private_bundle_shape_without_information_extraction():
    plan = build_judged_bundle_state_events(
        _private_bundle_fixture(),
        namespace="Benchmark:Private Hard",
        source_hash="private-bundle-hash",
        top_k=20,
        extractor_version="trial-private-v1",
    )

    assert len(plan.questions) == 1
    assert len(plan.state_events) == 2
    assert {event["event_type"] for event in plan.state_events} == {"preference_set"}
    assert [event["metadata"]["memory_hash"] for event in plan.state_events] == ["oldhash", "newhash"]
    assert plan.questions[0]["retrieved_count"] == 2
    assert plan.questions[0]["selected_event_count"] == 2
    assert plan.questions[0]["question_hash"] != "private-pref-1"


def test_build_judged_bundle_state_events_memory_granularity_keeps_full_text_fallback_value():
    plan = build_judged_bundle_state_events(
        _private_bundle_fixture(),
        namespace="Benchmark:Private Hard",
        source_hash="private-bundle-hash",
        top_k=20,
        extractor_version="trial-private-v1",
        event_granularity="memory",
    )

    assert plan.state_events[0]["value"] == {"text": plan.state_events[0]["value_text"]}


def test_run_judged_bundle_state_trial_reports_sanitized_private_projection_overlap(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    repo = FakeStateTrialRepo()

    report = run_judged_bundle_state_trial(
        repo,
        _private_bundle_fixture(),
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
    )

    assert report["ok"] is True
    assert report["mode"] == "judged_bundle_state_trial"
    assert report["counts"]["state_questions"] == 1
    assert report["counts"]["state_events_planned"] == 2
    assert report["counts"]["active_answer_overlap_questions"] == 1
    assert report["questions"][0]["active_answer_overlap_terms"] >= 2
    assert report["questions"][0]["ground_truth_term_count"] >= 2
    assert report["questions"][0]["active_answer_overlap_ratio"] > 0
    assert report["counts"]["best_answer_overlap_questions"] == 1
    assert report["counts"]["projection_selection_loss_questions"] == 0
    assert report["counts"]["best_retrieved_answer_overlap_questions"] == 1
    assert report["counts"]["state_event_extraction_loss_questions"] == 0
    assert report["questions"][0]["best_answer_overlap_terms"] >= report["questions"][0]["active_answer_overlap_terms"]
    assert report["questions"][0]["best_answer_overlap_ratio"] >= report["questions"][0]["active_answer_overlap_ratio"]
    assert report["questions"][0]["best_retrieved_answer_overlap_ratio"] >= report["questions"][0]["best_answer_overlap_ratio"]
    serialized = json.dumps(report, sort_keys=True)
    assert "citadel" not in serialized
    assert "harbor" not in serialized
    assert "sandstone" not in serialized


def test_run_judged_bundle_state_trial_dry_run_projects_without_accepting(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    repo = FakeStateTrialRepo()

    report = run_judged_bundle_state_trial(
        repo,
        _private_bundle_fixture(),
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
        dry_run=True,
    )

    assert report["ok"] is True
    assert report["dry_run"] is True
    assert repo.accepted == []
    assert repo.edges == []
    assert repo.rebuilt == []
    assert report["writes_applied"] == 2
    assert report["counts"]["events_accepted"] == 0
    assert report["counts"]["events_would_accept"] == 2
    assert report["counts"]["edges_inserted"] == 0
    assert report["counts"]["edges_would_insert"] == 1
    assert report["counts"]["projection_facts"] == 1
    assert report["counts"]["active_answer_overlap_questions"] == 1
    assert report["counts"]["projection_selection_loss_questions"] == 0
    assert report["counts"]["state_event_extraction_loss_questions"] == 0
    assert report["questions"][0]["route"] == "state_projection_preview"
    serialized = json.dumps(report, sort_keys=True)
    assert "citadel" not in serialized
    assert "harbor" not in serialized


def test_run_judged_bundle_state_trial_reports_typed_object_summary_when_flagged(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_TYPED_STATE_V2", "1")
    monkeypatch.setenv("KONTEXT_TYPED_OBJECT_SUMMARY", "1")
    repo = FakeStateTrialRepo()

    report = run_judged_bundle_state_trial(
        repo,
        _private_bundle_fixture(),
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
        dry_run=True,
    )
    rendered = json.dumps(report, sort_keys=True)

    assert report["typed_object_status_summary"] == {
        "total": 1,
        "valid_schema": 1,
        "statuses": {"active": 1},
        "event_relations": {"supersedes": 1},
    }
    assert "citadel" not in rendered
    assert "harbor" not in rendered


def test_projected_answer_value_prefers_structured_fact_value_over_raw_text():
    fact = CurrentStateFact(
        id=None,
        namespace="benchmark:private-hard",
        subject_id="subject-1",
        state_key="benchmark.preference.private",
        fact_value={"preference": "citadel interface"},
        fact_text="User: I now prefer the masked interface and mentioned unrelated harbor notes.",
        active_event_id="event-current",
        support_event_ids=["event-current"],
        superseded_event_ids=[],
        cancelled_event_ids=[],
        confidence=1.0,
        trust_tier="benchmark_fixture",
        status="active",
        effective_at=None,
        projection_version="projection-test",
        metadata={},
    )
    text_only = CurrentStateFact(
        **{
            **fact.__dict__,
            "fact_value": {"text": "User: raw text fallback."},
            "fact_text": "User: raw text fallback.",
        }
    )

    assert _projected_answer_value(fact) == "citadel interface"
    assert _projected_answer_value(text_only) == "User: raw text fallback."


def test_run_judged_bundle_state_trial_scores_typed_projected_answer_without_raw_leak(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    bundle = _private_bundle_fixture()
    bundle["questions"][0]["ground_truth_answer"] = "Use the citadel interface."
    bundle["questions"][0]["retrieved_memories_by_top_k"]["20"] = [
        {
            "id": "mem-typed-current",
            "memory_hash": "typedhash",
            "memory": "User: I now prefer the masked interface and mentioned unrelated harbor notes.",
            "metadata": {
                "timestamp": "2023-06-02",
                "source_ids": ["typed-current"],
                "typed_state_value": {"preference": "citadel interface"},
            },
        }
    ]

    report = run_judged_bundle_state_trial(
        FakeStateTrialRepo(),
        bundle,
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
        dry_run=True,
    )
    row = report["questions"][0]
    serialized = json.dumps(report, sort_keys=True)

    assert row["active_answer_overlap_terms"] == row["ground_truth_term_count"]
    assert row["active_answer_overlap_ratio"] == 1.0
    assert "citadel" not in serialized
    assert "masked interface" not in serialized


def test_run_judged_bundle_state_trial_scores_projection_loss_against_projected_candidate_values(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    bundle = _private_bundle_fixture()
    bundle["questions"][0]["ground_truth_answer"] = "Use the citadel interface because it matches the deploy checklist."
    bundle["questions"][0]["retrieved_memories_by_top_k"]["20"] = [
        {
            "id": "mem-old",
            "memory_hash": "oldtypedhash",
            "memory": "User: I prefer the citadel interface because it matches the deploy checklist.",
            "metadata": {
                "timestamp": "2023-06-01",
                "source_ids": ["old-typed"],
                "typed_state_value": {"preference": "citadel interface"},
            },
        },
        {
            "id": "mem-new",
            "memory_hash": "newtypedhash",
            "memory": "User: I now prefer the masked interface and mentioned unrelated harbor notes.",
            "metadata": {
                "timestamp": "2023-06-02",
                "source_ids": ["new-typed"],
                "typed_state_value": {"preference": "citadel interface"},
            },
        },
    ]

    report = run_judged_bundle_state_trial(
        FakeStateTrialRepo(),
        bundle,
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
    )

    row = report["questions"][0]
    serialized = json.dumps(report, sort_keys=True)

    assert report["counts"]["projection_selection_loss_questions"] == 0
    assert row["active_memory_rank"] == 2
    assert row["best_event_memory_rank"] == 2
    assert "citadel" not in serialized
    assert "masked interface" not in serialized


def test_run_judged_bundle_state_trial_reports_projection_selection_loss_without_raw_text(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    bundle = _private_bundle_fixture()
    bundle["questions"][0]["retrieved_memories_by_top_k"]["20"] = [
        {
            "id": "mem-best",
            "memory_hash": "besthash",
            "memory": "User: I prefer the citadel interface.",
            "metadata": {"timestamp": "2023-06-01", "source_ids": ["best"]},
        },
        {
            "id": "mem-latest",
            "memory_hash": "latesthash",
            "memory": "User: I now prefer the harbor interface.",
            "metadata": {"timestamp": "2023-06-02", "source_ids": ["latest"]},
        },
    ]

    report = run_judged_bundle_state_trial(
        FakeStateTrialRepo(),
        bundle,
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
    )

    assert report["counts"]["projection_selection_loss_questions"] == 1
    assert report["questions"][0]["best_answer_overlap_terms"] > report["questions"][0]["active_answer_overlap_terms"]
    assert report["questions"][0]["projection_overlap_gap"] > 0
    assert report["questions"][0]["active_memory_rank"] == 2
    assert report["questions"][0]["best_event_memory_rank"] == 1
    serialized = json.dumps(report, sort_keys=True)
    assert "citadel" not in serialized
    assert "harbor" not in serialized


def test_run_judged_bundle_state_trial_reports_state_event_extraction_loss_without_raw_text(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    bundle = _private_bundle_fixture()
    bundle["questions"][0]["retrieved_memories_by_top_k"]["20"] = [
        {
            "id": "mem-selected",
            "memory_hash": "selectedhash",
            "memory": "User: I now prefer the harbor interface.",
            "metadata": {"timestamp": "2023-06-02", "source_ids": ["selected"]},
        },
        {
            "id": "mem-unselected",
            "memory_hash": "unselectedhash",
            "memory": "Assistant: The citadel interface is the documented setting.",
            "metadata": {"timestamp": "2023-06-03", "source_ids": ["unselected"]},
        },
    ]

    report = run_judged_bundle_state_trial(
        FakeStateTrialRepo(),
        bundle,
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
    )

    assert report["counts"]["state_event_extraction_loss_questions"] == 1
    assert report["questions"][0]["best_retrieved_answer_overlap_terms"] > report["questions"][0]["best_answer_overlap_terms"]
    assert report["questions"][0]["state_event_extraction_gap"] > 0
    serialized = json.dumps(report, sort_keys=True)
    assert "citadel" not in serialized
    assert "harbor" not in serialized


def test_run_judged_bundle_state_trial_memory_granularity_keeps_retrieved_support_sanitized(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    bundle = _private_bundle_fixture()
    bundle["questions"][0]["retrieved_memories_by_top_k"]["20"] = [
        {
            "id": "mem-selected",
            "memory_hash": "selectedhash",
            "memory": "User: I now prefer the harbor interface.",
            "metadata": {"timestamp": "2023-06-02", "source_ids": ["selected"]},
        },
        {
            "id": "mem-unselected",
            "memory_hash": "unselectedhash",
            "memory": "Assistant: The citadel interface is the documented setting.",
            "metadata": {"timestamp": "2023-06-03", "source_ids": ["unselected"]},
        },
    ]

    report = run_judged_bundle_state_trial(
        FakeStateTrialRepo(),
        bundle,
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
        event_granularity="memory",
    )

    assert report["counts"]["state_events_planned"] == 2
    assert report["counts"]["state_event_extraction_loss_questions"] == 0
    assert report["questions"][0]["best_answer_overlap_terms"] == report["questions"][0]["best_retrieved_answer_overlap_terms"]
    assert report["questions"][0]["active_memory_rank"] == 1
    assert report["questions"][0]["best_event_memory_rank"] == 2
    serialized = json.dumps(report, sort_keys=True)
    assert "citadel" not in serialized
    assert "harbor" not in serialized


def test_run_judged_bundle_state_trial_memory_granularity_uses_query_marker_rank_selector(monkeypatch):
    monkeypatch.setenv("KONTEXT_BENCHMARK_STATE_MODEL_ENABLED", "1")
    bundle = _private_bundle_fixture()
    bundle["questions"][0]["question"] = "What staging interface does the user prefer?"
    bundle["questions"][0]["ground_truth_answer"] = "Use the citadel staging interface."
    bundle["questions"][0]["retrieved_memories_by_top_k"]["20"] = [
        {
            "id": "mem-later-generic",
            "memory_hash": "laterhash",
            "memory": "User: I prefer the harbor interface.",
            "metadata": {"timestamp": "2023-06-03", "source_ids": ["later"]},
        },
        {
            "id": "mem-query-specific",
            "memory_hash": "specifichash",
            "memory": "User: I prefer the citadel staging interface.",
            "metadata": {"timestamp": "2023-06-01", "source_ids": ["specific"]},
        },
    ]

    report = run_judged_bundle_state_trial(
        FakeStateTrialRepo(),
        bundle,
        namespace="benchmark:private-hard",
        source_hash="private-bundle-hash",
        extractor_version="trial-private-v1",
        top_k=20,
        event_granularity="memory",
    )

    assert report["counts"]["projection_selection_loss_questions"] == 0
    assert report["questions"][0]["active_memory_rank"] == 2
    assert report["questions"][0]["best_event_memory_rank"] == 2
    assert report["questions"][0]["active_answer_overlap_terms"] == report["questions"][0]["ground_truth_term_count"]
    serialized = json.dumps(report, sort_keys=True)
    assert "citadel" not in serialized
    assert "harbor" not in serialized
