from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kontext_v2.state_ingestion import (
    accept_reviewed_state_candidate,
    normalize_state_event_proposals,
    stage_state_event_proposals,
    state_ingestion_status,
)


class FakeStateIngestionRepo:
    def __init__(self) -> None:
        self.staged: list[dict] = []
        self.accepted: list[str] = []
        self.accepted_namespaces: list[str] = []
        self.edges: list[dict] = []
        self.rebuilt: list[str] = []

    def stage_state_event_candidate(self, **kwargs):
        self.staged.append(kwargs)
        return SimpleNamespace(
            id=f"candidate-{len(self.staged)}",
            namespace=kwargs["namespace"],
            subject_type=kwargs["subject_type"],
            subject_key=kwargs["subject_key"],
            state_key=kwargs["state_key"],
            event_type=kwargs["event_type"],
            value_hash="hash-from-repo",
            idempotency_key="idem-from-repo",
            trust_tier=kwargs["trust_tier"],
            status=kwargs["status"],
        )

    def accept_state_event_candidate(self, candidate_id: str, *, namespace: str):
        self.accepted.append(candidate_id)
        self.accepted_namespaces.append(namespace)
        return SimpleNamespace(
            id=f"event-{len(self.accepted)}",
            candidate_id=candidate_id,
            namespace=namespace,
            state_key="project.status",
            event_type="status_change",
            value_hash="accepted-value-hash",
        )

    def insert_state_event_edge(self, **kwargs):
        self.edges.append(kwargs)
        return SimpleNamespace(id=len(self.edges), **kwargs)

    def rebuild_current_state_projection(self, *, namespace: str):
        self.rebuilt.append(namespace)
        return [SimpleNamespace(id="fact-1"), SimpleNamespace(id="fact-2")]

    def state_model_counts(self, *, namespace: str):
        return {
            "namespace": namespace,
            "candidates_by_status": {"needs_review": 1},
            "events": 0,
            "facts_by_status": {},
        }


def _raw_state_events(secret: str = "secret-token-123") -> dict:
    return {
        "state_events": [
            {
                "source_kind": "benchmark",
                "source_id": "beam-hard-6",
                "source_span": "turn:1",
                "subject_type": "Project",
                "subject_key": "OB1 / Kontext",
                "state_key": "project.status",
                "event_type": "Status_Change",
                "value": {"text": f"state ingestion staged {secret}"},
                "value_text": f"state ingestion staged {secret}",
                "confidence": 0.92,
                "trust_tier": "extracted_high",
            }
        ]
    }


def _raw_state_events_pair() -> dict:
    raw = _raw_state_events(secret="first-secret")
    second = dict(raw["state_events"][0])
    second["source_span"] = "turn:2"
    second["value"] = {"text": "second-secret"}
    second["value_text"] = "second-secret"
    raw["state_events"].append(second)
    return raw


def test_normalize_state_event_proposals_builds_stable_stage_payload_without_raw_result_leak():
    proposals = normalize_state_event_proposals(
        _raw_state_events(),
        namespace="Benchmark:BEAM",
        source_hash="source-hash-1",
        extractor_version="state-extractor-v1",
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.namespace == "benchmark:beam"
    assert proposal.subject_type == "project"
    assert proposal.subject_key == "ob1-kontext"
    assert proposal.state_key == "project.status"
    assert proposal.event_type == "status_change"
    assert proposal.source_span_hash
    assert proposal.value_hash
    assert proposal.idempotency_key
    assert "secret-token-123" not in json.dumps(proposal.safe_result(), sort_keys=True)


def test_state_event_staging_is_disabled_until_flags_are_enabled(monkeypatch):
    monkeypatch.delenv("KONTEXT_STATE_MODEL_ENABLED", raising=False)
    monkeypatch.delenv("KONTEXT_STATE_INGESTION_ENABLED", raising=False)
    repo = FakeStateIngestionRepo()

    result = stage_state_event_proposals(
        repo,
        _raw_state_events(),
        namespace="benchmark:beam",
        source_hash="source-hash-2",
        extractor_version="state-extractor-v1",
    )

    assert result["mode"] == "disabled"
    assert result["writes_applied"] == 0
    assert repo.staged == []
    assert repo.accepted == []


def test_state_event_staging_creates_review_candidates_without_auto_accept(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_AUTO_ACCEPT_ENABLED", "1")
    repo = FakeStateIngestionRepo()

    result = stage_state_event_proposals(
        repo,
        _raw_state_events(),
        namespace="benchmark:beam",
        source_hash="source-hash-3",
        extractor_version="state-extractor-v1",
    )

    assert result["mode"] == "stage"
    assert result["writes_applied"] == 1
    assert result["counts"] == {"staged": 1, "auto_accepted": 0, "skipped": 0, "errors": []}
    assert repo.staged[0]["status"] == "needs_review"
    assert repo.staged[0]["namespace"] == "benchmark:beam"
    assert repo.staged[0]["subject_key"] == "ob1-kontext"
    assert repo.staged[0]["state_key"] == "project.status"
    assert repo.accepted == []
    assert result["results"][0]["review_status"] == "needs_review"
    assert "secret-token-123" not in json.dumps(result, sort_keys=True)


def test_auto_accept_requires_env_and_call_arg_then_rebuilds_once_per_namespace(monkeypatch):
    repo = FakeStateIngestionRepo()

    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_AUTO_ACCEPT_ENABLED", "1")

    result = stage_state_event_proposals(
        repo,
        _raw_state_events_pair(),
        namespace="Benchmark:BEAM",
        source_hash="source-hash-auto",
        extractor_version="state-extractor-v1",
        allow_auto_accept=True,
    )

    assert result["counts"] == {"staged": 2, "auto_accepted": 2, "skipped": 0, "errors": []}
    assert repo.accepted == ["candidate-1", "candidate-2"]
    assert repo.accepted_namespaces == ["benchmark:beam", "benchmark:beam"]
    assert repo.rebuilt == ["benchmark:beam"]
    assert all(item["auto_accepted"] is True for item in result["results"])
    assert "first-secret" not in json.dumps(result, sort_keys=True)
    assert "second-secret" not in json.dumps(result, sort_keys=True)


def test_auto_accept_stays_off_when_auto_accept_flag_is_absent(monkeypatch):
    repo = FakeStateIngestionRepo()

    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")
    monkeypatch.delenv("KONTEXT_STATE_AUTO_ACCEPT_ENABLED", raising=False)

    result = stage_state_event_proposals(
        repo,
        _raw_state_events_pair(),
        namespace="Benchmark:BEAM",
        source_hash="source-hash-auto-off",
        extractor_version="state-extractor-v1",
        allow_auto_accept=True,
    )

    assert result["counts"] == {"staged": 2, "auto_accepted": 0, "skipped": 0, "errors": []}
    assert repo.accepted == []
    assert repo.rebuilt == []
    assert all(item["auto_accepted"] is False for item in result["results"])


def test_accept_reviewed_candidate_is_the_only_event_projection_write_path():
    repo = FakeStateIngestionRepo()

    result = accept_reviewed_state_candidate(
        repo,
        "candidate-1",
        namespace="Benchmark:BEAM",
        edges=[
            {
                "target_event_id": "event-0",
                "edge_type": "supersedes",
                "state_key": "project.status",
                "reason": "newer reviewed state",
            }
        ],
        rebuild_projection=True,
    )

    assert repo.accepted == ["candidate-1"]
    assert repo.accepted_namespaces == ["benchmark:beam"]
    assert repo.edges == [
        {
            "namespace": "benchmark:beam",
            "source_event_id": "event-1",
            "target_event_id": "event-0",
            "edge_type": "supersedes",
            "state_key": "project.status",
            "reason_hash": result["edges"][0]["reason_hash"],
        }
    ]
    assert repo.rebuilt == ["benchmark:beam"]
    assert result["mode"] == "accept"
    assert result["event_id"] == "event-1"
    assert result["review_status"] == "accepted"
    assert result["projection_fact_count"] == 2
    assert "newer reviewed state" not in json.dumps(result, sort_keys=True)


def test_state_ingestion_status_uses_sanitized_counts_only(monkeypatch):
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")
    repo = FakeStateIngestionRepo()

    result = state_ingestion_status(repo, namespace="Benchmark:BEAM")

    assert result == {
        "enabled": True,
        "namespace": "benchmark:beam",
        "candidates_by_status": {"needs_review": 1},
        "events": 0,
        "facts_by_status": {},
    }
