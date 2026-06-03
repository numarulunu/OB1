from __future__ import annotations

from typing import Any


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    external_mem0_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    memory_type TEXT NOT NULL DEFAULT '',
    current_status TEXT NOT NULL DEFAULT 'active',
    memory_tier TEXT NOT NULL DEFAULT 'active',
    signal_strength DOUBLE PRECISION,
    source_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_document TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', title), 'A') ||
        setweight(to_tsvector('simple', text), 'B')
    ) STORED
);

CREATE TABLE IF NOT EXISTS memory_versions (
    id BIGSERIAL PRIMARY KEY,
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    external_mem0_id TEXT NOT NULL,
    version_source TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    source_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS categories (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_categories (
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'mem0_domain',
    PRIMARY KEY (memory_id, category_id)
);

CREATE TABLE IF NOT EXISTS relations (
    id BIGSERIAL PRIMARY KEY,
    source_memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
    target_memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embeddings (
    memory_id UUID PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding VECTOR(16) NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS retrieval_queries (
    id BIGSERIAL PRIMARY KEY,
    query_hash TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT 'kontext',
    profile TEXT NOT NULL DEFAULT '',
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_external_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE retrieval_queries ADD COLUMN IF NOT EXISTS service TEXT NOT NULL DEFAULT 'kontext';
ALTER TABLE retrieval_queries ADD COLUMN IF NOT EXISTS profile TEXT NOT NULL DEFAULT '';


CREATE TABLE IF NOT EXISTS memory_intake_audit (
    id BIGSERIAL PRIMARY KEY,
    source_hash TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_flags (
    id BIGSERIAL PRIMARY KEY,
    external_mem0_id TEXT NOT NULL,
    flag_type TEXT NOT NULL,
    reason_hash TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    origin TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_observations (
    id BIGSERIAL PRIMARY KEY,
    observation_id TEXT UNIQUE NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    project_root TEXT NOT NULL DEFAULT '',
    cwd TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    files JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_hash TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_document TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', title), 'A') ||
        setweight(to_tsvector('simple', summary), 'B') ||
        setweight(to_tsvector('simple', project), 'C') ||
        setweight(to_tsvector('simple', event_type), 'C') ||
        setweight(to_tsvector('simple', project_root), 'D') ||
        setweight(to_tsvector('simple', cwd), 'D')
    ) STORED
);
CREATE TABLE IF NOT EXISTS hook_heartbeats (
    id BIGSERIAL PRIMARY KEY,
    origin TEXT NOT NULL DEFAULT '',
    hook_type TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL,
    marker_hash TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ingestion_audit (
    id BIGSERIAL PRIMARY KEY,
    source_hash TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS mirror_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    dry_run BOOLEAN NOT NULL DEFAULT true,
    rows_seen INTEGER NOT NULL DEFAULT 0,
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS topic_dossiers (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    markdown TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS state_subjects (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'live',
    subject_type TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    scope JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace, subject_type, subject_key)
);

CREATE TABLE IF NOT EXISTS state_event_candidates (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'live',
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL,
    source_span_hash TEXT NOT NULL DEFAULT '',
    subject_type TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    state_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    value_text TEXT NOT NULL DEFAULT '',
    value_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    prior_value_text TEXT NOT NULL DEFAULT '',
    effective_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ,
    actor_role TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    trust_tier TEXT NOT NULL DEFAULT 'extracted_low',
    status TEXT NOT NULL DEFAULT 'staged',
    extractor_version TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace, idempotency_key),
    CHECK (event_type IN ('assertion', 'preference_set', 'instruction_set', 'knowledge_update', 'status_change', 'correction', 'cancellation', 'supersession')),
    CHECK (trust_tier IN ('manual', 'trusted_agent', 'benchmark_fixture', 'legacy_memory_import', 'deterministic_import_mapping', 'extracted_high', 'extracted_low')),
    CHECK (status IN ('staged', 'accepted', 'rejected', 'needs_review'))
);

CREATE TABLE IF NOT EXISTS state_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    candidate_id UUID REFERENCES state_event_candidates(id),
    namespace TEXT NOT NULL DEFAULT 'live',
    subject_id UUID NOT NULL REFERENCES state_subjects(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL,
    source_span_hash TEXT NOT NULL DEFAULT '',
    event_hash TEXT NOT NULL,
    state_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    value_text TEXT NOT NULL DEFAULT '',
    value_hash TEXT NOT NULL,
    prior_value_text TEXT NOT NULL DEFAULT '',
    effective_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ,
    actor_role TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    trust_tier TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    extractor_version TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace, event_hash),
    CHECK (event_type IN ('assertion', 'preference_set', 'instruction_set', 'knowledge_update', 'status_change', 'correction', 'cancellation', 'supersession')),
    CHECK (trust_tier IN ('manual', 'trusted_agent', 'benchmark_fixture', 'legacy_memory_import', 'deterministic_import_mapping', 'extracted_high', 'extracted_low')),
    CHECK (status IN ('accepted', 'rejected'))
);

CREATE TABLE IF NOT EXISTS state_event_edges (
    id BIGSERIAL PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'live',
    source_event_id UUID NOT NULL REFERENCES state_events(id) ON DELETE CASCADE,
    target_event_id UUID NOT NULL REFERENCES state_events(id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL,
    state_key TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    reason_hash TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_event_id, target_event_id, edge_type, state_key),
    CHECK (edge_type IN ('supersedes', 'cancels', 'corrects', 'supports', 'contradicts'))
);

CREATE TABLE IF NOT EXISTS current_state_facts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'live',
    subject_id UUID NOT NULL REFERENCES state_subjects(id) ON DELETE CASCADE,
    state_key TEXT NOT NULL,
    fact_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    fact_text TEXT NOT NULL DEFAULT '',
    active_event_id UUID REFERENCES state_events(id),
    support_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    superseded_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    cancelled_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    trust_tier TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    effective_at TIMESTAMPTZ,
    rebuilt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    projection_version TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_document TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', fact_text), 'A') ||
        setweight(to_tsvector('simple', state_key), 'B')
    ) STORED,
    UNIQUE (namespace, subject_id, state_key),
    CHECK (status IN ('active', 'cancelled', 'ambiguous', 'unknown'))
);

CREATE INDEX IF NOT EXISTS idx_memories_external_mem0_id
    ON memories (external_mem0_id);
CREATE INDEX IF NOT EXISTS idx_memories_metadata
    ON memories USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_memories_search_document
    ON memories USING GIN (search_document);
CREATE INDEX IF NOT EXISTS idx_memories_rank_order
    ON memories (signal_strength DESC NULLS LAST, updated_at DESC NULLS LAST, external_mem0_id ASC);
CREATE INDEX IF NOT EXISTS idx_memories_active_hot
    ON memories (signal_strength DESC NULLS LAST, updated_at DESC NULLS LAST, external_mem0_id ASC)
    WHERE current_status = 'active' AND memory_tier = 'active';
CREATE INDEX IF NOT EXISTS idx_memory_categories_category_id
    ON memory_categories (category_id);
CREATE INDEX IF NOT EXISTS idx_project_observations_search_document
    ON project_observations USING GIN (search_document);
CREATE INDEX IF NOT EXISTS idx_project_observations_created_at
    ON project_observations (created_at);
CREATE INDEX IF NOT EXISTS idx_project_observations_files
    ON project_observations USING GIN (files);
CREATE INDEX IF NOT EXISTS idx_mirror_sync_runs_latest
    ON mirror_sync_runs (source, finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_hook_heartbeats_origin_created_at
    ON hook_heartbeats (origin, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_queries_created_profile
    ON retrieval_queries (created_at DESC, profile);
CREATE INDEX IF NOT EXISTS idx_retrieval_queries_query_hash_created
    ON retrieval_queries (query_hash, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_state_subjects_namespace_key
    ON state_subjects (namespace, subject_type, subject_key);
CREATE INDEX IF NOT EXISTS idx_state_candidates_status
    ON state_event_candidates (namespace, status, trust_tier, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_state_events_subject_key_time
    ON state_events (namespace, subject_id, state_key, effective_at DESC NULLS LAST, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_state_events_hash
    ON state_events (namespace, event_hash);
CREATE INDEX IF NOT EXISTS idx_state_edges_target
    ON state_event_edges (target_event_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_current_state_facts_namespace_key
    ON current_state_facts (namespace, state_key, status);
CREATE INDEX IF NOT EXISTS idx_current_state_facts_search_document
    ON current_state_facts USING GIN (search_document);

-- Embeddings placeholder safeguard.
-- Production retrieval today is lexical + tsvector + hand-tuned scorer with
-- zero reads/writes against the `embeddings` table. The historical CREATE
-- TABLE declared `embedding VECTOR(16) NOT NULL` plus three NOT-NULL text
-- placeholders, which would crash any future batch refresh job that tries to
-- write a row before a real embedding has been computed. These idempotent
-- ALTERs make the placeholder columns nullable / default-friendly and add
-- the `embedding_dirty` flag + `embedding_text_hash` that a future
-- batch-embedding job will use to find and tie rows to source text.
-- Real embeddings are NOT enabled by these statements; they only stop the
-- placeholder from being a silent footgun.
ALTER TABLE embeddings ALTER COLUMN embedding DROP NOT NULL;
ALTER TABLE embeddings ALTER COLUMN model SET DEFAULT '';
ALTER TABLE embeddings ALTER COLUMN dimensions SET DEFAULT 0;
ALTER TABLE embeddings ALTER COLUMN content_hash SET DEFAULT '';
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_dirty BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_text_hash TEXT NOT NULL DEFAULT '';
"""


def apply_schema(conn: Any) -> None:
    conn.execute(SCHEMA_SQL)
    conn.commit()


def list_tables(conn: Any) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        return [row[0] for row in cur.fetchall()]
