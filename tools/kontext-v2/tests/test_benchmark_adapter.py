from pathlib import Path

import os

import psycopg

from kontext_v2.benchmarks.adapter import BenchmarkMessage, KontextBenchmarkAdapter
from kontext_v2.benchmarks.fixtures import load_locomo_real_fixture, load_locomo_tiny_fixture
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "locomo_tiny.json"
REAL_FIXTURE = Path(__file__).parent / "fixtures" / "locomo_real_shape.json"


def test_load_locomo_tiny_fixture_returns_conversations_and_questions():
    fixture = load_locomo_tiny_fixture(FIXTURE)

    assert fixture["dataset"] == "locomo_tiny"
    assert len(fixture["conversations"]) == 2
    assert fixture["conversations"][0]["conversation_id"] == "tiny-conv-1"
    assert fixture["questions"][0]["question"] == "Where is Alice planning to travel in June?"
    assert fixture["questions"][0]["expected_terms"] == ["berlin", "june"]


def test_load_locomo_real_fixture_normalizes_sessions_and_questions():
    fixture = load_locomo_real_fixture(
        REAL_FIXTURE,
        conversation_indices=[0],
        max_questions=1,
    )

    assert fixture["dataset"] == "locomo10"
    assert len(fixture["conversations"]) == 1
    assert fixture["conversations"][0]["conversation_id"] == "sample-0"
    assert len(fixture["conversations"][0]["sessions"]) == 2
    assert fixture["conversations"][0]["sessions"][0]["session_id"] == "session_1"
    assert fixture["conversations"][0]["sessions"][0]["date"] == "2023-05-07"
    assert fixture["conversations"][0]["sessions"][0]["source_ids"] == ["D1:1", "D1:2"]
    assert fixture["conversations"][0]["sessions"][0]["messages"][0]["role"] == "user"
    assert fixture["conversations"][0]["sessions"][0]["messages"][0]["source_id"] == "D1:1"
    assert fixture["questions"][0]["question_id"] == "sample-0-q-1"
    assert fixture["questions"][0]["category"] == "temporal"
    assert fixture["questions"][0]["evidence"] == ["D1:1"]


def _adapter(run_id: str) -> KontextBenchmarkAdapter:
    conn = psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"])
    apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM memories
            WHERE metadata->>'source' = 'benchmark'
              AND metadata->>'benchmark_run_id' = %s
            """,
            (run_id,),
        )
    conn.commit()
    return KontextBenchmarkAdapter(conn=conn, dataset="locomo_tiny", run_id=run_id)


def test_adapter_add_messages_writes_isolated_benchmark_memories():
    adapter = _adapter("unit-adapter-add")
    try:
        added = adapter.add(
            messages=[BenchmarkMessage("user", "Alice: Berlin trip in June.")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-06-01",
            source_ids=["D1:1"],
        )

        memory = adapter.repo.fetch_by_external_id(added.results[0]["id"])
        assert memory is not None
        assert memory.metadata["source"] == "benchmark"
        assert memory.metadata["benchmark_run_id"] == "unit-adapter-add"
        assert memory.metadata["profile"] == "benchmark"
        assert memory.metadata["is_live_memory"] is False
        assert memory.metadata["source_ids"] == ["D1:1"]
    finally:
        adapter.close()


def test_adapter_search_returns_mem0_like_results_without_raw_debug():
    adapter = _adapter("unit-adapter-search")
    try:
        adapter.add(
            messages=[BenchmarkMessage("user", "Alice: Berlin trip in June.")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-06-01",
            source_ids=["D1:1"],
        )

        results = adapter.search(
            "Where is Alice traveling in June?",
            "benchmark-locomo-tiny-1",
            top_k=5,
        )

        assert results
        assert results[0]["id"].startswith("benchmark:locomo_tiny:unit-adapter-search:")
        assert "memory" in results[0]
        assert isinstance(results[0]["score"], float)
        assert results[0]["metadata"]["source_ids"] == ["D1:1"]
        assert "query_debug" not in results[0]
    finally:
        adapter.close()

def test_adapter_search_filters_user_before_top_k_cap():
    adapter = _adapter("unit-adapter-user-filter")
    try:
        adapter.add(
            messages=[BenchmarkMessage("user", "Target: orchard lantern detail.")],
            user_id="target-user",
            conversation_id="target-conv",
            session_id="session_1",
            source_ids=["D1:1"],
        )
        for index in range(25):
            adapter.add(
                messages=[BenchmarkMessage("user", "Noise: orchard lantern detail.")],
                user_id=f"noise-user-{index}",
                conversation_id=f"noise-conv-{index}",
                session_id="session_1",
                source_ids=[f"N{index}"],
            )

        results = adapter.search("orchard lantern detail", "target-user", top_k=5)

        assert [row["metadata"]["source_ids"] for row in results] == [["D1:1"]]
    finally:
        adapter.close()

def test_adapter_search_uses_session_context_for_multi_hop_questions():
    adapter = _adapter("unit-adapter-session-context")
    try:
        adapter.add(
            messages=[BenchmarkMessage("user", "Caroline went to the LGBTQ support group.")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-05-07",
            source_ids=["D1:1"],
        )
        adapter.add(
            messages=[BenchmarkMessage("assistant", "It was on May 7, 2023.")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-05-07",
            source_ids=["D1:2"],
        )

        results = adapter.search(
            "When did Caroline go to the LGBTQ support group?",
            "benchmark-locomo-tiny-1",
            top_k=5,
        )

        assert results
        assert results[0]["metadata"]["source_ids"] == ["D1:2"]
    finally:
        adapter.close()

def test_adapter_search_prefers_session_observation_when_scores_tie():
    adapter = _adapter("unit-adapter-session-kind-boost")
    try:
        messages = [BenchmarkMessage("user", "Riley archive permit code blue.")]
        adapter.add(
            messages=messages,
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-05-07",
            source_ids=["D1:1", "D1:2"],
            observation_kind="turn",
        )
        adapter.add(
            messages=messages,
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-05-07",
            source_ids=["D1:1", "D1:2"],
            observation_kind="session",
        )

        results = adapter.search(
            "Riley archive permit code blue",
            "benchmark-locomo-tiny-1",
            top_k=2,
        )

        assert results
        assert results[0]["metadata"]["observation_kind"] == "session"
        assert results[0]["metadata"]["source_ids"] == ["D1:1", "D1:2"]
    finally:
        adapter.close()
