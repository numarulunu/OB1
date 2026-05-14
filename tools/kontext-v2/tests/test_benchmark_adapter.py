from pathlib import Path

from kontext_v2.benchmarks.fixtures import load_locomo_tiny_fixture


FIXTURE = Path(__file__).parent / "fixtures" / "locomo_tiny.json"


def test_load_locomo_tiny_fixture_returns_conversations_and_questions():
    fixture = load_locomo_tiny_fixture(FIXTURE)

    assert fixture["dataset"] == "locomo_tiny"
    assert len(fixture["conversations"]) == 2
    assert fixture["conversations"][0]["conversation_id"] == "tiny-conv-1"
    assert fixture["questions"][0]["question"] == "Where is Alice planning to travel in June?"
    assert fixture["questions"][0]["expected_terms"] == ["berlin", "june"]
import os

import psycopg

from kontext_v2.benchmarks.adapter import BenchmarkMessage, KontextBenchmarkAdapter
from kontext_v2.schema import apply_schema


def _adapter(run_id: str) -> KontextBenchmarkAdapter:
    conn = psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"])
    apply_schema(conn)
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
        )

        memory = adapter.repo.fetch_by_external_id(added.results[0]["id"])
        assert memory is not None
        assert memory.metadata["source"] == "benchmark"
        assert memory.metadata["benchmark_run_id"] == "unit-adapter-add"
        assert memory.metadata["profile"] == "benchmark"
        assert memory.metadata["is_live_memory"] is False
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
        assert "query_debug" not in results[0]
    finally:
        adapter.close()
