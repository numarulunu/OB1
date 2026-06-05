# Mem0 V1.2 Dream Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert audit flags into safe, batch-driven memory cleanup and consolidation actions.

**Architecture:** Add an offline runner that reads the audit ledger, groups flagged exact memories, fetches current memory records, creates a dry-run plan, and executes only exact-ID updates/deletes with explicit `--execute`. The hot ingestion path remains non-destructive.

**Tech Stack:** Python 3, pytest, JSONL audit ledger, existing `Mem0Client`, optional OpenRouter-compatible LLM for consolidation packs.

---

### Task 1: Define Maintenance Data Model

**Files:**
- Create: `tools/mem0-remote-mcp/dream_maintenance.py`
- Test: `tools/mem0-remote-mcp/test_dream_maintenance.py`

- [ ] Add dataclasses: `FlagCandidate`, `MaintenanceGroup`, `MaintenanceAction`, and `MaintenancePlan`.
- [ ] Support actions: `keep`, `update`, `delete`, `merge`, `downgrade_tier`, `ask_user`.
- [ ] Require exact memory IDs for `delete`, `update`, `merge`, and `downgrade_tier`.
- [ ] Run the focused test file and verify model validation failures for missing IDs.

### Task 2: Parse Audit Flags Into Groups

**Files:**
- Modify: `tools/mem0-remote-mcp/dream_maintenance.py`
- Test: `tools/mem0-remote-mcp/test_dream_maintenance.py`

- [ ] Parse JSONL rows with `action == "flag"`.
- [ ] Group by `memory_id` first, then by normalized proposal content when no ID exists.
- [ ] Skip malformed rows and keep a skipped-row count.
- [ ] Preserve `flag_type`, `confidence`, `origin`, `reason`, and timestamp.

### Task 3: Build Dry-Run Planner

**Files:**
- Modify: `tools/mem0-remote-mcp/dream_maintenance.py`
- Test: `tools/mem0-remote-mcp/test_dream_maintenance.py`

- [ ] For `delete_candidate`: propose delete only if exact ID exists and confidence is high enough or repeated by multiple origins.
- [ ] For `merge_candidate`: propose merge only when all source IDs are known; otherwise `ask_user`.
- [ ] For `stale_candidate`: prefer `downgrade_tier` or `update current_status` before delete.
- [ ] For `conflict_candidate`: propose `ask_user` unless an LLM consolidation result provides a high-confidence active interpretation.
- [ ] Default to `keep` when confidence is weak.

### Task 4: Add Optional LLM Consolidation Packs

**Files:**
- Modify: `tools/mem0-remote-mcp/dream_maintenance.py`
- Reuse: `tools/mem0-remote-mcp/ingestion_llm.py`
- Test: `tools/mem0-remote-mcp/test_dream_maintenance.py`

- [ ] Create compact packs containing memory IDs, current memory text, metadata, and flag reasons.
- [ ] Do not include raw source chats.
- [ ] Ask the LLM for JSON actions only.
- [ ] Validate every returned action against exact IDs and allowed action types.
- [ ] If validation fails, write an `ask_user` action instead of executing.

### Task 5: Add CLI Runner

**Files:**
- Modify: `tools/mem0-remote-mcp/dream_maintenance.py`
- Create: `tools/mem0-remote-mcp/dream_maintenance_cli.py`
- Test: `tools/mem0-remote-mcp/test_dream_maintenance_cli.py`

- [ ] Implement `--audit-log`, `--output`, `--limit-groups`, `--execute`, `--max-deletes`, `--use-llm`, and `--model` options.
- [ ] Default mode must be dry-run.
- [ ] Execution must write a rollback JSONL containing every `before` record before update/delete.
- [ ] Refuse `--execute` if `--max-deletes` is missing and any delete action exists.

### Task 6: Execute Through Existing Mem0 Client

**Files:**
- Modify: `tools/mem0-remote-mcp/dream_maintenance.py`
- Test: `tools/mem0-remote-mcp/test_dream_maintenance.py`

- [ ] Use `Mem0Client.update` for updates, merge outputs, status changes, and tier downgrades.
- [ ] Use `Mem0Client.delete` only for exact IDs from the approved maintenance plan.
- [ ] Append maintenance result rows to a separate ledger path, not the hot ingestion audit ledger.
- [ ] Report counts for kept, updated, deleted, merged, downgraded, ask-user, and failed.

### Task 7: Verification

**Files:**
- Review only unless tests fail.

- [ ] Run `python -m pytest tools\mem0-remote-mcp -q`.
- [ ] Run dry-run against a small copied audit sample.
- [ ] Confirm no raw chat, secrets, profile tokens, or API keys are printed.
- [ ] Update `project_log.md`.

### Deployment Gate

Dream maintenance can be built locally first. Do not run live deletes on the VPS until a dry-run plan is reviewed and the user approves `--execute` with a delete cap.
