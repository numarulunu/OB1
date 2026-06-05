# Mem0 Hosted MCP Ingestion Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a continuous background ingestion layer to the hosted Mem0 MCP server so ChatGPT, Codex, Claude, and future clients can turn normal exchanges into distilled durable memories without raw transcript dumping.

**Architecture:** Keep self-hosted Mem0 as the canonical store and `tools/mem0-remote-mcp` as the live access layer. Add a small server-side ingestion pipeline that does deterministic prefiltering, Qwen/OpenAI-compatible extraction, proposal validation, dedupe/update planning, audit logging, auto-save/auto-update, and flag-only maintenance actions. Reuse policy ideas from `tools/mem0-extractor` and Kontext, but do not resume the old OB1 batch rollout and do not patch upstream Mem0 internals.

**Tech Stack:** Python 3.12, FastAPI, FastMCP, stdlib `urllib`, JSONL audit ledger, self-hosted Mem0 REST API, OpenAI-compatible chat completions through OpenRouter/Qwen by default, pytest.

---

## Current Baseline

- The hosted MCP server is in `tools/mem0-remote-mcp`.
- `core.py` already has `Mem0Client.search`, `fetch`, `save`, `update`, and `delete`, plus profile specs and memory-tier handling.
- `server.py` already exposes `search`/`fetch` for all profiles and `save`/`update`/`delete` for writer profiles.
- `README.md` already documents profile URLs, write permissions, and memory tiers.
- `tools/mem0-extractor` contains useful batch policy code, but the live MCP layer should copy/adapt minimal ideas instead of importing that package by path.
- The old `.local/mem0-extractor/batch-runs/ob1-main-qwen36` rollout is not the active task.

## Decisions Locked In

- `ingest_exchange` runs after substantive user/assistant exchanges.
- Auto-write is default for trusted writer profiles.
- User messages are primary truth.
- Assistant messages count only for accepted decisions, completed actions, summaries, project state, or stable interpretations.
- Generic advice, tool logs, stdout/stderr, stack traces, raw JSON blobs, transcript artifacts, and generic lesson/anatomy sludge are dropped.
- V1 auto-saves and auto-updates. It does not live hard-delete.
- Delete, merge, stale, false, and conflict cases become audit flags for a later dream/maintenance pass.
- All mutations and flags write an audit row with source hash, sanitized preview, profile/client, action, confidence, reason, proposal data, and before/after IDs when available.
- The audit ledger stores no full raw chat by default.
- Qwen through OpenRouter is the default extractor because this is high-volume. Provider, model, base URL, API key, timeout, and enabled state are environment-configurable.
- Agents get `submit_memory_override` for important memories that may have been missed by the extractor. Overrides still dedupe, audit, and obey no-live-delete.
- Hook mapping: session start searches only; every exchange ingests; important tool result ingests only filtered outcomes; post-compact/session end ingests summaries; dream cleanup is later.

## Non-Goals

- Do not continue the old OB1 snapshot cleanup/backfill.
- Do not add automatic hard-delete execution to the hot path.
- Do not store raw transcripts in audit logs.
- Do not modify upstream Mem0 package code.
- Do not add a full dream cleanup engine in this plan. This plan creates the flag/audit surface the dream pass will consume later.
- Do not deploy or restart the VPS service until local tests pass and the user approves the remote change.

## Success Criteria

- `python -m pytest tools\mem0-remote-mcp -q` passes locally.
- Read-only MCP profiles do not expose ingestion write tools.
- Writer MCP profiles expose `extract_memories`, `ingest_exchange`, `submit_memory_override`, and `flag_memory`.
- `extract_memories` can run without writing.
- `ingest_exchange` can save/update/skip/flag with fake LLM and fake Mem0 clients in tests.
- Raw delete proposals normalize into flag-only actions.
- The Docker image includes all new runtime modules.
- The audit ledger path is persistent in Docker compose.
- Documentation explains how clients should call the new tools.

## File Structure

- Create `tools/mem0-remote-mcp/ingestion.py`: message normalization, deterministic gate, source hashing, sanitized previews, extraction prompt, response parser, proposal validation.
- Create `tools/mem0-remote-mcp/ingestion_llm.py`: OpenAI-compatible chat-completions client.
- Create `tools/mem0-remote-mcp/audit_ledger.py`: append-only JSONL audit ledger and source-hash duplicate checks.
- Modify `tools/mem0-remote-mcp/core.py`: metadata extras on save/update, exact duplicate checks, proposal application, flag-only maintenance actions.
- Modify `tools/mem0-remote-mcp/server.py`: ingestion env config and MCP tools.
- Modify `tools/mem0-remote-mcp/Dockerfile`: copy the new runtime modules.
- Modify `tools/mem0-remote-mcp/docker-compose.yaml`: persist audit storage under `/data`.
- Modify `tools/mem0-remote-mcp/README.md`: ingestion tools, env vars, hook protocol, audit behavior.
- Modify `AGENTS.md` and `CLAUDE.md`: concise Mem0 ingestion protocol for Codex/Claude.
- Create `tools/mem0-remote-mcp/test_ingestion.py`: normalization, hash, gate, prompt, parser tests.
- Create `tools/mem0-remote-mcp/test_ingestion_apply.py`: fake Mem0/LLM application and audit tests.
- Modify `tools/mem0-remote-mcp/test_core.py`: metadata-extra and proposal-application tests.
- Modify `tools/mem0-remote-mcp/test_server_profiles.py`: ingestion capability assumptions.
- Modify `project_log.md`: final project activity entry after verified implementation.

---

### Task 1: Add Ingestion Domain Model And Deterministic Gate

**Files:**
- Create: `tools/mem0-remote-mcp/ingestion.py`
- Create: `tools/mem0-remote-mcp/test_ingestion.py`

- [ ] **Step 1: Write failing tests for message normalization, source hash, preview, and gate**

Add tests that import:

```python
from ingestion import build_sanitized_preview, normalize_messages, should_call_llm, source_hash
```

Required cases:

```python
def test_source_hash_is_stable_for_same_exchange():
    messages = [{'role': 'user', 'content': 'We decided Mem0 ingestion auto-writes after every exchange.'}]
    assert source_hash(normalize_messages(messages)) == source_hash(normalize_messages(messages))


def test_preview_is_single_line_and_truncated():
    messages = [{'role': 'user', 'content': 'line one\nline two ' + ('x' * 600)}]
    preview = build_sanitized_preview(normalize_messages(messages), max_chars=120)
    assert '\n' not in preview
    assert len(preview) <= 120


def test_gate_skips_tiny_chatter():
    decision = should_call_llm(normalize_messages([{'role': 'user', 'content': 'ok thanks'}]))
    assert decision.keep is False
    assert decision.reason == 'low_signal_chatter'


def test_gate_keeps_mem0_architecture_decision():
    text = 'We decided the Mem0 hosted MCP ingestion layer should auto-write by default using Qwen.'
    decision = should_call_llm(normalize_messages([{'role': 'user', 'content': text}]))
    assert decision.keep is True
    assert 'ai' in decision.domains
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `python -m pytest tools\mem0-remote-mcp\test_ingestion.py -q`

Expected result: failure because `ingestion.py` does not exist yet.

- [ ] **Step 3: Implement the domain primitives**

Create `ingestion.py` with these public objects:

```python
@dataclass(frozen=True)
class IngestionMessage:
    role: str
    content: str


@dataclass(frozen=True)
class GateDecision:
    keep: bool
    reason: str
    domains: list[str]


@dataclass(frozen=True)
class IngestionProposal:
    action: str
    content: str
    domains: list[str]
    memory_type: str
    signal_strength: int
    current_status: str
    memory_tier: str
    confidence: float
    reason: str
    existing_id: str = ''
    flag_type: str = ''
```

Implement `normalize_messages`, `source_hash`, `build_sanitized_preview`, and `should_call_llm`.

Gate rules:

- Drop tiny chatter under 24 meaningful characters unless it contains explicit durable commands.
- Drop tool-log/transcript-processing terms such as `stdout`, `stderr`, `traceback`, `speaker diarization`, and `canonical names`.
- Keep explicit decisions, preferences, architecture, project state, workflows, AI systems, business/money, opera, Vocality-own-voice, relationships, family, psychology, and identity-shaping material.
- For vocality, keep own voice/career/method signal; drop generic lesson/anatomy sludge.

- [ ] **Step 4: Run Task 1 tests**

Run: `python -m pytest tools\mem0-remote-mcp\test_ingestion.py -q`

Expected result: pass.

---

### Task 2: Add Extraction Prompt, Parser, And OpenAI-Compatible LLM Client

**Files:**
- Modify: `tools/mem0-remote-mcp/ingestion.py`
- Create: `tools/mem0-remote-mcp/ingestion_llm.py`
- Modify: `tools/mem0-remote-mcp/test_ingestion.py`

- [ ] **Step 1: Add failing prompt/parser tests**

Add tests for these requirements:

- `build_extraction_prompt` says user messages are primary truth.
- Assistant messages only count for accepted decisions/actions/summaries/project state/stable interpretations.
- V1 has no hard-delete.
- Output must be JSON only.
- Raw `action='delete'` normalizes to `action='flag'` and `flag_type='delete_candidate'`.

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tools\mem0-remote-mcp\test_ingestion.py -q`

Expected result: failure because prompt/parser functions are missing.

- [ ] **Step 3: Implement prompt and parser in `ingestion.py`**

Add `POLICY_TEXT`, `build_extraction_prompt(messages, source, context_messages=None)`, and `parse_extraction_response(raw)`. Response schema supports `save`, `update`, `skip`, and `flag`; `flag_type` supports `delete_candidate`, `merge_candidate`, `conflict_candidate`, and `stale_candidate`.

Parser rules:

- Invalid JSON raises `ValueError` with `invalid extraction JSON`.
- Missing `proposals` raises `ValueError` with `proposals field is required`.
- Empty/bare content for `save` and `update` proposals is rejected.
- `delete` becomes `flag/delete_candidate`.
- Unknown actions become `skip` with reason preserved.
- `signal_strength` clamps to 1-10.
- `confidence` clamps to 0-1.
- Memory tier normalizes to `active`, `historical`, or `cold`.

- [ ] **Step 4: Implement `ingestion_llm.py`**

Create `LLMConfig`, `build_chat_body`, and `call_llm`. The client posts to `{base_url.rstrip('/')}/chat/completions`, uses `temperature: 0`, uses `response_format: {'type': 'json_object'}`, returns `choices[0].message.content`, raises clear `RuntimeError` on missing key/HTTP/malformed response, and never logs API keys.

- [ ] **Step 5: Run Task 2 tests**

Run: `python -m pytest tools\mem0-remote-mcp\test_ingestion.py -q`

Expected result: pass.

---

### Task 3: Add Append-Only Audit Ledger

**Files:**
- Create: `tools/mem0-remote-mcp/audit_ledger.py`
- Create: `tools/mem0-remote-mcp/test_ingestion_apply.py`

- [ ] **Step 1: Write failing audit tests**

Add tests proving `AuditLedger.append` creates parent directories, writes one UTF-8 JSONL row with `ts`, `action`, `source_hash`, and `preview`, and `has_source_hash` returns true for a recently written source hash. The test must not require or store full raw message content.

Test shape:

```python
def test_audit_ledger_appends_and_detects_source_hash(tmp_path):
    ledger = AuditLedger(tmp_path / 'audit.jsonl')
    ledger.append({'action': 'save', 'source_hash': 'abc123', 'preview': 'short preview'})
    assert ledger.has_source_hash('abc123') is True
    assert ledger.has_source_hash('missing') is False
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tools\mem0-remote-mcp\test_ingestion_apply.py -q`

Expected result: failure because `audit_ledger.py` does not exist.

- [ ] **Step 3: Implement `AuditLedger`**

Create `AuditLedger` with `__init__(path)`, `append(row)`, and `has_source_hash(source_hash, scan_limit=5000)`. It must add ISO UTC `ts`, create the parent directory, write one JSON object per line with `ensure_ascii=False`, scan recent rows for duplicate source hashes, and ignore malformed legacy audit rows instead of crashing the hot path.

- [ ] **Step 4: Run Task 3 tests**

Run: `python -m pytest tools\mem0-remote-mcp\test_ingestion_apply.py -q`

Expected result: pass.

---

### Task 4: Extend Mem0Client For Metadata Extras And Proposal Application

**Files:**
- Modify: `tools/mem0-remote-mcp/core.py`
- Modify: `tools/mem0-remote-mcp/test_core.py`
- Modify: `tools/mem0-remote-mcp/test_ingestion_apply.py`

- [ ] **Step 1: Add failing metadata-extra tests**

In `test_core.py`, add tests for `save` with `metadata_extra={'source_hash': 'abc', 'ingestion_origin': 'codex'}` and `update` with `metadata_extra={'source_hash': 'abc'}`. Verify extra metadata merges with the existing `source`, `client`, `domains`, `memory_type`, `signal_strength`, `current_status`, and `memory_tier` fields, and update still preserves `last_modified_by`, `last_modified_via`, and `last_modified_reason`.

- [ ] **Step 2: Add failing proposal-application tests**

In `test_ingestion_apply.py`, use fake clients and fake ledgers to test:

- `save` proposal with no exact duplicate calls `POST /memories` and writes a `save` audit row.
- Exact duplicate content becomes `skip` and writes a `skip` audit row.
- `update` proposal with `existing_id` calls `PUT /memories/{id}`, fetches before/after through existing `update`, and writes an `update` audit row.
- `flag/delete_candidate` never calls Mem0 `DELETE` and writes a `flag` audit row.

- [ ] **Step 3: Run tests and confirm failure**

Run: `python -m pytest tools\mem0-remote-mcp\test_core.py tools\mem0-remote-mcp\test_ingestion_apply.py -q`

Expected result: failure because metadata extras and application helpers do not exist.

- [ ] **Step 4: Add `metadata_extra` to `save` and `update`**

Modify signatures:

```python
def save(content: str, domains: list[str] | None = None, memory_type: str = 'note', signal_strength: int | float | None = None, current_status: str = 'active', memory_tier: str = 'active', metadata_extra: dict[str, Any] | None = None) -> dict[str, Any]:
```

```python
def update(memory_id: str, content: str, reason: str, domains: list[str] | None = None, memory_type: str | None = None, signal_strength: int | float | None = None, current_status: str | None = None, memory_tier: str | None = None, metadata_extra: dict[str, Any] | None = None) -> dict[str, Any]:
```

Merge behavior:

```python
if metadata_extra:
    metadata.update({key: value for key, value in metadata_extra.items() if value is not None})
```

Existing call sites must continue to work unchanged.

- [ ] **Step 5: Add proposal application helpers**

Add methods or module-level helpers in `core.py`: `ingestion_metadata`, `exact_duplicate_id`, `best_existing_memory_id`, and `apply_ingestion_proposal`.

Rules:

- `skip` writes audit only.
- `flag` writes audit only and never deletes.
- `update` uses `existing_id` when present.
- `update` without `existing_id` searches top 5 and updates only when lexical overlap is strong enough; otherwise it becomes `save` if confidence is high, or `flag/conflict_candidate` if confidence is low.
- `save` searches for exact normalized duplicate text before writing.
- New high-confidence memories call `save` with ingestion metadata.
- Audit rows include `source_hash`, `preview`, `origin`, `action`, `confidence`, `reason`, and memory IDs when available.

- [ ] **Step 6: Run Task 4 tests**

Run: `python -m pytest tools\mem0-remote-mcp\test_core.py tools\mem0-remote-mcp\test_ingestion_apply.py -q`

Expected result: pass.

---

### Task 5: Expose Hosted MCP Ingestion Tools

**Files:**
- Modify: `tools/mem0-remote-mcp/server.py`
- Modify: `tools/mem0-remote-mcp/test_server_profiles.py`

- [ ] **Step 1: Add profile-capability tests**

Extend `test_server_profiles.py` so it proves ChatGPT, Codex, and Claude writer profiles are ingestion-capable when `can_write=True`, while Perplexity remains read-only unless `MCP_PERPLEXITY_CAN_WRITE=true`.

- [ ] **Step 2: Add ingestion environment config in `server.py`**

Add env reads for `INGESTION_ENABLED`, `INGESTION_LLM_BASE_URL`, `INGESTION_LLM_MODEL`, `INGESTION_LLM_API_KEY`, `OPENROUTER_API_KEY`, `INGESTION_LLM_TIMEOUT`, and `INGESTION_AUDIT_LOG`. Use OpenRouter and `qwen/qwen3.6-flash` as defaults. Server startup must not fail if the ingestion API key is missing; search/fetch should remain available. Ingestion tools should return a clear disabled/config error result if they need the LLM and no key exists.

- [ ] **Step 3: Add an extraction helper inside `make_mcp_profile`**

The helper should normalize messages, compute `source_hash` and `preview`, skip if the audit ledger already has the source hash, run `should_call_llm`, call the LLM only when the gate keeps, parse proposals, and return `source_hash`, `preview`, `gate`, `proposals`, and `errors`. This helper must not mutate Mem0.

- [ ] **Step 4: Expose `extract_memories` for writer profiles**

Inside `if spec.can_write:`, add `extract_memories(messages, context_messages=None)`. It returns JSON with the source hash, preview, gate result, proposal dicts, and errors. It never writes.

- [ ] **Step 5: Expose `ingest_exchange` for writer profiles**

Add `ingest_exchange(messages, context_messages=None)`. It runs extraction and applies each proposal through `client.apply_ingestion_proposal`. It returns compact counts: `saved`, `updated`, `skipped`, `flagged`, `errors`, and `source_hash`.

- [ ] **Step 6: Expose `submit_memory_override` for writer profiles**

Add `submit_memory_override(content, reason, domains=None, memory_type='pattern', signal_strength=8, current_status='active', memory_tier='active', confidence=0.9)`. It builds a `save` proposal and sends it through the same dedupe/audit/apply path.

- [ ] **Step 7: Expose `flag_memory` for writer profiles**

Add `flag_memory(id, flag_type, reason, confidence=0.8)`. It fetches the exact memory, writes a flag audit row, and returns a compact confirmation. It must not call `DELETE`.

- [ ] **Step 8: Run hosted MCP tests**

Run: `python -m pytest tools\mem0-remote-mcp -q`

Expected result: pass.

---

### Task 6: Add Local End-To-End Tests With Fake LLM And Fake Mem0

**Files:**
- Modify: `tools/mem0-remote-mcp/test_ingestion_apply.py`

- [ ] **Step 1: Add fake extraction/apply test**

Test a fake Qwen JSON response with one durable `save` proposal: `Ionut wants Mem0 ingestion to auto-write after every substantive exchange using Qwen.` Assert the apply path calls Mem0 `POST /memories`, includes `source_hash` metadata, and appends an audit row.

- [ ] **Step 2: Add fake flag test**

Parse a fake `delete_candidate` proposal and assert no Mem0 delete call occurs while the audit ledger receives `action='flag'` and `flag_type='delete_candidate'`.

- [ ] **Step 3: Run end-to-end tests**

Run: `python -m pytest tools\mem0-remote-mcp\test_ingestion.py tools\mem0-remote-mcp\test_ingestion_apply.py -q`

Expected result: pass.

---

### Task 7: Package Runtime Files And Persistent Audit Storage

**Files:**
- Modify: `tools/mem0-remote-mcp/Dockerfile`
- Modify: `tools/mem0-remote-mcp/docker-compose.yaml`
- Modify: `tools/mem0-remote-mcp/README.md`

- [ ] **Step 1: Update Dockerfile runtime copy list**

Change the runtime copy line to:

```dockerfile
COPY audit_ledger.py core.py ingestion.py ingestion_llm.py server.py ./
```

- [ ] **Step 2: Persist audit storage**

Update `docker-compose.yaml` so `memory-mcp` mounts `./data:/data`.

- [ ] **Step 3: Document ingestion env vars**

Add README documentation for `INGESTION_ENABLED`, `INGESTION_LLM_BASE_URL`, `INGESTION_LLM_MODEL`, `INGESTION_LLM_API_KEY`, `OPENROUTER_API_KEY`, `INGESTION_LLM_TIMEOUT`, and `INGESTION_AUDIT_LOG`. State that `INGESTION_LLM_API_KEY` wins over `OPENROUTER_API_KEY` when both exist.

- [ ] **Step 4: Document ingestion tools**

Document `extract_memories`, `ingest_exchange`, `submit_memory_override`, and `flag_memory`. State that V1 ingestion never hard-deletes and the audit ledger stores hashes/previews/proposals, not full raw chat.

- [ ] **Step 5: Run package tests**

Run: `python -m pytest tools\mem0-remote-mcp -q`

Expected result: pass.

---

### Task 8: Add Hook Guidance To Agent Docs

**Files:**
- Modify: `tools/mem0-remote-mcp/README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document hook mapping in README**

Add this behavior:

- Session start: use `search`/`fetch` for relevant active context only; do not ingest.
- Every substantive exchange: call `ingest_exchange` with the latest user/assistant messages and optional small context window.
- Tool results: ingest only material outcomes such as deploys, migrations, test/build results after edits, production config changes, or user-approved destructive operations.
- Post-compact/session end: call `ingest_exchange` on the compact/session summary.
- Dream maintenance: a later batch job reads audit flags and decides delete/merge/consolidation.

- [ ] **Step 2: Add concise agent instructions**

Add a `Mem0 Ingestion Protocol` section to `AGENTS.md` and `CLAUDE.md`:

```markdown
When the hosted Mem0 MCP exposes `ingest_exchange`, call it after substantive user/assistant exchanges so durable memory can update in the background. Use `submit_memory_override` when the automatic extractor may miss a high-value memory. Use `flag_memory` for stale, duplicate, false, or conflicting memories. Do not hard-delete through ingestion. Do not ingest raw tool logs, secrets, temporary logs, or generic assistant advice.
```

- [ ] **Step 3: Review docs diff**

Run: `git diff -- AGENTS.md CLAUDE.md tools\mem0-remote-mcp\README.md`

Expected result: only ingestion protocol and hook guidance changed.

---

### Task 9: Full Local Verification

**Files:**
- Review only unless tests require small fixes.

- [ ] **Step 1: Run all hosted MCP tests**

Run: `python -m pytest tools\mem0-remote-mcp -q`

Expected result: pass.

- [ ] **Step 2: Run extractor tests only if shared code was touched**

Run this only if implementation modified `tools/mem0-extractor` or shared policy code: `python -m pytest tools\mem0-extractor -q`

Expected result: pass.

- [ ] **Step 3: Inspect diff for accidental secrets/raw data**

Run: `git diff -- tools\mem0-remote-mcp AGENTS.md CLAUDE.md project_log.md`

Expected result: no secrets, no raw chat dumps, no unrelated rewrites.

---

### Task 10: Prepare Hosted Deployment With Explicit Approval Gate

**Files:**
- Local code only until the user approves remote deploy.

- [ ] **Step 1: Inspect non-secret deployment references**

Run:

```powershell
rg -n 'mem0-remote-mcp|memory-mcp|18890|memory-mcp\.ionutrosu\.xyz|rclone|backup' 'C:\Users\Gaming PC\Desktop\self-hosting-setup'
```

Expected result: identify the existing deploy path, service name, and backup/rclone context without printing secrets.

- [ ] **Step 2: State the remote deploy command before running it**

Before any SSH/deploy/restart command, state the host alias, remote directory, exact command, expected effect, verification command, and rollback command. Do not run the remote command until the user confirms.

- [ ] **Step 3: Deploy after confirmation only**

Use the confirmed host alias and remote path. The command should follow this shape:

```bash
cd /opt/mem0-remote-mcp && docker compose up -d --build && curl -fsS http://127.0.0.1:18890/health
```

Expected result: the health check returns JSON with `ok: true` and expected profiles.

---

### Task 11: Live Smoke Test With Temporary Memory

**Files:**
- No local file edits unless a bug is found.

- [ ] **Step 1: Dry-run extraction smoke**

Using a writer MCP profile, call `extract_memories` with a harmless canary exchange containing the phrase `silver river candle`. Expected result: one `save` proposal or a clear gate/config result. Do not expose profile tokens in logs or chat.

- [ ] **Step 2: Apply ingestion smoke**

Call `ingest_exchange` with the same canary exchange. Expected result: `saved` count is 1 or `skipped` if already processed by source hash.

- [ ] **Step 3: Verify retrieval**

Search for `silver river candle` through the same profile. Expected result: the temporary canary memory is returned.

- [ ] **Step 4: Clean up canary through explicit delete only**

Because ingestion hot path must not delete, use the existing explicit `delete` tool with the exact canary memory ID and reason `temporary ingestion smoke test cleanup`. Expected result: exact temporary memory removed; no broad deletes.

---

### Task 12: Project Log And Durable State Update

**Files:**
- Modify: `project_log.md`

- [ ] **Step 1: Add project log entry**

Add a concise dated entry with summary, files touched, verification commands and results, deployment status, and remaining next step: dream maintenance pass over audit flags.

- [ ] **Step 2: Save/update Mem0 project-state memory**

After implementation and verification, save a compact memory saying the hosted Mem0 MCP now has an ingestion layer, which tools are exposed, which model/provider is configured, and whether hosted deployment/live smoke passed.

- [ ] **Step 3: Final verification before completion claim**

Run: `python -m pytest tools\mem0-remote-mcp -q`

Expected result: pass.

---

## Implementation Notes

- Keep edits surgical. Do not rewrite the existing MCP server structure unless a test forces it.
- Keep the hot path resilient: failed ingestion should return a clear error for the ingestion tool, not break search/fetch.
- Do not print env values, MCP profile tokens, OpenRouter keys, Mem0 API keys, raw chat contents, or raw audit rows.
- Prefer fake clients and fake LLM responses in tests. Do not spend OpenRouter credits for local unit tests.
- Ingestion `delete` means `flag/delete_candidate`, not hard delete.
- The existing explicit MCP `delete` tool remains available for exact-ID deletes outside the ingestion hot path.
- If the worktree contains unrelated existing changes, do not revert them. Diff only the touched files.

## Execution Handoff

Plan complete when this file is cleanly ordered and self-reviewed. Implementation can proceed inline in this session or with task-scoped workers. Because the current worktree is dirty, do not commit unless the user explicitly asks for commits.
