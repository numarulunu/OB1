# Mem0 V1.4 Retrieval Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve memory retrieval quality so agents get the right compact context before answering.

**Architecture:** Move retrieval ranking out of `core.py` into a focused module that fuses vector, lexical, entity, domain, memory tier, signal strength, and current-status signals. Keep the public `search` API stable while adding optional filters.

**Tech Stack:** Python 3, pytest, existing Mem0 HTTP API, optional Postgres lexical search, existing memory metadata.

---

### Task 1: Extract Retrieval Ranking Module

**Files:**
- Create: `tools/mem0-remote-mcp/retrieval.py`
- Modify: `tools/mem0-remote-mcp/core.py`
- Test: `tools/mem0-remote-mcp/test_retrieval.py`

- [ ] Move pure helpers `lexical_tokens`, `lexical_score`, `merge_ranked_memories`, and tier normalization into `retrieval.py` or wrap them without changing behavior.
- [ ] Keep compatibility tests proving current search behavior still passes.
- [ ] Run `python -m pytest tools\mem0-remote-mcp\test_core.py tools\mem0-remote-mcp\test_retrieval.py -q`.

### Task 2: Add Fused Scoring

**Files:**
- Modify: `tools/mem0-remote-mcp/retrieval.py`
- Test: `tools/mem0-remote-mcp/test_retrieval.py`

- [ ] Add `score_memory(query, memory, requested_domains=None, requested_tiers=None)`.
- [ ] Include lexical overlap, phrase match, Mem0/vector score, domain match, memory tier boost, signal strength, and active-status boost.
- [ ] Penalize `cold` tier unless explicitly requested.
- [ ] Keep `historical` retrievable for psychology, relationships, family-origin, and identity queries.

### Task 3: Add Entity And Alias Hints

**Files:**
- Modify: `tools/mem0-remote-mcp/retrieval.py`
- Test: `tools/mem0-remote-mcp/test_retrieval.py`

- [ ] Extract simple entities from capitalized names, project names, and known domains without adding a new dependency.
- [ ] Boost rows that contain exact entity/name hits.
- [ ] Add tests for people/project queries where lexical entity match beats generic semantic match.

### Task 4: Add Search Filters

**Files:**
- Modify: `tools/mem0-remote-mcp/core.py`
- Modify: `tools/mem0-remote-mcp/server.py`
- Test: `tools/mem0-remote-mcp/test_core.py`
- Test: `tools/mem0-remote-mcp/test_server_profiles.py`

- [ ] Keep existing `search(query, top_k, memory_tiers)` compatible.
- [ ] Add optional `domains`, `memory_types`, and `current_statuses` filters.
- [ ] Apply filters after fetching enough candidates, not before retrieval is starved.
- [ ] Cap `top_k` at 20 unless explicitly changed later.

### Task 5: Add Retrieval Quality Fixtures

**Files:**
- Create: `tools/mem0-remote-mcp/test_retrieval_quality_fixtures.py`

- [ ] Add fixtures for business/workflow, psychology/relationship, opera/vocality, and AI systems queries.
- [ ] Assert high-signal active rows outrank generic low-signal rows.
- [ ] Assert cold rows can still appear when explicitly requested.

### Task 6: Verification

**Files:**
- Review only unless tests fail.

- [ ] Run `python -m pytest tools\mem0-remote-mcp -q`.
- [ ] Run a live read-only search smoke after deployment approval.
- [ ] Update `project_log.md`.

### Deployment Gate

Deploy only after tests pass. Retrieval changes affect every client, so remote smoke should test at least one psychology query, one project query, and one infrastructure query without printing private memory contents in chat.
