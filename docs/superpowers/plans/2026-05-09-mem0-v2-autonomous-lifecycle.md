# Mem0 V2 Autonomous Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scheduled, auditable memory lifecycle automation that consolidates, downgrades, refreshes, and cleans memory like a human-style second brain.

**Architecture:** Build on V1.2 dream maintenance and V1.3 monitoring. Add lifecycle jobs that create dry-run plans by default, maintain project/person/domain dossiers, detect contradictions, and apply exact-ID changes only with backups, delete caps, and audit trails.

**Tech Stack:** Python 3, pytest, existing Mem0 MCP server, existing maintenance runner, cron or container scheduler, JSONL audit and rollback ledgers.

---

### Task 1: Add Lifecycle Job Model

**Files:**
- Create: `tools/mem0-remote-mcp/lifecycle.py`
- Test: `tools/mem0-remote-mcp/test_lifecycle.py`

- [ ] Add job types: `consolidate_domain`, `refresh_dossier`, `resolve_contradictions`, `decay_low_use`, `process_flags`.
- [ ] Add `LifecyclePlan` and `LifecycleResult` dataclasses.
- [ ] Every job must support dry-run output before execution.

### Task 2: Dossier Refresh

**Files:**
- Modify: `tools/mem0-remote-mcp/lifecycle.py`
- Test: `tools/mem0-remote-mcp/test_lifecycle.py`

- [ ] Define dossier domains: `psychology`, `relationships`, `family_origin`, `business`, `ai`, `systems`, `opera`, `vocality`, `money_execution`.
- [ ] Fetch relevant memories by domain and tier.
- [ ] Generate compact dossier cards through the configured LLM.
- [ ] Save or update dossier memories with `memory_type='dossier'`, `current_status='active'`, and `memory_tier='active'`.
- [ ] Never delete source memories during dossier refresh.

### Task 3: Contradiction Handling

**Files:**
- Modify: `tools/mem0-remote-mcp/lifecycle.py`
- Test: `tools/mem0-remote-mcp/test_lifecycle.py`

- [ ] Detect possible contradictions from audit flags, repeated updates, and conflicting status metadata.
- [ ] Default to `ask_user` when the current truth is unclear.
- [ ] Preserve competing interpretations as historical when they explain current behavior.
- [ ] Delete only pure duplicates or explicitly false memories through the V1.2 maintenance execution path.

### Task 4: Tier Decay And Promotion

**Files:**
- Modify: `tools/mem0-remote-mcp/lifecycle.py`
- Test: `tools/mem0-remote-mcp/test_lifecycle.py`

- [ ] Downgrade stale low-signal active memories to historical/cold when they are not current operating context.
- [ ] Keep identity-shaping, psychology, relationship, family-origin, and major project memories protected from automatic cold downgrade.
- [ ] Promote frequently retrieved or recently updated memories to active when they affect current decisions.

### Task 5: Scheduler Wrapper

**Files:**
- Create: `tools/mem0-remote-mcp/lifecycle_cli.py`
- Modify: `tools/mem0-remote-mcp/docker-compose.yaml`
- Test: `tools/mem0-remote-mcp/test_lifecycle_cli.py`

- [ ] Add CLI options: `--job`, `--dry-run`, `--execute`, `--output`, `--max-updates`, `--max-deletes`, and `--rollback-log`.
- [ ] Add a container-friendly command that can be triggered manually or by cron.
- [ ] Do not enable automatic live deletes by default.

### Task 6: Backup And Rollback Guardrails

**Files:**
- Modify: `tools/mem0-remote-mcp/lifecycle.py`
- Modify: `tools/mem0-remote-mcp/dream_maintenance.py`
- Test: `tools/mem0-remote-mcp/test_lifecycle.py`

- [ ] Before live execution, export all affected memories into rollback JSONL.
- [ ] Refuse execution if rollback write fails.
- [ ] Enforce max delete and max update caps.
- [ ] Store execution result counts in the maintenance ledger.

### Task 7: Full Verification

**Files:**
- Review only unless tests fail.

- [ ] Run `python -m pytest tools\mem0-remote-mcp -q`.
- [ ] Run one dry-run lifecycle job against a copied fixture ledger.
- [ ] Run one no-delete live smoke only after user approval.
- [ ] Update `project_log.md`.

### Deployment Gate

V2 should not run as an unattended live mutator until V1.2 maintenance and V1.3 monitoring are proven in real use. First deployment should run scheduled dry-runs only and write reviewable plans.
