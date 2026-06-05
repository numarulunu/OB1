from __future__ import annotations

import json
from types import SimpleNamespace

from kontext_v2.mcp_bridge import McpProfile, _call_tool
from kontext_v2.mcp_server import list_tools


STATE_TOOL_NAMES = {"state_ingestion_status", "stage_state_events", "accept_state_event_candidate"}


class FakeStateRepo:
    def __init__(self) -> None:
        self.staged: list[dict] = []
        self.accepted: list[str] = []
        self.accepted_namespaces: list[str] = []
        self.edges: list[dict] = []
        self.rebuilt: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def stage_state_event_candidate(self, **kwargs):
        self.staged.append(kwargs)
        return SimpleNamespace(
            id="candidate-1",
            status=kwargs["status"],
            value_hash="repo-value-hash",
            idempotency_key="repo-idem",
        )

    def accept_state_event_candidate(self, candidate_id: str, *, namespace: str):
        self.accepted.append(candidate_id)
        self.accepted_namespaces.append(namespace)
        return SimpleNamespace(
            id="event-1",
            namespace=namespace,
            state_key="project.status",
            event_type="status_change",
            value_hash="accepted-value-hash",
        )

    def insert_state_event_edge(self, **kwargs):
        self.edges.append(kwargs)
        return SimpleNamespace(id=1, **kwargs)

    def rebuild_current_state_projection(self, *, namespace: str):
        self.rebuilt.append(namespace)
        return [SimpleNamespace(id="fact-1")]

    def state_model_counts(self, *, namespace: str):
        return {
            "namespace": namespace,
            "candidates_by_status": {"needs_review": 1},
            "events": 0,
            "facts_by_status": {},
        }


class FakeStateService:
    def __init__(self) -> None:
        self.repo_instance = FakeStateRepo()

    def repo(self):
        return self.repo_instance


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_state_mcp_tools_are_hidden_by_default(monkeypatch):
    monkeypatch.delenv("KONTEXT_STATE_MODEL_ENABLED", raising=False)
    monkeypatch.delenv("KONTEXT_STATE_INGESTION_ENABLED", raising=False)

    names = {tool["name"] for tool in list_tools(write_enabled=True, dry_run_write_enabled=True)}

    assert STATE_TOOL_NAMES.isdisjoint(names)


def test_state_mcp_tools_are_flagged_and_write_gated(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")

    read_only_names = {tool["name"] for tool in list_tools(write_enabled=False, dry_run_write_enabled=True)}
    write_names = {tool["name"] for tool in list_tools(write_enabled=True, dry_run_write_enabled=True)}

    assert "state_ingestion_status" in read_only_names
    assert {"stage_state_events", "accept_state_event_candidate"}.isdisjoint(read_only_names)
    assert STATE_TOOL_NAMES <= write_names


def test_state_mcp_accept_schema_exposes_namespace(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")

    tools = list_tools(write_enabled=True, dry_run_write_enabled=True)
    by_name = {tool["name"]: tool for tool in tools}

    assert "namespace" in by_name["accept_state_event_candidate"]["inputSchema"]["properties"]
    assert "namespace" in by_name["search"]["inputSchema"]["properties"]


def test_state_mcp_stage_tool_stages_sanitized_candidates(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")
    service = FakeStateService()
    profile = McpProfile(name="codex", token="token", can_write=True, can_dry_run_write=True)

    result = _payload(
        _call_tool(
            service,
            profile,
            "stage_state_events",
            {
                "namespace": "benchmark:beam",
                "source_hash": "source-hash-1",
                "extractor_version": "mcp-test-v1",
                "state_events": [
                    {
                        "subject_type": "project",
                        "subject_key": "ob1-kontext",
                        "state_key": "project.status",
                        "event_type": "status_change",
                        "value": {"text": "secret current state"},
                        "value_text": "secret current state",
                        "confidence": 0.9,
                    }
                ],
            },
        )
    )

    assert result["mode"] == "stage"
    assert result["writes_applied"] == 1
    assert service.repo_instance.staged[0]["status"] == "needs_review"
    assert service.repo_instance.accepted == []
    assert result["results"][0]["id"] == "candidate-1"
    assert "secret current state" not in json.dumps(result, sort_keys=True)


def test_state_mcp_accept_tool_accepts_and_rebuilds_without_raw_reason(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")
    service = FakeStateService()
    profile = McpProfile(name="codex", token="token", can_write=True, can_dry_run_write=True)

    result = _payload(
        _call_tool(
            service,
            profile,
            "accept_state_event_candidate",
            {
                "id": "candidate-1",
                "namespace": "benchmark:beam",
                "edges": [
                    {
                        "target_event_id": "event-0",
                        "edge_type": "supersedes",
                        "state_key": "project.status",
                        "reason": "reviewed replacement reason",
                    }
                ],
                "rebuild_projection": True,
            },
        )
    )

    assert service.repo_instance.accepted == ["candidate-1"]
    assert service.repo_instance.accepted_namespaces == ["benchmark:beam"]
    assert service.repo_instance.edges[0]["edge_type"] == "supersedes"
    assert service.repo_instance.rebuilt == ["benchmark:beam"]
    assert result["review_status"] == "accepted"
    assert result["projection_fact_count"] == 1
    assert "reviewed replacement reason" not in json.dumps(result, sort_keys=True)


def test_state_mcp_status_tool_returns_counts_for_readonly_profiles(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")
    service = FakeStateService()
    profile = McpProfile(name="codex", token="token", can_write=False, can_dry_run_write=True)

    result = _payload(_call_tool(service, profile, "state_ingestion_status", {"namespace": "benchmark:beam"}))

    assert result["enabled"] is True
    assert result["namespace"] == "benchmark:beam"
    assert result["candidates_by_status"] == {"needs_review": 1}
