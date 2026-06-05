# Kontext V2 DB Performance Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce avoidable Python-side scans in Kontext V2 without changing cutover state or retrieval scoring weights.

**Architecture:** Use PostgreSQL's existing tsvector/GIN support for project-observation search, add indexed/keyset support to memory listing while preserving offset compatibility for sync jobs, and cap production retrieval candidate scans to a bounded size now that DB-assisted candidate loading is available.

**Tech Stack:** Python, psycopg, PostgreSQL generated tsvector columns, pytest.

---

### Task 1: Project Observation SQL Pushdown

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/repository.py`
- Test: `tools/kontext-v2/tests/test_repository_query_plans.py`

- [ ] Add a failing fake-connection test proving `project_search("query")` uses `search_document @@ websearch_to_tsquery('simple', %s)` and `ts_rank_cd`.
- [ ] Implement a SQL-backed `project_search` path that returns compact rows and falls back to the existing Python scoring only when the SQL path has no matches.
- [ ] Keep `project_fetch`, `project_timeline`, and `project_file_context` behavior unchanged.

### Task 2: Memory List Keyset And Indexes

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/repository.py`
- Modify: `tools/kontext-v2/kontext_v2/schema.py`
- Test: `tools/kontext-v2/tests/test_repository_query_plans.py`
- Test: `tools/kontext-v2/tests/test_schema.py`

- [ ] Add failing fake-connection tests for `list_memory_rows(..., current_status="active", memory_tier="active", after=row)`.
- [ ] Preserve existing `limit/offset` behavior for sync/exact-freshness callers.
- [ ] Add full rank-order and active-hot partial indexes to `SCHEMA_SQL`.
- [ ] Fix the existing missing newline between the project-observation files index and mirror-sync index.

### Task 3: Retrieval Candidate Cap

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/retrieval.py`
- Test: `tools/kontext-v2/tests/test_retrieval_ranking.py`

- [ ] Add a regression test proving sensitive/project-domain retrieval does not request more than 2,500 list rows when DB-assisted search falls back.
- [ ] Cap `candidate_limit` at 2,500 after the domain-based limit is computed.

### Verification

- [ ] `python -m py_compile` on touched Python files.
- [ ] Focused pytest for repository query-plan, schema string, and retrieval cap tests.
- [ ] Existing focused retrieval tests still pass.
- [ ] VPS deploy with backup.
- [ ] VPS health plus `EXPLAIN`/index smoke where safe.
