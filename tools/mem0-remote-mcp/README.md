# Hosted Mem0 MCP

Remote MCP server for the self-hosted Mem0 memory database.

## Purpose

This service gives ChatGPT, Perplexity, Codex, Claude, and VPS/container agents one shared HTTPS MCP host with separate private profile URLs:

- ChatGPT profile: `search`, `fetch`, `save`, `update`, `delete`, `extract_memories`, `ingest_exchange`, `submit_memory_override`, `flag_memory` by default.
- Codex profile: `search`, `fetch`, `save`, `update`, `delete`, `extract_memories`, `ingest_exchange`, `submit_memory_override`, `flag_memory`.
- Claude profile: `search`, `fetch`, `save`, `update`, `delete`, `extract_memories`, `ingest_exchange`, `submit_memory_override`, `flag_memory`.
- Perplexity profile: `search`, `fetch` by default; set `MCP_PERPLEXITY_CAN_WRITE=true` only if write access is intentionally needed.

Writer profiles save distilled durable memories with `infer=false`. Update/delete tools require exact memory IDs and a reason, so agents can maintain memory organically without broad destructive operations.

## Ingestion Layer

Writer profiles include a server-side ingestion layer for background memory updates after substantive exchanges:

- `extract_memories`: dry-run extraction. It returns proposed memories and flags without writing to Mem0.
- `ingest_exchange`: extracts and applies proposals. It can save, update, skip, or flag; it does not hard-delete.
- `submit_memory_override`: trusted-agent safety net for a high-value memory the automatic extractor may miss. It uses the same dedupe and audit path.
- `flag_memory`: flags an exact memory as stale, duplicate, false, conflicting, merge-worthy, or delete-worthy for later dream maintenance. It does not hard-delete.
- `ingestion_status`: safe aggregate health/status summary. It reports counts, pending flags, recent errors, and usage availability without raw chats, full memory contents, profile tokens, or API keys.

V1 ingestion never hard-deletes. Delete, merge, stale, false, and conflict decisions become audit flags for later batch maintenance. The audit ledger stores source hashes, sanitized previews, proposal metadata, action results, and memory IDs where available; it does not store full raw chat by default.

## Ingestion Environment

- `INGESTION_ENABLED`: defaults to `true`; set to `false` to disable extraction tools without disabling search/fetch.
- `INGESTION_LLM_BASE_URL`: defaults to `https://openrouter.ai/api/v1`.
- `INGESTION_LLM_MODEL`: defaults to `qwen/qwen3.6-flash`; keep automatic ingestion off the hot path unless explicitly needed.
- `INGESTION_LLM_API_KEY`: explicit ingestion LLM key. Wins over `OPENROUTER_API_KEY` when both exist.
- `OPENROUTER_API_KEY`: fallback key for the default OpenRouter/Qwen route.
- `INGESTION_LLM_TIMEOUT`: request timeout in seconds; defaults to `20` so slow extractor calls fail quickly without tying up clients.
- `INGESTION_AUDIT_LOG`: JSONL audit path; defaults to `/data/mem0-mcp-audit.jsonl`.
- `RETRIEVAL_TELEMETRY_ENABLED`: defaults to `true`; set to `false` to disable search telemetry.
- `RETRIEVAL_TELEMETRY_LOG`: JSONL retrieval telemetry path; defaults to `/data/mem0-mcp-retrieval.jsonl`.
- `HOOK_HEARTBEAT_LOG`: JSONL hook heartbeat path; defaults to `/data/mem0-mcp-hooks.jsonl`.
- `MAINTENANCE_FLAG_THRESHOLD`: pending flag count that marks dream maintenance as due; defaults to `10`.

## Hook Protocol

- Session start: use `search`/`fetch` for relevant active context only; do not ingest.
- Session start or reconnect: call `ingestion_status` when available to confirm the memory layer is healthy and check `maintenance.due`. Do not expose profile URLs, API keys, tokens, raw chats, or full memory contents.
- Hooks are reminder-only by default. They send content-free heartbeat rows and may inject one bounded maintenance reminder only when deterministic thresholds say cleanup is due.
- Routine memory writes are agent-first: use `save`, exact-ID `update`, `submit_memory_override`, and `flag_memory` organically.
- Do not call `ingest_exchange` after every exchange. Use `extract_memories` for dry-run debugging, and reserve `ingest_exchange` for explicit batch/audit work where the user accepts the cost.
- Dream maintenance is CLI-first: run `dream_maintenance_cli.py` dry-run, summarize aggregate actions, and execute only after explicit approval with rollback logs and delete caps.

## Client Compliance

Use `client_compliance_probe.py` against the audit ledger to verify that real clients are leaving ingestion audit rows. A client that can search/fetch but never produces `save` or `update` rows is connected but not behaving like background memory.

```powershell
python tools\mem0-remote-mcp\client_compliance_probe.py --audit-log .local\sample-audit.jsonl --expected-origin chatgpt --expected-origin codex --expected-origin claude
```

V1.9 adds hook heartbeat and combined client status checks. Hook heartbeats prove that Codex/Claude client hooks are actually firing in real sessions. Heartbeat rows are content-free: timestamp, origin, hook type, optional source, and optional marker hash only.

```powershell
python tools\mem0-remote-mcp\client_compliance_status_cli.py --audit-log data\mem0-mcp-audit.jsonl --retrieval-log data\mem0-mcp-retrieval.jsonl --hook-log data\mem0-mcp-hooks.jsonl --expected-origin codex --expected-origin claude --expected-origin chatgpt --expected-origin perplexity
python tools\mem0-remote-mcp\stale_config_guard.py --path $HOME\.codex\config.toml --path $HOME\.codex\hooks.json --path $HOME\.claude\.mcp.json --path $HOME\.claude\settings.json
```

Expected client status categories are `healthy`, `hook_only`, `search_only`, `write_only`, `partial`, `missing`, and `error`. The stale-config guard fails if active configs regress to deprecated `codex_hooks`, active Kontext/Tokenomy MCP servers or hooks, missing Mem0 MCP, or missing `mem0_context_hook` references.

## Deployment Shape

- VPS path: `/opt/mem0-remote-mcp`
- Local host port: `127.0.0.1:18890` through an explicit port bind
- Public route: `https://memory-mcp.ionutrosu.xyz/mcp/<profile-token>`
- Mem0 backend from the MCP container: `http://mem0:8000` on the external Docker network `mem0-dev_mem0_network`
- Full snapshot database route: `MEM0_LEXICAL_DATABASE_URL` should use the Mem0 Postgres service name (`postgres:5432`) on that network, not a transient container IP.

Profile tokens and Mem0 API keys are stored only in the remote `.env` and client MCP configs.

## Verify

```powershell
python -m pytest tools\mem0-remote-mcp -q
```


Set `MCP_CHATGPT_CAN_WRITE=false` only if the ChatGPT profile should be forced back to read-only.

## Memory Tiers

Memory tiers use `memory_tier` metadata: `active`, `historical`, or `cold`. `active` is the default. Search can filter by tier; save/update can set the tier. Tiers are wrapper metadata stored in Mem0, not an upstream Mem0 schema patch.

## Retrieval Quality

Search uses fused ranking on top of Mem0 results: semantic score, lexical overlap, adjacent phrase overlap, exact entity/project/person hints, domain metadata, memory type, memory tier, signal strength, and current status. V1.11/V1.12 add a query router inspired by Pinecone-style retrieval systems: trivial prompts can short-circuit, sensitive relationship/psychology queries fetch a deeper candidate pool, project/infrastructure queries get routed domain boosts, and exact entity queries get stronger entity matching. The public `top_k` result count is capped at 20, while the server fetches a larger routed candidate set first so ranking and metadata filters do not starve retrieval.

Optional search filters:

- `domains`
- `memory_tiers`
- `memory_types`
- `current_statuses`

Cold-tier memories are penalized by default but remain searchable when explicitly requested. Historical psychology, relationship, family, and identity memories are intentionally kept retrievable because formative context can explain current behavior.


## Retrieval Telemetry

V1.5 records safe search telemetry for retrieval debugging: timestamp, origin/client, query hash, short query preview, filters, result count, returned IDs/titles, and latency. It does not store full memory text, raw chat transcripts, API keys, or profile tokens. `ingestion_status` includes aggregate retrieval stats when telemetry is enabled.

`retrieval_eval.py` can run fixed retrieval cases against the live Mem0 backend using `MEM0_API_KEY` and `MEM0_BASE_URL`; use it for regression checks before changing ranking rules. A non-secret starter fixture lives at `retrieval_eval_cases.sample.json`.

V1.13 adds `retrieval_eval_cases.v1.13.json`, a domain-based quality pack for the real second-brain surface: AI systems, MCP infrastructure, Vocality/business, opera funding, finance, family/relationship psychology, own-voice context, and cleanup policy. Default CLI output is safe: it prints counts, pass rate, MRR, and failed case names only. Use `--json` or `--output` only for private reports because those include case queries and result IDs.

Example live run from the service container:

```bash
docker exec mem0-remote-mcp-memory-mcp-1 python retrieval_eval.py --cases retrieval_eval_cases.v1.13.json --top-k 5 --min-pass-rate 0.8 --min-cases 10
```


## Lifecycle Decay

V1.6 lifecycle maintenance uses retrieval epochs, not wall-clock time. One retrieval telemetry search event equals one epoch. A memory can decay only after enough memory searches have happened without that memory being returned. If the system is unused for days or weeks, memories do not decay just because calendar time passed.

`lifecycle_cli.py` currently supports a dry-run-first `decay_low_use` job over a supplied memory JSON export and a retrieval telemetry log. Execution requires `--execute` plus `--max-updates`; it writes rollback rows before changing tiers. Protected psychology, relationship, family-origin, identity, opera, systems, major project, high-signal, and dossier memories are kept out of automatic cold downgrade. Frequently retrieved historical/cold memories can be promoted back upward.

Example dry run:

```powershell
python tools\mem0-remote-mcp\lifecycle_cli.py --job decay_low_use --memories-json memories.json --telemetry-log data\mem0-mcp-retrieval.jsonl --output lifecycle-plan.json
```


## Live Memory Snapshots

V1.7 adds `memory_snapshot_cli.py` and `--live-snapshot` support in `lifecycle_cli.py`. The snapshot command fetches the live Mem0 memory set through the configured Mem0 API and writes a private JSON file for lifecycle dry-runs. By default it stores only IDs and metadata, not memory text. Use `--include-text` only for private jobs that explicitly need full text.

Example standalone snapshot:

```powershell
python tools\mem0-remote-mcp\memory_snapshot_cli.py --output data\mem0-mcp-lifecycle-snapshot.json
```

Example lifecycle dry-run with live snapshot:

```powershell
python tools\mem0-remote-mcp\lifecycle_cli.py --job decay_low_use --live-snapshot --snapshot-output data\mem0-mcp-lifecycle-snapshot.json --telemetry-log data\mem0-mcp-retrieval.jsonl --output data\mem0-mcp-lifecycle-plan.json
```

The command stdout reports counts only. It does not print raw memory contents, API keys, or profile tokens.


## Metadata Enrichment

V1.14 adds `metadata_enrichment_cli.py` for dry-run-first memory-type cleanup. It targets relationship, psychology, family, identity, and personal-life memories whose `memory_type` is too generic, then proposes deterministic updates such as `clinical_context`, `relationship_pattern`, `formative_event`, `identity_pattern`, or `person_context`.

Dry-run live snapshot example:

```bash
docker exec mem0-remote-mcp-memory-mcp-1 python metadata_enrichment_cli.py --live-snapshot --snapshot-output /data/mem0-mcp-metadata-snapshot.json --output /data/mem0-mcp-metadata-plan.json
```

Execution is guarded and requires `--execute --max-updates N`. It writes rollback rows before updates and maintenance rows after updates. The default stdout reports counts only and does not print raw memory text.

## Project Observations

V1.15 adds `project_observations_cli.py` for append-only project continuity observations. Rows are stored at `/data/mem0-mcp-project-observations.jsonl` by default and include compact metadata only: origin, project paths, branch/worktree, event type, title, summary, touched files, commands, tests, decisions, next steps, memory IDs, and source hash.

The observation store redacts common secret patterns before writing, skips duplicate `source_hash` rows, tolerates malformed JSONL rows during reads, and supports recent-event and by-file lookup.

V1.16 extends the Codex/Claude hook script with best-effort capture for `post_tool_use`, `session_end`, and `post_compact`. The hook posts compact metadata to `/project-observation/<profile-token>` using a short timeout and suppresses failures. It records tool name, command category, pass/fail status, touched paths, and a sanitized outcome summary only. It does not send stdout, stderr, raw command output, file contents, transcripts, API keys, or profile tokens.

V1.17 adds progressive project retrieval MCP tools on top of this log:

- `project_search`: compact index rows only: id, date, type, title, project, file count, and token estimate.
- `project_timeline`: compact chronological context around an explicit observation id or query anchor.
- `project_fetch`: full observation details only for an explicit id returned by search/timeline.
- `project_file_context`: prior observation titles for a file plus a recommendation on whether a full file read is still needed. This is advisory only and does not block reads.

Project retrieval telemetry stores event name, query hash, result count, and result ids only. It does not store raw queries, raw logs, file contents, transcripts, or secrets.
