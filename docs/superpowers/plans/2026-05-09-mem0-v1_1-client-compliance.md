# Mem0 V1.1 Client Compliance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove and harden that ChatGPT, Codex, Claude, and VPS/container agents actually call Mem0 ingestion tools after substantive exchanges.

**Architecture:** Keep the MCP server unchanged unless tests show a server gap. Add a compliance probe, update client-facing instructions, and document exact smoke tests per client. Use the audit ledger as the source of truth for whether ingestion happened.

**Tech Stack:** Python 3, pytest, existing FastMCP server, existing JSONL audit ledger, existing `AGENTS.md` and `CLAUDE.md` instruction files.

---

### Task 1: Add Audit Ledger Read Helpers

**Files:**
- Modify: `tools/mem0-remote-mcp/audit_ledger.py`
- Test: `tools/mem0-remote-mcp/test_audit_ledger_stats.py`

- [ ] Write tests for reading recent rows without loading raw chat dumps. Use a temp JSONL file with sanitized rows only.
- [ ] Add `iter_recent(limit: int = 500)` that yields parsed dict rows from the end of the ledger and skips malformed rows.
- [ ] Add `latest_by_origin()` returning the newest timestamp and action counts per `origin`.
- [ ] Run `python -m pytest tools\mem0-remote-mcp\test_audit_ledger_stats.py -q`.

### Task 2: Add Local Compliance Probe CLI

**Files:**
- Create: `tools/mem0-remote-mcp/client_compliance_probe.py`
- Test: `tools/mem0-remote-mcp/test_client_compliance_probe.py`

- [ ] Write tests around pure functions: build canary exchange, parse audit stats, detect missing origins, and format a safe report.
- [ ] Implement a CLI that reads an audit ledger path and expected origins.
- [ ] The CLI must never print profile tokens, API keys, raw chat text, or full memory contents.
- [ ] Output should say which origins have recent `ingest_exchange` writes, flags, skips, or errors.
- [ ] Run `python -m pytest tools\mem0-remote-mcp\test_client_compliance_probe.py -q`.

### Task 3: Tighten Client Instructions

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `tools/mem0-remote-mcp/README.md`

- [ ] Add a concise rule: after any substantive exchange, call `ingest_exchange` with the latest user and assistant messages.
- [ ] Add a rule for summaries/compaction: call `ingest_exchange` with the final compact summary.
- [ ] Add a rule for tool outcomes: ingest only material outcomes such as deploys, migrations, tests after edits, decisions, and user corrections.
- [ ] Keep the no-raw-transcript and no-secret rules explicit.
- [ ] Run `git diff -- AGENTS.md CLAUDE.md tools\mem0-remote-mcp\README.md` and verify only instruction text changed.

### Task 4: Real-Client Smoke Checklist

**Files:**
- Create: `docs/mem0-client-compliance-checklist.md`

- [ ] Document one smoke test each for ChatGPT, local Codex, local Claude, VPS Codex, VPS Claude, and Perplexity.
- [ ] Each smoke test should use a harmless unique canary phrase and then verify through search or audit stats.
- [ ] Include expected outcomes: `search/fetch works`, `ingest_exchange available`, `ingestion audit row appears`, and `canary cleanup completed`.
- [ ] State that Perplexity remains read-only unless intentionally enabled.

### Task 5: Verification

**Files:**
- Review only unless tests fail.

- [ ] Run `python -m pytest tools\mem0-remote-mcp -q`.
- [ ] Run the local compliance probe on a local sample ledger.
- [ ] Run remote `/health` check without printing MCP tokens.
- [ ] Add a `project_log.md` entry with tests, files touched, and remaining client smoke work.

### Deployment Gate

Do not deploy remote instruction/config changes until the user explicitly asks. For remote work, state host, path, command, expected effect, verification, and rollback first.
