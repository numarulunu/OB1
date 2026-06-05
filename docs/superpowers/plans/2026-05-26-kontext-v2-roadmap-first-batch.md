# Kontext V2 Roadmap First Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the first low-risk Kontext V2 roadmap items that improve privacy, protected-memory safety, and deploy confidence without changing production cutover state.

**Architecture:** Keep existing path-token MCP routes working while adding header-token support. Normalize and test protected autobiographical boundary variants. Sanitize compact project observations before storage. Add lightweight cutover/canary artifacts that read sanitized reports only.

**Tech Stack:** Python, FastAPI TestClient, pytest, PostgreSQL-backed repository code, existing Kontext V2 scripts.

---

### Task 1: Protected-History Boundary Regression

**Files:**
- Modify: `tools/kontext-v2/tests/test_retention_policy.py`

- [ ] Add a failing test that proves `family-origin`, `family_origin`, and `Family Origin` are all protected.
- [ ] Run `python -m pytest tools/kontext-v2/tests/test_retention_policy.py -q` and confirm the new test catches any normalization regression.
- [ ] Keep `kontext_v2/retention.py` unchanged if existing normalization already passes.

### Task 2: MCP Authorization Header Compatibility

**Files:**
- Modify: `tools/kontext-v2/tests/test_mcp_bridge.py`
- Modify: `tools/kontext-v2/kontext_v2/mcp_bridge.py`

- [ ] Add failing tests for `POST /mcp` and `GET /mcp` with `Authorization: Bearer <token>`.
- [ ] Keep existing `/mcp/{token}` tests passing.
- [ ] Implement a shared resolver that accepts bearer, `x-kontext-token`, `x-mcp-token`, and path token where present.
- [ ] Do not remove path-token routes in this batch.

### Task 3: Project Observation Sanitization

**Files:**
- Create: `tools/kontext-v2/kontext_v2/sanitize.py`
- Modify: `tools/kontext-v2/tests/test_kontext_project_observations.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`

- [ ] Add a failing test that `record_project_observation` redacts credential-like prefixes and caps summary length to 2000 chars.
- [ ] Implement a small sanitizer with synthetic-pattern tests only; do not use or print real secrets.
- [ ] Apply the sanitizer before inserting `title`, `summary`, `next_steps`, and `error_excerpt` metadata fields.

### Task 4: Cutover Decision Artifact

**Files:**
- Create: `tools/kontext-v2/cutover_decision.md`

- [ ] Add a non-secret cutover decision document with owner, current mode, blockers, evidence sources, and review cadence.
- [ ] Keep production cutover disabled.

### Task 5: Regression Canary Scaffold

**Files:**
- Create: `tools/kontext-v2/scripts/regression_canary.py`
- Create: `tools/kontext-v2/tests/test_regression_canary.py`

- [ ] Add tests for sanitized report loading and threshold pass/fail output.
- [ ] Implement a no-network canary that reads existing sanitized reports and emits aggregate JSON only.
- [ ] Do not call paid model APIs or print raw memory text.

### Verification

- [ ] `python -m py_compile` on all touched Python files.
- [ ] Focused pytest for changed tests.
- [ ] `git diff --check` on touched files.
- [ ] Optional VPS deploy only after local verification is clean.
