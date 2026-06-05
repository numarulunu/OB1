from __future__ import annotations

from kontext_v2.retrieval import search_memories, state_projection_rows


class FakeStateRepo:
    def __init__(self) -> None:
        self.state_queries: list[dict] = []

    def search_current_state_facts(self, query: str, *, namespace: str = "live", top_k: int = 5) -> list[dict]:
        self.state_queries.append({"query": query, "namespace": namespace, "top_k": top_k})
        return [
            {
                "external_mem0_id": "state-live-project-status",
                "title": "Current state: project.status",
                "text": "OB1 state model foundation is implemented.",
                "fact_value": {"text": "OB1 state model foundation is implemented."},
                "state_event_type": "status_change",
                "metadata": {
                    "retrieval_path": "state_projection",
                    "namespace": namespace,
                    "state_key": "project.status",
                    "state_status": "active",
                    "active_event_id": "event-current",
                    "support_event_ids": ["event-current", "event-support"],
                    "superseded_event_ids": ["event-old"],
                },
                "memory_type": "current_state",
                "current_status": "active",
                "memory_tier": "active",
                "signal_strength": 10.0,
                "rank": 1.0,
                "_retrieval_path": "state_projection",
            }
        ]

    def list_memory_rows(self, limit: int = 1000, offset: int = 0, **_: object) -> list[dict]:
        return [
            {
                "external_mem0_id": "memory-stale-project-status",
                "title": "Older project status",
                "text": "OB1 state model is not built yet.",
                "metadata": {"domains": ["ai", "systems"], "current_status": "active"},
                "memory_type": "project_state",
                "current_status": "active",
                "memory_tier": "active",
                "signal_strength": 8.0,
                "rank": 0.0,
            }
        ]


class FakeCancelledStateRepo(FakeStateRepo):
    def search_current_state_facts(self, query: str, *, namespace: str = "live", top_k: int = 5) -> list[dict]:
        self.state_queries.append({"query": query, "namespace": namespace, "top_k": top_k})
        return [
            {
                "external_mem0_id": "state-live-project-status-cancelled",
                "title": "Current state: project.status",
                "text": "Current project status was cancelled.",
                "fact_value": {},
                "state_event_type": "cancellation",
                "metadata": {
                    "retrieval_path": "state_projection",
                    "namespace": namespace,
                    "state_key": "project.status",
                    "state_status": "cancelled",
                    "active_event_id": "event-cancel",
                    "support_event_ids": [],
                    "superseded_event_ids": [],
                    "cancelled_event_ids": ["event-old"],
                },
                "memory_type": "current_state",
                "current_status": "cancelled",
                "memory_tier": "active",
                "signal_strength": 0.0,
                "rank": 1.0,
                "_retrieval_path": "state_projection",
            }
        ]


def test_state_projection_routing_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KONTEXT_STATE_ROUTING_ENABLED", raising=False)
    monkeypatch.delenv("KONTEXT_STATE_MODEL_ENABLED", raising=False)
    repo = FakeStateRepo()

    rows = search_memories(
        repo,
        "What is the current OB1 state model status?",
        top_k=3,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert repo.state_queries == []
    assert rows[0]["external_mem0_id"] == "memory-stale-project-status"


def test_explicit_current_state_query_uses_projection_first(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "1")
    repo = FakeStateRepo()

    rows = search_memories(
        repo,
        "What is the current OB1 state model status?",
        top_k=3,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert repo.state_queries == [
        {"query": "What is the current OB1 state model status?", "namespace": "live", "top_k": 3}
    ]
    assert rows[0]["external_mem0_id"] == "state-live-project-status"
    assert rows[0]["_retrieval_path"] == "state_projection"


def test_state_projection_rows_lift_typed_event_contract(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "1")
    monkeypatch.delenv("KONTEXT_TYPED_STATE_V2", raising=False)
    repo = FakeStateRepo()

    rows = state_projection_rows(repo, "What is the current OB1 state model status?", 3)

    assert rows[0]["current_value"] == {"text": "OB1 state model foundation is implemented."}
    assert rows[0]["state_event_type"] == "status_change"
    assert rows[0]["state_key"] == "project.status"
    assert rows[0]["active_event_id"] == "event-current"
    assert rows[0]["support_event_ids"] == ["event-current", "event-support"]
    assert rows[0]["superseded_event_ids"] == ["event-old"]
    assert rows[0]["_retrieval_rank"] == 1
    assert "typed_state_v2" not in rows[0]["metadata"]


def test_state_projection_rows_attach_typed_state_v2_when_flagged(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_TYPED_STATE_V2", "1")
    repo = FakeStateRepo()

    rows = state_projection_rows(repo, "What is the current OB1 state model status?", 3)

    typed = rows[0]["metadata"]["typed_state_v2"]
    assert rows[0]["typed_state_v2"] == typed
    assert typed["schema_version"] == "typed-state-v2"
    assert typed["status"] == "active"
    assert typed["event_relation"] == "supersedes"
    assert typed["state_key"] == "project.status"
    assert typed["active_event_id"] == "event-current"


def test_state_projection_rows_use_caller_namespace_not_env(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_NAMESPACE", "live")
    repo = FakeStateRepo()

    rows = search_memories(
        repo,
        "What is the current OB1 state model status?",
        top_k=3,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
        namespace="Benchmark:BEAM",
    )

    assert repo.state_queries == [
        {"query": "What is the current OB1 state model status?", "namespace": "benchmark:beam", "top_k": 3}
    ]
    assert rows[0]["metadata"]["namespace"] == "benchmark:beam"


def test_cancelled_projection_suppresses_stale_memory_fallthrough(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "1")
    repo = FakeCancelledStateRepo()

    rows = search_memories(
        repo,
        "What is the current OB1 state model status?",
        top_k=3,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert [row["external_mem0_id"] for row in rows] == ["state-live-project-status-cancelled"]
    assert rows[0]["state_status"] == "cancelled"
    assert rows[0]["metadata"]["state_status"] == "cancelled"


def test_state_projection_rows_dominate_merge_via_score(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "1")
    repo = FakeStateRepo()

    rows = search_memories(
        repo,
        "What is the current OB1 state model status?",
        top_k=3,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert rows[0]["external_mem0_id"] == "state-live-project-status"
    assert rows[0]["_retrieval_score"] > rows[1]["_retrieval_score"]


def test_history_query_does_not_use_projection_only_route(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_ROUTING_ENABLED", "1")
    repo = FakeStateRepo()

    rows = search_memories(
        repo,
        "When did the OB1 state model status change?",
        top_k=3,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert repo.state_queries == [
        {"query": "When did the OB1 state model status change?", "namespace": "live", "top_k": 3}
    ]
    assert [row["external_mem0_id"] for row in rows[:2]] == [
        "state-live-project-status",
        "memory-stale-project-status",
    ]
