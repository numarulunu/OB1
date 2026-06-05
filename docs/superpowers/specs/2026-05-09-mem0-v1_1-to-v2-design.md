# Mem0 Hosted MCP V1.1 To V2 Design

**Date:** 2026-05-09

**Scope:** harden the hosted Mem0 MCP from a working ingestion server into a reliable second-brain operating layer for ChatGPT, Codex, Claude, Perplexity, and VPS/container agents.

## Current Baseline

The hosted MCP server already runs at `tools/mem0-remote-mcp` and is deployed on the VPS. Writer profiles expose `extract_memories`, `ingest_exchange`, `submit_memory_override`, and `flag_memory`. The hot path uses deterministic gating, Qwen/OpenRouter extraction, dedupe-aware save/update, and flag-only delete/stale/merge/conflict handling. The audit ledger is append-only JSONL and stores sanitized previews, source hashes, proposals, actions, and memory IDs.

This design does not replace Mem0. It adds the missing reliability layer around Mem0: compliance checks, maintenance, monitoring, better retrieval, and lifecycle automation.

## Design Principles

- Keep Mem0 as the canonical memory database.
- Keep the MCP server as the shared agent interface.
- Keep automatic write enabled for trusted writer profiles.
- Do not hard-delete from per-exchange ingestion.
- Route delete, stale, duplicate, merge, and conflict decisions through audited maintenance.
- Preserve rich psychology, relationship, identity, business, project, workflow, opera, vocality, and AI-system signal.
- Drop raw transcript dumping, low-signal tool chatter, generic advice, generic vocal lesson sludge, and temporary logs.
- Prefer small, testable modules over expanding `server.py` and `core.py` indefinitely.
- Every version must be independently shippable and testable.

## Version Map

### V1.1 Client Compliance And Hook Reliability

Goal: prove that real clients actually use memory ingestion, then tighten instructions/hooks where they do not.

Add a compliance probe and documentation that checks whether ChatGPT, Codex, Claude, and VPS/container agents can search, fetch, and call `ingest_exchange`. Keep this mostly outside the server hot path. If clients fail to call ingestion reliably, add stronger client-side instructions and optional local hook scripts for environments that support hooks.

### V1.2 Dream Maintenance

Goal: turn accumulated flags into safe cleanup actions.

Add an offline maintenance runner that reads audit flags, fetches exact memories, groups candidates, creates a dry-run apply plan, and only executes exact-ID updates/deletes when explicitly run with `--execute`. This is where stale, duplicate, false, merge, and conflict candidates are resolved.

### V1.3 Monitoring And Cost Visibility

Goal: make silent failure obvious.

Add local/remote status reporting over the audit ledger: recent ingestion count, saves, updates, flags, skips, errors, last successful write per origin, extraction failures, timeout rate, and rough model-cost estimates when usage metadata is available.

### V1.4 Retrieval Quality

Goal: improve what agents retrieve before answering.

Upgrade search from simple lexical-plus-vector merging into a small fused retrieval layer: vector results, lexical hits, entity/person/project hints, domain filters, memory tier boosts, signal strength, and current-status weighting. Keep default results compact and relevant.

### V2 Autonomous Lifecycle

Goal: approximate human memory maintenance.

Add scheduled lifecycle jobs that consolidate patterns, refresh current project/person/domain dossiers, downgrade stale low-use memory to historical/cold, flag contradictions, and produce auditable cleanup plans. Execution remains guarded by backups, exact IDs, and delete caps.

## Build Order

Recommended order:

1. V1.3 minimal monitoring first if visibility is needed before more automation.
2. V1.1 client compliance immediately after, because working tools do not guarantee agents call them.
3. V1.2 dream maintenance, because flags already exist and will grow.
4. V1.4 retrieval quality, because it improves every answer after the database is under control.
5. V2 lifecycle automation, after V1.2 and V1.3 provide safety rails.

If speed matters more than strict version order, build a thin `ingestion_status` tool first, then proceed with V1.1.

## Success Criteria

- Every version has tests and a smoke check.
- No secrets, raw chats, MCP profile tokens, or API keys are printed or logged.
- Hot-path ingestion remains fast and non-destructive.
- Maintenance can dry-run without credentials and execute only with explicit flags.
- Agents have a clear way to prove whether memory ingestion is working.
- A future operator can inspect what happened, why it happened, and how to roll it back.
