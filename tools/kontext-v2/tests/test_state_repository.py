from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema
from kontext_v2.state_ingestion import accept_reviewed_state_candidate, stage_state_event_proposals


def _state_table_counts(conn, namespace: str) -> dict[str, int]:
    with conn.cursor() as cur:
        return {
            "subjects": cur.execute(
                "SELECT count(*) FROM state_subjects WHERE namespace = %s",
                (namespace,),
            ).fetchone()[0],
            "candidates": cur.execute(
                "SELECT count(*) FROM state_event_candidates WHERE namespace = %s",
                (namespace,),
            ).fetchone()[0],
            "accepted_candidates": cur.execute(
                "SELECT count(*) FROM state_event_candidates WHERE namespace = %s AND status = 'accepted'",
                (namespace,),
            ).fetchone()[0],
            "events": cur.execute(
                "SELECT count(*) FROM state_events WHERE namespace = %s",
                (namespace,),
            ).fetchone()[0],
            "edges": cur.execute(
                "SELECT count(*) FROM state_event_edges WHERE namespace = %s",
                (namespace,),
            ).fetchone()[0],
            "facts": cur.execute(
                "SELECT count(*) FROM current_state_facts WHERE namespace = %s",
                (namespace,),
            ).fetchone()[0],
        }


def _stage_repository_candidate(repo: KontextRepository, namespace: str, suffix: str = "one"):
    return repo.stage_state_event_candidate(
        namespace=namespace,
        source_kind="fixture",
        source_id=f"fixture-{suffix}",
        source_hash=f"state-source-{suffix}",
        source_span_hash=f"span-{suffix}",
        subject_type="Project",
        subject_key="OB1 / Kontext",
        state_key="Project Status",
        event_type="assertion",
        value={"text": f"state model {suffix}"},
        value_text=f"state model {suffix}",
        trust_tier="benchmark_fixture",
        status="staged",
        extractor_version="fixture-v1",
    )


def test_repository_stages_accepts_rebuilds_and_searches_current_state():
    namespace = f"test:{uuid.uuid4()}"
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)

        first_candidate = repo.stage_state_event_candidate(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-1",
            source_hash="state-source-1",
            source_span_hash="span-1",
            subject_type="Project",
            subject_key="OB1 / Kontext",
            state_key="Project Status",
            event_type="assertion",
            value={"text": "state model not built"},
            value_text="state model not built",
            trust_tier="benchmark_fixture",
            status="staged",
            extractor_version="fixture-v1",
        )
        replayed_candidate = repo.stage_state_event_candidate(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-1",
            source_hash="state-source-1",
            source_span_hash="span-1",
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project_status",
            event_type="assertion",
            value={"text": "state model not built"},
            value_text="state model not built",
            trust_tier="benchmark_fixture",
            status="staged",
            extractor_version="fixture-v1",
        )

        assert replayed_candidate.id == first_candidate.id

        old_event = repo.accept_state_event_candidate(first_candidate.id, namespace=namespace)
        accepted_again = repo.accept_state_event_candidate(first_candidate.id, namespace=namespace)
        assert accepted_again.id == old_event.id

        new_event = repo.insert_state_event(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-2",
            source_hash="state-source-2",
            source_span_hash="span-2",
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project_status",
            event_type="assertion",
            value={"text": "state model foundation implemented"},
            value_text="state model foundation implemented",
            trust_tier="benchmark_fixture",
            extractor_version="fixture-v1",
        )
        repo.insert_state_event_edge(
            namespace=namespace,
            source_event_id=new_event.id,
            target_event_id=old_event.id,
            edge_type="supersedes",
            state_key="project_status",
        )

        facts = repo.rebuild_current_state_projection(namespace=namespace)
        search_rows = repo.search_current_state_facts(
            "What is the current OB1 Kontext project status?",
            namespace=namespace,
            top_k=3,
        )
        history = repo.list_state_history(
            namespace=namespace,
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project_status",
        )

    assert len(facts) == 1
    assert facts[0].status == "active"
    assert facts[0].fact_text == "state model foundation implemented"
    assert facts[0].superseded_event_ids == [old_event.id]
    assert search_rows[0]["_retrieval_path"] == "state_projection"
    assert search_rows[0]["text"] == "state model foundation implemented"
    assert {event.id for event in history["events"]} == {old_event.id, new_event.id}
    assert history["edges"][0].edge_type == "supersedes"


def test_repository_lists_state_candidate_headers_and_sanitized_counts():
    namespace = f"test:{uuid.uuid4()}"
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)

        first = repo.stage_state_event_candidate(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-headers",
            source_hash="state-source-headers",
            source_span_hash="span-headers",
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project.status",
            event_type="status_change",
            value={"text": "raw status text should not appear in counts"},
            value_text="raw status text should not appear in counts",
            trust_tier="benchmark_fixture",
            status="needs_review",
            extractor_version="fixture-v2",
        )
        replayed = repo.stage_state_event_candidate(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-headers",
            source_hash="state-source-headers",
            source_span_hash="span-headers",
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project.status",
            event_type="status_change",
            value={"text": "raw status text should not appear in counts"},
            value_text="raw status text should not appear in counts",
            trust_tier="benchmark_fixture",
            status="needs_review",
            extractor_version="fixture-v2",
        )

        candidates = repo.list_state_event_candidates(namespace=namespace, status="needs_review")
        counts = repo.state_model_counts(namespace=namespace)

    assert replayed.id == first.id
    assert [candidate.id for candidate in candidates] == [first.id]
    assert candidates[0].status == "needs_review"
    assert counts == {
        "namespace": namespace,
        "candidates_by_status": {"needs_review": 1},
        "events": 0,
        "facts_by_status": {},
    }


def test_repository_state_event_hash_allows_time_distinct_reassertions():
    namespace = f"test:{uuid.uuid4()}"
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)

        first = repo.insert_state_event(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-reassert",
            source_hash="state-source-reassert",
            source_span_hash="span-reassert",
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project.status",
            event_type="assertion",
            value={"text": "state model foundation implemented"},
            value_text="state model foundation implemented",
            trust_tier="benchmark_fixture",
            extractor_version="fixture-v1",
            effective_at="2026-06-01T00:00:00+00:00",
            observed_at="2026-06-01T00:00:00+00:00",
        )
        second = repo.insert_state_event(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-reassert",
            source_hash="state-source-reassert",
            source_span_hash="span-reassert",
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project.status",
            event_type="assertion",
            value={"text": "state model foundation implemented"},
            value_text="state model foundation implemented",
            trust_tier="benchmark_fixture",
            extractor_version="fixture-v1",
            effective_at="2026-06-03T00:00:00+00:00",
            observed_at="2026-06-03T00:00:00+00:00",
        )

    assert first.id != second.id
    assert first.event_hash != second.event_hash


def test_repository_rejects_state_event_edge_key_that_mismatches_endpoint_events():
    namespace = f"test:{uuid.uuid4()}"
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)

        old_event = repo.insert_state_event(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-old",
            source_hash="state-source-edge-old",
            source_span_hash="span-old",
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project.status",
            event_type="assertion",
            value={"text": "state model not built"},
            value_text="state model not built",
            trust_tier="benchmark_fixture",
            extractor_version="fixture-v1",
        )
        new_event = repo.insert_state_event(
            namespace=namespace,
            source_kind="fixture",
            source_id="fixture-new",
            source_hash="state-source-edge-new",
            source_span_hash="span-new",
            subject_type="project",
            subject_key="ob1-kontext",
            state_key="project.status",
            event_type="assertion",
            value={"text": "state model foundation implemented"},
            value_text="state model foundation implemented",
            trust_tier="benchmark_fixture",
            extractor_version="fixture-v1",
        )

        with pytest.raises(ValueError, match="state edge state_key must match"):
            repo.insert_state_event_edge(
                namespace=namespace,
                source_event_id=new_event.id,
                target_event_id=old_event.id,
                edge_type="supersedes",
                state_key="project.status.drifted",
            )


def test_accept_candidate_rolls_back_subject_when_event_insert_fails(monkeypatch):
    namespace = f"test:{uuid.uuid4()}"
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        candidate = _stage_repository_candidate(repo, namespace, "rollback-insert")

        def fail_insert_event(**kwargs):
            raise RuntimeError("simulated event insert failure")

        monkeypatch.setattr(repo, "insert_state_event", fail_insert_event)

        with pytest.raises(RuntimeError, match="simulated event insert failure"):
            repo.accept_state_event_candidate(candidate.id, namespace=namespace)

        counts = _state_table_counts(conn, namespace)

    assert counts == {
        "subjects": 0,
        "candidates": 1,
        "accepted_candidates": 0,
        "events": 0,
        "edges": 0,
        "facts": 0,
    }


def test_review_accept_rolls_back_event_when_edge_insert_fails():
    namespace = f"test:{uuid.uuid4()}"
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        candidate = _stage_repository_candidate(repo, namespace, "rollback-edge")

        with pytest.raises(ValueError, match="state edge requires existing source and target events"):
            accept_reviewed_state_candidate(
                repo,
                candidate.id,
                namespace=namespace,
                edges=[
                    {
                        "target_event_id": str(uuid.uuid4()),
                        "edge_type": "supports",
                        "state_key": "project.status",
                        "reason": "invalid target should roll back accepted event",
                    }
                ],
                rebuild_projection=True,
            )

        counts = _state_table_counts(conn, namespace)

    assert counts == {
        "subjects": 0,
        "candidates": 1,
        "accepted_candidates": 0,
        "events": 0,
        "edges": 0,
        "facts": 0,
    }


def test_auto_accept_projection_failure_does_not_commit_partial_state(monkeypatch):
    namespace = f"test:{uuid.uuid4()}"
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    monkeypatch.setenv("KONTEXT_STATE_MODEL_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_INGESTION_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_STATE_AUTO_ACCEPT_ENABLED", "1")

    raw = {
        "state_events": [
            {
                "source_kind": "fixture",
                "source_id": "fixture-auto-rollback",
                "source_span": "span-auto-rollback",
                "subject_type": "Project",
                "subject_key": "OB1 / Kontext",
                "state_key": "Project Status",
                "event_type": "assertion",
                "value": {"text": "auto accept should roll back"},
                "value_text": "auto accept should roll back",
                "trust_tier": "benchmark_fixture",
            }
        ]
    }

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)

        def fail_projection(**kwargs):
            raise RuntimeError("simulated projection failure")

        monkeypatch.setattr(repo, "rebuild_current_state_projection", fail_projection)

        result = stage_state_event_proposals(
            repo,
            raw,
            namespace=namespace,
            source_hash="state-source-auto-rollback",
            extractor_version="fixture-v1",
            allow_auto_accept=True,
        )
        counts = _state_table_counts(conn, namespace)

    assert result["counts"]["auto_accepted"] == 0
    assert result["counts"]["errors"] == ["RuntimeError"]
    assert counts == {
        "subjects": 0,
        "candidates": 0,
        "accepted_candidates": 0,
        "events": 0,
        "edges": 0,
        "facts": 0,
    }
