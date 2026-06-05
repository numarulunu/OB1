# Kontext V2 Batch 7A Trust And Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining high-risk trust-chain and protected-memory holes from the post-hardening SMAC audit.

**Architecture:** Tighten evidence gates before they feed scorecards/readiness, and add protected-autobiographical-history checks at both intake and repository persistence layers. Keep changes surgical and compatible with current mirror-read-only operation.

**Tech Stack:** Python 3, pytest, FastAPI support scripts, PostgreSQL-backed Kontext repository.

---

### Task 1: Judged Evidence Floors

**Files:**
- Modify: `tools/kontext-v2/scripts/judged_benchmark_verify.py`
- Modify: `tools/kontext-v2/scripts/judged_batch_verify.py`
- Modify: `tools/kontext-v2/scripts/kontext_goal_scorecard.py`
- Modify: `tools/kontext-v2/scripts/kontext_cutover_readiness.py`
- Test: `tools/kontext-v2/tests/test_judged_benchmark_verify.py`
- Test: `tools/kontext-v2/tests/test_judged_batch_verify.py`
- Test: `tools/kontext-v2/tests/test_kontext_goal_scorecard.py`
- Test: `tools/kontext-v2/tests/test_kontext_cutover_readiness.py`

- [ ] Add failing tests proving 3-question or 10-question micro-samples no longer pass the official judged gate.
- [ ] Raise per-report judged benchmark default `--min-questions` to 30.
- [ ] Raise batch verifier `MIN_BATCH_QUESTIONS` to 30 and expose the floor in summaries.
- [ ] Make scorecard derive proof from sample size, weighted accuracy, model-call evidence, and readiness evidence instead of trusting a boolean.
- [ ] Make cutover readiness require minimum judged questions and weighted accuracy.
- [ ] Verify focused judged/scorecard/readiness tests pass.

### Task 2: Protected Intake Updates

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/intake.py`
- Test: `tools/kontext-v2/tests/test_intake_apply.py`

- [ ] Add failing tests proving updates against protected family/relationship/psychology memories are skipped unless explicitly overridden.
- [ ] Add failing test proving a duplicate save cannot silently overwrite a protected memory path.
- [ ] Import and use `is_protected_autobiographical_history` in the update/save branches.
- [ ] Return compact `skipped_protected` results without writing raw memory text.
- [ ] Verify focused intake tests pass.

### Task 3: Repository Flag Defense

**Files:**
- Modify: `tools/kontext-v2/kontext_v2/repository.py`
- Test: add `tools/kontext-v2/tests/test_record_memory_flag_protection.py`

- [ ] Add failing repository test proving `delete_candidate` or `stale_candidate` flags are blocked for protected memories.
- [ ] Add an opt-out `protection_check=False` for explicit administrative bypasses.
- [ ] Keep existing caller behavior default-protected.
- [ ] Verify focused repository tests pass against local DB if available; otherwise verify live VPS after deploy.

### Task 4: Deploy And Verify

**Files:**
- Modify: `project_log.md`

- [ ] Run `py_compile`, focused pytest, and `git diff --check`.
- [ ] Back up current VPS files under `/opt/kontext/backups/<timestamp>-batch-7a-trust-protection`.
- [ ] Copy only changed files to `/opt/kontext/src`, rebuild `kontext:latest`, recreate only `kontext` and `kontext-worker`.
- [ ] Verify container health, deployed module compile, judged micro-sample blocked, protected update blocked, protected flag blocked.
- [ ] Update `project_log.md` and save one compact Kontext project-state memory.
