# Contributing to Kontext

Kontext is the active memory backend and project-continuity system in this repo. Treat legacy Mem0, Open Brain, and OB1 material as compatibility, migration, rollback, or upstream-provenance context unless a task explicitly says otherwise.

## Before You Change Code

1. Read `README.md`, `CLAUDE.md`, `AGENTS.md`, and the relevant `tools/kontext-v2/README.md` section.
2. Check the latest relevant entries in `project_log.md`.
3. Define the verification you will run before editing.
4. Keep changes narrow. Do not refactor adjacent runtime code just because it looks messy.

## Where Work Usually Goes

| Area | Path |
| --- | --- |
| Kontext backend, MCP bridge, gates, benchmark tooling | `tools/kontext-v2/` |
| Client hooks and local connector helpers | `tools/kontext-v2/client_hooks/` |
| Benchmark/cutover evidence notes | `docs/kontext-v2-benchmark-matrix.md` and `project_log.md` |
| Retained upstream compatibility material | `recipes/`, `skills/`, `resources/`, `integrations/`, `dashboards/` |

## Safety Rules

- Never commit secrets, `.env` files, auth/session files, private keys, private benchmark bundles, raw memory dumps, or API tokens.
- Never paste secrets or raw private payloads into reports, logs, tests, fixtures, or docs.
- Do not mutate VPS `/opt/kontext` unless the task explicitly requires deployment or server verification.
- Do not bulk-delete memories, Archive/category rows, project logs, or historical evidence. Cleanup must be scoped, backed up, and exact-ID or benchmark-namespace based.
- Do not treat old Mem0/Open Brain/OB1 naming as current unless the file is explicitly upstream compatibility or history.

## Verification

Use focused checks for the touched area. Common local checks from the repo root:

```bash
python -m pytest tools/kontext-v2/tests -q
python -m py_compile tools/kontext-v2/scripts/*.py
git diff --check
```

For docs/ignore-only changes, tests may be unnecessary, but still run whitespace and secret-shape scans on the staged diff.

## Project Log

For meaningful work, append a concise `project_log.md` entry with:

- Date and summary
- Files touched
- Verification run
- Decisions or review-only follow-ups

## Pull Requests

Use clear commit messages that describe the operational change. Before pushing, confirm:

- The working tree contains only intentional changes.
- The staged diff contains no secrets or private payloads.
- Kontext V2 remains described as primary.
- Legacy Mem0/Open Brain/OB1 references are either removed from clone-facing docs or explicitly marked as compatibility/provenance.
