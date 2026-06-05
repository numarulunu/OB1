# Kontext V2 Shadow Cutover Report

Date: 2026-05-13
Status: shadow-ready, not cutover-ready

## Executive Summary

Kontext V2 is deployed on the VPS and is healthy as a read-only mirror. It now matches Mem0 on the current 12-case live retrieval eval under the Codex profile, but it should not replace Mem0 yet.

Recommendation: keep Mem0 as the source of truth and keep Kontext in shadow mode while collecting more real-client evidence and improving parity around writes, sync depth, project observations, dashboard/category UX, and tool coverage.

## Current Deployment

- VPS path: `/opt/kontext`
- Public service: `kontext.ionutrosu.xyz` behind existing access controls
- Runtime mode: `mirror_read_only`
- Writes: disabled
- Services:
  - `kontext`: running, healthy
  - `kontext-v2-db`: running, healthy
  - `kontext-worker`: running
- Last deploy rollback archive: `/opt/kontext-backup-20260512T235157Z-v2-deploy.tgz`

## Fresh Checks

Fresh remote status checks on 2026-05-13:

- `/health`: OK
- `/api/v2/health`: OK
- `/api/v2/sync/status`: OK
- `/api/v2/tools`: OK
- Mem0 `/health`: OK

Kontext V2 health summary:

- `mode`: `mirror_read_only`
- `writes.enabled`: `false`
- mirrored memory count reported by API: `77`
- latest sync status: `ok`
- latest sync rows seen: `20`
- latest sync changes: `created=0`, `updated=1`, `unchanged=19`, `error_count=0`

Kontext exposed tools:

- `search`
- `fetch`
- `ingestion_status`
- `project_search`
- `project_timeline`
- `project_fetch`
- `project_file_context`

Mem0 exposed more live tools than Kontext, including direct writer and maintenance tools. That gap is intentional while Kontext remains read-only.

## Live Retrieval Eval

Fresh live MCP reliability eval under Codex profile, top_k=5, 12 cases:

| Service | Passed | Pass Rate | Errors | Tool Count |
| --- | ---: | ---: | --- | ---: |
| Kontext | 12/12 | 1.0 | none | 7 |
| Mem0 | 12/12 | 1.0 | none | 15 |

Overlap comparison:

- shared-any cases: 3/12
- shared-first cases: 1/12
- no-overlap cases: 9/12

Interpretation: Kontext is retrieving valid answers for all eval cases, but it often retrieves different IDs than Mem0. That is acceptable for shadow mode, but not enough alone for cutover. Before cutover, we need confidence that the different results are semantically correct, fresh, and stable across real workflows.

## Cutover Gates

Do not point Claude/Codex at Kontext as the primary memory MCP until these are true:

1. Retrieval remains at or above Mem0 across repeated live evals.
2. Exact-ID freshness checks pass after recent Mem0 writes and updates.
3. Write-path dry runs match policy for save, update, flag, and override flows.
4. Project observation tools return useful rows after real agent sessions, not only empty/placeholder outputs.
5. Sync depth and scheduling are reliable enough that Kontext is not behind Mem0 during active work.
6. Dashboard/category/dossier UX is useful enough to justify switching architecture, not only matching backend retrieval.
7. Several days of shadow traffic show no stale-context regressions.
8. Rollback remains simple: switch clients back to Mem0 MCP without database cleanup.

## Scheduled Shadow Reporting

A VPS-side scheduled shadow report is now installed:

- runner: `/opt/kontext/scripts/kontext_shadow_report.py`
- cron: `/etc/cron.d/kontext-shadow-report`
- cadence: daily at `03:17` server time
- reports: `/opt/kontext/reports/latest.json` and timestamped `kontext-shadow-report-*.json` files

The report runs health checks, a capped dry-run mirror sync report, and the live MCP reliability eval under the Codex profile. The report is aggregate-only and excludes secrets, remote `.env` contents, raw chats, raw memory text, raw queries, top rows, and top IDs.

First verified report on 2026-05-13:

- report `ok=true`
- endpoint checks OK
- sync dry-run OK, `processed_rows=20`
- live MCP reliability: Kontext `12/12`, Mem0 `12/12`
- sanitization check: zero env secret-value hits and zero forbidden raw keys
## Current Recommendation

Keep the architecture as:

- Mem0: live source of truth
- Kontext V2: read-only shadow mirror, eval target, category/dashboard candidate
- Hooks: continue reminding agents to use memory organically, without hot-path Qwen ingestion
- Dream cleanup: CLI-first, user-approved

Next recommended work:

1. Add scheduled Kontext sync/eval reporting so shadow evidence accumulates automatically.
2. Add project-observation ingestion into Kontext from real hook/project events.
3. Build the category/dossier dashboard layer that makes Kontext valuable beyond Mem0 parity.
4. Run a second live eval after real use, then compare drift.
5. Only after those pass, prepare a reversible client-level cutover test for one client/profile.

## Decision

No cutover now. Kontext V2 is deployed and worth shadowing, but Mem0 stays source of truth.
