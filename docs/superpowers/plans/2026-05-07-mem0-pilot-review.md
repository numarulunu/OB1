# Mem0 Pilot Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local review workflow and Mem0-ready import export for the curated OB1 Human Memory V2 cards without mutating Supabase or a Mem0 server.

**Architecture:** Add one local-only script beside the shadow-cleanup utilities. It reads the validated V2 dry-run cards, writes a self-contained browser review dashboard, emits Mem0 OSS/API request JSONL, and can later POST those requests to a staging Mem0 server under an explicit execute flag.

**Tech Stack:** Python 3.14 standard library, pytest, static HTML/JavaScript, JSONL artifacts under `.local/open-brain-cleanup/human-v2/mem0/`, no OpenRouter, no Supabase mutation, no remote mutation by default.

---

### Task 1: Local Review And Mem0 Export Tool

**Files:**
- Create: `recipes/shadow-cleanup/review_mem0_pipeline.py`
- Create: `recipes/shadow-cleanup/test_review_mem0_pipeline.py`
- Modify: `recipes/shadow-cleanup/README.md`

- [ ] Add tests for converting a V2 memory card into a Mem0 `POST /memories` request with `infer=false`, `version=v2`, immutable storage, and source metadata preserved.
- [ ] Add tests for selecting a pilot subset that prioritizes high-signal psychology, relationships, identity-shaping, AI, opera, and workflow cards.
- [ ] Add tests for exporting a self-contained HTML dashboard without printing raw memory content to stdout.
- [ ] Implement `export-dashboard`, `export-mem0`, and `import-mem0` CLI commands. `import-mem0` must dry-run unless `--execute` is explicitly passed.
- [ ] Update `recipes/shadow-cleanup/README.md` with the local commands and safety rules.
- [ ] Verify with `python -m pytest recipes\shadow-cleanup\test_review_mem0_pipeline.py -q` and the full V2 tests.

### Task 2: Generate Current Artifacts

**Files:**
- Runtime output: `.local/open-brain-cleanup/human-v2/mem0/`
- Desktop output: `~/Desktop/OB1-Mem0-Pilot-Review-20260507/`

- [ ] Export the local review dashboard from `.local/open-brain-cleanup/human-v2/dry-run/v2-insert-candidates.jsonl`.
- [ ] Export a full Mem0 OSS request JSONL for all V2 cards.
- [ ] Export a top-priority pilot Mem0 OSS request JSONL capped at 300 cards.
- [ ] Verify counts only; do not print card contents.

### Task 3: VPS Staging Gate

**Files:**
- No local code changes required unless deployment details are provided.

- [ ] Ask for the VPS SSH alias/domain and preferred Mem0 domain before remote work.
- [ ] Before running remote commands, state host, directory, exact commands, expected effect, verification, and rollback.
- [ ] Deploy Mem0 staging only after explicit approval for the remote command set.
- [ ] Import the pilot JSONL first, test search, then decide whether to import all cards.
