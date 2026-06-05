# Kontext V2 Shadow Query Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make real Kontext retrieval usage measurable enough to guide future ranking work without storing or printing raw queries, raw chats, or raw memory text.

**Architecture:** Keep using the existing `retrieval_queries` table, but enrich the JSON `filters` payload with privacy-preserving query features and add indexes for analysis. Add a report script that reads only sanitized telemetry and emits aggregate health/quality signals.

**Tech Stack:** Python, PostgreSQL, existing Kontext V2 MCP logging path, pytest.

---

### Task 1: Safe Query Feature Extraction

**Files:**
- Create: `tools/kontext-v2/kontext_v2/retrieval_shadow.py`
- Test: `tools/kontext-v2/tests/test_retrieval_shadow.py`

- [ ] Add tests for token counts, length buckets, intent flags, and hashed term fingerprints.
- [ ] Assert raw query words do not appear in serialized features.
- [ ] Implement deterministic, bounded features only.

### Task 2: MCP Search Log Enrichment

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/mcp_bridge.py`
- Modify: `tools/kontext-v2/tests/test_mcp_shadow_logging.py`

- [ ] Add `top_k` and `query_features` to the existing `filters` payload sent to `record_retrieval_query`.
- [ ] Keep existing domains/types/tiers/status filters unchanged.
- [ ] Do not store raw query text.

### Task 3: Shadow Report CLI

**Files:**
- Create: `tools/kontext-v2/scripts/retrieval_shadow_report.py`
- Test: `tools/kontext-v2/tests/test_retrieval_shadow_report.py`

- [ ] Add tests for aggregate rows, profile counts, latency summaries, no-result rate, duplicate query count, and feature counts.
- [ ] Assert raw query/memory strings never appear in report JSON.
- [ ] Implement DB loading with `KONTEXT_V2_DATABASE_URL`/`DATABASE_URL` and optional JSON output.

### Task 4: Schema Indexes And Deploy Copy

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/schema.py`
- Modify: `tools/kontext-v2/tests/test_schema.py`
- Modify: `tools/kontext-v2/Dockerfile.dashboard-hotpatch`

- [ ] Add retrieval-query indexes by created/profile and query hash.
- [ ] Copy the helper and report script in the hotpatch image.

### Verification

- [ ] `python -m py_compile` on touched Python files.
- [ ] Focused pytest for new telemetry/report/schema tests.
- [ ] Existing MCP shadow logging test when local DB is available; otherwise verify on VPS after deploy.
- [ ] VPS report smoke emits aggregate counts only.
