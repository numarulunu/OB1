# Mem0 Project Continuity V1.14-V1.17 Implementation Plan

**Goal:** Finish V1.14 and add Claude-Mem-style project continuity so Codex/Claude keep reliable project memory across compaction without bloating context.

**Current state:** V1.13 is live. V1.14 Metadata Enrichment is built locally and previously passed `python -m pytest tools\mem0-remote-mcp -q` with `135 passed`. V1.13 eval passed `10/12`; failures were relationship/psychology `memory_type` misses. Continue implementation in a fresh Codex session, not `codex resume --last`.

## Safety Rules

- Do not print API keys, profile tokens, DB passwords, raw memory text, or raw chats.
- Preserve remote `.env` and `/opt/mem0-remote-mcp/data` on deploy.
- Create rollback archives before remote changes.
- Default maintenance jobs to dry-run.
- Use one `ingest_exchange` for background memory writes; direct `save/update/delete` only for explicit exact-memory edits.

## Task 1: Deploy V1.14 Metadata Enrichment

- [ ] Run local tests: `python -m pytest tools\mem0-remote-mcp -q`.
- [ ] Create remote rollback archive for `/opt/mem0-remote-mcp` excluding `.env` and `data`.
- [ ] Sync local service files to `/opt/mem0-remote-mcp`, preserving `.env` and `data`.
- [ ] Rebuild/restart only `memory-mcp`.
- [ ] Verify `https://memory-mcp.ionutrosu.xyz/health` returns `ok=true`.

## Task 2: Run V1.14 Dry-Run, Apply, Eval

- [ ] Run `metadata_enrichment_cli.py --live-snapshot` inside the container and write `/data/mem0-mcp-metadata-plan.json`.
- [ ] Inspect aggregate counts only; do not print raw snapshot contents.
- [ ] If sane, apply with `--execute --max-updates N`, where `N` is the approved cap.
- [ ] Rerun `retrieval_eval.py --cases retrieval_eval_cases.v1.13.json --top-k 5 --min-pass-rate 0.8 --min-cases 10`.
- [ ] Expected: pass rate stays at least `0.8`; relationship/psychology failures improve.

## Task 3: Build V1.15 Project Observation Core

- [ ] Create `project_observations.py`, `project_observations_cli.py`, and tests.
- [ ] Add schema: id, timestamp, origin, project root, cwd, git branch, worktree root, event type, title, summary, files read/modified, commands, tests, decisions, next steps, memory ids, source hash.
- [ ] Store append-only JSONL at `/data/mem0-mcp-project-observations.jsonl`.
- [ ] Add deterministic redaction and dedupe by source hash.
- [ ] Add CLI: append, recent, by-file.
- [ ] Test malformed rows, dedupe, filtering, redaction, and file lookup.

## Task 4: Build V1.16 Hook Capture

- [ ] Add hook modes for `post_tool_use`, `session_end`, and `post_compact`.
- [ ] Capture only compact metadata: tool name, cwd, touched paths, command category, pass/fail, short sanitized outcome.
- [ ] Never store raw command output, file contents, transcripts, or secrets.
- [ ] Keep hook calls fire-and-forget with short timeout.
- [ ] Preserve valid Codex `hookSpecificOutput` and Claude `additionalContext` or `suppressOutput` formats.
- [ ] Run stale config guard after installing hooks.

## Task 5: Build V1.17 Progressive Project Retrieval

- [ ] Add MCP tools: `project_search`, `project_timeline`, `project_fetch`, `project_file_context`.
- [ ] `project_search` returns compact index only: id, date, type, title, project, files count, token estimate.
- [ ] `project_timeline` returns chronological context around an anchor/query.
- [ ] `project_fetch` returns full details only for explicit IDs.
- [ ] `project_file_context` returns prior observation titles for a file and recommends whether full file read is needed. Do not hard-block reads yet.
- [ ] Add safe retrieval telemetry without raw content.

## Task 6: Deploy V1.15-V1.17

- [ ] Run full local tests.
- [ ] Create rollback archive.
- [ ] Sync, rebuild, restart, health-check.
- [ ] Run private MCP smoke without printing tokens.
- [ ] Run `client_compliance_status_cli.py` for Codex, Claude, ChatGPT, Perplexity.
- [ ] Update `project_log.md` with summary, files, verification, decisions, and next step.

## Task 7: Skills Audit

- [ ] Extract transcript for `https://www.youtube.com/watch?v=eRS3CmvrOvA&list=WL&index=75`.
- [ ] Inventory Codex skills under `~/.codex/skills` and `~/.agents/skills`.
- [ ] Inventory Claude enabled plugins/skills from `~/.claude/settings.json` and plugin dirs.
- [ ] Classify recommendations as `already_have`, `have_variation`, `worth_installing`, `not_worth_installing`, or `risky_or_duplicate`.
- [ ] Install only skills that materially improve code review, planning, debugging, project continuity, memory operations, or frontend quality.
- [ ] Avoid duplicate memory systems unless scoped and disableable.

## Fresh Session Handoff

```text
Continue the OB1 Mem0 MCP work from project state, not old chat history.
Read: docs/superpowers/plans/2026-05-10-mem0-project-continuity-v114-v117.md
Next: deploy V1.14, run live metadata dry-run/apply with cap, rerun V1.13 retrieval eval, then build V1.15 Project Continuity Layer.
Avoid direct Mem0 save/update/delete except explicit exact-memory edits. Prefer one ingest_exchange after material outcomes.
```
