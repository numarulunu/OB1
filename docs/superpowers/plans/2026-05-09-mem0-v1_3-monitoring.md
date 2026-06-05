# Mem0 V1.3 Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ingestion health, failures, flags, and rough cost visible without exposing secrets or raw conversations.

**Architecture:** Add audit-ledger statistics, a safe `ingestion_status` MCP tool, a local CLI report, and optional health metadata. Use aggregate counts and sanitized previews only.

**Tech Stack:** Python 3, pytest, FastMCP, FastAPI `/health`, JSONL audit ledger.

---

### Task 1: Add Audit Stats Module

**Files:**
- Create: `tools/mem0-remote-mcp/monitoring.py`
- Test: `tools/mem0-remote-mcp/test_monitoring.py`

- [ ] Implement `AuditStats` dataclass with counts by action, origin, flag type, and error type.
- [ ] Implement `build_audit_stats(path, recent_limit=5000)`.
- [ ] Track last successful save/update per origin.
- [ ] Track last error timestamp and message prefix capped at 160 chars.
- [ ] Ensure malformed rows are counted but ignored.

### Task 2: Add Safe Cost Estimation

**Files:**
- Modify: `tools/mem0-remote-mcp/ingestion_llm.py`
- Modify: `tools/mem0-remote-mcp/server.py`
- Test: `tools/mem0-remote-mcp/test_monitoring.py`

- [ ] Extend LLM responses internally to capture provider `usage` when present.
- [ ] Record model name and token counts in audit rows when available.
- [ ] Do not estimate dollars unless prices are configured through non-secret env values.
- [ ] If no usage exists, expose `usage_unavailable` rather than guessing.

### Task 3: Add `ingestion_status` MCP Tool

**Files:**
- Modify: `tools/mem0-remote-mcp/server.py`
- Test: `tools/mem0-remote-mcp/test_server_profiles.py`

- [ ] Expose `ingestion_status` to all profiles.
- [ ] Return JSON with status, recent action counts, last success per origin, recent error count, pending flag count, and ingestion enabled/model name.
- [ ] Never return profile tokens, API keys, raw messages, or full memory content.
- [ ] Add profile tests confirming the tool is available for read-only and writer profiles.

### Task 4: Add CLI Report

**Files:**
- Create: `tools/mem0-remote-mcp/ingestion_status_cli.py`
- Test: `tools/mem0-remote-mcp/test_ingestion_status_cli.py`

- [ ] Support `--audit-log`, `--json`, and `--recent-limit`.
- [ ] Default output should be human-readable and compact.
- [ ] JSON output should match the MCP status schema.

### Task 5: Health Check Hardening

**Files:**
- Modify: `tools/mem0-remote-mcp/server.py`
- Test: `tools/mem0-remote-mcp/test_server_profiles.py`

- [ ] Add non-secret health metadata: service name, profile names, ingestion enabled, audit ledger writable boolean.
- [ ] Keep `/health` fast and avoid reading thousands of audit rows there.

### Task 6: Verification

**Files:**
- Review only unless tests fail.

- [ ] Run `python -m pytest tools\mem0-remote-mcp -q`.
- [ ] Run local status CLI against a sample ledger.
- [ ] Check remote `/health` after deployment only with explicit approval.
- [ ] Update `project_log.md`.

### Deployment Gate

Deploy after tests pass. Remote verification should call `/health` and the `ingestion_status` MCP tool through one profile without printing the profile URL.
