import json
import os
from pathlib import Path

import psycopg

from kontext_v2.importer import import_mem0_export
from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import search_memories
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "mem0_sanitized_export.json"


def test_search_finds_domain_and_memory_type_hits():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-ai-architecture-1"],),
            )
        conn.commit()
        repo = KontextRepository(conn)
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-ai-architecture-1",
                title="OB1 memory architecture",
                text="OB1 memory architecture uses Mem0 as live source and Kontext V2 as planned mirror.",
                metadata={
                    "domains": ["ai", "systems", "infrastructure"],
                    "memory_type": "project_state",
                    "current_status": "test_fixture",
                    "memory_tier": "active",
                    "signal_strength": 9,
                },
                memory_type="project_state",
                current_status="test_fixture",
                memory_tier="active",
                signal_strength=9,
                source_hash="fixture-architecture-hash",
            )
        )
        results = search_memories(
            repo,
            query="current architecture of my AI memory system",
            top_k=5,
            domains=["ai", "systems", "infrastructure"],
            memory_types=["project_state"],
            memory_tiers=[],
            current_statuses=["test_fixture"],
        )

    assert results
    assert results[0]["external_mem0_id"] == "mem-ai-architecture-1"
    assert "ai" in results[0]["metadata"]["domains"]
    assert results[0]["memory_type"] == "project_state"


def test_existing_mem0_eval_case_file_is_reusable():
    import importlib.util
    import sys

    module_path = Path("tools/mem0-remote-mcp/retrieval_eval.py")
    spec = importlib.util.spec_from_file_location("mem0_retrieval_eval", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    cases = module.load_eval_cases(
        "tools/mem0-remote-mcp/retrieval_eval_cases.v1.13.json"
    )

    assert len(cases) >= 10
    assert {case.name for case in cases} >= {
        "ai-memory-architecture",
        "mem0-ingestion-pipeline",
    }


def test_search_reranks_sensitive_metadata_over_generic_text_match():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-generic-family", "mem-relationship-pattern"],),
            )
        conn.commit()
        repo = KontextRepository(conn)
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-generic-family",
                title="Generic family note",
                text="Mother family origin note with weak generic context.",
                metadata={"domains": ["notes"], "memory_type": "note", "signal_strength": 2},
                memory_type="note",
                current_status="test_sensitive",
                memory_tier="active",
                signal_strength=2,
                source_hash="generic-family-hash",
            )
        )
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-relationship-pattern",
                title="Relationship pattern",
                text="Attachment trust escalation and withdrawal pattern.",
                metadata={
                    "domains": ["relationships", "psychology", "family"],
                    "memory_type": "relationship_pattern",
                    "signal_strength": 9,
                },
                memory_type="relationship_pattern",
                current_status="test_sensitive",
                memory_tier="active",
                signal_strength=9,
                source_hash="relationship-pattern-hash",
            )
        )
        results = search_memories(
            repo,
            query="mother family origin relationship pattern",
            top_k=2,
            domains=[],
            memory_types=[],
            memory_tiers=[],
            current_statuses=["test_sensitive"],
        )

    assert [row["external_mem0_id"] for row in results][0] == "mem-relationship-pattern"


def test_search_normalizes_legacy_family_origin_metadata_for_eval_filters():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-family-origin-legacy"],),
            )
        conn.commit()
        repo = KontextRepository(conn)
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-family-origin-legacy",
                title="Family origin pattern",
                text="Mother origin trust calibration and childhood hypervigilance.",
                metadata={
                    "domains": ["family_origin", "psychology"],
                    "memory_type": "identity_shaping",
                    "signal_strength": 8,
                },
                memory_type="identity_shaping",
                current_status="active",
                memory_tier="active",
                signal_strength=8,
                source_hash="family-origin-legacy-hash",
            )
        )
        results = search_memories(
            repo,
            query="mother family origin formative pattern",
            top_k=3,
            domains=["family"],
            memory_types=["formative_event"],
            memory_tiers=[],
            current_statuses=[],
        )

    assert results
    assert results[0]["external_mem0_id"] == "mem-family-origin-legacy"
    assert "family" in results[0]["metadata"]["domains"]
    assert results[0]["memory_type"] == "formative_event"


def test_search_can_rank_domain_type_candidate_without_exact_query_text():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-weak-lexical", "mem-strong-metadata"],),
            )
        conn.commit()
        repo = KontextRepository(conn)
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-weak-lexical",
                title="Generic money note",
                text="Silverfin PFA tax retirement funding plan.",
                metadata={"domains": ["notes"], "memory_type": "note", "signal_strength": 1},
                memory_type="note",
                current_status="test_finance",
                memory_tier="active",
                signal_strength=1,
                source_hash="weak-lexical-hash",
            )
        )
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-strong-metadata",
                title="Financial context",
                text="Income architecture and long-term runway planning.",
                metadata={"domains": ["finance", "business"], "memory_type": "decision", "signal_strength": 9},
                memory_type="decision",
                current_status="test_finance",
                memory_tier="active",
                signal_strength=9,
                source_hash="strong-metadata-hash",
            )
        )
        results = search_memories(
            repo,
            query="Silverfin PFA tax retirement funding",
            top_k=2,
            domains=[],
            memory_types=[],
            memory_tiers=[],
            current_statuses=["test_finance"],
        )

    assert [row["external_mem0_id"] for row in results][0] == "mem-strong-metadata"

def test_search_normalizes_ai_systems_workflow_rows_as_project_state():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-ai-workflow-project"],),
            )
        conn.commit()
        repo = KontextRepository(conn)
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-ai-workflow-project",
                title="AI workflow architecture",
                text="Codex Claude ChatGPT memory automation architecture.",
                metadata={"domains": ["ai", "systems"], "memory_type": "workflow", "signal_strength": 8},
                memory_type="workflow",
                current_status="test_ai_workflow",
                memory_tier="active",
                signal_strength=8,
                source_hash="ai-workflow-project-hash",
            )
        )
        results = search_memories(
            repo,
            query="AI agents memory automation system preferences",
            top_k=1,
            domains=[],
            memory_types=["project_state"],
            memory_tiers=[],
            current_statuses=["test_ai_workflow"],
        )

    assert results[0]["external_mem0_id"] == "mem-ai-workflow-project"
    assert results[0]["memory_type"] == "project_state"

def test_search_normalizes_vocality_business_decision_as_business_context():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-vocality-business-decision"],),
            )
        conn.commit()
        repo = KontextRepository(conn)
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-vocality-business-decision",
                title="Vocality current state",
                text="Student migration and offer workflow for Vocality.",
                metadata={"domains": ["business", "vocality"], "memory_type": "decision", "signal_strength": 8},
                memory_type="decision",
                current_status="test_vocality",
                memory_tier="active",
                signal_strength=8,
                source_hash="vocality-business-decision-hash",
            )
        )
        results = search_memories(
            repo,
            query="current state of Vocality student migration workflow",
            top_k=1,
            domains=[],
            memory_types=["business_context"],
            memory_tiers=[],
            current_statuses=["test_vocality"],
        )

    assert results[0]["external_mem0_id"] == "mem-vocality-business-decision"
    assert results[0]["memory_type"] == "business_context"
