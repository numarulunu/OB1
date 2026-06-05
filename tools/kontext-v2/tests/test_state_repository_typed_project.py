from __future__ import annotations

from kontext_v2.models import CurrentStateFact
from kontext_v2.repository import KontextRepository


def test_project_typed_state_rebuilds_projection_with_typed_emitter(monkeypatch):
    repo = object.__new__(KontextRepository)
    fact = CurrentStateFact(
        id=None,
        namespace="benchmark:beam",
        subject_id="subject-1",
        state_key="project.status",
        fact_value={"text": "green"},
        fact_text="Current status is green.",
        active_event_id="event-current",
        support_event_ids=["event-current"],
        superseded_event_ids=[],
        cancelled_event_ids=[],
        confidence=0.9,
        trust_tier="benchmark_fixture",
        status="active",
        effective_at=None,
        projection_version="projection-test",
        metadata={},
    )
    calls: dict[str, object] = {}

    def fake_rebuild_current_state_projection(**kwargs):
        calls.update(kwargs)
        typed = kwargs["typed_answer_emitter"](fact)
        return [
            CurrentStateFact(
                **{
                    **fact.__dict__,
                    "metadata": {"typed_state_v2": typed},
                }
            )
        ]

    monkeypatch.setattr(repo, "rebuild_current_state_projection", fake_rebuild_current_state_projection)

    facts = repo.project_typed_state(namespace="Benchmark:BEAM", projection_version="projection-test")

    assert calls["namespace"] == "Benchmark:BEAM"
    assert calls["projection_version"] == "projection-test"
    assert facts[0].metadata["typed_state_v2"]["schema_version"] == "typed-state-v2"
    assert facts[0].metadata["typed_state_v2"]["event_relation"] == "supports"
