import importlib.util
import sys
from pathlib import Path

from kontext_v2.benchmarks.mem0_parity import (
    Mem0ParityRerankConfig,
    mem0_style_score_row,
    rerank_with_mem0_parity,
)


MEM0_RETRIEVAL = Path("tools/mem0-remote-mcp/retrieval.py")


def _load_legacy_retrieval():
    spec = importlib.util.spec_from_file_location("legacy_mem0_retrieval", MEM0_RETRIEVAL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row(
    text: str,
    *,
    score: float = 0.0,
    tier: str = "cold",
    status: str = "active",
    signal: float = 5.0,
    memory_type: str = "benchmark_observation",
    updated_at: str = "2026-05-30T00:00:00+00:00",
) -> dict:
    return {
        "external_mem0_id": f"row-{abs(hash(text))}",
        "text": text,
        "memory": text,
        "score": score,
        "memory_type": memory_type,
        "current_status": status,
        "memory_tier": tier,
        "signal_strength": signal,
        "updated_at": updated_at,
        "metadata": {
            "memory_type": memory_type,
            "current_status": status,
            "memory_tier": tier,
            "signal_strength": signal,
            "domains": ["benchmark", "workflow"],
        },
    }


def test_mem0_style_score_matches_legacy_order_on_controlled_rows():
    legacy = _load_legacy_retrieval()
    query = "Where should Avery store the launch checklist?"
    evidence = _row("user: Avery stores the launch checklist in the amber binder.", score=0.1)
    distractor = _row("user: The grocery list mentions oranges and rice.", score=0.9)

    legacy_ranked = legacy.rank_memories(query, [distractor, evidence], requested_tiers=["cold"], top_k=2)
    parity_ranked = sorted(
        [distractor, evidence],
        key=lambda row: mem0_style_score_row(query, row, requested_tiers={"cold"}),
        reverse=True,
    )

    assert legacy_ranked[0]["memory"] == evidence["text"]
    assert parity_ranked[0]["text"] == evidence["text"]


def test_mem0_style_score_penalizes_superseded_and_cold_like_legacy_ranker():
    legacy = _load_legacy_retrieval()
    query = "What is the active release instruction?"
    current = _row(
        "user: Active release instruction: send the client the slate PDF.",
        tier="active",
        status="active",
        signal=8,
    )
    superseded = _row(
        "user: Old release instruction: send the client the amber PDF.",
        tier="cold",
        status="superseded",
        signal=8,
    )

    legacy_ranked = legacy.rank_memories(query, [superseded, current], top_k=2)

    assert legacy_ranked[0]["memory"] == current["text"]
    assert mem0_style_score_row(query, current) > mem0_style_score_row(query, superseded)


def test_mem0_parity_rrf_can_pull_evidence_into_top20_without_raw_fields():
    query = "Where is the amber notebook?"
    ranked = []
    for index in range(29):
        row = _row(f"user: unrelated status row {index}", score=0.0)
        ranked.append((index, 100.0 - index, row))
    evidence = _row("user: The amber notebook is stored on the cedar desk.", score=0.0)
    ranked.append((29, 1.0, evidence))

    reranked = rerank_with_mem0_parity(
        query,
        ranked,
        Mem0ParityRerankConfig(enabled=True, candidate_window=30, rrf_k=20, locked_head_size=0),
        question_category="information_extraction",
    )

    top20_ids = [row.get("external_mem0_id") for _, _, row in reranked[:20]]
    assert evidence["external_mem0_id"] in top20_ids
    assert all("prompt" not in row and "answer" not in row for _, _, row in reranked[:20])
