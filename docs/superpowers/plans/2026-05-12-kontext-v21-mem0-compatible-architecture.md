# Kontext V2.1 Mem0-Compatible Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Kontext V2 from a fast read mirror into a Mem0-compatible backend that can shadow, then safely replace, the hosted Mem0 MCP wrapper.

**Architecture:** Keep Mem0 as source of truth during V2.1. Kontext V2 adds Mem0-shaped MCP tools, local write/intake audit tables, deterministic extraction/gating/dedupe, project-observation storage, retrieval parity, and cutover checks. Every write path defaults to dry-run or local-only audit until explicit cutover gates pass.

**Tech Stack:** Python 3.12/3.14 tests, FastAPI JSON-RPC MCP bridge, PostgreSQL 16 + pgvector, psycopg 3, pytest, existing `tools/mem0-remote-mcp` wrapper modules as behavioral reference.

---

## Scope And Safety

- Mem0 remains source of truth until a later explicit cutover.
- No Mem0 save/update/delete calls are added in Kontext V2.1.
- No raw memory text, raw chats, secrets, profile tokens, or remote `.env` values are printed in tests, logs, telemetry, or project logs.
- Kontext write tools first return dry-run proposals and local audit rows. Applying writes to Kontext memory tables is introduced only after deterministic tests prove dedupe/update behavior.
- Remote deploys must preserve `/opt/mem0-remote-mcp/.env`, `/opt/mem0-remote-mcp/data`, and the `kontext-v2-data` Docker volume.

## File Map

- Modify `tools/kontext-v2/kontext_v2/mcp_server.py`: Mem0-compatible tool schemas and pure tool handlers.
- Modify `tools/kontext-v2/kontext_v2/mcp_bridge.py`: JSON-RPC dispatch for all read/write/project tools.
- Modify `tools/kontext-v2/kontext_v2/http_api.py`: HTTP equivalents for status/intake/project endpoints when useful.
- Modify `tools/kontext-v2/kontext_v2/schema.py`: write audit, memory flags, project observations, and optional embedding job tables.
- Modify `tools/kontext-v2/kontext_v2/repository.py`: persistence helpers for audit rows, flags, project observations, and applied writes.
- Create `tools/kontext-v2/kontext_v2/intake.py`: deterministic gating, source hashing, previews, proposal normalization, and dry-run/apply orchestration.
- Create `tools/kontext-v2/kontext_v2/project_observations.py`: project continuity storage and compact retrieval.
- Create `tools/kontext-v2/kontext_v2/cutover_eval.py`: parity reports for tool contract, retrieval, write dry-run, project observations, and telemetry.
- Add focused tests under `tools/kontext-v2/tests/` for each task.

---

### Task 1: Mem0-Compatible MCP Tool Contract And Safe Dry-Run Dispatch

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/mcp_server.py`
- Modify: `tools/kontext-v2/kontext_v2/mcp_bridge.py`
- Test: `tools/kontext-v2/tests/test_mcp_contract_parity.py`
- Test: `tools/kontext-v2/tests/test_mcp_bridge_write_tools.py`

- [x] **Step 1: Write failing contract tests**

Add tests that assert read-only Kontext exposes Mem0-compatible read/project tools:

```python
def test_read_only_tool_surface_includes_project_retrieval_tools():
    names = {tool["name"] for tool in list_tools(write_enabled=False)}
    assert {"search", "fetch", "ingestion_status", "project_search", "project_timeline", "project_fetch", "project_file_context"} <= names
    assert "ingest_exchange" not in names
```

Add tests that assert write-enabled profiles expose write tools with real input schemas:

```python
def test_write_tool_definitions_match_mem0_shape():
    tools = {tool["name"]: tool for tool in list_tools(write_enabled=True)}
    assert {"extract_memories", "ingest_exchange", "submit_memory_override", "flag_memory"} <= set(tools)
    assert tools["extract_memories"]["inputSchema"]["properties"]["messages"]["type"] == "array"
    assert tools["ingest_exchange"]["inputSchema"]["properties"]["context_messages"]["type"] == "array"
    assert tools["submit_memory_override"]["inputSchema"]["properties"]["content"]["type"] == "string"
    assert tools["flag_memory"]["inputSchema"]["properties"]["reason"]["type"] == "string"
```

Add bridge tests that call `extract_memories`, `ingest_exchange`, `submit_memory_override`, and `flag_memory` through `/mcp/{token}` and assert the response is JSON, dry-run/local-only, and contains no raw secrets.

- [x] **Step 2: Run RED**

Run:

```powershell
python -B -m pytest tools\kontext-v2\tests\test_mcp_contract_parity.py tools\kontext-v2\tests\test_mcp_bridge_write_tools.py -q
```

Expected: fails because project tools and several write dispatch branches are missing or underspecified.

- [x] **Step 3: Implement minimal schemas and pure dry-run handlers**

In `mcp_server.py`, add input schemas for messages, overrides, flags, project search/fetch/timeline/file context. Add pure functions:

```python
def extract_memories_dry_run(messages: list[dict[str, str]], context_messages: list[dict[str, str]] | None, origin: str) -> dict[str, Any]:
    return ingest_exchange_dry_run(messages=[*(context_messages or []), *messages], origin=origin) | {"tool": "extract_memories"}

def submit_memory_override_dry_run(content: str, reason: str, origin: str, **metadata: Any) -> dict[str, Any]:
    return {"ok": True, "mode": "dry_run", "writes_applied": 0, "tool": "submit_memory_override", "origin": origin, "proposal": {"action": "save", "content_hash": sha256(content), "reason": reason}}

def flag_memory_dry_run(memory_id: str, flag_type: str, reason: str, confidence: float, origin: str) -> dict[str, Any]:
    return {"ok": True, "mode": "dry_run", "writes_applied": 0, "tool": "flag_memory", "origin": origin, "flag": {"id": memory_id, "flag_type": flag_type, "reason_hash": sha256(reason), "confidence": clamped_confidence}}
```

Do not store raw content in the dry-run response except current `ingest_exchange_dry_run` proposal text, which is already local-only and tested. If text must be summarized, store hashes and compact metadata.

- [x] **Step 4: Implement JSON-RPC dispatch**

In `mcp_bridge.py`, route the write tools only when `profile.can_write` is true. Route project tools in read-only mode with safe empty responses for now:

```python
if name == "project_search":
    return _text_result({"rows": [], "count": 0, "status": "not_configured"})
```

Unsupported write calls on read-only profiles must still return JSON-RPC error `unsupported tool`.

- [x] **Step 5: Run GREEN**

Run the focused tests again. Expected: all selected tests pass.

- [x] **Step 6: Run regression**

Run:

```powershell
python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q
```

Expected: full suite passes.

---

### Task 2: Local Intake Audit Tables And Dry-Run Persistence

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/schema.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`
- Create: `tools/kontext-v2/kontext_v2/intake.py`
- Test: `tools/kontext-v2/tests/test_intake_audit.py`

- [x] Add `memory_intake_audit` and `memory_flags` tables with source hash, origin, action, status, metadata, and timestamps.
- [x] Write tests that `ingest_exchange` dry-run records one aggregate audit row without raw chat text.
- [x] Implement repository helpers `record_intake_audit()`, `recent_intake_audit()`, and `record_memory_flag()`.
- [x] Expose audit counts in `ingestion_status_tool()`.
- [x] Verify focused tests and full regression.

---

### Task 3: Deterministic Extraction, Gating, And Proposal Normalization

**Files:**
- Create/modify: `tools/kontext-v2/kontext_v2/intake.py`
- Reference: `tools/mem0-remote-mcp/ingestion.py`
- Test: `tools/kontext-v2/tests/test_intake_extraction.py`

- [x] Port source hashing, preview sanitization, high-signal gate, and proposal parser behavior from Mem0.
- [x] Convert destructive delete/merge/stale proposals into flag-only proposals.
- [x] Clamp confidence and signal strength to safe ranges.
- [x] Keep extract-only tool dry-run by default.
- [x] Verify focused tests and full regression.

---

### Task 4: Kontext-Local Apply Path With Exact Dedupe

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/intake.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`
- Test: `tools/kontext-v2/tests/test_intake_apply.py`

- [x] Add `apply=False` default to ingestion orchestration.
- [x] When `apply=True`, save only new high-confidence memories and update only exact matched IDs or exact dedupe matches.
- [x] Preserve memory versions on every update.
- [x] Never hard-delete from broad proposals; write flags instead.
- [x] Verify focused tests and full regression.

---

### Task 5: Project Continuity Layer Parity

**Files:**
- Create: `tools/kontext-v2/kontext_v2/project_observations.py`
- Modify: `tools/kontext-v2/kontext_v2/schema.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`
- Modify: `tools/kontext-v2/kontext_v2/mcp_bridge.py`
- Test: `tools/kontext-v2/tests/test_kontext_project_observations.py`

- [x] Add `project_observations` table with observation id, project, title, summary, files, metadata, created_at.
- [x] Implement `project_search`, `project_fetch`, `project_timeline`, and `project_file_context` with compact rows matching Mem0 wrapper behavior.
- [x] Ensure search/timeline outputs do not expose raw secrets or full file contents.
- [x] Verify focused tests and full regression.

---

### Task 5.5: Agent-First Memory Reminder And CLI Dream Cleanup

**Files:**
- Modify: `tools/mem0-remote-mcp/monitoring.py`
- Modify: `tools/mem0-remote-mcp/client_hooks/mem0_context_hook.py`
- Modify: `tools/mem0-remote-mcp/server.py`
- Modify: `tools/mem0-remote-mcp/ingestion_status_cli.py`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `tools/mem0-remote-mcp/README.md`
- Test: `tools/mem0-remote-mcp/test_monitoring.py`
- Test: `tools/mem0-remote-mcp/test_client_hooks.py`

- [x] Move memory-write policy to agent-first direct MCP writes; keep `ingest_exchange` out of the routine hot path.
- [x] Add deterministic `maintenance` status to safe aggregate monitoring, with pending-flag thresholds and no raw memory/chat content.
- [x] Add hook maintenance-status lookup and one bounded reminder only when cleanup is due.
- [x] Keep dream cleanup CLI-first: dry-run, aggregate summary, explicit approval, rollback logs, and delete caps.
- [x] Verify focused tests and Mem0 remote MCP regression.

---

### Task 6: Retrieval Parity Upgrade

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/retrieval.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`
- Test: `tools/kontext-v2/tests/test_retrieval_fusion.py`

- [ ] Add query expansion and fused ranking using full-text rank, metadata/domain boosts, recency, signal strength, and optional vector score.
- [ ] Keep all telemetry sanitized.
- [ ] Compare against V1.13 eval cases and exact-ID freshness checks.
- [ ] Verify focused tests and full regression.

---

### Task 7: Cutover Eval And Shadow Report

**Files:**
- Create: `tools/kontext-v2/kontext_v2/cutover_eval.py`
- Test: `tools/kontext-v2/tests/test_cutover_eval.py`

- [ ] Produce one aggregate report covering tool contract parity, retrieval pass rate, exact-ID freshness, write dry-run parity, project-observation parity, sync freshness, and telemetry health.
- [ ] Report only counts, IDs, hashes, and pass/fail status.
- [ ] Define hard gates for any future cutover: retrieval >= Mem0, no stale exact-ID misses, write dry-run proposals match policy, project tools pass, and at least several days of shadow traffic.
- [ ] Verify focused tests and full regression.

---

### Task 8: VPS Deploy And Shadow-Only Client Trial

**Files:**
- Modify runtime files only under `/opt/kontext/src/kontext_v2/` after local tests pass.
- Update: `project_log.md`

- [ ] Create rollback archive under `/opt/kontext-backup-<timestamp>-v21.tgz`.
- [ ] Copy changed runtime files to `/opt/kontext/src/kontext_v2/`.
- [ ] Rebuild `kontext:latest`; recreate only `kontext` and `kontext-worker`.
- [ ] Smoke `/api/v2/health`, `/api/v2/sync/status`, MCP `tools/list`, write dry-run calls, project tool calls, and V1.13 retrieval eval.
- [ ] Do not alter `/opt/mem0-remote-mcp/.env`, `/opt/mem0-remote-mcp/data`, or `kontext-v2-data`.

---

## Self-Review

- Spec coverage: covers tool contract, write pipeline, audit, project continuity, retrieval, cutover eval, and deploy/shadow trial.
- Placeholder scan: no task depends on an undefined external decision; later tasks refine behavior but each has concrete files and checks.
- Type consistency: tool names match hosted Mem0 MCP names: `search`, `fetch`, `ingestion_status`, `extract_memories`, `ingest_exchange`, `submit_memory_override`, `flag_memory`, `project_search`, `project_fetch`, `project_timeline`, `project_file_context`.
- Execution decision: user asked to get to work, so use inline execution and start Task 1 immediately with TDD.
