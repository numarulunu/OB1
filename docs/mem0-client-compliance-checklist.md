# Mem0 Client Compliance Checklist

Use this checklist after deploying hosted MCP changes or changing client MCP configuration. The goal is to prove each real client can retrieve memory and leave an ingestion audit row.

Do not paste MCP profile URLs, profile tokens, API keys, raw chats, or full memory contents into reports.

## Shared Canary Shape

Use a harmless unique phrase for each client, for example:

`mem0 compliance canary <client> <YYYYMMDD-HHMM>`

Ask the client to do four things:

1. Call `ingestion_status` and report only aggregate status.
2. Search Mem0 for the canary phrase to confirm retrieval works.
3. Call `ingest_exchange` with the latest canary user/assistant exchange.
4. Search for the canary after a short indexing delay, then delete the exact canary memory if one was created.

Expected result:

- `ingestion_status` exists.
- `search`/`fetch` works.
- `ingest_exchange` is available for writer clients.
- Audit ledger shows a row for that origin.
- Temporary canary memory is cleaned up by exact ID when created.

## ChatGPT

Use the ChatGPT connector configured with the ChatGPT profile.

Prompt:

```text
Run a Mem0 compliance smoke test. Use the canary phrase "mem0 compliance canary chatgpt <timestamp>". First call ingestion_status and summarize only aggregate status. Then search for the canary, call ingest_exchange for this exchange, wait briefly if needed, search again, and delete only the exact temporary canary memory if it was saved. Do not reveal connector URLs, tokens, API keys, or raw tool payloads.
```

Expected:

- ChatGPT sees `ingestion_status`.
- ChatGPT can search/fetch.
- ChatGPT can call `ingest_exchange` if the profile is writer-enabled.
- Compliance probe shows `chatgpt` as `writing` or at least `seen_no_write` for a skipped canary.

## Local Codex

Use this repo or another Codex session that has the hosted Mem0 MCP connected.

Prompt:

```text
Run a Mem0 compliance smoke test with canary "mem0 compliance canary codex-local <timestamp>". Use ingestion_status, search, ingest_exchange, then exact-ID cleanup if a temporary memory is created. Do not print secrets or profile URLs.
```

Expected:

- `codex` origin appears in the audit ledger.
- If the canary is skipped as low-signal, the status may be `seen_no_write`; for a durable test phrase, it should become `writing`.

## Local Claude

Use the local Claude profile connected to the hosted MCP.

Prompt:

```text
Run a Mem0 compliance smoke test with canary "mem0 compliance canary claude-local <timestamp>". Use ingestion_status, search, ingest_exchange, then exact-ID cleanup if a temporary memory is created. Do not print secrets or profile URLs.
```

Expected:

- `claude` origin appears in the audit ledger.
- Claude can call `ingest_exchange`, not only `search`/`fetch`.

## VPS Codex

Run the same Codex smoke test inside the VPS/container environment where Codex is installed.

Expected:

- The audit row should still use the `codex` profile origin.
- This verifies the VPS/container config is not stale relative to local Codex.

## VPS Claude

Run the same Claude smoke test inside the VPS/container environment where Claude is installed.

Expected:

- The audit row should use the `claude` profile origin.
- This catches the prior failure mode where Claude was connected in one environment but not another.

## Perplexity

Perplexity is read-only by default.

Expected:

- `search`/`fetch` works.
- `ingest_exchange`, `save`, `update`, and `delete` should not be available unless `MCP_PERPLEXITY_CAN_WRITE=true` was intentionally enabled.
- Compliance probe should not require Perplexity as a writer unless the profile has been changed.

## Local Probe Command

For a copied or mounted audit ledger:

```powershell
python tools\mem0-remote-mcp\client_compliance_probe.py --audit-log .local\mem0-mcp-audit.jsonl --expected-origin chatgpt --expected-origin codex --expected-origin claude
```

Interpretation:

- `writing`: client produced at least one `save` or `update` audit row.
- `seen_no_write`: client touched ingestion but only skipped/flagged/extracted in the scanned window.
- `error`: client has recent ingestion errors.
- `missing`: no recent audit row for that origin.
