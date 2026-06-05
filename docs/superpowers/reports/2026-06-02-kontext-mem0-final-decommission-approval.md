# Kontext Mem0 Final Decommission Approval Packet

Date: 2026-06-02

Status: ready for owner review, not approved for destructive execution.

## Current Decision

Kontext is the active source of truth from the retirement gate's point of view.
Legacy Mem0 data and rollback artifacts must remain preserved until Ionut gives
explicit final decommission approval.

The required approval phrase is:

`I approve final Mem0 decommission/delete using the 2026-06-02 approval packet.`

Do not execute destructive steps without that exact approval or a clearer later
approval naming final Mem0 decommission/delete.

## Verified Green Evidence

- Retirement gate: `/opt/kontext/reports/kontext-mem0-retirement-gate-replacement-20260601T212201Z.json`
- Retirement gate result: `ok=true`, `pass=8`, `fail=0`
- Observer latest: `/opt/kontext/reports/cutover-observer/latest.json`
- Observer result: `ok=true`, `pass=2`, `fail=0`, `warn=0`
- Observer recommendation: `mem0_runtime_can_be_disabled_after_final_delete_approval`
- No active judged benchmark process at the last live check.
- Kontext health evidence: mirror count `209227` from Kontext MCP status.
- ID-set sync evidence: Mem0-visible IDs `4238`, Kontext matches `4238`, missing `0`.
- Quality evidence:
  - LoCoMo30 Kontext: `28/30`, raw-payload hits `0`, real model calls.
  - LongMemEval30 selector: `24/30`, raw-payload hits `0`, real model calls.
  - Paired hard BEAM: Kontext `4/6` vs legacy Mem0 offline `4/6`, raw-payload hits `0`, real model calls.
- MCP canary evidence: `18` tools, `0` writes.
- Write audit evidence: OK, read-after-write OK, error count `0`.
- Legacy Mem0 gate evidence: writes disabled, runtime dependency disabled for normal flow, recent writes `0`, recent reads `0`.

## Fresh Backup Evidence

- Kontext DB dump: `/opt/kontext/backups/20260601T211908Z-final-kontext-db-refresh/kontext_v2_pg_dumpall.sql.gz`
- Kontext dump size: `435367436` bytes.
- Legacy Mem0 export: `/opt/mem0-remote-mcp/backups/20260601T211908Z-final-mem0-export-refresh/mem0_pg_dumpall.sql.gz`
- Legacy Mem0 export size: `30180867` bytes.
- Both backup directories include `SHA256SUMS.txt`.

Recommended before destructive deletion: copy both backup directories off the VPS
to a separate storage location and verify the SHA256 manifests there. The current
retirement gate only proves on-server backup/export existence.

## Non-Destructive Final Recheck

Run these before any destructive command:

```bash
python3 /opt/kontext/scripts/kontext_mem0_retirement_gate.py \
  --input /opt/kontext/reports/kontext-mem0-retirement-gate-replacement-20260601T212201Z-input.json \
  --output /opt/kontext/reports/kontext-mem0-retirement-gate-final-predelete-$(date -u +%Y%m%dT%H%M%SZ).json

python3 /opt/kontext/scripts/kontext_cutover_observer.py \
  --retirement-gate /opt/kontext/reports/kontext-mem0-retirement-gate-replacement-20260601T212201Z.json \
  --label mem0-final-predelete \
  --output /opt/kontext/reports/cutover-observer/kontext-cutover-observer-final-predelete-$(date -u +%Y%m%dT%H%M%SZ).json \
  --max-input-age-hours 24
```

Proceed only if both remain `ok=true`.

## Destructive Steps Requiring Explicit Approval

These are intentionally not executed by this packet.

1. Stop legacy Mem0 MCP service:

```bash
cd /opt/mem0-remote-mcp
docker compose stop memory-mcp
```

2. Verify Kontext still answers through active clients and observer remains green.

3. Archive legacy Mem0 app/config/data directories rather than deleting first:

```bash
mkdir -p /opt/decommissioned
mv /opt/mem0-remote-mcp /opt/decommissioned/mem0-remote-mcp-$(date -u +%Y%m%dT%H%M%SZ)
```

4. Do not remove Docker volumes until there is a separate approval explicitly
   naming volume deletion. Directory archival is reversible; volume deletion is not.

5. After 24-72 hours of clean Kontext-only operation, owner may separately approve
   Docker image/container/volume cleanup.

## Rollback

If Kontext health, client routing, write audit, or observer status fails after
service stop/archive:

1. Move archived directory back to `/opt/mem0-remote-mcp`.
2. Start the legacy service with `docker compose up -d`.
3. Keep Kontext DB volumes, Mem0 DB volumes, and both backup/export artifacts intact.
4. Re-run the retirement observer and preserve the failing report.

## Hard Stops

- No raw memories, benchmark payloads, prompts, judge text, secrets, tokens, profile URLs, or `.env` contents may be printed.
- No Mem0 hard deletion without explicit approval.
- No Docker volume deletion without a separate explicit approval naming volume deletion.
- No deletion if the latest retirement gate or observer is stale or not green.
