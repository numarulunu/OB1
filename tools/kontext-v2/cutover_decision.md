# Kontext V2 Cutover Decision

Owner: Ionut
Last refreshed: 2026-06-02

Current production posture: Kontext V2 is the active source of truth for normal Codex, Claude, and ChatGPT-visible memory routes. Legacy Mem0 remains preserved as rollback/export data only. The previous 8-gate retirement report and observer are green, but final Mem0 decommission/delete now also requires a refreshed typed-state trial gate, a real-embedding policy decision, and explicit owner approval.

## Current Mode

- Kontext service mode: primary source of truth for normal memory routes.
- Legacy Mem0: runtime dependency disabled for normal flow; preserved for rollback/export until final approval.
- Production cutover flag (`production_cutover_enabled`): not used as hard-delete approval.
- Write posture: agent-first, no routine automatic ingestion. Hooks heartbeat-only.
- Final destructive action: not approved. Use `docs/superpowers/reports/2026-06-02-kontext-mem0-final-decommission-approval.md`.

## Final Retirement Evidence

- Previous retirement gate: `/opt/kontext/reports/kontext-mem0-retirement-gate-replacement-20260601T212201Z.json`, `ok=true`, `pass=8`, `fail=0`. This predates the typed-state trial gate and is no longer sufficient by itself for final deletion approval.
- Observer latest: `/opt/kontext/reports/cutover-observer/latest.json`, `ok=true`, `pass=2`, `fail=0`, `warn=0`.
- Pre-delete recheck: `/opt/kontext/reports/kontext-mem0-retirement-gate-final-predelete-20260601T212604Z.json`, `pass=8`, `fail=0`. This is historical pre-typed-state evidence.
- Fresh Kontext DB backup: `/opt/kontext/backups/20260601T211908Z-final-kontext-db-refresh/kontext_v2_pg_dumpall.sql.gz`.
- Fresh legacy Mem0 export: `/opt/mem0-remote-mcp/backups/20260601T211908Z-final-mem0-export-refresh/mem0_pg_dumpall.sql.gz`.
- Both backup directories include SHA256 manifests.
- Final approval packet: `/opt/kontext/reports/kontext-mem0-final-decommission-approval-20260602.md`.

## Required Evidence Before Cutover

Status legend: ✅ shipped + verified, 🟡 partial / in progress, ❌ not started.

- ✅ Fresh mirror sync report shows zero unacceptable drift (rolling exact-ID freshness checks pass; latest sanitized shadow report `ok=true`).
- ✅ MCP canary passes for Codex and Claude routes (apply-mode write canary 2026-05-23, codex desktop cutover 2026-05-24, claude writes enabled 2026-05-24).
- ✅ Regression canary scaffold deployed (`scripts/regression_canary.py`; 2026-05-26).
- ✅ Protected autobiographical history hardening complete (retention.py boundary normalization with dash/underscore collapse, 2026-05-26).
- ✅ Retrieval recall only: OB1 base 12/12 MRR 1.0, expanded 36/36 MRR 1.0, LoCoMo top-50 100%, LongMemEval top-50 100%, BEAM 10M hard offsets 7-8 top-k50 8/8 (with opt-in cross-encoder + category-scoped normalization). This proves evidence availability, not judged answer correctness.
- ✅ No unauthenticated raw-memory routes are exposed (SMAC P0 hardening 2026-05-26 added token requirement to `/dashboard/snapshot` and `/fetch/{id}`).
- 🟡 Authorization-header migration: `Authorization: Bearer` route accepts header tokens (2026-05-26); legacy `/api/v2/mcp/{token}` path is still active for client compatibility and not removed.
- 🟡 Real-usage shadow query logging: retrieval shadow telemetry shipped (`retrieval_shadow.py` + sanitized `query_features` recorded into `retrieval_queries.filters`, 2026-05-26). The shadow report CLI now exposes zero-result, high-latency, and repeated-query breakdowns (2026-05-26 — current batch); calibration runs against real production telemetry still pending.
- 🟡 Judged answer smoke only: weighted accuracy 1.0 was verified across 3 micro-slice questions (LoCoMo1 + LongMemEval1 + BEAM1, $0.002253 spend). This N=3 smoke is not a retirement proof.
- 🟡 Paired BEAM hard slice: Kontext and legacy Mem0 tied `4/6` on the same six question hashes. This means no observed legacy-Mem0 advantage on the shared hard frontier; it does not prove BEAM parity or BEAM pass quality.
- 🟡 Typed-state trial evidence: pending refreshed full hard-slice no-paid run with state flags enabled. Future retirement-gate input must include `projection_selection_loss_questions=0` and `state_event_extraction_loss_questions=0`.
- 🟡 Placeholder `VECTOR(16)` embedding column is no longer a silent footgun: `embedding` is now nullable, placeholder text columns have safe defaults, and `embedding_dirty` + `embedding_text_hash` are in place so any future batch-embedding job can write rows without crashing on the legacy NOT NULL constraint (2026-05-26 — current batch). **Real embeddings remain not wired and are a deletion blocker until a separate embedding policy is approved.**

## Known Blockers

- Final Mem0 decommission/delete requires explicit owner approval. The approval packet names the required approval phrase and hard stops.
- A refreshed retirement gate must include the typed-state trial gate. Existing `8/8` retirement artifacts are pre-gate evidence and cannot authorize deletion alone.
- Real embedding wiring remains unresolved. Placeholder-vector safety fixes are not a semantic replacement for real embeddings.
- BEAM remains a monitored risk, not a hard deletion blocker by itself. The paired `4/6` vs `4/6` result is evidence against a legacy-Mem0 advantage, not proof that Kontext answers BEAM current-state questions correctly.
- Docker volume deletion requires a separate explicit approval even after any service stop or directory archival.
- Legacy MCP path-token URLs may still exist for compatibility. Do not remove them as part of Mem0 decommission unless a separate client-auth migration plan is approved.
- Dashboard raw body bulk exposure is closed for the Kontext V2 snapshot path. Exact memory body preview remains available only through authenticated exact-memory routes.
- Real graph extraction beyond `_derived_relations` (TF-IDF over shared tags/categories) is not built. This is not a Mem0-retirement blocker.

## Rollback Runbook

Rollback trigger:

- Any cutover observer report with `recommendation=rollback_or_hold_mem0_primary`.
- Any stale input-freshness failure on scorecard or cutover-readiness evidence.
- Any protected autobiographical-history write, delete, archive, stale-flag, or cold-downgrade regression.
- Any loss of Kontext health, Mem0 health, or read-after-write verification during a write canary.

Immediate actions:

1. Keep `production_cutover_enabled=false`. Do not flip the cutover flag while any blocker exists.
2. Stop new production cutover work. Keep current Codex/Claude agent-first writes scoped to the already-approved primary profiles only.
3. Switch any experimental clients back to the legacy Mem0 connector/profile that was active before the experiment.
4. Preserve evidence: keep the failing observer report, readiness report, scorecard, and rollback-drill report under `/opt/kontext/reports/`.
5. Do not hard-delete Kontext data, database volumes, audit logs, or Mem0 backup data during rollback.

Restore path:

1. Use the newest relevant backup under `/opt/kontext/backups/` for application files only.
2. Recreate only the `kontext` and `kontext-worker` containers after restoring files. Do not recreate or wipe the Postgres volume.
3. Preserve `/opt/kontext/.env`, `/opt/kontext/docker-compose.yml`, `/opt/mem0-remote-mcp/.env`, and `/opt/mem0-remote-mcp/data`.
4. If Mem0 rollback is needed, use the preserved legacy Mem0 service as the source of truth; do not reverse-publish unreviewed Kontext rows into Mem0.

Verification after rollback:

1. `/api/v2/health` reports `ok=true` locally on the VPS.
2. Legacy Mem0 MCP health reports healthy without printing tokens or raw memory text.
3. Codex and Claude can retrieve through the selected primary connector.
4. The cutover observer exits nonzero or reports hold when evidence is stale or failed.
5. Protected autobiographical-history synthetic probes still block cleanup/delete/archive/stale/cold pressure.

## Embedding Architecture Rule (deferred)

When real embeddings are wired in a future batch:

- Generation must be **batch-only**. The hot write path sets `embedding_dirty=true` and returns; a cron job (`embed_refresh_cli.py`, not yet built) processes batches of dirty rows.
- `embedding_text_hash` ties each stored vector to the exact source text it was computed from, so dirty detection is precise across content edits.
- If the chosen model has a different dimension than the legacy 16-dim placeholder, add a new column (e.g., `embedding_384 VECTOR(384)`) alongside; do NOT alter the placeholder column type.
- Storage cost: 384-dim grows storage ~24× per row; 768-dim ~48×. Gate model selection by the projected storage cost on the live `memories` count.
- Cross-encoder rerank in production is gated on threshold env var (`KONTEXT_RERANK_TOP_K_THRESHOLD`, not yet wired) — never always-on, always with an explicit cost cap.

## Review Cadence

- Review after each deploy that changes retrieval, memory lifecycle, MCP auth, or dashboard raw-memory access.
- If all required evidence is green for one week of normal use, schedule an explicit cutover review instead of silently drifting.
- Next scheduled review: when retrieval shadow telemetry has accumulated at least 100 sanitized real-query samples across both Codex and Claude profiles, AND `body=text` is removed from the dashboard snapshot payload.

## Decision Log

- 2026-05-26: Initial decision artifact created. Status: no cutover.
- 2026-05-26 (later): Protected-history boundary hardening + Authorization-header migration shipped (first Mastermind roadmap batch). Status: no cutover.
- 2026-05-26 (later): DB performance batch shipped (tsquery push-down for project_observations, keyset pagination, partial index `idx_memories_active_hot`). Status: no cutover.
- 2026-05-26 (later): Retrieval shadow telemetry batch shipped (sanitized `query_features` recorded; report CLI emits aggregates). Status: no cutover.
- 2026-05-26 (later): Embedding placeholder footgun closed (nullable, dirty flag, text_hash); shadow report extended with zero-result / high-latency / repeated-hash breakdowns; this decision file refreshed to reflect actual shipped state. Status: no cutover. The cutover gate stayed closed because judged accuracy depth was still too small and shadow telemetry needed accumulated samples.
- 2026-05-26 (Batch 7B): Cutover observer now checks input file freshness, readiness/scorecard reports include `generated_at`, the observer runner passes a max input age, and this runbook defines rollback triggers and verification. Status: no cutover. The latest live observer correctly holds cutover because one scorecard input is stale and strict readiness still has failing gates.
- 2026-06-02: Retirement gate refreshed around the replacement-quality proof. Kontext is source of truth, legacy Mem0 runtime dependency is disabled for normal flow, fresh Kontext/Mem0 backups exist with SHA256 manifests, and observer latest is green. Status: final Mem0 decommission/delete pending explicit owner approval only.
- 2026-06-02 (SMAC validity update): BEAM is downgraded from hard blocker to monitored risk because the paired hard slice tied `4/6` vs `4/6`, but that tie is not a parity proof. The deletion gate now requires typed-state trial evidence and a real-embedding policy decision before any final Mem0 deletion approval.
