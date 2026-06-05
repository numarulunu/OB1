# Kontext V2 Mem0 Mirror Design

## Purpose

Kontext V2 is the VPS-hosted owned memory core. Its first role is to mirror the current Mem0 system closely enough that Codex, Claude, ChatGPT, and Perplexity can eventually switch MCP endpoints without losing retrieval quality, memory metadata, safety behavior, or auditability.

Mem0 remains the live source of truth until Kontext V2 passes parity tests. Kontext V2 must not perform destructive writes to Mem0 during the mirror phase.

## Name

The system name is **Kontext V2**.

## Initial Goal

Build a read-first mirror of Mem0 with a Postgres + pgvector backend, a Mem0-compatible MCP surface, and a parity harness that proves whether Kontext V2 behaves like Mem0 or improves on it.

## Architecture

Kontext V2 runs on the VPS as a central service.

- Postgres stores canonical records, metadata, relations, audit rows, and retrieval telemetry.
- pgvector stores embeddings for semantic retrieval.
- Postgres full-text search handles literal and keyword retrieval.
- A FastAPI MCP service exposes Mem0-compatible tools.
- A mirror importer pulls memories from Mem0 into Kontext V2 and preserves Mem0 IDs as external IDs.
- A parity harness compares Mem0 and Kontext V2 outputs without printing raw memory text or secrets.

## Data Model V0

Core tables:

- `memories`: atomic memory rows with `id`, `external_mem0_id`, `title`, `text`, `metadata`, `memory_type`, `current_status`, `memory_tier`, `signal_strength`, timestamps, and source hashes.
- `memory_versions`: append-only history of imported and edited memory states.
- `categories`: owned category/topic taxonomy.
- `memory_categories`: many-to-many memory/category assignments.
- `relations`: graph edges such as `supports`, `contradicts`, `supersedes`, `caused_by`, `part_of`, `co_occurs`.
- `embeddings`: pgvector vectors tied to memory IDs and embedding model/version.
- `retrieval_queries`: safe telemetry with query hashes, filters, result IDs, latency, and origin.
- `ingestion_audit`: compact audit rows for extraction/write decisions.
- `topic_dossiers`: generated human-readable category/topic pages.

Markdown/topic pages are projections of the database, not the source of truth. Manual edits should become proposed database patches.

## MCP Compatibility V0

Kontext V2 should first implement the Mem0-compatible read/status surface:

- `search`
- `fetch`
- `ingestion_status`

Then add write/proposal tools only after read parity passes:

- `ingest_exchange`
- `extract_memories`
- `submit_memory_override`
- `flag_memory`

Direct delete/update behavior must stay exact-ID guarded and rollback/audit logged.

## Mirror Rules

- Mem0 is live source of truth during V0.
- Kontext V2 imports from Mem0 by exact external ID.
- Imports are idempotent by `external_mem0_id` and source hash.
- Metadata must be preserved, including domains, memory_type, memory_tier, current_status, and signal_strength.
- Raw profile tokens, secrets, raw chats, and remote env contents must never be logged or printed.
- Parity reports may include counts, IDs, metadata hit/miss flags, and aggregate metrics, but not raw memory text.

## Parity Test Suite

### 1. Schema And Import Parity

Given a sanitized Mem0 export fixture, importing into Kontext V2 should:

- create one Kontext memory per Mem0 memory;
- preserve external Mem0 IDs;
- preserve metadata fields;
- create memory versions;
- not duplicate rows on repeated import;
- report skipped/changed/created counts.

Acceptance: repeated import is stable and row counts do not grow unexpectedly.

### 2. Tool Contract Parity

For the same request shape, Kontext V2 MCP should match Mem0-compatible behavior for:

- `search` parameters: `query`, `top_k`, `domains`, `memory_tiers`, `memory_types`, `current_statuses`;
- `fetch` by exact ID;
- `ingestion_status` aggregate fields;
- malformed inputs and bounds.

Acceptance: client code can swap endpoint/profile URL without changing tool call shape.

### 3. Retrieval Eval Parity

Use the existing Mem0 V1.13 retrieval cases as the baseline case pack.

Metrics:

- pass rate;
- domain hit rate;
- memory_type hit rate;
- expected ID hit rate where known;
- MRR where expected IDs exist;
- zero-result count;
- latency p50/p95.

Acceptance for first read-only mirror:

- Kontext V2 pass rate is at least Mem0 pass rate minus 5 percentage points;
- no critical category drops to zero hits;
- zero-result count is not worse than Mem0 by more than one case;
- failed cases list contains no raw query results or memory text.

Target before switching clients: Kontext V2 matches or beats Mem0 on pass rate and critical sensitive/project cases.

### 4. Side-By-Side Search Diff

For each eval query, run Mem0 and Kontext V2 and compare:

- result count;
- top result IDs where mirrored external IDs exist;
- domain overlap;
- memory_type overlap;
- rank delta for known external IDs.

Acceptance: parity report explains differences using metadata and rank signals, not raw memory content.

### 5. Fetch Parity

For sampled mirrored IDs, `fetch` should return the same stable fields:

- ID/external ID;
- title/text presence;
- metadata fields;
- timestamps where available;
- safe missing-ID behavior.

Acceptance: no missing mirrored IDs in the sampled set.

### 6. Write-Path Dry Run

Before enabling writes, `ingest_exchange` should run in dry-run/proposal mode:

- extracts candidate memories;
- maps proposed actions to create/update/flag;
- does not write by default;
- records audit rows;
- redacts secrets and raw logs.

Acceptance: proposal output is deterministic enough for tests and never writes without explicit execution mode.

### 7. Dashboard/Category Projection Tests

Generated topic dossiers should:

- group memories by category;
- include source memory IDs;
- show conflicts/stale candidates;
- round-trip manual edits into proposed DB patches;
- never become the primary source of truth.

Acceptance: modifying a dossier creates a review proposal, not silent database drift.

## Initial Test Artifacts To Build

- `tools/kontext-v2/tests/test_mem0_import_parity.py`
- `tools/kontext-v2/tests/test_mcp_contract_parity.py`
- `tools/kontext-v2/tests/test_retrieval_eval_parity.py`
- `tools/kontext-v2/tests/test_side_by_side_diff.py`
- `tools/kontext-v2/tests/fixtures/mem0_sanitized_export.json`
- `tools/kontext-v2/parity_eval.py`
- `tools/kontext-v2/mem0_mirror_import.py`

## Implementation Order

1. Create local Kontext V2 package skeleton and tests using sanitized fixtures.
2. Implement Postgres schema migrations locally with testcontainers or a local Docker Postgres.
3. Implement idempotent Mem0 mirror importer.
4. Implement read-only MCP tools.
5. Implement side-by-side parity harness against live Mem0 and local Kontext V2.
6. Deploy read-only Kontext V2 to VPS behind a private profile URL.
7. Run live parity evals.
8. Add write-proposal mode only after read parity is acceptable.

## Non-Goals For V0

- Replacing Mem0 as live source of truth.
- Enabling automatic writes to Kontext V2.
- Deleting or mutating Mem0 data.
- Building the full dashboard before retrieval parity exists.
- Treating Markdown as the canonical database.

## Open Questions

- Which embedding model should V0 use: local sentence-transformers, OpenAI-compatible embeddings, or the same model family currently used by Mem0 if available?
- Should categories initially be imported from Mem0 metadata domains, or created as a separate taxonomy layer?
- Should parity evals run only locally first, or immediately include a private VPS staging endpoint?
