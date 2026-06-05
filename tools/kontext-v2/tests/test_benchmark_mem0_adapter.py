from pathlib import Path

from kontext_v2.benchmarks.adapter import BenchmarkMessage
from kontext_v2.benchmarks.mem0_adapter import Mem0BenchmarkAdapter


MEM0_RETRIEVAL = Path("tools/mem0-remote-mcp/retrieval.py")


def test_mem0_benchmark_adapter_ranks_with_legacy_ranker_without_database_writes():
    adapter = Mem0BenchmarkAdapter(
        "unit_dataset",
        "unit-run",
        retrieval_module_path=MEM0_RETRIEVAL,
    )

    adapter.add(
        [BenchmarkMessage("user", "The amber notebook is stored on the cedar desk.", "turn-1")],
        "benchmark-user",
        "conv-1",
        "session-1",
        "2026-05-30",
        source_ids=["turn-1"],
    )
    adapter.add(
        [BenchmarkMessage("user", "The grocery list mentions oranges and rice.", "turn-2")],
        "benchmark-user",
        "conv-1",
        "session-2",
        "2026-05-30",
        source_ids=["turn-2"],
    )

    rows = adapter.search("Where is the amber notebook?", "benchmark-user", top_k=2)

    assert rows[0]["memory"] == "user: The amber notebook is stored on the cedar desk."
    assert rows[0]["metadata"]["source_ids"] == ["turn-1"]
    assert rows[0]["metadata"]["source"] == "benchmark"
    assert rows[0]["metadata"]["is_live_memory"] is False
    assert not hasattr(adapter, "conn")
