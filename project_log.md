# Project Log

## 2026-05-07 - Human Memory V2 Mem0 Pilot Review Bridge

Summary:
- Added a local-only review and Mem0 export bridge for OB1 Human Memory V2 cards.
- Generated a self-contained Desktop review dashboard and Mem0 OSS request JSONL artifacts.
- No Supabase rows, Mem0 server, or remote VPS state were mutated.

Files touched:
- `docs/superpowers/plans/2026-05-07-mem0-pilot-review.md`
- `recipes/shadow-cleanup/review_mem0_pipeline.py`
- `recipes/shadow-cleanup/test_review_mem0_pipeline.py`
- `recipes/shadow-cleanup/README.md`
- `tool_registry.md`

Artifacts generated:
- `C:\Users\Gaming PC\Desktop\OB1-Mem0-Pilot-Review-20260507\review-dashboard.html`
- `.local/open-brain-cleanup/human-v2/mem0/mem0-full-import-requests.jsonl`
- `.local/open-brain-cleanup/human-v2/mem0/mem0-pilot-top300-requests.jsonl`

Verification:
- `python recipes\shadow-cleanup\review_mem0_pipeline.py import-mem0 --requests .local\open-brain-cleanup\human-v2\mem0\mem0-pilot-top300-requests.jsonl --base-url https://mem0.example.com`
- `python -m pytest recipes\shadow-cleanup\test_review_mem0_pipeline.py recipes\shadow-cleanup\test_human_memory_v2.py -q`

Decisions:
- Keep OB1 as the staging/audit layer until Mem0 retrieval is proven on a pilot subset.
- Export curated V2 cards to Mem0 with `infer=false` so Mem0 stores the already-distilled memory card instead of re-extracting from raw chat.
- Default `import-mem0` to dry-run; require `--execute` for any network mutation.

Next step:
- Get VPS host/domain details, deploy a staging Mem0 instance after explicit remote command approval, import the 300-card pilot, and test retrieval quality.

## 2026-05-07 - Mem0 VPS Staging Deployment

Summary:
- Deployed self-hosted Mem0 OSS staging on the app server under `/opt/mem0-staging`.
- Exposed dashboard and API through Pangolin: `https://mem0.ionutrosu.xyz` and `https://mem0-api.ionutrosu.xyz`.
- Started Mem0 with the existing OpenRouter key for the LLM side and `deepseek/deepseek-v4-pro` as the default LLM model.
- Did not import memory cards because no real embedding provider key is configured yet.

Files/artifacts touched:
- `docs/superpowers/plans/2026-05-07-mem0-vps-staging-deployment.md`
- Remote `/opt/mem0-staging/src/server/docker-compose.yaml`
- Remote `/opt/mem0-staging/src/server/.env`
- Remote Mem0 dashboard `Dockerfile` and `package.json` build pins
- Local helper scripts under `.local/mem0-staging/`

Verification:
- `https://mem0.ionutrosu.xyz` returns `307`.
- `https://mem0-api.ionutrosu.xyz/docs` returns `200`.
- `https://mem0-api.ionutrosu.xyz/configure` without auth returns `401`.
- Mem0 API, dashboard, and Postgres containers are running; dashboard and Postgres are healthy.
- Mem0 pilot import dry-run reports `request_count=300` and `attempted=0`.

Decisions:
- Bound Mem0 dashboard/API to localhost-only ports `13000` and `18888`.
- Removed the official compose Postgres host-port exposure.
- Patched dashboard build from Node 20 to Node 22 and pinned pnpm 10.33.0 because current upstream dashboard build pulls pnpm 11, which fails on Node 20 and requires build-script approval.

Next step:
- Configure a real embedding provider key, then run the 300-card pilot import with `--execute` and test retrieval before full import.

## 2026-05-07 - Mem0 dashboard CORS and LLM cost fix

Summary:
- Fixed Mem0 dashboard admin setup network error by changing the API container `DASHBOARD_URL` override from localhost to `https://mem0.ionutrosu.xyz`.
- Switched Mem0 default LLM from `deepseek/deepseek-v4-pro` to `deepseek/deepseek-v4-flash` for cheaper OpenRouter-backed memory operations.

Verification:
- CORS preflight for `https://mem0-api.ionutrosu.xyz/auth/register` from `https://mem0.ionutrosu.xyz` returns `200` with `Access-Control-Allow-Origin` set correctly.
- `GET /auth/setup-status` returns `{"needsSetup":true}` with correct CORS headers.
- Invalid register POST now reaches API and returns validation `422` instead of browser-blocking network failure.
- Containers remain running; dashboard and Postgres are healthy.

Decision:
- Keep DeepSeek v4 Flash as the default LLM for now. A real OpenAI or Gemini embedding key is still required before importing memory cards.

## 2026-05-07 - Mem0 OpenAI Pilot Import

Summary:
- Completed Mem0 setup with OpenAI as the active provider for the self-hosted staging instance.
- Switched the Mem0 LLM model from `gpt-5.4-nano` to `gpt-4.1-nano-2025-04-14` because the current Mem0 Chat Completions integration sends `max_tokens`, which newer GPT-5.4 nano rejects.
- Imported the 300-card OB1 Human Memory V2 pilot into Mem0 with `infer=false`.

Files touched:
- `recipes/shadow-cleanup/review_mem0_pipeline.py`
- `recipes/shadow-cleanup/test_review_mem0_pipeline.py`
- Remote Mem0 runtime config via `/configure`

Verification:
- Mem0 API docs return `200`; unauthenticated `/configure` returns `401`.
- Import smoke: 5 attempted, 5 succeeded.
- Pilot remainder: 295 attempted, 295 succeeded, 0 failed.
- Search checks using `filters={user_id, agent_id}` returned 3 results for opera/psychology, AI/workflows, and relationships/family-origin queries.
- `python -m pytest recipes\shadow-cleanup\test_review_mem0_pipeline.py -q` passed: 6 passed.

Decisions:
- Use `gpt-4.1-nano-2025-04-14` for compatibility and low cost in this Mem0 build.
- Keep `text-embedding-3-small` for embeddings.
- Continue using curated `infer=false` imports rather than raw-chat extraction.
- Use `filters` for Mem0 search; top-level `user_id`/`agent_id` are rejected by this Mem0 search API.

Next step:
- Review pilot retrieval quality, rotate the exposed generated Mem0 API key, then decide whether to import the remaining 1,889 curated cards.

## 2026-05-07 - Mem0 Full Curated Import

Summary:
- Ran retrieval QA against the 300-card Mem0 pilot and confirmed searches returned domain-relevant results.
- Detected that the full import file did not begin with the pilot rows, so avoided a blind `--skip 300` import.
- Generated a remaining-only Mem0 import JSONL by excluding the 300 pilot cards via stable content/source/type identity.
- Imported the remaining 1,889 curated Human Memory V2 cards into Mem0.

Files/artifacts touched:
- `recipes/shadow-cleanup/review_mem0_pipeline.py`
- `recipes/shadow-cleanup/test_review_mem0_pipeline.py`
- `.local/open-brain-cleanup/human-v2/mem0/mem0-full-import-remaining-after-pilot-requests.jsonl`
- Remote Mem0 Postgres database at `public.mem0_memories`

Verification:
- Remaining-only file generation: 2,189 full rows, 300 pilot identities found, 1,889 remaining rows written.
- Remaining import result: 1,889 attempted, 1,889 succeeded, 0 failed.
- Final remote DB count: `SELECT COUNT(*) FROM public.mem0_memories;` returned 2,189.
- Search QA returned 5 results for each tested domain query using `filters={user_id, agent_id}`.
- Mem0 public checks: dashboard `307`, API docs `200`, protected config without auth `401`.
- `python -m pytest recipes\shadow-cleanup\test_review_mem0_pipeline.py -q` passed: 6 passed.

Decisions:
- Do not use export-index-based `ob1_card_id` for cross-file dedupe, because the ID includes export order.
- Use content + memory type + source IDs as the safe identity for excluding already imported pilot cards.

Next step:
- Rotate the exposed generated Mem0 client API key, then connect Codex/Claude/OpenClaw or other tools to Mem0 and test live memory retrieval/insertion.

## 2026-05-07 - Codex Mem0 MCP Bridge

Summary:
- Added a local stdio MCP bridge so Codex can search and save distilled memories in the self-hosted Mem0 instance.
- Registered the bridge globally in Codex as `mem0`.
- Created a dedicated Mem0 client API key labeled `Codex MCP local bridge` and stored it outside the repo in the Codex sandbox secrets directory.

Files/artifacts touched:
- `tools/mem0-codex-mcp/mem0_mcp_server.py`
- `tools/mem0-codex-mcp/run_mem0_mcp.ps1`
- `tools/mem0-codex-mcp/test_mem0_mcp_server.py`
- `tools/mem0-codex-mcp/README.md`
- `%USERPROFILE%\.codex\.sandbox-secrets\mem0-codex-mcp.env` outside repo
- Global Codex MCP config via `codex mcp add mem0 ...`

Verification:
- `python -m pytest tools\mem0-codex-mcp\test_mem0_mcp_server.py -q` passed: 3 passed.
- `codex mcp list` shows `mem0` enabled.
- Direct MCP stdio smoke test initialized `mem0-codex-mcp`, listed `memory_search` and `memory_save`, and returned 2 Mem0 search results.

Decisions:
- Expose only `memory_search` and `memory_save` initially.
- `memory_save` uses `infer=false` and is meant for distilled durable memory, not raw transcripts.
- Search uses Mem0 `filters.user_id` because this Mem0 API rejects top-level `user_id` in search calls.

Next step:
- Start a fresh Codex session and confirm the `mem0` MCP tools appear in the session tool list.

## 2026-05-07 - Claude Mem0 MCP Bridge

Summary:
- Reused the local Mem0 MCP bridge for Claude Code.
- Added `-SecretPath` support to the bridge wrapper so each AI client can use its own Mem0 client key.
- Created a dedicated Mem0 API key labeled `Claude MCP local bridge` and stored it outside the repo.
- Registered the bridge as a user-scoped Claude MCP server named `mem0`.
- Revoked the unused dashboard-generated `mem0` API key after dedicated Codex and Claude keys were confirmed.

Files/artifacts touched:
- `tools/mem0-codex-mcp/run_mem0_mcp.ps1`
- `%USERPROFILE%\.claude\.sandbox-secrets\mem0-claude-mcp.env` outside repo
- Claude user config `C:\Users\Gaming PC\.claude.json`
- Remote Mem0 API keys table

Verification:
- Direct MCP stdio smoke test with the Claude secret path initialized `mem0-codex-mcp`, listed `memory_search` and `memory_save`, and returned 2 Mem0 search results.
- `claude mcp get mem0` reports `Status: Connected` and user scope.
- `codex mcp list` still shows `mem0` enabled.
- Active Mem0 API keys are now only `Claude MCP local bridge` and `Codex MCP local bridge`.
- `python -m pytest tools\mem0-codex-mcp\test_mem0_mcp_server.py -q` passed: 3 passed.

Next step:
- Start fresh Codex and Claude sessions to confirm the clients expose `memory_search` and `memory_save` in-session.

## 2026-05-07 - Hosted Mem0 MCP Server

Summary:
- Added and deployed a hosted read-only MCP server for the self-hosted Mem0 memory database.
- Exposed the service through Pangolin at `https://memory-mcp.ionutrosu.xyz` with a private path token.
- Switched Codex and Claude from local stdio MCP bridges to the hosted HTTP MCP server.
- Revoked the old per-client local bridge Mem0 API keys after the hosted server was verified.
- Wrote a local ChatGPT setup note to the Desktop without printing the private MCP URL in chat.

Files/artifacts touched:
- `tools/mem0-remote-mcp/core.py`
- `tools/mem0-remote-mcp/server.py`
- `tools/mem0-remote-mcp/test_core.py`
- `tools/mem0-remote-mcp/Dockerfile`
- `tools/mem0-remote-mcp/docker-compose.yaml`
- `tools/mem0-remote-mcp/requirements.txt`
- `tools/mem0-remote-mcp/README.md`
- Remote `/opt/mem0-remote-mcp`
- Remote Pangolin resource `memory-mcp`
- `C:\Users\Gaming PC\Desktop\Mem0-ChatGPT-MCP-Setup.txt`

Verification:
- `python -m pytest tools\mem0-remote-mcp\test_core.py -q` passed: 4 passed.
- Remote health check `http://127.0.0.1:18890/health` returned `200`.
- Public health check `https://memory-mcp.ionutrosu.xyz/health` returned `200`.
- MCP protocol smoke test over HTTPS listed `search,fetch` and returned 2 search results.
- `codex mcp list` shows hosted `mem0` URL enabled.
- `claude mcp get mem0` reports hosted HTTP MCP status connected.
- Active Mem0 client API keys now include only `Hosted Memory MCP bridge`.

Decisions:
- Hosted MCP is read-only for now: `search` and `fetch` only.
- ChatGPT gets the hosted MCP URL from a local Desktop setup file rather than from chat output.
- Backups should use direct `pg_dump` first; n8n can orchestrate/notify later but should not be the only backup authority.

Next step:
- Connect ChatGPT web custom app/developer mode to the private MCP URL and test memory retrieval. Then add a scheduled `pg_dump` backup job after explicit approval of retention and destination.

## 2026-05-07 - Mem0 Hosted MCP Profiles and Backup Hardening

Summary:
- Split hosted Mem0 MCP into profile-specific private URLs: ChatGPT read-only, Codex writer, Claude writer.
- Added hosted `save` tool for Codex/Claude with `infer=false` and client metadata; no update/delete tools exposed.
- Adjusted ChatGPT read-only `search`/`fetch` responses toward OpenAI MCP connector expectations.
- Confirmed existing daily server backup to Google Drive and added explicit Mem0 Postgres `pg_dumpall` to `/root/backup.sh`.

Files touched:
- `tools/mem0-remote-mcp/core.py`
- `tools/mem0-remote-mcp/server.py`
- `tools/mem0-remote-mcp/test_core.py`
- `tools/mem0-remote-mcp/test_server_profiles.py`
- `tools/mem0-remote-mcp/README.md`
- Remote: `/opt/mem0-remote-mcp/.env`
- Remote: `/root/backup.sh`

Verification:
- `python -m pytest tools\mem0-remote-mcp -q` -> 7 passed.
- Hosted health check returned HTTP 200.
- Remote MCP tool lists: ChatGPT `search,fetch`; Codex/Claude `search,fetch,save`.
- Codex writer smoke saved a temporary memory and deleted it afterward.
- Backup script syntax check passed; Mem0 dump smoke produced a valid compressed dump and temporary smoke file was removed.

Decisions:
- Use separate Mem0 API keys per hosted MCP profile for audit and revocation.
- Keep ChatGPT read-only first; writer access is only for Codex/Claude.
- Keep backup orchestration in the existing server backup script rather than n8n; n8n can be added later for notifications only.

Next step:
- Retry ChatGPT connector creation with the read-only URL from the Desktop setup file. If ChatGPT still rejects it, inspect whether ChatGPT custom apps require OAuth or additional app metadata for this account/UI path.

## 2026-05-08 - Mem0 Organic Mutation Tools

- Added exact-ID `update` and `delete` behavior to hosted Mem0 MCP writer profiles.
- Made ChatGPT writer-capable by default, with `MCP_CHATGPT_CAN_WRITE=false` as the opt-out.
- Updated `CLAUDE.md`, `AGENTS.md`, `GENERAL_AI_RULES.md`, and hosted MCP README with memory mutation protocol.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 15 passed. Hosted MCP deployed and live temporary-memory mutation smoke passed.
- Note: Mem0 update responses are not full memory objects, so the MCP fetches the memory after `PUT` before returning `after`.
- Next step: reconnect/reload clients if a connector UI caches the old tool list.

## 2026-05-08 - Mem0 Memory Tier Metadata

- Added `memory_tier` metadata support to hosted Mem0 MCP: `active`, `historical`, `cold`.
- `save` and `update` can set tiers; `search` can filter by tiers; `fetch`/`search` expose tier metadata.
- Updated Mem0 MCP README and local `CLAUDE.md`/`AGENTS.md` with tier policy.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 17 passed.
- Next step: deploy hosted MCP and live smoke-test a temporary tiered memory.

## 2026-05-08 - Mem0 Extractor V1 Scaffold

- Added `tools/mem0-extractor`, an external dry-run-first extraction layer for Mem0 memory proposals.
- Implemented deterministic Human Memory V2 grading, memory tiers, schema normalization, OpenAI-compatible LLM calls, JSONL response caching, dedupe action planning, and a session dry-run CLI.
- Live Mem0 mutation is intentionally disabled in the v1 CLI; it writes local proposal reports only.
- Verification: `python -m pytest tools\mem0-extractor -q` -> 15 passed.
- Next step: run a small real session dry-run, inspect proposal quality, then build the explicit apply/review command once the proposals are trusted.

## 2026-05-08 - Mem0 Extractor Apply Planner

- Added `tools/mem0-extractor/apply_report.py` for guarded proposal application planning.
- The command reads extractor reports, normalizes proposals, can dedupe through Mem0 search, writes a local apply plan, and only mutates Mem0 with explicit `--execute`.
- Offline mode supports no-credential review plans.
- Broad delete support remains intentionally out of scope.
- Verification: `python -m pytest tools\mem0-extractor\test_apply_report.py -q` -> 6 passed.
- Next step: run a real small LLM dry-run, inspect proposals, then run `apply_report.py` without `--execute` against live Mem0 to validate dedupe behavior.

## 2026-05-08 - Mem0 Extractor Small Real Dry Run

- Ran `extract_session.py` on a 40-row slice from the latest OB1 extracted-memory snapshot, using OpenRouter Qwen via `.local\model-bakeoff\.env`.
- Output report: `.local\mem0-extractor\real-small-report-20260508-030612.json`.
- Counts: 40 input rows, 12 deterministic candidates, 11 LLM proposals, 0 live writes.
- Ran `apply_report.py --offline`; output plan: `.local\mem0-extractor\real-small-apply-plan-20260508-030612.json`.
- Plan counts: 10 save, 1 skip, 0 update, 0 failed.
- Metadata observation: all proposals came back as `memory_type=pattern`; next prompt/schema pass should push stronger type diversity before broad use.
- Live Mem0 dedupe was not run because `MEM0_API_KEY` is not loaded in the local shell.

## 2026-05-08 - Mem0 Extractor Type Tightening

- Tightened extractor typing so `pattern` is a last-resort memory type.
- Added a type decision tree to the LLM prompt, candidate `suggested_memory_type`, deterministic parser repair for lazy `pattern` outputs, and report-level metadata quality scoring.
- Added an apply guard: reports with failed metadata quality cannot be executed unless `--allow-quality-failed` is explicitly passed.
- Re-ran the same 40-row real OB1 slice through OpenRouter Qwen. Previous run: 11/11 `pattern`. Tightened run: 12 proposals, 2 `pattern`, 9 `workflow`, 1 `decision`; metadata quality passed with pattern ratio 0.1667.
- Offline apply plan for the tightened report: 10 save, 2 skip, 0 update, 0 failed.
- Artifacts: `.local\mem0-extractor\real-small-report-tightened-20260508-040207.json` and `.local\mem0-extractor\real-small-apply-plan-tightened-20260508-040207.json`.

## 2026-05-08 - Perplexity Mem0 MCP Profile

- Added a separate hosted MCP profile for Perplexity in `tools/mem0-remote-mcp`.
- The Perplexity profile uses `MCP_PERPLEXITY_TOKEN`, is read-only by default, and exposes only `search`/`fetch` unless `MCP_PERPLEXITY_CAN_WRITE=true` is explicitly set.
- Deployed to VPS `/opt/mem0-remote-mcp` and regenerated a clean Perplexity token after repairing malformed env entries.
- Wrote the private setup URL to `C:\Users\Gaming PC\Desktop\Perplexity-Mem0-MCP-Setup.txt` without printing it in chat.
- Verification: public `/health` includes `perplexity`; MCP tools smoke returned `fetch,search`; sanitized search smoke returned 3 results.
- Note: a remote `.env` tail was accidentally printed during troubleshooting and should not be repeated; the Perplexity token was regenerated afterward.

## 2026-05-08 - Mem0 Extractor Signal Calibration And Review Exports

- Tightened deterministic Human Memory V2 grading: emotionally intense identity/relationship memories now score higher; generic finance facts are explicit junk; generic teacher/student vocal lesson material no longer passes just because it mentions a teacher; reusable Vocality method/own-voice signal is preserved.
- Added domain-specific metadata gates to extractor reports. Generic vocal/business/finance sludge, missing domains, low-signal saves, and pattern overuse block `--execute`; under-scored psychology/relationship/family-origin proposals are flagged for review without blocking.
- Added compact Markdown/CSV review exports to `apply_report.py` via `--review-dir`, `--review-prefix`, and `--review-preview-chars`.
- Updated `tools/mem0-extractor/README.md` and `tool_registry.md` for Mem0 Extractor v0.2.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 60 passed. Bounded deterministic dry run on the existing 40-row sample wrote `.local\mem0-extractor\real-small-report-v02-deterministic-12-20260508.json`; offline apply wrote `.local\mem0-extractor\real-small-apply-plan-v02-deterministic-12-20260508.json` and compact review files under `.local\mem0-extractor\reviews\`.

## 2026-05-08 - Mem0 Extractor 200-Row Qwen 3.6 Flash Dry Run

- Created a 200-row input slice from `.local\open-brain-cleanup\snapshots\thoughts-20260503-102322.jsonl`.
- Initial single 200-candidate run with `qwen/qwen3-235b-a22b-2507` timed out with no report/cache output; a 50-row batch completed but failed quality due to `pattern_overuse`; another 50-row batch failed on incomplete OpenRouter response.
- Switched to `qwen/qwen3.6-flash` after checking OpenRouter's public model list. Processed the full 200-row sample as eight 25-row dry-run slices.
- Combined report: `.local\mem0-extractor\ob1-200-combined-report-v02-qwen36flash-20260508.json`.
- Offline apply plan: `.local\mem0-extractor\ob1-200-combined-apply-plan-v02-qwen36flash-20260508.json`.
- Review exports: `.local\mem0-extractor\reviews\ob1-200-v02-qwen36flash.md` and `.local\mem0-extractor\reviews\ob1-200-v02-qwen36flash.csv`.
- Results: 200 input rows, 199 candidates, 1 deterministic drop, 54 proposals, metadata quality passed, pattern ratio 0.0741, offline plan counts 50 save / 4 skip / 0 failed.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 60 passed.
- Next step: inspect the compact review file, then run a live Mem0 dedupe dry-run without `--execute` before any writes.

## 2026-05-08 - Mem0 Extractor Automated Audit

- Added `tools/mem0-extractor/audit_report.py` for automated QA of extractor reports before apply. It checks duplicate proposal content, junk leakage, missing/unknown source IDs, missing domains, low-signal saves, suspicious proposal rates, weak pattern typing, high-signal cold tiers, and protected-domain low scoring.
- Added `tools/mem0-extractor/report_repair.py` to repair model-shortened UUID source IDs and remove invalid hallucinated source IDs before apply.
- Ran audit on the 200-row Qwen 3.6 Flash report. Initial audit found provenance issues: 17 shortened UUID prefixes and 1 hallucinated combined UUID. Repair resolved 17 prefixes and removed 1 invalid source ID without changing any proposal action.
- Repaired report audit passed: 54 proposals, 0 hard flags, 4 soft `weak_pattern_type` flags, quality score 76.
- Rebuilt offline apply plan/review from the repaired report: 50 save / 4 skip / 0 failed.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 68 passed.
- Next step: either retype the 4 weak `pattern` soft flags with a small targeted LLM pass, or proceed to live Mem0 dedupe dry-run once `MEM0_API_KEY` is available locally.

## 2026-05-08 - Mem0 Extractor Pilot Audit Cleanup

- Investigated the 4 `weak_pattern_type` soft flags from the repaired 200-row Qwen 3.6 Flash pilot.
- Found all 4 flagged rows were already `action=skip`, low-signal/cold rejected proposals. Retagging them would be pointless because they are not candidates for Mem0 writes.
- Updated `audit_report.py` so `skip` and `ask_user` rows do not create memory-quality flags. They still remain visible in distributions and apply plans.
- Re-ran audit on the repaired 200-row report: 54 proposals, 0 hard flags, 0 soft flags, quality score 100.
- Rebuilt offline apply plan/review exports from the repaired report: `.local\mem0-extractor\ob1-200-combined-apply-plan-v02-qwen36flash-repaired-v2-20260508.json` and `.local\mem0-extractor\reviews\ob1-200-v02-qwen36flash-repaired-v2.md`/`.csv`.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 69 passed.
- Live Mem0 dedupe is still blocked locally because no `MEM0_API_KEY` was found in the checked env/config files.

## 2026-05-08 - Local Mem0 Extractor API Key And Live Dedupe Dry Run

- Created a dedicated Mem0 API key on the self-hosted Mem0 service for local extractor use, labeled `mem0-extractor-local-20260508`.
- Stored it locally at `.local\mem0-extractor\mem0.env`; `.local/` is gitignored. The key value was not printed in logs/chat.
- Removed the temporary remote transfer file after copying the env file locally.
- Ran live Mem0 dedupe dry-run against the repaired 200-row Qwen 3.6 Flash report without `--execute`.
- Output plan: `.local\mem0-extractor\ob1-200-live-dedupe-plan-v02-qwen36flash-repaired-20260508.json`.
- Review exports: `.local\mem0-extractor\reviews\ob1-200-live-dedupe-v02-qwen36flash-repaired.md` and `.csv`.
- Dedupe result: 54 plans, 50 save / 4 skip / 0 update / 0 ask_user / 0 failed. All 50 save actions were dry-run only with reason `new_memory`; no live writes were made.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 69 passed.
- Next step: explicitly approve `--execute` for the 50-memory pilot, or run a stricter dedupe/model-review pass first.

## 2026-05-08 - Mem0 Extractor 50-Memory Pilot Executed

- Executed the repaired/audited 200-row Qwen 3.6 Flash pilot against live Mem0 using `.local\mem0-extractor\mem0.env`.
- Command used `--execute` with report `.local\mem0-extractor\ob1-200-combined-report-v02-qwen36flash-repaired-20260508.json`.
- Output: `.local\mem0-extractor\ob1-200-live-executed-v02-qwen36flash-repaired-20260508.json`.
- Review exports: `.local\mem0-extractor\reviews\ob1-200-live-executed-v02-qwen36flash-repaired.md` and `.csv`.
- Result: 54 plans, 50 save / 4 skip / 0 update / 0 ask_user / 0 failed. Verification script counted 50 executed results, 50 saved IDs, 50 unique saved IDs.
- Search smoke against live Mem0 returned results and at least one `source=mem0-extractor` hit without printing memory contents.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 69 passed.
- Next step: build batch orchestration/resume log, then scale from 200-row pilot to larger chunks with audit/repair/dedupe/execute gates.

## 2026-05-08 - Mem0 Extractor Batch Orchestrator

- Added `tools/mem0-extractor/batch_orchestrator.py`, a sequential resume-safe runner for larger OB1-to-Mem0 cleanup/import passes.
- The orchestrator chunks an input file, writes per-batch input/report/repaired-report/audit/apply/review files, records a manifest, skips completed batches on rerun, and requires explicit `--execute` for live writes.
- Pipeline per batch: `extract_session.build_report` -> `report_repair.repair_source_ids` -> `audit_report.audit_report` -> `apply_report` dedupe/apply planning -> review exports.
- Added `tools/mem0-extractor/test_batch_orchestrator.py` covering stable paths, JSONL batch writing, manifest updates, and resume behavior.
- Smoke run: deterministic offline orchestrator on the 200-row pilot input processed batches 1-2, then a second run skipped 1-2 and processed 3-4 using the same manifest.
- Updated README with the resume-safe batch command shape.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 74 passed.
- Next step: run the orchestrator on the main OB1 snapshot in small live-dedupe dry-run batches, then execute trusted batches after audit.

## 2026-05-08 - Mem0 Extractor Main Snapshot First Batch Wave

- Ran the resume-safe orchestrator on the main OB1 snapshot `.local\open-brain-cleanup\snapshots\thoughts-20260503-102322.jsonl` with `qwen/qwen3.6-flash`, batch size 25, and `--stop-after-batches 4`.
- Dry-run manifest: `.local\mem0-extractor\batch-runs\ob1-main-qwen36\ob1-main-qwen36-manifest.json`.
- Dry-run result: 100 rows, 100 candidates, 25 proposals, 0 hard audit flags, 2 soft audit flags (`low_proposal_rate`, `high_proposal_rate`), 24 planned saves, 1 ask-user hold, 0 failures.
- Executed the four repaired batch reports directly with `apply_report.py --execute` to avoid re-spending model tokens.
- Live execution outputs: `.local\mem0-extractor\batch-runs\ob1-main-qwen36\ob1-main-qwen36-batch-001-apply-executed.json` through `batch-004-apply-executed.json`, with review exports under the same run's `reviews\` directory.
- Live result: 24 saves, 1 ask-user hold, 0 updates, 0 skips, 0 failures.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 74 passed.
- Next step: continue with another controlled wave, preferably 4-8 new batches, then inspect aggregate audit/dedupe counts before executing.

## 2026-05-08 - Mem0 Extractor Main Snapshot Batches 13-28

- Continued the main OB1 snapshot rollout from batch 13 using `qwen/qwen3.6-flash`, batch size 25, under `.local\mem0-extractor\batch-runs\ob1-main-qwen36\`.
- Batch 14 initially failed audit on `generic_vocal_lesson_sludge`; one junk proposal was auto-demoted to `skip`, the curated report passed audit, and batch 14 executed from the curated report.
- A transient DNS error interrupted dry-run at batch 21; DNS recovered and the retry completed batches 21-28.
- Batches 13-28 are now dry-run completed and live executed. Batches 1-28 are executed overall.
- Total executed result across batches 1-28: 323 saves, 1 skip, 2 ask-user holds, 0 updates, 0 failures.
- Next batch: 29 of 800.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 74 passed.

## 2026-05-08 - Mem0 Extractor Main Snapshot Batches 29-60

- Scaled main OB1 snapshot rollout to a 32-batch wave from batch 29 through 60 using `qwen/qwen3.6-flash`, batch size 25, under `.local\mem0-extractor\batch-runs\ob1-main-qwen36\`.
- Dry-run/audit result for batches 29-60: 800 input rows, 721 candidates, 365 proposals, 0 hard audit flags after curation, 32 soft flags, mean audit score 94.0.
- Batch 48 initially failed on `transcript_process_junk`; one transcript-processing artifact was auto-demoted to `skip`, then the curated report passed audit and dry-run planning.
- Batch 44 had metadata `pattern_overuse`, but all 24 proposals were already `skip`; executed batch 44 with `--allow-quality-failed` because it wrote zero memories.
- Live execution result for batches 29-60: 331 saves, 25 skips, 9 ask-user holds, 0 updates, 0 failures.
- Total executed result across batches 1-60: 654 saves, 26 skips, 11 ask-user holds, 0 updates, 0 failures.
- Next batch: 61 of 800.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 74 passed.

## 2026-05-08 - Mem0 Extractor Main Snapshot Batches 61-92

- Continued the main OB1 snapshot rollout with a 32-batch wave from batch 61 through 92 using `qwen/qwen3.6-flash`, batch size 25, under `.local\mem0-extractor\batch-runs\ob1-main-qwen36\`.
- Dry-run/audit result for batches 61-92: 800 input rows, 654 candidates, 200 proposals, 0 hard audit flags after curation, 16 soft flags, mean audit score 97.0.
- Batch 64 and batch 80 each had one `generic_vocal_lesson_sludge` proposal auto-demoted to `skip`; both curated reports passed audit and dry-run planning.
- Batch 81 execution had one transient `URLError` after 7 saves; isolated the failed first proposal, live-deduped it, executed it successfully, and reconciled the batch report to 8 saves / 0 failures.
- Live execution result for batches 61-92: 188 saves, 11 skips, 1 ask-user hold, 0 updates, 0 failures.
- Total executed result across batches 1-92: 842 saves, 37 skips, 12 ask-user holds, 0 updates, 0 failures.
- Next batch: 93 of 800.
- Verification: `python -m pytest tools\mem0-extractor tools\mem0-remote-mcp -q` -> 74 passed.

## 2026-05-08 - Hosted Mem0 MCP Ingestion Layer Local Build

- Implemented the local hosted MCP ingestion layer in `tools\mem0-remote-mcp`: deterministic exchange gating, source hashing, sanitized previews, Qwen/OpenAI-compatible extraction client, JSON proposal parser, append-only audit ledger, metadata extras, dedupe-aware proposal application, and flag-only delete/merge/stale/conflict handling.
- Added writer-profile MCP tools: `extract_memories`, `ingest_exchange`, `submit_memory_override`, and `flag_memory`. Existing explicit exact-ID `delete` remains separate from ingestion; ingestion itself does not hard-delete.
- Updated Docker packaging to include the new runtime modules and persist `/data/mem0-mcp-audit.jsonl` through `./data:/data`.
- Updated hosted MCP README plus `AGENTS.md` and `CLAUDE.md` with the ingestion/hook protocol.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 40 passed.
- Deployment status: not deployed/restarted on VPS yet. Next step is explicit approval for hosted deploy, then a temporary `silver river candle` live smoke test and cleanup.

## 2026-05-08 - Hosted Mem0 MCP Ingestion Layer VPS Deploy

- Deployed the hosted MCP ingestion layer to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp` after creating remote rollback archive `/opt/mem0-remote-mcp-backup-20260508T204055Z.tgz`.
- Preserved remote `.env` and existing data, copied updated runtime files, rebuilt/restarted `memory-mcp`, and added ingestion env keys without printing secret values. `INGESTION_LLM_TIMEOUT` is now `20` seconds.
- Live MCP writer profile exposes `extract_memories`, `ingest_exchange`, `submit_memory_override`, and `flag_memory`.
- Smoke results: `extract_memories` reached the live Qwen/OpenRouter path and returned one `skip` proposal for an explicitly temporary canary; `submit_memory_override` saved a temporary `silver river candle` canary; search found it after indexing delay; exact-ID delete removed it; `ingest_exchange` then returned `already_processed` with `skipped=1` and no errors.
- Hot-path bug found and fixed during smoke: synchronous LLM/Mem0 HTTP work inside async MCP tools could block the server. Patched `extract_memories`, `ingest_exchange`, `submit_memory_override`, and `flag_memory` to run blocking work in worker threads via `anyio.to_thread.run_sync`.
- Security hardening: disabled Uvicorn access logs for the hosted MCP process so private tokenized MCP paths are not logged on future requests, then force-recreated the container.
- Verification: remote `/health` returns `ok=true` with profiles `chatgpt`, `codex`, `claude`, `perplexity`; remote container is running; local `python -m pytest tools\mem0-remote-mcp -q` -> 40 passed; local `py_compile` passed for all hosted MCP runtime modules.
- Next step: monitor real client behavior from ChatGPT/Codex/Claude, then build the dream maintenance pass over audit flags.

## 2026-05-09 - Mem0 V1.3 Monitoring Local Build

- Implemented local monitoring support for the hosted Mem0 MCP ingestion layer.
- Added `tools/mem0-remote-mcp/monitoring.py` for safe aggregate audit stats: action counts, origin counts, flag counts, error classes, last success per origin, last error, and token usage totals when provider usage is present.
- Added `tools/mem0-remote-mcp/ingestion_status_cli.py` for local JSON/human status reports without raw previews, full memory text, secrets, API keys, or profile tokens.
- Added `tools/mem0-remote-mcp/server_status.py` for non-secret health metadata and audit-log writability checks.
- Updated `ingestion_llm.py` with backward-compatible `call_llm_with_metadata()` so model/usage metadata can be recorded when providers return it.
- Updated `core.py` and `server.py` so ingestion audit rows can include sanitized LLM model/token usage and writer/read-only MCP profiles expose `ingestion_status`.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 51 passed. `python -m py_compile` on runtime modules passed.
- Deployment status: local only; VPS deploy still requires explicit approval.
- Next step: deploy V1.3 to VPS, then verify `/health` and `ingestion_status` remotely without printing profile URLs/tokens.

## 2026-05-09 - Mem0 V1.3 Monitoring VPS Deploy

- Deployed V1.3 monitoring to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp`.
- Created remote rollback archive `/opt/mem0-remote-mcp-backup-20260508T220825Z.tgz` during the successful corrected deploy.
- Fixed Docker packaging during deploy by adding `monitoring.py` and `server_status.py` to the image copy list; the first rebuild exposed the missing module before health came up.
- Remote `/health` now returns `ok=true`, profiles `chatgpt`, `codex`, `claude`, `perplexity`, ingestion enabled, and audit writable.
- Remote MCP smoke through a private profile token listed 10 tools and confirmed `ingestion_status`; calling `ingestion_status` returned `status_ok=true`, `ingestion_enabled=true`, `error_count=0`, `pending_flags=0`, `usage_status=usage_unavailable`.
- Verification: local `python -m pytest tools\mem0-remote-mcp -q` -> 51 passed before deploy; public `https://memory-mcp.ionutrosu.xyz/health` verified after deploy.
- Next step: V1.1 client compliance checks to prove real ChatGPT/Codex/Claude sessions call `ingest_exchange`, or V1.2 dream maintenance if cleanup flags become the priority.

## 2026-05-09 - Mem0 V1.1 Client Compliance Local Build

- Implemented local client-compliance support for hosted Mem0 MCP.
- Added `AuditLedger.iter_recent()` and `AuditLedger.latest_by_origin()` for safe recent audit reads; malformed legacy rows are skipped and UTF-8 BOM JSONL rows are accepted.
- Added `tools/mem0-remote-mcp/client_compliance_probe.py`, a safe CLI/report builder that classifies expected origins as `writing`, `seen_no_write`, `error`, or `missing` without printing raw previews, memory contents, profile URLs, API keys, or tokens.
- Tightened `AGENTS.md`, `CLAUDE.md`, and hosted MCP README ingestion protocol: call `ingestion_status` at session start/reconnect, call `ingest_exchange` after substantive exchanges and compact summaries, ingest only material tool outcomes, and keep no-raw-transcript/no-secret rules explicit.
- Added `docs/mem0-client-compliance-checklist.md` with smoke tests for ChatGPT, local Codex, local Claude, VPS Codex, VPS Claude, and Perplexity.
- During local probe smoke, PowerShell-created JSONL exposed a UTF-8 BOM edge case; fixed audit parsing and added regression coverage.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 59 passed. `python -m py_compile tools\mem0-remote-mcp\audit_ledger.py tools\mem0-remote-mcp\client_compliance_probe.py tools\mem0-remote-mcp\server.py` passed. Local sample compliance probe correctly reported codex=writing, claude=seen_no_write, chatgpt=missing.
- Deployment status: local only. Next step is deploy/sync V1.1 to VPS and run real-client smoke tests.

## 2026-05-09 - Mem0 V1.2 Dream Maintenance Local Build

- Implemented local dry-run-first dream maintenance for hosted Mem0 MCP.
- Added `tools/mem0-remote-mcp/dream_maintenance.py` with flag parsing, grouping, deterministic dry-run planning, compact LLM pack generation, LLM action validation, and exact-ID guarded execution helpers.
- Added `tools/mem0-remote-mcp/dream_maintenance_cli.py` with `--audit-log`, `--output`, `--limit-groups`, `--execute`, `--max-deletes`, rollback log, and maintenance log options. Default mode is dry-run and writes a reviewable plan JSON.
- Planner behavior: high-confidence exact-ID `delete_candidate` -> delete action; weak delete -> keep; `stale_candidate` -> downgrade to historical/unknown; exact multi-ID `merge_candidate` -> merge; conflict -> ask_user.
- Live execution requires exact IDs, writes rollback rows before update/delete, writes results to a separate maintenance ledger, and refuses delete execution without `--max-deletes`.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 71 passed. `python -m py_compile tools\mem0-remote-mcp\dream_maintenance.py tools\mem0-remote-mcp\dream_maintenance_cli.py tools\mem0-remote-mcp\server.py` passed.
- Sample dry-run on `.local\sample-dream-audit.jsonl` wrote `.local\sample-dream-plan.json` with 2 actions: delete `mem-delete` and downgrade `mem-stale` to historical/unknown; no live execution was run.
- Deployment status: local only. Next step is decide whether to deploy/sync V1.1+V1.2 to VPS or continue with V1.4 retrieval quality locally.

## 2026-05-09 - Mem0 V1.4 Retrieval Quality Local Build

- Implemented local fused retrieval ranking for hosted Mem0 MCP in `tools/mem0-remote-mcp/retrieval.py`.
- Ranking now combines Mem0 semantic score, lexical overlap, entity/project/person hints, domain metadata, memory tier, signal strength, and current status.
- Updated `Mem0Client.search()` to fetch a larger candidate set before filtering, cap public `top_k` at 20, apply optional `domains`, `memory_tiers`, `memory_types`, and `current_statuses` filters, then return fused-ranked results.
- Updated the hosted MCP `search` tool signature to expose the new optional filters while preserving the existing `query`, `top_k`, and `memory_tiers` arguments.
- Added retrieval quality tests for cold-tier behavior, historical psychology/relationship retention, entity boosting, and domain-specific fixtures covering business/workflow, psychology/relationships, opera/vocality, and AI systems.
- Updated Docker packaging to include `retrieval.py` and documented retrieval behavior in the hosted MCP README.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 83 passed. `python -m py_compile tools\mem0-remote-mcp\audit_ledger.py tools\mem0-remote-mcp\core.py tools\mem0-remote-mcp\ingestion.py tools\mem0-remote-mcp\ingestion_llm.py tools\mem0-remote-mcp\monitoring.py tools\mem0-remote-mcp\retrieval.py tools\mem0-remote-mcp\server_status.py tools\mem0-remote-mcp\server.py` passed.
- Deployment status: local only. Next step is deploy V1.1, V1.2, and V1.4 together after explicit approval, then run safe remote search smokes without printing profile tokens or private memory contents.

## 2026-05-09 - Mem0 V1.1/V1.2/V1.4 VPS Deploy

- Deployed the hosted Mem0 MCP local hardening stack to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp` after user approval.
- Created valid rollback archive `/opt/mem0-remote-mcp-backup-20260508T233948Z.tgz`; a malformed extra archive `/opt/mem0-remote-mcp-backup-.tgz` also exists from an initial PowerShell quoting mistake and was left untouched.
- Copied current MCP files while preserving remote `.env` and `data/`.
- Rebuilt/restarted the `memory-mcp` Docker service. Docker packaging now includes runtime modules plus `client_compliance_probe.py`, `dream_maintenance.py`, `dream_maintenance_cli.py`, and `ingestion_status_cli.py` so V1.1/V1.2 tooling is available inside the container.
- Remote `/health` returned `ok=true`, profiles `chatgpt`, `codex`, `claude`, `perplexity`, ingestion enabled, and audit writable.
- Remote container `py_compile` passed for all packaged MCP modules.
- Remote MCP smoke through an internal profile token confirmed 10 tools, `ingestion_status`, new `search` filters (`domains`, `memory_types`, `current_statuses`), `ok=true`, `error_count=0`, `pending_flags=0`, and 3 filtered search results without printing memory contents.
- Remote dream-maintenance dry-run smoke over the live audit ledger produced 0 actions, as expected with no pending flags.
- Remote compliance probe against the live audit ledger reported `codex=writing` for the checked origin.
- Deployment status: live on VPS. Next step is real-client compliance checks for ChatGPT, Claude, Codex, and Perplexity after clients reconnect to the updated MCP tool schema.


## 2026-05-09 - Mem0 V1.5 Retrieval Telemetry and Eval Harness Local Build

- Implemented local V1.5 retrieval observability for the hosted Mem0 MCP.
- Added `tools/mem0-remote-mcp/retrieval_telemetry.py` for safe JSONL search telemetry: origin, query hash, short query preview, filters, result count, returned IDs/titles, and latency. It intentionally excludes full memory text, raw chats, API keys, and profile tokens.
- Wired `Mem0Client.search()` to record telemetry when `retrieval_telemetry_log` is configured; internal ingestion dedupe searches opt out to avoid noisy audit bloat.
- Extended `ingestion_status` through `monitoring.build_status_payload()` so it can include safe aggregate retrieval stats from the telemetry log.
- Added deterministic query expansion in `retrieval.py` for implicit intent phrases like stuck/blocked, relationship conflict, family origin, finance/funding, vocality/opera, and VPS/MCP infrastructure queries.
- Added `tools/mem0-remote-mcp/retrieval_eval.py` plus `retrieval_eval_cases.sample.json` for fixed retrieval regression checks using expected IDs/domains/memory types and MRR-style scoring.
- Updated Docker packaging and hosted MCP README for V1.5 telemetry/eval environment knobs.
- Verification: targeted V1.5 tests initially failed on missing modules/functions, then passed. Full local `python -m pytest tools\mem0-remote-mcp -q` -> 91 passed. Runtime `py_compile` passed for all hosted MCP modules. Sample eval fixture load check returned 4 cases.
- Deployment status: local only. Next step is deploy V1.5 to VPS after explicit approval, then verify `/health`, `ingestion_status` retrieval stats, and one safe search telemetry row without printing profile tokens or private memory contents.

## 2026-05-09 - Mem0 V1.5 Retrieval Telemetry and Eval Harness VPS Deploy

- Deployed Mem0 V1.5 to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp` after user approval.
- Created rollback archive `/opt/mem0-remote-mcp-backup-20260509T204440Z.tgz`; a failed partial archive from the first tar attempt was removed. Existing older rollback archives were left untouched.
- Copied V1.5 service files through `/tmp/mem0-remote-mcp-v15`, preserving remote `.env` and `data/`, then rebuilt/restarted the `memory-mcp` Docker service.
- Remote `/health` returned `ok=true` with profiles `chatgpt`, `codex`, `claude`, and `perplexity`, ingestion enabled, and audit writable.
- Remote container `py_compile` passed for all packaged MCP modules.
- Safe retrieval telemetry smoke wrote one deploy-smoke search event and reported retrieval stats through `ingestion_status` without printing profile tokens or memory contents.
- MCP smoke through an internal profile token confirmed 10 tools, `search`, `ingestion_status`, and search schema fields `query`, `top_k`, `memory_tiers`, `domains`, `memory_types`, and `current_statuses`.
- Deployment status: live on VPS. Next step is monitor real-client telemetry/compliance after ChatGPT, Codex, Claude, and Perplexity reconnect.

## 2026-05-10 - Mem0 V1.6 Retrieval-Epoch Lifecycle Decay Local Build

- Implemented local V1.6 lifecycle decay for the hosted Mem0 MCP in `tools/mem0-remote-mcp/lifecycle.py`.
- Decay is based on retrieval epochs, not wall-clock time: one retrieval telemetry search event equals one epoch. If the memory system is unused, memories do not decay just because calendar time passed.
- Added `RetrievalAccessIndex` over `mem0-mcp-retrieval.jsonl`, tracking current retrieval epoch, per-memory access count, last seen epoch, and epochs since last access.
- Added dry-run lifecycle planning for `decay_low_use`: low-signal active memories can downgrade to historical only after enough retrieval epochs without being returned; historical memories can downgrade to cold after a larger epoch threshold; frequently retrieved historical/cold memories can promote upward.
- Protected psychology, relationships, family-origin, identity, opera, systems, major project, dossier, high-signal, and formative memories from automatic cold downgrade.
- Added rollback-guarded execution for lifecycle updates with mandatory `max_updates`, rollback JSONL, and maintenance JSONL.
- Added `tools/mem0-remote-mcp/lifecycle_cli.py` for scheduler-friendly dry-run/execute operation over a memory JSON export and retrieval telemetry log. Default is dry-run; execution requires `--execute --max-updates`.
- Updated Docker packaging and README lifecycle docs.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 99 passed. Runtime `py_compile` passed for all hosted MCP modules. Targeted lifecycle tests first failed on missing module/CLI, then passed after implementation.
- Deployment status: local only. Next step is deploy V1.6 to VPS after explicit approval, then add a safe memory-export source so the scheduled dry-run can evaluate the live memory set automatically.

## 2026-05-10 - Mem0 V1.6 Retrieval-Epoch Lifecycle Decay VPS Deploy

- Deployed Mem0 V1.6 lifecycle decay to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp` after user approval.
- Created rollback archive `/opt/mem0-remote-mcp-backup-20260509T214558Z.tgz` before copying files.
- Copied V1.6 service files through `/tmp/mem0-remote-mcp-v16`, preserved remote `.env` and `data/`, rebuilt/restarted the `memory-mcp` Docker service, then removed the staging/smoke temp directories.
- Remote `/health` returned `ok=true` with profiles `chatgpt`, `codex`, `claude`, and `perplexity`, ingestion enabled, and audit writable.
- Remote Docker service `memory-mcp` is running.
- Remote container `py_compile` passed for all packaged MCP modules, including `lifecycle.py` and `lifecycle_cli.py`.
- Remote lifecycle CLI smoke used a synthetic memory export and retrieval telemetry fixture. Dry run produced summary `{"downgrade_tier": 1, "keep": 1}` and the saved plan check confirmed no synthetic raw memory text was present in the plan output.
- Deployment status: live on VPS. Next step is add a safe live memory-export source or snapshot job so scheduled lifecycle dry-runs can evaluate the actual Mem0 memory set automatically.

## 2026-05-10 - Mem0 V1.7 Live Snapshot Source VPS Deploy

- Implemented V1.7 live memory snapshots for hosted Mem0 MCP.
- Added `tools/mem0-remote-mcp/memory_snapshot.py` and `memory_snapshot_cli.py` to export a safe lifecycle snapshot from the live Mem0 store. Default snapshots include memory IDs and metadata only; full memory text is excluded unless `--include-text` is explicitly set for a private job.
- Extended `lifecycle_cli.py` with `--live-snapshot`, `--snapshot-output`, `--snapshot-page-size`, `--snapshot-max-pages`, and `--include-snapshot-text`, so lifecycle dry-runs can create a fresh live snapshot before planning.
- Fixed `Mem0Client.get_all()` for the self-hosted backend: when `MEM0_LEXICAL_DATABASE_URL` is configured it reads the full `mem0_memories` table through Postgres; otherwise it falls back to the self-hosted HTTP `GET /memories?user_id=...` endpoint, which may be limited by the backend.
- Remote MCP `.env` was corrected to point `MEM0_LEXICAL_DATABASE_URL` at the Mem0 Postgres container address instead of host `127.0.0.1:5432`, which belonged to a different Postgres service. No DB password was printed.
- Created rollback archive `/opt/mem0-remote-mcp-backup-20260509T224041Z.tgz` before deploying the V1.7 fix.
- Copied V1.7 service files through `/tmp/mem0-remote-mcp-v17-fix`, preserved remote `.env` and `data/`, rebuilt/restarted `memory-mcp`, then removed staging and smoke temp files.
- Verification: targeted local `python -m pytest tools\mem0-remote-mcp\test_memory_snapshot.py tools\mem0-remote-mcp\test_lifecycle_cli.py -q` -> 9 passed. Full local `python -m pytest tools\mem0-remote-mcp -q` -> 106 passed. Runtime local `py_compile` passed for packaged modules.
- Remote `/health` returned `ok=true`, profiles `chatgpt`, `codex`, `claude`, and `perplexity`, ingestion enabled, and audit writable. Remote Docker service `memory-mcp` is running. Remote container `py_compile` passed.
- Remote live snapshot smoke exported 3,443 rows with `include_text=false`; a follow-up check confirmed `has_text_key=false` and metadata present.
- Remote lifecycle live dry-run over the snapshot and retrieval telemetry produced summary `{keep: 3443}`. No lifecycle mutations were executed.
- Deployment status: live on VPS. Next step is schedule or manually run periodic live lifecycle dry-runs, then review whether the decay policy is too conservative once enough retrieval telemetry accumulates.

## 2026-05-10 - Mem0 V1.8 Hook Hardening And Stable Network Route

- Stabilized the hosted MCP container networking. `tools/mem0-remote-mcp/docker-compose.yaml` now uses an explicit `127.0.0.1:18890` port bind and joins the external Docker network `mem0-dev_mem0_network` instead of host networking.
- Updated remote MCP `.env` so `MEM0_BASE_URL=http://mem0:8000`, `MEM0_LEXICAL_DATABASE_URL` uses `postgres:5432`, `MCP_HOST=0.0.0.0`, and `MEM0_DOCKER_NETWORK=mem0-dev_mem0_network`. This removes dependence on the transient Mem0 Postgres container IP.
- Created rollback archive `/opt/mem0-remote-mcp-backup-20260509T232731Z.tgz` before the network-route deploy. Remote `docker compose config --quiet` passed and `memory-mcp` restarted with `127.0.0.1:18890->18890/tcp`.
- Added versioned client hook script `tools/mem0-remote-mcp/client_hooks/mem0_context_hook.py` plus `test_client_hooks.py`. The hook injects Mem0 retrieval/ingestion discipline for session start, user prompt, and post-compact contexts without reading secrets or calling the API directly.
- Local Claude: replaced old Kontext memory hooks in `~/.claude/settings.json` with Mem0 hook commands, removed Tokenomy status line metadata, installed `~/.claude/mem0_context_hook.py`, replaced `~/.claude/.mcp.json` with only `mem0`, and registered Mem0 through `claude mcp add --transport http --scope user mem0`. JSON was rewritten UTF-8 without BOM.
- Local Codex: enabled stable `[features].hooks = true`, removed deprecated `codex_hooks`, installed `~/.codex/hooks.json` with Mem0-only `SessionStart` and `UserPromptSubmit` hooks, and copied `~/.codex/mem0_context_hook.py`.
- Added the concise Mem0 retrieval/ingestion protocol to local global `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`.
- Synced Claude/Codex settings, MCP config, hook scripts, and instruction files to VPS host paths `/root/.claude` and `/root/.codex`, and to the active ttyd container paths `/config/.claude` and `/config/.codex`. Existing files were timestamp-backed up first; a stale `/root/.codex/hooks.json` directory was moved aside.
- Verification: host and container JSON configs validate; host/container hook scripts run; host/container Codex config has `hooks = true` and no `codex_hooks`; host/container active hook configs reference `mem0_context_hook` and no active Kontext/Tokenomy hook references.
- Remote MCP profile tool compliance: ChatGPT, Codex, and Claude expose 10 tools including `search`, `fetch`, `ingest_exchange`, and `delete`; Perplexity exposes read-only `search`/`fetch`/status only.
- Remote write compliance smoke: ChatGPT, Codex, and Claude profiles each saved a short compliance canary through `submit_memory_override`; each canary was found and deleted by exact ID. `client_compliance_probe.py` then reported `writing: chatgpt, codex, claude`, `missing: none`, `errors: none`.
- Verification: full local `python -m pytest tools\mem0-remote-mcp -q` -> 110 passed. `py_compile` passed for the hook script and touched runtime modules. Public `/health` returned `ok=true`; remote `memory-mcp` is running.
- Deployment status: live on VPS and synced to local/VPS clients. Next step is observe real Claude/Codex sessions after restart to confirm the hooks produce natural Mem0 search/ingestion behavior, then decide whether to make hook enforcement stronger or keep it advisory.

## 2026-05-10 - Mem0 V1.9 Hook Heartbeat And Compliance Observability VPS Deploy

- Implemented V1.9 observability for the hosted Mem0 MCP. This is measurement/hardening, not new memory intelligence.
- Added `tools/mem0-remote-mcp/hook_heartbeat.py` and a content-free `/hook-heartbeat/{token}` endpoint plus `hook_heartbeat` MCP tool. Heartbeat rows store timestamp, profile origin, hook type, optional source, and optional marker hash only; no prompts, raw chats, memory text, profile URLs, tokens, or API keys.
- Updated `client_hooks/mem0_context_hook.py` so Codex/Claude hooks send best-effort heartbeats derived from the configured Mem0 MCP URL. Heartbeat failures are swallowed. A no-stdin guard was added after a VPS manual smoke exposed that direct POSIX invocations could block waiting on stdin.
- Added `client_compliance_status_cli.py`, which combines hook heartbeat, retrieval telemetry, and ingestion audit logs into per-client statuses: `healthy`, `hook_only`, `search_only`, `write_only`, `partial`, `missing`, or `error`.
- Added `stale_config_guard.py` to fail active config regressions to deprecated `codex_hooks`, active Kontext/Tokenomy MCP servers or hooks, missing Mem0 MCP config, or missing `mem0_context_hook` references.
- Updated Docker packaging and README docs for V1.9 logs and CLI usage.
- Deployed to VPS `/opt/mem0-remote-mcp` and preserved remote `.env` and `data/`. Rollback archives created: `/opt/mem0-remote-mcp-backup-20260510T002126Z.tgz` and final V1.9b `/opt/mem0-remote-mcp-backup-20260510T002619Z.tgz`.
- Synced the hardened hook script to local `~/.codex`/`~/.claude`, VPS `/root/.codex`/`/root/.claude`, and active ttyd container `/config/.codex`/`/config/.claude`.
- Verification: local `python -m pytest tools\mem0-remote-mcp -q` -> 120 passed; local and remote `py_compile` passed for new/touched modules; public `/health` returned `ok=true`; installed VPS Codex/Claude hook scripts completed under `timeout 10s`; stale-config guard passed for local, VPS host, and copied ttyd container configs.
- Live status after deploy: Codex is `healthy` with hooks, searches, and ingestion rows. Claude has heartbeats and ingestion rows but no recent retrieval telemetry in the scanned window, so it reports `partial` until a real Claude session searches Mem0. ChatGPT reports `write_only` because web ChatGPT has no local hook heartbeat. Perplexity reports `missing` because it is read-only and has not been used recently.
- Deployment status: live. Next step is run a real Claude prompt that searches Mem0, then rerun `client_compliance_status_cli.py` and decide whether to add scheduled lifecycle dry-run reporting as V1.10.

## 2026-05-10 - Mem0 V1.10 Token-Aware Hook Tightening And Usage Report VPS Deploy

- Tightened `client_hooks/mem0_context_hook.py` so user-prompt guidance says to search Mem0 only when remembered context could materially change the answer. It now explicitly skips trivial acknowledgements, pure formatting, generic coding syntax, one-off shell questions, and prompts where visible context is enough.
- Kept ingestion guidance limited to durable corrections, decisions, project state, and important personal/work context; raw logs, secrets, and low-signal chatter remain excluded.
- Added `tools/mem0-remote-mcp/usage_report.py` and `usage_report_cli.py` for safe aggregate usage visibility across hook heartbeats, retrieval telemetry, and ingestion audit rows. It reports counts and token totals by origin without raw prompt text, query previews, memory text, profile URLs, tokens, or API keys.
- Updated Docker packaging and synced the tightened hook to local `~/.codex`/`~/.claude`, VPS `/root/.codex`/`/root/.claude`, and active ttyd `/config/.codex`/`/config/.claude`.
- Created rollback archive `/opt/mem0-remote-mcp-backup-20260510T110751Z.tgz` before deploying V1.10 to VPS `/opt/mem0-remote-mcp`.
- Verification: local targeted tests for usage report and hooks passed; full local `python -m pytest tools\mem0-remote-mcp -q` -> 125 passed; py_compile passed for new/touched modules; public `/health` returned `ok=true`; remote hook JSON validates.
- Live usage report after deploy: hooks=207, searches=23, ingestion_rows=23, total_tokens=50,607. Codex accounts for 21 searches, 21 ingestion rows, and 50,607 ingestion tokens; Claude accounts for 185 hook heartbeats but 0 recent searches; ChatGPT has 1 search and 1 ingestion row; deploy-smoke has 1 search. This confirms ingestion is the meaningful token cost, not heartbeat/search telemetry.
- Deployment status: live. Next step is run another usage report after a normal workday to see whether the tighter hook guidance reduces unnecessary searches/ingestion.


## 2026-05-10 - Mem0 V1.11/V1.12 Query Router And Hybrid Retrieval Local Build

- Implemented local retrieval improvements in `tools/mem0-remote-mcp` based on Pinecone-style takeaways without switching backends.
- Added `QueryRoute` and `route_query()` in `retrieval.py` to classify searches as `skip`, `sensitive_deep`, `project`, `entity_exact`, or `normal`.
- Wired `Mem0Client.search()` to use routed candidate depth: trivial searches can short-circuit; sensitive relationship/psychology/family searches fetch a deeper candidate pool; project/infrastructure searches get routed domain boosts; backend candidate requests remain capped at 100 to avoid increasing worst-case load.
- Strengthened hybrid ranking with adjacent phrase overlap, expanded known entity terms, multi-domain coverage boosts, high-signal memory type boosts, low-signal generic note penalties, and junk-memory penalties for transcript artifacts such as speaker diarization/canonical-name sludge.
- Updated retrieval tests for query routing, sensitive candidate depth, and entity/domain ranking; updated README retrieval-quality docs.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 128 passed. Runtime compile: `python -m py_compile tools\mem0-remote-mcp\retrieval.py tools\mem0-remote-mcp\core.py` passed.
- Deployment status: local only. Next step is user-approved VPS deploy with rollback archive, remote health check, remote compile, and a safe live search smoke.

## 2026-05-10 - Mem0 V1.11/V1.12 Query Router And Hybrid Retrieval VPS Deploy

- Deployed local Mem0 V1.11/V1.12 retrieval improvements to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp` after user approval.
- Created rollback archive `/opt/mem0-remote-mcp-backup-20260510T115929Z.tgz` before copying service files. Remote `.env` and `data/` were preserved.
- Rebuilt and restarted only the `memory-mcp` Docker service.
- Verification: public `/health` returned `ok=true` with profiles `chatgpt`, `codex`, `claude`, and `perplexity`; remote `py_compile` passed for `retrieval.py`, `core.py`, and `server.py`; route smoke classified the Mem0 Docker/VPS query as `project` with domains `ai|systems|infrastructure`; live search smoke returned 2 result IDs without printing memory text.
- Deployment status: live on VPS. Next step is observe retrieval telemetry after normal use and decide whether V1.13 should add stricter metadata-first retrieval or a selective rerank layer.

## 2026-05-10 - Mem0 V1.13 Retrieval Quality Pack Local Build

- Implemented local V1.13 retrieval quality pack in `tools/mem0-remote-mcp`.
- Extended `retrieval_eval.py` with `pass_rate`, failed count, threshold checks (`--min-pass-rate`, `--min-cases`), optional JSON output path, and safe default human output that reports counts, MRR, and failed case names without printing raw queries or memory text.
- Added `retrieval_eval_cases.v1.13.json` with 12 domain-based second-brain eval cases covering AI systems, MCP infrastructure, agent preferences, Vocality/business, opera funding, finance/PFA, family-origin psychology, Luiza/relationship patterns, execution psychology, own-voice career context, and memory cleanup policy.
- Packaged the V1.13 eval case file in the Docker image and documented the live container run command in the README.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 130 passed; `python -m py_compile tools\mem0-remote-mcp\retrieval_eval.py` passed; `python tools\mem0-remote-mcp\retrieval_eval.py --help` passed.
- Deployment status: local only. Next step is user-approved VPS deploy, then run the live V1.13 eval inside the container and use failures to tune retrieval/metadata.

## 2026-05-10 - Mem0 V1.13 Retrieval Quality Pack VPS Deploy

- Deployed Mem0 V1.13 retrieval quality pack to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp` after user approval.
- Created rollback archive `/opt/mem0-remote-mcp-backup-20260510T122101Z.tgz` before copying service files. Remote `.env` and `data/` were preserved.
- Rebuilt and restarted only the `memory-mcp` Docker service. Docker image now includes `retrieval_eval_cases.v1.13.json`.
- Verification: public `/health` returned `ok=true`; remote `py_compile` passed for `retrieval_eval.py`, `retrieval.py`, `core.py`, and `server.py`; V1.13 case pack loaded 12 cases.
- Live retrieval eval: `python retrieval_eval.py --cases retrieval_eval_cases.v1.13.json --top-k 5 --min-pass-rate 0.8 --min-cases 10 --output /tmp/mem0-v113-retrieval-eval.json` returned exit 0 with `cases=12 passed=10 pass_rate=0.8333`. Failed cases were `mother-family-origin-patterns` and `luiza-relationship-patterns`; both had domain hits but memory-type misses, indicating metadata/type enrichment is the next tuning target rather than broad retrieval failure.
- Deployment status: live on VPS. Next step: V1.14 metadata enrichment or selective reranker targeting relationship/psychology memory-type consistency.

## 2026-05-10 - Mem0 V1.14 Metadata Enrichment Local Build

- Implemented local V1.14 metadata/type enrichment in `tools/mem0-remote-mcp` to address V1.13 eval failures where relationship/psychology memories had correct domains but weak or mismatched `memory_type` values.
- Added `metadata_enrichment.py` with deterministic, dry-run-first planning for relationship, psychology, family/family_origin, identity, and personal_life memories. It proposes safer specific types such as `clinical_context`, `relationship_pattern`, `formative_event`, `identity_pattern`, and `person_context` only when the current type is weak/generic.
- Added rollback-guarded execution via `execute_metadata_enrichment_plan()`, requiring `max_updates` before any live update. Rollback rows are written before updates and maintenance rows after updates.
- Added `metadata_enrichment_cli.py` with `--memories-json` or `--live-snapshot`, `--output`, `--execute`, `--max-updates`, rollback log, and maintenance log options. Default mode is dry-run. Live snapshots include text because deterministic classification needs text; stdout and plan output remain count/action oriented and avoid raw memory text.
- Packaged the new module/CLI in Docker and documented dry-run/execute behavior in README.
- Verification: `python -m pytest tools\mem0-remote-mcp -q` -> 135 passed; `python -m py_compile tools\mem0-remote-mcp\metadata_enrichment.py tools\mem0-remote-mcp\metadata_enrichment_cli.py` passed; `python tools\mem0-remote-mcp\metadata_enrichment_cli.py --help` passed.
- Deployment status: local only. Next step is user-approved VPS deploy, then live dry-run metadata plan, inspect update counts/types, apply with a conservative `--max-updates`, and rerun V1.13 retrieval eval.

## 2026-05-10 - Mem0 V1.14 Metadata Enrichment VPS Deploy And V1.15 Observation Core Local Build

- Deployed Mem0 V1.14 metadata enrichment to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp` after user approval.
- Created rollback archive `/opt/mem0-remote-mcp-backup-20260510T173902Z.tgz`, preserved remote `.env` and `data/`, copied local service files, and rebuilt/restarted only `memory-mcp`.
- Verification after deploy: public `https://memory-mcp.ionutrosu.xyz/health` returned `ok=true`; remote container `py_compile` passed for metadata/retrieval/server modules; `metadata_enrichment_cli.py --help` exposed expected live-snapshot/execute/max-update options.
- Live V1.14 dry-run over the memory snapshot found `160` candidate `memory_type` updates: current types were `identity_shaping` and `person`; target types were `clinical_context`, `formative_event`, `identity_pattern`, `person_context`, and `relationship_pattern`.
- Applied a capped `25`-update subset focused on V1.13 failed eval types: `10` `relationship_pattern`, `10` `formative_event`, and `5` `clinical_context`. Execution reported `25` updated and `0` failed.
- Reran live V1.13 retrieval eval inside the container: `cases=12 passed=12 pass_rate=1.0`, fixing the previous `mother-family-origin-patterns` and `luiza-relationship-patterns` failures.
- Built local V1.15 Project Observation Core with TDD. Added `project_observations.py`, `project_observations_cli.py`, and `test_project_observations.py`; Docker packaging now includes the observation modules and README documents the append-only JSONL store.
- V1.15 observation rows are stored at `/data/mem0-mcp-project-observations.jsonl` by default, redact common secret patterns, dedupe by `source_hash`, tolerate malformed JSONL rows, and support `append`, `recent`, and `by-file` CLI operations.
- Verification: targeted V1.15 RED failed on missing `project_observations`; targeted GREEN `python -m pytest tools\mem0-remote-mcp\test_project_observations.py -q` -> 3 passed; `python -m py_compile tools\mem0-remote-mcp\project_observations.py tools\mem0-remote-mcp\project_observations_cli.py` passed; full `python -m pytest tools\mem0-remote-mcp -q` -> 138 passed.
- Deployment status: V1.14 live on VPS; V1.15 built locally only. Next step is build V1.16 hook capture, then V1.17 progressive project retrieval before deploying V1.15-V1.17 together.

## 2026-05-10 - Mem0 V1.16/V1.17 Project Continuity Local Build

- Built local V1.16 hook capture for the hosted Mem0 MCP project continuity layer.
- Extended `client_hooks/mem0_context_hook.py` with best-effort `post_tool_use`, `session_end`, and `post_compact` capture. The hook derives `/project-observation/<profile-token>` from the configured MCP URL, sends with a short timeout, and suppresses failures/output.
- Hook capture records compact metadata only: tool name, command category, pass/fail status, touched paths, sanitized outcome, and source hash. It does not send stdout/stderr, raw command output, file contents, transcripts, API keys, or profile tokens.
- Added `/project-observation/{token}` in `server.py`, writing authenticated-origin observations to `/data/mem0-mcp-project-observations.jsonl` through `ProjectObservationStore`.
- Built local V1.17 progressive project retrieval. Added `project_retrieval.py` and MCP tools `project_search`, `project_timeline`, `project_fetch`, and `project_file_context`.
- `project_search` and `project_timeline` return compact rows only. `project_fetch` returns full observation details only by explicit ID. `project_file_context` returns prior observation titles for a file and an advisory `recommend_full_file_read` flag; it does not block reads.
- Added safe project retrieval telemetry at `/data/mem0-mcp-project-retrieval.jsonl`, storing event name, query hash, result count, and result IDs only, not raw query text or content.
- Updated Docker packaging and README docs for V1.16/V1.17.
- Verification: V1.16 RED failed on missing hook-capture functions/fields/endpoint, then targeted V1.16 tests passed. V1.17 RED failed on missing `project_retrieval`, then targeted V1.17 tests passed. `python -m py_compile` passed for touched runtime modules. Local stale-config guard returned `ok=true` for Codex/Claude configs. Full `python -m pytest tools\mem0-remote-mcp -q` -> 146 passed.
- Deployment status: V1.16/V1.17 local only, not deployed or installed into local/remote hooks yet. Next step is user-approved VPS deploy/sync of V1.15-V1.17 together with rollback archive, remote health check, private MCP smoke, and client compliance status.

## 2026-05-10 - Mem0 V1.15-V1.17 Project Continuity VPS Deploy And Hook Sync

- Deployed V1.15-V1.17 project continuity layer to VPS `root@178.104.203.128` under `/opt/mem0-remote-mcp` after user approval.
- Created rollback archives during deploy iterations: `/opt/mem0-remote-mcp-backup-20260510T190948Z.tgz`, `/opt/mem0-remote-mcp-backup-20260510T191214Z.tgz`, and final hookfix deploy rollback `/opt/mem0-remote-mcp-backup-20260510T191852Z.tgz`. Remote `.env` and `data/` were preserved each time.
- The first remote compile smoke exposed a Docker packaging bug: the image did not copy `client_hooks/mem0_context_hook.py`. Added regression coverage `test_dockerfile_packages_client_hook_script`, fixed Dockerfile with `COPY client_hooks ./client_hooks`, and redeployed.
- Local hook dry-run then exposed an empty-input bug in `build_project_observation_payload()` (`tool_name or hook` / `unknown` were undefined). Added empty-input regression coverage, fixed fallback strings, and redeployed the corrected hookfix image.
- Final local verification after both fixes: `python -m pytest tools\mem0-remote-mcp -q` -> 148 passed; `py_compile` passed for touched runtime modules.
- Final remote verification: public `https://memory-mcp.ionutrosu.xyz/health` returned `ok=true`; remote container `py_compile` passed for project observation/retrieval/server/hook modules; private MCP smoke initialized successfully, listed `15` tools, found all project tools, appended a synthetic sanitized project observation, and confirmed `project_search`/`project_file_context` results without printing tokens or raw memory/log content.
- Synced corrected `mem0_context_hook.py` to local `~/.codex` and `~/.claude`, VPS `/root/.codex` and `/root/.claude`, and active ttyd container `/config/.codex` and `/config/.claude`; existing files were timestamp-backed up first.
- Normalized remote host and ttyd configs to Linux `python3` hook commands where needed. Added Claude Mem0 `SessionEnd` and `PostToolUse` hooks while preserving existing guardrails. Codex hook script was synced; Codex config remained limited to supported existing events.
- Stale config guard returned `ok=true` for local, VPS host, and ttyd container configs.
- Client compliance status after deploy: Codex `healthy`; ChatGPT `partial`; Claude `partial` until it produces recent retrieval telemetry; Perplexity `missing` because it is read-only/not recently used; deploy-smoke `search_only`. This is an observability status, not a service-health failure.
- Deployment status: V1.15-V1.17 live on VPS and hook scripts synced. Next step is use a real Claude/Codex session to generate natural project observations, then rerun `client_compliance_status_cli.py` and inspect only aggregate project-observation/retrieval telemetry counts.
## 2026-05-11 - Mem0/Context Hook Context Hygiene Local Fix

- Investigated hook context growth after visible `UserPromptSubmit` and context-mode hook blocks appeared in Codex conversations.
- Root cause: `tools/mem0-remote-mcp/client_hooks/mem0_context_hook.py` emitted visible `additionalContext` for `session_start`, `user_prompt`, and `post_compact`; Codex context-mode also injected the large `<context_window_protection>` block through `context-mode hook codex sessionstart`.
- Changed the Mem0 hook implementation so all routine modes return `{"suppressOutput": true}` while preserving background heartbeat and project-observation sends.
- Updated local active hook scripts at `~/.codex/mem0_context_hook.py` and `~/.claude/mem0_context_hook.py`; timestamp backups were created first.
- Removed the stale Codex `[features].codex_hooks` config line and disabled only the Codex context-mode hooks that can inject context (`SessionStart` and `UserPromptSubmit`). Kept non-context context-mode tool hooks active.
- Verification: targeted hook tests first failed against the old behavior, then passed; full `python -m pytest tools\mem0-remote-mcp -q` -> 148 passed; direct local hook smokes returned suppressed/empty outputs for routine Mem0 and remaining context-mode hooks.
- Decision: routine memory/context hooks should record telemetry and preserve external context stores, but should not add recurring text to the model context. Safety guardrails may still inject short warnings only on risky events.
- Next step: sync the same hook hygiene to VPS/ttyd configs only with explicit approval, then add observation diagnostics as V1.18.

## 2026-05-12 - Kontext V2 Mem0 Mirror Local Tasks 1-4

- Started local Kontext V2 under `tools/kontext-v2` as a Postgres + pgvector Mem0 mirror package. Mem0 remains live source of truth; no VPS deploy or Mem0 mutation was performed.
- Implemented package bootstrap, local config, Docker Compose pgvector service, schema helpers, repository upsert/fetch, sanitized Mem0 importer, and read-only MCP contract helpers.
- Local Postgres default was moved to `localhost:55434` because `55432` was already occupied by another local Postgres container. Tests include a guard refusing non-local/non-55434 database URLs before schema writes.
- Added sanitized fixture import parity coverage and robustness checks for malformed metadata, invalid or non-finite `signal_strength`, missing IDs, and missing text. Bad rows are skipped instead of aborting the import.
- Hardened MCP tool definitions with `inputSchema`, read-only default tools, write tools behind `write_enabled=True`, query requirement, `top_k` bounds, and filter normalization for strings, arrays, scalars, and malformed dicts.
- Verification: `python -B -m pytest tools\kontext-v2\tests\test_package_bootstrap.py tools\kontext-v2\tests\test_schema.py tools\kontext-v2\tests\test_mem0_import_parity.py tools\kontext-v2\tests\test_mcp_contract_parity.py -q` passed `10` tests; warnings were pytest-asyncio/Python deprecation warnings only. Cache scan under `tools/kontext-v2` returned no generated cache files.
- Review: Task 1-4 implementation passed focused spec and code-quality review after fixes for DB safety, importer malformed-row handling, MCP tool schema, filter normalization, and `top_k=0` clamping.
- Next step: Task 5, implement hybrid retrieval and eval parity using the existing Mem0 V1.13 eval case format, still local-only.

## 2026-05-12 - Kontext V2 Retrieval Parity Foundation Local Task 5

- Implemented Task 5 under `tools/kontext-v2`: lexical retrieval wrapper and repository search over Postgres full-text search plus metadata filters.
- Added `kontext_v2/retrieval.py` and `KontextRepository.search_rows()` with parameterized SQL, domain JSONB filtering, memory type/tier/status filters, rank ordering, signal-strength tie-break, and update-time tie-break.
- Added retrieval parity tests that import the sanitized fixture, clean only the two fixture IDs before import, and verify the AI-memory architecture query returns `mem-ai-architecture-1` with expected domain/type metadata.
- Wired the existing Mem0 V1.13 eval case format into Kontext tests using an importlib loader for `tools/mem0-remote-mcp/retrieval_eval.py`; no live Mem0 calls are made by the test.
- Strengthened the Kontext V2 test DB guard to require `kontext_v2@localhost:55434/kontext_v2`, not just a local host/port.
- Verification: `python -B -m pytest tools\kontext-v2\tests -q` passed `12` tests; warnings were pytest-asyncio/Python deprecation warnings only. A negative guard smoke refused `postgresql://kontext_v2:kontext_v2@localhost:55434/other_db` before test execution.
- Review: Task 5 passed focused spec review and code-quality re-review after DB isolation fixes.
- Next step: Task 6, implement safe side-by-side parity reports; still local-only, with Mem0 remaining live source of truth.

## 2026-05-12 - Kontext V2 Safe Side-By-Side Diff Local Task 6

- Implemented Task 6 under `tools/kontext-v2`: safe side-by-side parity diff reports for Mem0 and Kontext search outputs.
- Added `kontext_v2/parity.py` with `compare_search_results()`, which reports query hash, result counts, shared external IDs, domain sets, and memory-type sets only.
- Added `tests/test_side_by_side_diff.py` to verify Mem0 `id` and Kontext `external_mem0_id` match and that raw `memory`/`text` content is not present in rendered reports.
- Verification: `python -B -m pytest tools\kontext-v2\tests -q` passed `13` tests; warnings were pytest-asyncio/Python deprecation warnings only.
- Review: Task 6 passed focused spec and code-quality reviews. No VPS deploy or Mem0 mutation was performed.
- Next step: Task 7, implement fetch parity and ingestion status helpers; still local-only.

## 2026-05-12 - Kontext V2 Fetch And Status Parity Local Task 7

- Implemented Task 7 under `tools/kontext-v2`: exact-ID fetch helper and safe aggregate ingestion/status helper for the Mem0-compatible MCP surface.
- Added `tests/test_fetch_parity.py`, `fetch_tool()`, `ingestion_status_tool()`, and `KontextRepository.count_memories()`.
- Fetch returns Mem0-shaped `id`, `memory`, `title`, and `metadata`; status reports aggregate mirror count and disabled writes without exposing raw memory dumps or secrets.
- Verification: `python -B -m pytest tools\kontext-v2\tests\test_fetch_parity.py -q` -> 1 passed; warnings were pytest-asyncio/Python deprecation warnings only.
- Commit status: no git commit was created because commits were not requested in this session.
- Next step: Task 8, implement write-path dry-run proposals; still local-only, with Mem0 remaining live source of truth.

## 2026-05-12 - Kontext V2 Write Dry-Run Proposals Local Task 8

- Implemented Task 8 under `tools/kontext-v2`: `ingest_exchange_dry_run()` for deterministic write-path proposals without applying database or Mem0 writes.
- Added `tests/test_write_path_dry_run.py` and verified the RED failure first: missing `ingest_exchange_dry_run` import.
- The helper hashes message content into `source_hash`, emits only proposal metadata for domain-signaled Kontext/Mem0 exchanges, and always reports `writes_applied: 0` in `dry_run` mode.
- Verification: `python -B -m pytest tools\kontext-v2\tests\test_write_path_dry_run.py -q` -> 1 passed; `python -B -m pytest tools\kontext-v2\tests -q` -> 15 passed. Warnings were pytest-asyncio/Python deprecation warnings only.
- Commit status: no git commit was created because commits were not requested in this session.
- Next step: Task 9, implement category dossier projections and edit-to-proposal parsing; still local-only.

## 2026-05-12 - Kontext V2 Category Dossiers Local Task 9

- Implemented Task 9 under `tools/kontext-v2`: category dossier rendering and edit-to-proposal parsing.
- Added `kontext_v2/dossiers.py` and `tests/test_dossiers.py`.
- RED verification failed first on missing `kontext_v2.dossiers`; first GREEN attempt caught a parser issue where the embedded edit-protocol example was treated as a real proposal.
- Fixed parsing so only proposal comments added in `edited_markdown` and absent from `original_markdown` become patch proposals.
- Verification: `python -B -m pytest tools\kontext-v2\tests\test_dossiers.py -q` -> 1 passed; `python -B -m pytest tools\kontext-v2\tests -q` -> 16 passed. Warnings were pytest-asyncio/Python deprecation warnings only.
- Commit status: no git commit was created because commits were not requested in this session.
- Next step: Task 10, build the local parity CLI; still local-only.

## 2026-05-12 - Kontext V2 Local Parity CLI Task 10

- Implemented Task 10 under `tools/kontext-v2`: local `parity_eval.py` CLI shell for future Mem0/Kontext side-by-side parity runs.
- Added `tests/test_parity_cli.py`; RED verification failed first because `tools/kontext-v2/parity_eval.py` did not exist.
- CLI currently supports safe argument parsing and dry/configured JSON output for `--cases`, `--kontext-database-url`, `--mem0-profile-url`, `--top-k`, `--output`, and `--dry-run`; it does not call live Mem0 or print raw memory text.
- Verification: `python -B -m pytest tools\kontext-v2\tests\test_parity_cli.py -q` -> 1 passed; `python -B -m pytest tools\kontext-v2\tests -q` -> 17 passed. Warnings were pytest-asyncio/Python deprecation warnings only.
- Commit status: no git commit was created because commits were not requested in this session.
- Next step: Task 11, run the broader local safety review including Mem0 MCP tests, status scan, and final log entry.

## 2026-05-12 - Kontext V2 Mem0 Mirror Local Foundation

- Built the initial Kontext V2 local package under `tools/kontext-v2` through Task 10 of the local mirror plan.
- Added Postgres + pgvector schema, Mem0 mirror importer, read-only MCP contract helpers, retrieval parity foundation, side-by-side safe diff reports, fetch/status helpers, write dry-run proposals, dossier projections, and parity CLI shell.
- Mem0 remains live source of truth; no destructive Mem0 writes were performed; no VPS deploy was performed.
- Safety scan: targeted status showed only `tools/kontext-v2/`, the Kontext V2 plan, and `project_log.md` in scope. No `.env`, `.log`, `__pycache__`, `.pyc`, secret, raw, or dump files were found under `tools/kontext-v2`; text scans only hit policy/log language, not secret values.
- Verification: `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> 165 passed. Warnings were pytest-asyncio/Python deprecation warnings only.
- Commit status: no git commit was created because commits were not requested in this session.
- Next step: implement live Mem0 export/import against a private staging database, then run side-by-side retrieval evals without printing raw memory text.

## 2026-05-12 - Kontext V2 Live Mem0 Staging Import And Parity Smoke

- Added a local-only live Mem0 import/parity path for Kontext V2.
- Added `kontext_v2/live_mem0.py` with a Mem0 API client, safe live snapshot import, eval-case seed import, and safe import summaries.
- Reworked `tools/kontext-v2/parity_eval.py` into a wrapper around `kontext_v2/parity_cli.py`, adding guarded local staging DB checks, private API-key env loading, live snapshot import, eval-case seeding, and side-by-side safe retrieval reports.
- Added tests in `test_live_mem0_import.py`, expanded `test_parity_cli.py`, and added a metadata-sensitive retrieval regression in `test_retrieval_eval_parity.py`.
- Ran guarded live staging smoke against `https://mem0-api.ionutrosu.xyz` using the local `.local/mem0-extractor/mem0.env` API key without printing the key or raw memory text. Output report: `.local/kontext-v2/live-parity-20260512.json`.
- Live import results: live snapshot saw 20 rows and eval seed saw 57 rows; local mirror count after import was 80 memories.
- Safe eval result: direct Mem0 API search passed 10/12 cases; Kontext staging search passed 6/12; overlap had shared results in 8/12 cases and shared first result in 0/12. Failures indicate Kontext needs the hosted MCP-style reranker/metadata normalization before client cutover.
- Safe report scan found no raw `memory`, `text`, or `content` fields in the live report; matches were only redaction test fixtures.
- Verification: `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> 170 passed. Warnings were pytest-asyncio/Python deprecation warnings only.
- No VPS deploy, no Mem0 writes, no git commit.
- Next step: implement Kontext retrieval parity pack using hosted MCP routing/ranking semantics and metadata normalization, then rerun the same live staging parity report.

## 2026-05-12 - Kontext V2 Retrieval Parity Pack Local Build

- Implemented the Kontext V2 Retrieval Parity Pack locally.
- Added hosted-MCP-style query expansion, domain hints, metadata normalization, and Python reranking in `kontext_v2/retrieval.py`.
- Added `KontextRepository.list_memory_rows()` so retrieval can rank broad local staging candidates instead of only strict Postgres FTS matches.
- Normalization now maps legacy domains/types needed by the eval pack, including `family_origin` -> `family`/`psychology`, AI systems workflow/event rows -> `project_state`, family/relationship legacy rows -> `formative_event` or `relationship_pattern`, psychology legacy rows -> `psychology_pattern`, opera/vocality rows -> `career_context`, finance rows -> `finance_context`, and Vocality business decisions -> `business_context`.
- Added regression tests for legacy family-origin filters, metadata candidates beating weak lexical notes, AI systems workflow normalization, and Vocality business-context normalization.
- Verification: `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> 174 passed. Warnings were pytest-asyncio/Python deprecation warnings only.
- Reran guarded live staging parity with no Mem0 writes, no VPS deploy, and no raw memory text/key output. Final safe report: `.local/kontext-v2/live-parity-20260512-retrieval-pack-v3.json`.
- Final safe parity result: Kontext staging passed 12/12 eval cases with domain hits 12/12 and memory-type hits 12/12. Direct Mem0 API baseline in the same run passed 9/12. Shared-any overlap was 8/12 and shared-first overlap 1/12, so quality gates pass but ranking order still differs from direct API.
- Local mirror count after final import/seed was 111 memories.
- Next step: package Kontext V2 behind a private VPS MCP endpoint for side-by-side client smoke, while keeping Mem0 as rollback/source of truth until real-client behavior is proven.

## 2026-05-12 - Kontext V2 Existing VPS App Integration

- Modified the existing VPS Kontext deployment at `root@178.104.203.128:/opt/kontext` for `kontext.ionutrosu.xyz`; no new domain/app was created.
- Created remote rollback archive `/opt/kontext-backup-20260512T124352Z.tgz` before changes, including `/opt/kontext` and the existing Kontext SQLite Docker volume data.
- Added `kontext_v2/http_api.py` locally with a FastAPI bridge for `/health`, `/tools`, `/search`, `/fetch/{id}`, and dry-run `/ingest_exchange`; mounted it remotely under `/api/v2` inside the existing Kontext app.
- Remote app changes: copied `kontext_v2` into `/opt/kontext/src`, added `psycopg[binary]`, copied the V1.13 retrieval eval cases into the image, added internal `kontext-v2-db` Postgres/pgvector service and volume, added `KONTEXT_V2_DATABASE_URL` only for the Kontext app, and disabled the inherited server healthcheck on `kontext-worker` so the worker no longer reports unhealthy just because it does not bind HTTP.
- Rebuilt `kontext:latest` as `sha256:4de8b3f362033dfcec6f1155ba6fc1084dea8659fb97dbd419f29c5810a306a9` and restarted only the existing Kontext compose services plus the new internal DB. Mem0 `/opt/mem0-remote-mcp/.env` and data were not modified.
- Imported a safe live Mem0 mirror into the new Postgres store without printing keys or raw memory text: live snapshot saw 20 rows; eval seed saw 58 rows; remote mirror count is 77.
- Remote safe eval: direct Mem0 API baseline passed 8/12; remote Kontext V2 search passed 12/12 with domain hits 12/12 and memory-type hits 12/12. Search smoke through `http://127.0.0.1:8200/api/v2/search` returned 3 results by ID only.
- Verification: local `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> 175 passed; remote `py_compile` and `docker compose config --quiet` passed; remote `/health`, `/api/v2/health`, and `/api/v2/tools` returned HTTP 200; `kontext`, `kontext-v2-db`, and `kontext-worker` are up, with the app and DB healthy.
- Public `https://kontext.ionutrosu.xyz/health` still redirects to Pangolin SSO with HTTP 302, as expected for the protected domain.
- Next step: add a proper private MCP transport or authenticated client-facing bridge on top of `/api/v2` before considering any client cutover from Mem0.

## 2026-05-12 - Kontext V2 Private MCP Bridge

- Added a private tokenized JSON-RPC MCP bridge for Kontext V2 under the existing `/api/v2/mcp/{token}` path on `kontext.ionutrosu.xyz`'s existing `/opt/kontext` deployment.
- Local code: added `kontext_v2/mcp_bridge.py`, mounted it from `kontext_v2/http_api.py`, and added `tests/test_mcp_bridge.py`.
- Auth model: reuses existing MCP profile tokens from the remote Mem0 environment via `env_file: /opt/mem0-remote-mcp/.env`; tokens are not printed or copied into logs. Exposed profile names only in `/api/v2/health`.
- Tool surface is intentionally read-only for now: `search`, `fetch`, and `ingestion_status`. Kontext writes remain disabled/dry-run; `search` returns compact rows without raw memory text, while `fetch` remains explicit-ID retrieval.
- Remote rollback archive before deploy: `/opt/kontext-backup-20260512T133601Z-mcp-bridge.tgz`.
- Rebuilt existing `kontext:latest` as `sha256:02c17e2661ccdacdc4a90c5961e3ea4b8076d69cf89e4155ca024cec25bd8987` and recreated only the `kontext` service. The existing `kontext-v2-db` volume and legacy SQLite volume were preserved. Mem0 service files/data were not modified.
- Verification: local `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> 178 passed. Remote `py_compile` and `docker compose config --quiet` passed. Remote `/api/v2/health` reports mirror count 77 and MCP profiles `chatgpt`, `claude`, `codex`, `perplexity` without tokens.
- Authenticated MCP smoke: Codex and Claude Kontext profiles initialized, listed tools, and searched successfully through `/api/v2/mcp/{token}`. Search smoke printed IDs only and confirmed no raw memory text in compact search output.
- Side-by-side smoke: Codex/Claude Kontext MCP returned 3 search IDs; Mem0 MCP returned 3 search IDs with one shared first ID for the tested architecture query. Mem0 MCP needed the standard `Accept: application/json, text/event-stream` header.
- Remote safe eval after bridge deploy: direct Mem0 API baseline passed 8/12; Kontext V2 passed 12/12 with domain hits 12/12 and memory-type hits 12/12.
- Public `https://kontext.ionutrosu.xyz/api/v2/health` still redirects to Pangolin SSO with HTTP 302, so public browser access remains protected.
- Next step: optionally add local Codex/Claude config entries for `kontext-v2` as disabled/side-by-side MCP servers, then run real-client retrieval without changing Mem0 as source of truth.

## 2026-05-12 - Kontext V2 vs Mem0 MCP Reliability Evaluation

- Added a safe MCP-vs-MCP reliability harness in `tools/kontext-v2/kontext_v2/mcp_reliability_eval.py` plus tests in `tools/kontext-v2/tests/test_mcp_reliability_eval.py`.
- Harness compares live Kontext V2 MCP (`/api/v2/mcp/{token}`) against live Mem0 MCP (`/mcp/{token}`) using the existing V1.13 retrieval eval cases. It outputs aggregate metrics, failed case names, latencies, and IDs only by default; detailed row summaries are opt-in.
- RED check first failed on missing `kontext_v2.mcp_reliability_eval`; implementation then passed focused tests and full local suite.
- Remote live eval used Codex and Claude profile tokens from `/opt/mem0-remote-mcp/.env` without printing token values. No Mem0 writes, Kontext writes, deploys, or client cutover were performed.
- Results for both Codex and Claude profiles: Kontext V2 MCP passed 12/12; Mem0 MCP passed 12/12. Domain hits and memory-type hits were 12/12 for both.
- Latency from the VPS vantage point: Kontext averaged about 21-25 ms per MCP call; Mem0 averaged about 829-857 ms. Mem0's initialize response shape did not set the harness `init_ok` flag, but tools/list and search succeeded with zero errors.
- Overlap was low: shared-any 3/12 cases and shared-first 1/12. This means both pass the broad eval gates, but they often retrieve different top memories.
- Current decision: Kontext V2 looks competitive or faster for the narrow retrieval eval, but it is not yet a Mem0 replacement because the mirror has only 77 memories, lacks automatic continuous sync, lacks write/ingestion/update/project-observation tools, and has not passed real-client long-running usage.
- Recommended next step: build continuous mirror sync + mutation/ingestion parity, then run a larger eval set with exact expected IDs and freshness tests before any source-of-truth cutover.

## 2026-05-12 - Kontext V2 Reliability Hardening Layer

- Summary: Added the next local hardening slice for Kontext V2 before any source-of-truth cutover: mirror sync audit/status, capped dry-run/apply snapshot sync, exact-ID freshness comparison, sanitized MCP retrieval trace logging, `/sync/status`, and an aggregate-only sync CLI.
- Files touched: `tools/kontext-v2/kontext_v2/schema.py`, `repository.py`, `importer.py`, `mirror_sync.py`, `sync_cli.py`, `parity.py`, `mcp_server.py`, `mcp_bridge.py`, `http_api.py`, plus tests `test_mirror_sync.py`, `test_exact_id_parity.py`, `test_mcp_shadow_logging.py`, and `test_schema.py`.
- Safety decisions: Mem0 remains source of truth; no Mem0 writes were added; sync defaults to dry-run unless `--apply` is explicitly passed; retrieval logs store query hash, profile, service, filters, top IDs, and latency only, not raw query text or raw memory text.
- Verification: focused RED first failed on missing `kontext_v2.mirror_sync` and `mem0_source_hash`; after implementation, focused tests passed (`6 passed`). Full local regression passed with `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> `185 passed`. Warnings were existing pytest-asyncio/FastAPI/Python deprecation warnings.
- Deployment status: local only at this log entry. Next step is a safe VPS deploy to `/opt/kontext`, preserving Mem0 `/opt/mem0-remote-mcp/.env` and `/opt/mem0-remote-mcp/data`, then run local-VPS health, capped sync dry-run/apply, and MCP reliability smokes without printing secrets or raw memory text.

## 2026-05-12 - Kontext V2 Reliability Hardening Deploy

- Deployed the Kontext V2 reliability hardening layer to `root@178.104.203.128:/opt/kontext` by copying updated `kontext_v2` runtime modules, rebuilding `kontext:latest`, and recreating only `kontext` and `kontext-worker`. The `kontext-v2-db` volume stayed mounted; `/opt/mem0-remote-mcp/.env` and `/opt/mem0-remote-mcp/data` were not edited.
- Rollback archive created before deploy: `/opt/kontext-backup-20260512T151326Z-reliability-layer.tgz`.
- Remote smoke: `/api/v2/health` and `/api/v2/sync/status` returned `ok=true`, 77 mirrored memories, MCP profiles `chatgpt`, `claude`, `codex`, and `perplexity`, and latest sync status `ok` after capped apply.
- Remote sync: capped dry-run with 5 rows returned unchanged=5; capped apply with 20 rows returned unchanged=19 and updated=1. Output was aggregate-only; no raw memories or secrets were printed.
- Remote live V1.13 MCP reliability eval after deploy: Codex and Claude profiles both had Kontext 12/12 and Mem0 12/12; domain hits 12/12 and memory-type hits 12/12. Kontext avg latency was about 21-25 ms; Mem0 avg latency was about 786-815 ms. Overlap remained low at shared-any 3/12 and shared-first 1/12.
- Remote retrieval trace audit: `retrieval_queries` held 192 MCP trace rows after eval, split 96 Codex and 96 Claude, storing aggregate trace metadata only.
- Final local verification after deploy: `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> `185 passed`. Warnings were existing pytest-asyncio/FastAPI/Python deprecation warnings.
- Decision: Kontext V2 is now safer to shadow in real clients, but not ready to replace Mem0 as source of truth. Remaining blockers are fuller mirror coverage/scheduling, write-ingestion parity, project-observation parity, dashboard/category UX, and longer real-client shadow evidence.

## 2026-05-12 - Kontext V2.1 Mem0-Compatible Architecture Plan And Local Tasks 1-2

- Wrote implementation plan `docs/superpowers/plans/2026-05-12-kontext-v21-mem0-compatible-architecture.md` to copy the hosted Mem0 wrapper architecture into Kontext V2 in safe slices: full MCP contract, intake audit, deterministic extraction/gating, local apply path, project continuity, retrieval fusion, cutover eval, and deploy/shadow trial.
- Completed Task 1 locally: Kontext MCP now exposes Mem0-compatible read/project tool names (`project_search`, `project_timeline`, `project_fetch`, `project_file_context`) and write-enabled dry-run tools (`extract_memories`, `ingest_exchange`, `submit_memory_override`, `flag_memory`) with concrete input schemas and JSON-RPC dispatch. Read-only profiles still reject write tools.
- Completed Task 2 locally: added `memory_intake_audit` and `memory_flags` tables, repository persistence helpers, sanitized dry-run audit rows for intake/override calls, sanitized flag rows for `flag_memory`, and aggregate intake/flag counts in `ingestion_status`.
- Safety: write tools still apply zero memory writes; override content and flag reasons are hashed in persisted audit/flag rows; tests verify raw secrets and raw chat text are not stored in those rows.
- Verification: focused Task 1 tests passed (`10 passed`), focused Task 2 tests passed (`3 passed`), and full local regression passed with `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> `193 passed`. Warnings were existing pytest-asyncio/FastAPI/Python deprecation warnings.
- Deployment status: local only for this V2.1 slice. Next step is Task 3: port Mem0 deterministic extraction/gating/proposal normalization into `kontext_v2/intake.py` before any new VPS deploy.

## 2026-05-12 - Kontext V2.1 Task 3 Intake Normalization

- Completed Task 3 locally from `docs/superpowers/plans/2026-05-12-kontext-v21-mem0-compatible-architecture.md`: added deterministic intake helpers in `tools/kontext-v2/kontext_v2/intake.py` for normalized messages, source hashes, sanitized previews, high-signal gating, and Mem0-style proposal normalization.
- Wired `ingest_exchange`/`extract_memories` dry-runs through the intake gate in `tools/kontext-v2/kontext_v2/mcp_server.py`. Responses expose hashes, gate metadata, and proposal counts, not raw chat text or secrets.
- Destructive proposal actions (`delete`, `merge`, `stale`, `conflict`) now normalize to flag-only proposals; confidence and signal strength are clamped to safe ranges.
- Verification: RED first failed on missing `kontext_v2.intake`; after implementation, `python -B -m pytest tools\kontext-v2\tests\test_intake_extraction.py -q` -> `10 passed`, and full regression `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> `203 passed`. Warnings were existing pytest-asyncio/FastAPI/Python deprecation warnings.
- Deployment status: local only. Next step is Task 4, local apply path and dedupe/update simulation, before any VPS deploy.

## 2026-05-12 - Kontext V2.1 Task 4 Local Apply Path

- Completed Task 4 locally from `docs/superpowers/plans/2026-05-12-kontext-v21-mem0-compatible-architecture.md`: added Kontext-local intake apply orchestration in `tools/kontext-v2/kontext_v2/intake.py` with `apply=False` as the default.
- Apply behavior is intentionally conservative: new saves require high confidence, exact duplicate content is skipped, updates require an exact `existing_id` or exact text match, and unmatched update proposals become conflict flags instead of broad updates.
- Added repository helpers in `tools/kontext-v2/kontext_v2/repository.py` for exact text dedupe and memory-version counts. Local ingestion writes use `kontext_ingestion` version rows, preserving history on every update.
- Destructive proposals remain flag-only and record rows in `memory_flags`; no hard-delete path was added.
- Verification: RED first failed on missing `apply_intake_proposals`; after implementation, `python -B -m pytest tools\kontext-v2\tests\test_intake_apply.py -q` -> `6 passed`, and full regression `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> `209 passed`. Warnings were existing pytest-asyncio/FastAPI/Python deprecation warnings.
- Deployment status: local only. Next step is Task 5, project continuity layer parity, before any VPS deploy.

## 2026-05-13 - Kontext V2.1 Task 5 Project Continuity Layer Parity

- Completed Task 5 locally from `docs/superpowers/plans/2026-05-12-kontext-v21-mem0-compatible-architecture.md`: added `tools/kontext-v2/kontext_v2/project_observations.py` plus a `project_observations` Postgres table.
- Implemented repository-backed `project_search`, `project_fetch`, `project_timeline`, and `project_file_context` in `tools/kontext-v2/kontext_v2/repository.py`, then routed the MCP bridge project tools to those methods instead of placeholder empty responses.
- Search/timeline/file-context responses return compact rows only: id, date, type, title, project, file count, and token estimate. Full details are only returned by exact `project_fetch`. Secret-like values in titles, summaries, commands, and metadata are redacted during observation ingestion.
- Adjusted the old placeholder bridge test to expect repo-backed empty-result shapes, and named the new Kontext test file `test_kontext_project_observations.py` to avoid a pytest module-name collision with Mem0's own `test_project_observations.py`.
- Verification: RED first failed on missing table/repository behavior; after implementation, `python -B -m pytest tools\kontext-v2\tests\test_kontext_project_observations.py tools\kontext-v2\tests\test_mcp_bridge_write_tools.py -q` -> `9 passed`, and full regression `python -B -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` -> `215 passed`. Warnings were existing pytest-asyncio/FastAPI/Python deprecation warnings.
- Deployment status: local only. Next step is Task 6, retrieval fusion and fallback policy, before any VPS deploy.

## 2026-05-13 - Mem0 agent-first reminders and CLI dream cleanup

- Summary: Added an agent-first memory maintenance path so Claude/Codex use direct Mem0 MCP writes organically while Qwen/`ingest_exchange` stays off the routine hot path. Added deterministic maintenance-due status and a bounded hook reminder for CLI dream cleanup approval.
- Files touched: `CLAUDE.md`, `AGENTS.md`, `tools/mem0-remote-mcp/monitoring.py`, `tools/mem0-remote-mcp/client_hooks/mem0_context_hook.py`, `tools/mem0-remote-mcp/server.py`, `tools/mem0-remote-mcp/ingestion_status_cli.py`, `tools/mem0-remote-mcp/README.md`, `tools/mem0-remote-mcp/test_monitoring.py`, `tools/mem0-remote-mcp/test_client_hooks.py`, `docs/superpowers/plans/2026-05-12-kontext-v21-mem0-compatible-architecture.md`.
- Verification: focused maintenance/hook tests passed; full Mem0 remote MCP regression passed locally (153 passed); full Kontext+Mem0 regression passed locally (220 passed). Deployed to VPS, rebuilt/restarted memory-mcp, remote py_compile passed, public/local health checks passed, maintenance-status endpoint returned ok, and local/VPS/ttyd hook smokes emitted suppressOutput only.
- Decisions: dream cleanup remains CLI-first and user-approved; no separate API cleanup model by default. Hooks inject only one compact maintenance reminder when deterministic thresholds say cleanup is due.
- Deployment: VPS /opt/mem0-remote-mcp, rollback archive /opt/mem0-remote-mcp-backup-20260512T234013Z.tgz; remote .env and data preserved.
- Next step: continue Kontext V2 retrieval parity Task 6 when ready.

## 2026-05-13 - Kontext V2 reconciliation and VPS deploy

- Summary: Reconciled local Kontext V2 against VPS /opt/kontext and deployed local-only runtime changes. Remote was missing newer Task 3-5/7 runtime files including intake.py, project_observations.py, and newer mcp_bridge.py, mcp_server.py, repository.py, and schema.py.
- Files deployed: tools/kontext-v2/kontext_v2/ plus parity_eval.py into /opt/kontext/src; Docker image kontext:latest rebuilt and kontext/kontext-worker recreated without recreating the kontext-v2-db service.
- Verification: local Kontext tests passed (67 passed); rollback archive created at /opt/kontext-backup-20260512T235157Z-v2-deploy.tgz; key deployed file hashes match local; remote py_compile passed; /health, /api/v2/health, /api/v2/sync/status, /api/v2/tools, search, and MCP tools/list smokes passed; live host MCP reliability eval passed 12/12 for Kontext and 12/12 for Mem0 under codex profile.
- Decisions: Kontext remains mirror_read_only with writes disabled. No cutover performed.
- Next step: produce the formal cutover/shadow report and decide how long to keep collecting shadow evidence before any MCP cutover discussion.

## 2026-05-13 - Kontext V2 shadow cutover report

- Summary: Produced formal Kontext V2 shadow/cutover report after fresh remote health checks and live MCP reliability comparison.
- Report: `docs/superpowers/reports/2026-05-13-kontext-v2-shadow-cutover-report.md`.
- Verification: remote `/health`, `/api/v2/health`, `/api/v2/sync/status`, `/api/v2/tools`, and Mem0 `/health` returned OK. Fresh live MCP eval under Codex profile passed 12/12 for Kontext and 12/12 for Mem0.
- Decision: Kontext is shadow-ready but not cutover-ready. Mem0 remains source of truth; Kontext stays `mirror_read_only` with writes disabled.
- Next step: add scheduled sync/eval reports and improve project-observation/category/dashboard layers before any client cutover test.

## 2026-05-13 - Kontext V2 scheduled shadow reporting

- Summary: Added VPS-hosted scheduled Kontext V2 shadow evidence reporting while keeping Mem0 as the live source of truth.
- Remote files: `/opt/kontext/scripts/kontext_shadow_report.py`, `/etc/cron.d/kontext-shadow-report`, `/opt/kontext/reports/latest.json`, and timestamped reports under `/opt/kontext/reports/`.
- Schedule: daily at `03:17` server time. The job runs endpoint health checks, a capped dry-run mirror sync report, and live MCP reliability eval under the Codex profile.
- Safety: report output is aggregate-only; no remote `.env` contents, secrets, raw chats, raw memory text, raw queries, top rows, or top IDs are written to the JSON report. Mem0 is not written. Kontext memory rows are not mutated by the sync dry-run.
- Debug note: the first manual run showed sync dry-run failure because the Kontext container could not resolve the Mem0 stack-internal host (`http://mem0:8000`). The runner now uses the public Mem0 API base for dry-run sync from the Kontext container.
- Verification: remote script `py_compile` passed; cron file installed; manual latest report returned `ok=true`, endpoint checks OK, sync dry-run `processed_rows=20`, and live MCP reliability `codex/kontext=12/12`, `codex/mem0=12/12`. Sanitization check found zero env secret-value hits and zero forbidden raw keys.
- Next step: accumulate scheduled shadow reports, then add project-observation/category dashboard layers before any cutover test.

## 2026-05-13 - Kontext frontend Claude Design handoff package

- Summary: Packaged the live-matching Kontext frontend/dashboard source for Claude Design redesign with the newer warm monochrome branding direction.
- Output: `handoffs/kontext-frontend-claude-design-2026-05-13.zip` plus unpacked handoff directory at `handoffs/kontext-frontend-claude-design-2026-05-13/`.
- Included: static React dashboard (`static_dashboard/`), dashboard templates (`templates/dashboard/`), dashboard/API integration references (`cloud/api.py`, `cloud/server.py`, `cloud/dashboard*.py`), root `README.md`/`SKILL.md`, `CLAUDE_DESIGN_PROMPT.md`, `FRONTEND_INVENTORY.md`, and `MANIFEST.sha256.txt`.
- Safety: excluded `.env`, secrets, auth/session files, databases, runtime state, caches, node_modules/build artifacts, and worktrees. `static_dashboard/data.js` was privacy-sanitized while preserving shape for UI redesign.
- Verification: 15 static/template frontend files matched the VPS live copy under `/opt/kontext/src` by SHA-256 before packaging. Zip verification found 25 files, prompt and manifest present, no forbidden names, no owner-name hits, and no private-key hits.
- Next step: send the zip and `CLAUDE_DESIGN_PROMPT.md` to Claude Design, then review the redesigned source before applying it back to Kontext.

## 2026-05-13 - Kontext Claude Design frontend wired and deployed

- Summary: Wired the Claude Design dashboard output into Kontext's static React dashboard and deployed it to the VPS.
- Local source changed: `C:\Users\Gaming PC\Desktop\Claude\Kontext\static_dashboard\index.html`, `static_dashboard/src/app.jsx`, `static_dashboard/src/primitives.jsx`, new design files under `static_dashboard/src/`, and `static_dashboard/fonts/Geist-VariableFont_wght.ttf`.
- Integration approach: preserved the live `/data.js` contract from `cloud.dashboard`; added `static_dashboard/src/data-adapter.js` to normalize the existing dashboard snapshot shape into the Claude Design shape (`meta`, `tiers`, `sources`, `relations`, `activity`, `events`, `stale`, `config`) before React mounts. Did not replace live data with Claude's synthetic data.
- Deployment: copied `static_dashboard` to VPS `/opt/kontext/src/static_dashboard`, rebuilt `kontext:latest` with `docker build -t kontext:latest /opt/kontext/src`, and recreated only `kontext` and `kontext-worker`; `kontext-v2-db` and data volume were preserved.
- Rollback: `/opt/kontext-static-dashboard-backup-20260513T2025Z.tgz` on the VPS. Local pre-change backup: `C:\Users\Gaming PC\Desktop\Claude\Kontext\static_dashboard.backup-before-claude-design-20260513T2018`.
- Verification: local adapter syntax passed and normalized the existing mock snapshot. Local static refs check found 18 refs and 0 missing. Playwright screenshot could not run because browser binaries are not installed for local Playwright. Remote service check after rebuild: `kontext` healthy, root `/` served new index with `src/data-adapter.js` and `src/app.css`, `/src/data-adapter.js`, `/src/app.css`, `/fonts/Geist-VariableFont_wght.ttf`, `/data.js`, and `/health` all returned 200; filtered logs showed no errors.
- Next step: visually review the live dashboard, then declutter view-by-view instead of doing another broad redesign pass.

## 2026-05-14 - Kontext V2 categories backend groundwork

- Summary: Started the backend-owned Categories layer for Kontext V2. Added deterministic category inference/default category definitions, repository CRUD helpers, auto-assignment on memory upsert, safe category memory listing, HTTP category endpoints, a dashboard snapshot category payload, and MCP category tools.
- Files touched: `tools/kontext-v2/kontext_v2/categories.py`, `tools/kontext-v2/kontext_v2/repository.py`, `tools/kontext-v2/kontext_v2/http_api.py`, `tools/kontext-v2/kontext_v2/mcp_server.py`, `tools/kontext-v2/kontext_v2/mcp_bridge.py`, `tools/kontext-v2/tests/test_categories.py`.
- Safety: category list/detail APIs expose IDs, titles, metadata, types, tiers, statuses, and counts, not raw memory text. Writes remain gated behind `write_enabled`; read-only mode can list categories and category memory headers only.
- Verification: local syntax compile passed for changed modules/tests. Non-DB checks passed: `python -m pytest tests/test_categories.py::test_category_inference_uses_metadata_and_tier_without_database tests/test_mcp_contract_parity.py -q --disable-warnings` -> `8 passed`. Full DB-backed category tests could not run because the local disposable Postgres on `localhost:55434` is unavailable and Docker Desktop is not running.
- Deployment status: local only. Next step: run DB-backed tests with the local Postgres test service, then wire the frontend to consume `data.categories`/V2 category snapshot and deploy after green tests.

## 2026-05-14 - Kontext V2 categories tests and frontend wiring

- Summary: Continued the Categories backend work. Started Docker Desktop, brought up the disposable local Kontext V2 Postgres with `docker compose up -d postgres`, ran DB-backed tests, and wired the dashboard frontend to prefer backend-provided `data.categories` while keeping the previous derived category fallback.
- Backend verification: `python -m pytest tools/kontext-v2/tests -q --disable-warnings` from `C:\Tools\OB1` passed with `71 passed`. Running the same suite from `tools/kontext-v2` still fails three existing path-sensitive tests that expect repo-root relative paths; repo-root is the correct invocation.
- Frontend files touched: `C:\Users\Gaming PC\Desktop\Claude\Kontext\static_dashboard\src\data-adapter.js`, `C:\Users\Gaming PC\Desktop\Claude\Kontext\static_dashboard\src\view-settings.jsx`.
- Frontend verification: `node --check static_dashboard/src/data-adapter.js` passed; backend-category adapter smoke passed; `node C:\Tools\OB1\handoffs\kontext-dashboard-ui-check.cjs C:\Users\Gaming PC\Desktop\Claude\Kontext\static_dashboard` passed.
- Deployment status: local only. Production VPS deploy still needs explicit approval because it rebuilds/recreates Kontext services, even though the DB volume must be preserved.
- Follow-up: added `idx_memory_categories_category_id` to `tools/kontext-v2/kontext_v2/schema.py` for category-memory listing performance. Re-ran `python -m pytest tools/kontext-v2/tests -q --disable-warnings` from repo root: `71 passed`.

## 2026-05-14 - Kontext V2 Categories deployed

- Summary: Deployed the backend-owned Categories layer and dashboard category wiring to VPS `root@178.104.203.128:/opt/kontext`.
- Local verification before deploy: `python -m pytest tools/kontext-v2/tests -q --disable-warnings` passed with `72 passed`; `node --check static_dashboard/src/data-adapter.js`, backend-category adapter smoke, and `kontext-dashboard-ui-check.cjs` passed.
- Files deployed: `src/kontext_v2/`, `src/static_dashboard/`, `src/cloud/dashboard_snapshot.py`, and a follow-up `src/kontext_v2/repository.py` with `refresh_auto_category_assignments`.
- Remote backups: `/opt/kontext-categories-backup-20260513T233749Z.tgz`, `/opt/kontext-categories-cloud-backup-20260513T234142Z.tgz`, `/opt/kontext-categories-repository-backup-20260513T234420Z.tgz`.
- Remote deploy: rebuilt `kontext:latest` and recreated only `kontext` and `kontext-worker`; `kontext-v2-db` remained running and healthy, preserving its data volume.
- Backfill: ran `refresh_auto_category_assignments(limit=10000)` in the `kontext` container. Result: 77 memories refreshed, 181 category assignments. Category counts: archive 12, design 22, identity 48, projects 28, systems 35, workflow 36.
- Remote verification: `/health` 200 ok, `/api/v2/health` 200 ok, `/api/v2/categories` 200 with 6 non-empty categories, `/api/v2/dashboard/snapshot` 200 with 6 categories, `/data.js` 200 with 6 non-empty categories, `/` served the dashboard with `src/data-adapter.js`, static category JS files returned 200, and filtered logs for `kontext`/`kontext-worker` showed 0 error lines after deploy.
- Decision: Kontext remains mirror-read-only for memory writes; Categories are persisted and dashboard-visible but not a Mem0 cutover.

## 2026-05-14 - Kontext category management deployed

- Summary: Deployed category-only metadata writes and the dashboard category management UI to VPS `root@178.104.203.128:/opt/kontext`.
- Files deployed: `src/kontext_v2/http_api.py`, `src/cloud/api.py`, `src/static_dashboard/src/view-settings.jsx`, `src/static_dashboard/src/app.css`.
- Safety: general memory writes remain mirror-read-only; only category metadata create/delete/assign/unassign is enabled through `category_write_enabled`.
- Verification: local backend tests passed (`73 passed`), category focused tests passed (`6 passed`), frontend UI/static checks passed, remote py_compile passed, new Docker image `sha256:c56bedd35006` built, and only `kontext`/`kontext-worker` were recreated. VPS-local smoke returned `/api/v2/health` ok, `/api/v2/categories` count 6 with `category_write_enabled=true`, temp category create/delete ok, dashboard/static assets 200, and filtered logs showed 0 recent error lines.
- Rollback: `/opt/kontext-category-management-backup-20260514T103503Z.tgz`.
- Note: unauthenticated public requests to `kontext.ionutrosu.xyz` are intercepted by the Pangolin auth layer, so deploy verification used the VPS-local service at `127.0.0.1:8200`.

## 2026-05-14 - Kontext dashboard simple-mode pass (local)

- Summary: Implemented a local first-pass dashboard simplification for layman-first default mode. The inspector is closed by default, nav labels are simpler (`Home`, `Memories`, `Categories`, `Map`, `Cleanup`), Categories no longer shows the abstract folder map, category management is hidden behind `Edit categories`, and Map is larger with labels/details quiet by default.
- Files touched: `static_dashboard/src/app.jsx`, `static_dashboard/src/shell.jsx`, `static_dashboard/src/view-settings.jsx`, `static_dashboard/src/view-relations.jsx`, `static_dashboard/src/view-entries.jsx`, `static_dashboard/src/view-decay.jsx`, `static_dashboard/src/app.css`, `C:\Tools\OB1\handoffs\kontext-dashboard-ui-check.cjs`.
- Verification: `kontext-dashboard-ui-check.cjs` passed. Playwright full desktop/mobile pass saved screenshots/report to `C:\Tools\OB1\handoffs\kontext-screenshots\simple-final-20260514T120723Z`; all views clicked successfully, inspector stayed closed by default, category map count was 0, Map measured 1136x720 desktop and 282x560 mobile, no horizontal overflow, 0 console errors, 0 page errors, and 0 failed requests.
- Deployment status: local only. VPS deploy is pending explicit approval because it rebuilds/recreates live Kontext containers.

## 2026-05-14 - Kontext dashboard simple-mode deployed

- Summary: Deployed the dashboard simple-mode pass to VPS `/opt/kontext`. Default view is now simpler: inspector closed by default; nav labels use `Home`, `Memories`, `Categories`, `Map`, `Cleanup`; Categories uses folder cards without the abstract map; edit tools are behind `Edit categories`; Map is larger with quiet labels/details.
- Files deployed: `static_dashboard/src/app.jsx`, `static_dashboard/src/shell.jsx`, `static_dashboard/src/view-settings.jsx`, `static_dashboard/src/view-relations.jsx`, `static_dashboard/src/view-entries.jsx`, `static_dashboard/src/view-decay.jsx`, `static_dashboard/src/app.css`.
- Remote deploy: rebuilt `kontext:latest` as `sha256:5a08d6c0749ea710ad85b10a741f5f14f936ea667f25488ac7b0dc77f820c00e` and recreated only `kontext` and `kontext-worker`; `kontext-v2-db` remained running and healthy.
- Rollback: `/opt/kontext-static-dashboard-simple-backup-20260514T125149Z.tgz`.
- Verification: local `kontext-dashboard-ui-check.cjs` passed before deploy. Remote VPS-local smoke returned `/` 200, `/api/v2/health` ok, `/api/v2/categories` ok with 6 categories and category writes enabled, static JS/CSS 200, simplified nav labels present, old `Relations` nav label absent, `k-category-map` absent from Categories/CSS, Map title and hero graph present, labels default false, and filtered recent logs for `kontext`/`kontext-worker` showed 0 error lines.

## 2026-05-14 - Kontext V2 benchmark adapter milestone 1

- Summary: Added local benchmark adapter, tiny LoCoMo-style predict-only fixture, sanitized report writer, and CLI smoke runner for Kontext V2.
- Files touched: `tools/kontext-v2/kontext_v2/benchmarks/*`, benchmark tests, tiny fixture, `.gitignore`, `tools/kontext-v2/README.md`.
- Verification: focused benchmark tests passed (`5 passed`); full Kontext V2 tests passed (`78 passed`); CLI smoke produced `matched=3/3`; generated reports did not contain raw fixture text (`rg` returned no matches).
- Decisions: Kept benchmark data isolated with `source=benchmark`, `profile=benchmark`, and `is_live_memory=false`; no judged model calls; no live Mem0 writes; no VPS deploy.
- Next step: Add full LoCoMo adapter path against the upstream benchmark runner, then measure Kontext before changing retrieval scoring.
## 2026-05-14 - Kontext V2 real LoCoMo predict-only smoke

- Summary: Added real LoCoMo predict-only benchmark support on top of the local Kontext V2 benchmark adapter. The loader can download/cache the public LoCoMo `locomo10.json`, parse real `session_N` turns while ignoring `session_N_date_time` keys, map numeric categories, keep per-turn `dia_id` evidence IDs in benchmark metadata, and run evidence-ID predict-only matching without answerer/judge model calls.
- Files touched: `tools/kontext-v2/kontext_v2/benchmarks/fixtures.py`, `tools/kontext-v2/kontext_v2/benchmarks/adapter.py`, `tools/kontext-v2/kontext_v2/benchmarks/locomo_predict.py`, `tools/kontext-v2/kontext_v2/benchmarks/__init__.py`, benchmark tests/fixtures, `.gitignore`, `tools/kontext-v2/README.md`.
- Safety: cached real dataset is ignored under `tools/kontext-v2/benchmark-data/`; generated reports stay aggregate/sanitized and do not include raw conversations, raw questions, raw answers, or raw memory text. Benchmark DB rows remain isolated with `source=benchmark`, `profile=benchmark`, and `is_live_memory=false`.
- Verification: new RED test first exposed adapter filtering after top-k cap; fixed by filtering benchmark dataset/run/user in SQL before scoring. Focused benchmark tests passed (`8 passed`); full Kontext V2 tests passed (`81 passed`). Real LoCoMo smoke on conversation `0`, max `5` questions, top-k `20` produced `matched=2/5`; leak scan over benchmark reports found no raw sample text/name matches.
- Decision: Kontext's current lexical retrieval can run the real benchmark path, but the 2/5 smoke score shows the next useful work is retrieval quality, not more harness plumbing.
- Next step: run a larger predict-only slice and compare against Mem0/expected baseline, then implement retrieval fusion/decay improvements against the benchmark instead of guessing.
## 2026-05-14 - Kontext V2 LoCoMo top-k sweep and miss analysis

- Summary: Added a larger LoCoMo predict-only sweep mode for Kontext V2. The runner can evaluate multiple top-k cutoffs from one max-top-k search and writes sanitized miss-analysis reports with aggregate reasons per cutoff/category.
- Files touched: `tools/kontext-v2/kontext_v2/benchmarks/reporting.py`, `tools/kontext-v2/kontext_v2/benchmarks/locomo_predict.py`, `tools/kontext-v2/tests/test_benchmark_locomo_predict.py`, `tools/kontext-v2/README.md`, `project_log.md`.
- Safety: reports still contain no raw conversation text, raw questions, raw answers, or raw memory text. They include question IDs, categories, match booleans/counts, miss reason labels, result IDs, and result hashes.
- Verification: focused benchmark tests passed (`10 passed`); full Kontext V2 tests passed (`83 passed`). Real LoCoMo sweep smoke on conversation `0`, max `20` questions, top-k `5,10,20,50` produced `9/20`, `11/20`, `11/20`, and `14/20` evidence hits. Leak scan over the generated sweep report returned no raw sample text/name matches.
- Finding: Top-k 50 still had 6 evidence-not-retrieved misses. Lower cutoffs additionally had evidence-below-cutoff misses, so the next retrieval work should improve both candidate recall and ranking.
- Next step: implement retrieval-quality improvements against these miss classes, starting with stronger Postgres candidate recall and scoring boosts for evidence/date/entity terms.
## 2026-05-15 - Kontext V2 retrieval quality pass

- Summary: Tightened Kontext V2 retrieval for the real LoCoMo benchmark. Added session-context-aware ranking, stronger temporal/date handling, proper-noun boosts, and benchmark-run cleanup before ingest so repeat runs stay deterministic.
- Files touched: `tools/kontext-v2/kontext_v2/retrieval.py`, `tools/kontext-v2/kontext_v2/benchmarks/adapter.py`, `tools/kontext-v2/kontext_v2/benchmarks/locomo_predict.py`, `tools/kontext-v2/tests/test_benchmark_adapter.py`, `tools/kontext-v2/README.md`, `project_log.md`.
- Verification: benchmark adapter tests passed (`11 passed`); full Kontext V2 tests passed (`84 passed`). Real LoCoMo sweep smoke on conversation `0`, max `20` questions, top-k `5,10,20,50` improved to `11/20`, `12/20`, `13/20`, and `14/20` hits. Leak scan on the generated sweep report found no raw sample text/name matches.
- Finding: the improvement mostly recovered evidence-below-cutoff misses at lower cutoffs; the remaining gap is still evidence-not-retrieved, especially on multi-hop questions. Average benchmark latency rose, so the next pass should focus on more selective candidate filtering instead of more broad per-row scoring.
- Next step: build a cheaper candidate retrieval stage or session-level summary path that improves recall without pushing benchmark latency up further.

## 2026-05-15 - Kontext V2 session context benchmark pass

- Summary: Added session-level benchmark context rows alongside per-turn evidence rows for sourced LoCoMo sessions. This preserves exact evidence IDs while giving retrieval a session-memory candidate for multi-hop questions.
- Files touched: `tools/kontext-v2/kontext_v2/benchmarks/adapter.py`, `tools/kontext-v2/kontext_v2/benchmarks/locomo_predict.py`, `tools/kontext-v2/tests/test_benchmark_locomo_predict.py`, `tools/kontext-v2/README.md`, `project_log.md`.
- Verification: new RED test first failed because sourced sessions only wrote per-turn rows; after implementation it passed. Benchmark-focused tests passed (`12 passed`); full Kontext V2 tests passed (`85 passed`). Larger real LoCoMo sweep on conversations `0,1`, max `50` questions, top-k `5,10,20,50` improved from `27/50`, `30/50`, `34/50`, `37/50` to `40/50`, `41/50`, `44/50`, `45/50`. Leak scan on the generated sweep report found no raw report keys or long raw strings.
- Finding: session context rows are the biggest local retrieval gain so far. Remaining top-50 misses are 1 open-domain and 4 multi-hop evidence-not-retrieved cases; top-k 50 is now `90%` on the larger slice, with average search latency around `360 ms`.
- Next step: inspect the remaining 5 misses without exposing raw text, then decide whether to add a lightweight entity/fact extraction layer or stop optimizing the lexical benchmark path and compare against Mem0 on the same slice.

## 2026-05-15 - Kontext V2 Mem0-ranker parity pass

- Summary: Compared Kontext V2 retrieval against the local Mem0 remote-MCP ranker on the same isolated LoCoMo benchmark rows, then copied the one clear useful behavior: favoring session observations for multi-hop retrieval.
- Files touched: `tools/kontext-v2/kontext_v2/retrieval.py`, `tools/kontext-v2/kontext_v2/benchmarks/adapter.py`, `tools/kontext-v2/tests/test_benchmark_adapter.py`, `tools/kontext-v2/README.md`, `project_log.md`.
- Verification data: safe local Mem0-ranker comparison on conversations `0,1`, max `50` questions showed Kontext at `40/50`, `41/50`, `44/50`, `45/50` for top-k `5,10,20,50`; Mem0 local ranker scored `30/50`, `34/50`, `38/50`, `38/50`. Mem0 had 2 top-50 hits Kontext missed, both multi-hop session-observation cases.
- Change: added `observation_kind` to benchmark search result metadata and gave `observation_kind=session` a small retrieval boost. New RED test first failed on missing/untiebroken session observation ranking, then passed after the change.
- Result: real LoCoMo sweep on conversations `0,1`, max `50` questions improved to `42/50`, `45/50`, `48/50`, `49/50`; top-k 50 is now `98%`. Report leak scan stayed clean. The only remaining top-50 miss is multi-hop with no evidence row present in the isolated benchmark rows, so it is not a ranking miss.
- Next step: stop LoCoMo micro-tuning for now and run broader repeated shadow parity on real project-memory queries plus exact-ID freshness/write-policy checks before any cutover.

## 2026-05-15 - Kontext V2 LoCoMo parity deployed to VPS

- Summary: Deployed the latest Kontext V2 LoCoMo retrieval parity work to VPS `root@178.104.203.128:/opt/kontext` and recovered a runtime dependency regression from the deploy.
- Local verification before deploy: `python -m pytest tools/kontext-v2/tests -q` passed with `86 passed`. Real LoCoMo sweep on conversations `0,1`, max `50` questions, top-k `5,10,20,50` scored `42/50`, `45/50`, `48/50`, and `49/50` with no raw benchmark leak in reports.
- Remote deploy: synced `tools/kontext-v2/kontext_v2`, `parity_eval.py`, and `requirements.txt` to `/opt/kontext/src`, rebuilt `kontext:latest`, and recreated only `kontext` and `kontext-worker`; `kontext-v2-db` stayed on the same container ID prefix `ab27e0340a5fb234ab11b24f` and its volume was not recreated.
- Rollback: `/opt/kontext-backup-20260514T224055Z-locomo-parity.tgz`.
- Fix during deploy: the first rebuilt app restarted because local `requirements.txt` had overwritten older remote runtime dependencies used by `cloud/*`. Restored the missing runtime packages in `/opt/kontext/src/requirements.txt` from the rollback-era set (`anthropic`, `jinja2`, `msgpack`, `pydantic`, `PyNaCl`, `sentence-transformers`, `zstandard`) and rebuilt image `sha256:68f2cbb4fbfe...`.
- Remote verification: `kontext` healthy, `kontext-worker` running, `/api/v2/health` ok, `/api/v2/sync/status` ok, `/api/v2/tools` returned 9 tools, key deployed modules passed `py_compile`, and filtered recent logs showed no `ModuleNotFound`, traceback, import, syntax, or error lines.
- Shadow report: fixed stale `/opt/kontext/scripts/kontext_shadow_report.py` local `/health` check to use `/docs`, then ran `/opt/kontext/scripts/kontext_shadow_report.py`. Latest report `/opt/kontext/reports/latest.json` is `ok=true`; dry-run sync saw 20 source rows with 0 created, 0 updated, 20 unchanged, 0 skipped; live MCP reliability under codex profile scored Kontext `12/12` and Mem0 `11/12`.
- Decision: Kontext remains `shadow_read_only_mem0_source_of_truth`; no cutover and no memory write path change.
- Next step: run repeated real project-memory shadow parity/exact-ID freshness checks over time, then decide whether to improve Mem0 metadata or Kontext ranking from observed real-query misses.

## 2026-05-15 - Kontext shadow parity and exact-ID freshness pass

- Summary: Ran the next real-project shadow checks after the LoCoMo parity deploy. Three fresh VPS shadow reports ran successfully and exact-ID freshness was checked across the full mirrored Kontext set without printing raw memory text.
- Repeated shadow results: reports `20260514T230329Z`, `20260514T230346Z`, and `20260514T230402Z` were all `ok=true`; dry-run sync processed 20 rows each time with 0 created, 0 updated, 20 unchanged, and 0 skipped. Live MCP reliability stayed stable: Kontext `12/12`, Mem0 `11/12`; the repeated Mem0 miss was `vocality-current-state` due to memory-type expectations, not empty retrieval.
- Exact-ID finding: the first exact-ID freshness run reported 0/77 fresh because the checker compared full Mem0 transport/source hashes. Diagnosis showed Mem0 fetch rows include volatile wrapper fields while mirrored text and metadata matched. Fixed `compare_exact_id_freshness` to use a canonical text+metadata hash for freshness while keeping raw source-hash comparison as a diagnostic.
- Verification: focused exact-ID test passed (`1 passed`), full Kontext V2 suite passed (`86 passed`), remote `kontext_v2/parity.py` deployed, `kontext` and `kontext-worker` recreated, API v2 health/sync/tools returned 200, `py_compile` passed, and filtered logs were clean.
- Exact-ID result after fix: 77 checked, 77 fresh, 0 stale, 0 type mismatches, 0 domain-overlap misses. All 77 still have raw source-hash mismatch, confirming the old check was too strict for Mem0 fetch-row wrappers.
- Miss classification: Kontext ranking does not need more LoCoMo-style tuning right now. The current real-project gap is Mem0/eval metadata taxonomy for Vocality: Mem0 returned relevant Vocality rows, but their `memory_type` was `decision` while the eval expected workflow/business/project-state types.
- Next step: add exact-ID freshness into the scheduled shadow report, then decide whether to broaden the Vocality eval expected types or enrich those Mem0 memories with a more precise workflow/business_context type.

## 2026-05-15 - Kontext scheduled freshness and Vocality eval taxonomy deployed

- Summary: Added exact-ID freshness to the scheduled Kontext shadow report and fixed the bounded Vocality eval taxonomy miss without changing Mem0 memories.
- Files touched: `tools/kontext-v2/scripts/kontext_shadow_report.py`, `tools/kontext-v2/kontext_v2/exact_id_freshness_cli.py`, `tools/kontext-v2/tests/test_shadow_report.py`, `tools/kontext-v2/tests/test_mcp_reliability_eval.py`, `tools/mem0-remote-mcp/retrieval_eval_cases.v1.13.json`, and `project_log.md`.
- Exact-ID reporting: shadow reports now run `kontext_v2.exact_id_freshness_cli` inside the `kontext` container and include an aggregate-only `exact_id_freshness` block. The report `ok` gate now fails if exact-ID freshness fails. No raw memory text, profile tokens, or remote env values are written to the report.
- Vocality eval fix: added `decision` as an accepted `memory_type` for the `vocality-current-state` eval case because live Mem0 retrieved relevant Vocality state/strategy rows typed as decisions. This fixes the eval expectation rather than mutating exact memories.
- Remote deploy: backed up `/opt/kontext` script/module state, deployed the shadow report script, deployed `exact_id_freshness_cli.py`, rebuilt `kontext:latest`, recreated only `kontext` and `kontext-worker`, then deployed the updated retrieval eval case file to `/opt/kontext/src/retrieval_eval_cases.v1.13.json`. `kontext-v2-db` stayed running and healthy.
- Remote verification: `/api/v2/health`, `/api/v2/sync/status`, and `/api/v2/tools` returned 200; container `py_compile` passed; filtered logs were clean. Fresh report `/opt/kontext/reports/kontext-shadow-report-20260514T233452Z.json` returned `ok=true`, `sync_dry_run=true`, `exact_id_freshness=true`, Kontext `12/12`, and Mem0 `12/12`.
- Local verification: RED tests first failed for missing scheduled exact-ID reporting and missing `decision` in the Vocality case. After implementation, focused tests passed and full Kontext V2 suite passed with `89 passed`.
- Decision: no direct Mem0 memory updates were needed. The current shadow gate is stronger now: retrieval parity, dry-run sync, and exact-ID freshness are all checked by the scheduled report.
- Next step: let scheduled reports accumulate, then use real misses rather than benchmark micro-tuning to decide future Kontext ranking or metadata work.
