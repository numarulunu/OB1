import os

import psycopg

from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import SCHEMA_SQL, apply_schema, list_tables


def test_schema_creates_core_tables():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)

        tables = set(list_tables(conn))

    assert {
        "memories",
        "memory_versions",
        "categories",
        "memory_categories",
        "relations",
        "embeddings",
        "retrieval_queries",
        "ingestion_audit",
        "topic_dossiers",
        "mirror_sync_runs",
        "state_subjects",
        "state_event_candidates",
        "state_events",
        "state_event_edges",
        "current_state_facts",
    }.issubset(tables)


def test_schema_contains_memory_rank_indexes_and_clean_statement_boundaries():
    assert "idx_memories_rank_order" in SCHEMA_SQL
    assert "idx_memories_active_hot" in SCHEMA_SQL
    assert "idx_retrieval_queries_created_profile" in SCHEMA_SQL
    assert "idx_retrieval_queries_query_hash_created" in SCHEMA_SQL
    assert "WHERE current_status = 'active' AND memory_tier = 'active'" in SCHEMA_SQL
    assert "files);CREATE INDEX" not in SCHEMA_SQL


def test_schema_contains_state_model_tables_and_indexes():
    assert "CREATE TABLE IF NOT EXISTS state_subjects" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS state_event_candidates" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS state_events" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS state_event_edges" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS current_state_facts" in SCHEMA_SQL
    assert "UNIQUE (namespace, subject_type, subject_key)" in SCHEMA_SQL
    assert "UNIQUE (namespace, event_hash)" in SCHEMA_SQL
    assert "idx_current_state_facts_search_document" in SCHEMA_SQL


def test_schema_embeddings_placeholder_is_safe_for_future_real_embeddings():
    """The legacy VECTOR(16) placeholder must not crash future writers.

    Real production retrieval has zero INSERT/SELECT/UPDATE against the
    ``embeddings`` table today, but the historical schema declared
    ``embedding VECTOR(16) NOT NULL`` plus other NOT-NULL placeholder fields.
    Any future code path that touches the table — for example a batch
    embedding refresh job — would have crashed because the placeholder
    forced a 16-dimensional value. The schema must:

      - Drop NOT NULL on the placeholder ``embedding`` column so rows can
        exist without a vector while a real embedding model is being
        backfilled.
      - Provide a sane DEFAULT for the placeholder text columns so a
        future writer does not have to know the legacy schema details.
      - Carry a ``embedding_dirty`` flag so a batch refresh job can find
        rows that need a real vector.
      - Carry a ``embedding_text_hash`` field so the refresh job can tie
        a stored vector to the exact source text it was computed from.
    """

    assert "ALTER TABLE embeddings ALTER COLUMN embedding DROP NOT NULL" in SCHEMA_SQL
    assert "ALTER TABLE embeddings ALTER COLUMN model SET DEFAULT" in SCHEMA_SQL
    assert "ALTER TABLE embeddings ALTER COLUMN dimensions SET DEFAULT" in SCHEMA_SQL
    assert "ALTER TABLE embeddings ALTER COLUMN content_hash SET DEFAULT" in SCHEMA_SQL
    assert "ADD COLUMN IF NOT EXISTS embedding_dirty BOOLEAN" in SCHEMA_SQL
    assert "ADD COLUMN IF NOT EXISTS embedding_text_hash TEXT" in SCHEMA_SQL


def test_repository_upserts_and_fetches_memory():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)

        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-test-1",
                title="Test memory",
                text="Ionut prefers owned memory infrastructure.",
                metadata={
                    "domains": ["ai", "systems"],
                    "memory_type": "decision",
                },
                memory_type="decision",
                current_status="active",
                memory_tier="active",
                signal_strength=8.0,
                source_hash="hash-1",
            )
        )

        fetched = repo.fetch_by_external_id("mem-test-1")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT version_source, snapshot, source_hash
                FROM memory_versions
                WHERE external_mem0_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                ("mem-test-1",),
            )
            version = cur.fetchone()

    assert fetched is not None
    assert fetched.external_mem0_id == "mem-test-1"
    assert fetched.metadata["domains"] == ["ai", "systems"]
    assert version is not None
    assert version[0] == "mem0_import"
    assert version[1]["source_hash"] == "hash-1"
    assert version[2] == "hash-1"
