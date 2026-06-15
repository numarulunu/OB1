# Agent Instructions for Kontext

This file is for Codex and other coding agents working in this repo. Follow `CLAUDE.md` for the repo contribution contract, and follow the local Kontext protocol below for Ionut-specific continuity.

## Local Kontext Retrieval Protocol

Kontext V2 is the primary long-term memory backend for Claude/Codex. The old `mem0` MCP name is legacy compatibility only; do not describe active Kontext calls as Mem0 in user-facing text. Legacy Mem0 is backup/rollback only unless Ionut explicitly asks to compare, diagnose parity, or restore.

- Before any substantive answer, decision, plan, implementation, review, or explanation, search the configured `kontext` MCP first when available.
- If the client still exposes the primary memory server under the old `mem0` MCP name, use it as the Kontext-backed route and call it Kontext in user-facing text.
- This is mandatory for anything involving Ionut's projects, preferences, workflows, business, opera career, psychology, relationships, AI systems, settings, infrastructure, or long-term goals.
- Do not rely only on current chat context when Kontext may contain relevant background. Actually call the memory search tool before answering.
- Use broad search queries that include the user's request plus likely domains, for example: project name, workflow, business, opera, psychology, relationships, settings, AI systems, infrastructure, or goals.
- Fetch full memories by ID when search results are relevant but too compressed to safely answer from the title/snippet alone.
- Integrate retrieved memories silently and naturally. Do not dump memory contents unless the user asks.
- If the primary memory MCP is unavailable, missing, or returns no relevant result, say that clearly before answering from current context.
- When the user gives durable corrections, current project state, stable preferences, or important life/work context, save a compact distilled memory if the active primary memory connector exposes a save/write tool. Do not save raw transcript text, secrets, temporary logs, or low-signal clutter.

## Local Kontext Mutation Protocol

When the Kontext connector exposes write tools, memory maintenance should be organic and low-friction. The user should be able to say that something is outdated, wrong, redundant, incomplete, or should be forgotten, and the agent should handle the memory edit. If the active primary connector is still named `mem0`, treat that as the Kontext-backed compatibility route and do not call it Mem0 in user-facing text.

- For new durable facts, preferences, decisions, project state, relationship/psychology context, or stable corrections, use `save` when no existing memory clearly covers the same point.
- For stale, wrong, incomplete, or partially outdated memories, search first, fetch the exact memory by ID, then use `update` with corrected compact content and a clear reason. Preserve useful metadata when possible.
- For false, duplicate, low-signal, or explicitly unwanted memories, search first, fetch the exact memory by ID, then use `delete` with a clear reason. Use hard delete only for exact-memory targets, not vague topic sweeps.
- If the user asks for a broad cleanup by topic, first present the candidate IDs/titles or ask for confirmation before deleting multiple memories.
- If unsure whether a memory is still true, surface the conflict briefly and ask instead of guessing.
- Never save, update, or delete secrets, raw transcripts, temporary logs, auth/session files, or large unprocessed dumps.

## Kontext Agent-First Write Protocol

When the hosted Kontext MCP exposes writer tools, memory maintenance is agent-first. Prefer exact, compact MCP writes over automatic LLM ingestion.

- At session start or after reconnecting, call `ingestion_status` when available to confirm aggregate health and check `maintenance.due`. Do not expose profile URLs, API keys, tokens, raw chats, or full memory contents.
- Do not call `ingest_exchange` after every exchange. Keep Qwen/automatic extraction off the hot path unless the user explicitly asks for a dry run or batch audit.
- For new durable facts, preferences, decisions, project state, or stable corrections, use `save` or `submit_memory_override` with compact distilled content.
- For stale or incomplete memories, search first, fetch the exact memory by ID, then use `update`.
- For duplicate, false, stale, merge-worthy, or conflict candidates, use `flag_memory`; do not hard-delete through ingestion.
- Use `extract_memories` as a dry-run/debugging tool only. Use `ingest_exchange` only when direct save/update/flag choices are insufficient for a compact material summary and the user accepts the cost/privacy tradeoff.
- Never save, update, ingest, or flag secrets, raw transcripts, temporary logs, auth/session files, code dumps, or generic assistant advice.

## Desktop And CLI Hook Protocol

Hooks are support rails, not the primary memory policy. The primary policy is still agent-first: search and write through Kontext when the work is substantive.

- Codex Desktop uses `C:/Users/Gaming PC/.codex/hooks.json` with `[features].hooks=true` in `C:/Users/Gaming PC/.codex/config.toml`.
- Claude Code uses hooks in `C:/Users/Gaming PC/.claude/settings.json`.
- The shared hook script is installed at `C:/Users/Gaming PC/.codex/mem0_context_hook.py` and `C:/Users/Gaming PC/.claude/mem0_context_hook.py`. The filename is legacy; the script now prefers Kontext.
- Hooks must not dump large context into the prompt. They should suppress output unless a compact maintenance reminder is due.
- Hooks may record content-free heartbeats and compact project observations through the public Kontext MCP route. They must not send raw chats, raw command output, file contents, secrets, auth/session files, or profile tokens.
- Claude Desktop / Claude.ai custom connectors do not run local Claude Code hooks. For those clients, rely on the custom connector plus this `AGENTS.md` instruction policy.

## Kontext Primary MCP Protocol

Kontext V2 is primary for Claude/Codex; legacy Mem0 remains available as rollback/backup.

- Prefer the direct `kontext` MCP when available.
- Treat a configured `mem0` MCP as the primary memory tool only when it is the Kontext-backed compatibility route, and call it Kontext in user-facing text.
- Do not routinely double-write to legacy Mem0. Use legacy Mem0 only when explicitly asked to compare, rollback, or diagnose parity.
- If both direct `kontext` and compatibility `mem0` routes are available, use one primary route and avoid duplicate writes.
- Never send secrets, raw chats, raw logs, auth/session files, or large dumps to either memory system.

## Memory Dream Maintenance Protocol

Dream cleanup is CLI-first and user-approved. Run it against the active primary memory backend when available; use legacy Mem0 maintenance scripts only when specifically maintaining the legacy backup or when no Kontext-native maintenance path exists yet.

- If a hook or `ingestion_status.maintenance` reports maintenance due, tell the user only aggregate counts and ask before cleanup.
- Start with a dry run: `python tools/mem0-remote-mcp/dream_maintenance_cli.py --audit-log <audit-log> --output mem0-dream-maintenance-plan.json`.
- Summarize the dry-run plan by counts/actions only unless the user asks for specific exact IDs. Do not dump raw memory text.
- Execute cleanup only after explicit approval, with rollback/maintenance logs and delete caps such as `--max-deletes 3`.
- No separate API cleanup model is used by default. Claude/Codex may review the compact CLI plan in-chat when ambiguous merges/conflicts need judgment.

## Protected Autobiographical History

Relationship, psychology, trauma, family-origin, identity, and formative-history memories are permanent autobiographical context. They may be stored as `historical` to preserve chronology, but `historical` does not mean disposable.

- Never delete, archive, cold-downgrade, stale-flag, or decay these memories because of age, low usage, or cleanup pressure.
- Update them only for factual correction, explicit user request, or a clearly superseding memory that preserves the old event as history.
- If old autobiographical history conflicts with a newer current-state memory, keep both: the newer memory governs current logistics, while the older memory remains explanatory background.
- Dream cleanup may flag these memories only for careful review/correction, not automatic removal or demotion.

## Memory Tiers

Use `memory_tier` metadata to control retrieval priority without deleting useful history.

- `active`: current facts, preferences, project state, operating rules, relationship/psychology patterns, and anything that should usually influence answers now. This is the default.
- `historical`: older or formative context that still explains the present, past versions of projects/relationships, dated decisions, and useful background that should be retrieved when relevant but not dominate current context.
- `cold`: rarely needed background, archived low-priority memories, or details kept for completeness. Do not save junk as cold; delete junk.

`current_status` and `memory_tier` are separate: status says whether the fact is active/dormant/resolved/unknown; tier says how aggressively it should be retrieved. When updating a stale memory, change both fields if needed.
