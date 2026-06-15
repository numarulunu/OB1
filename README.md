# Kontext

Kontext is Ionut's long-term memory and project-continuity infrastructure. It is built to give Codex, Claude, ChatGPT, and related workflows a reliable second-brain backend with low-noise retrieval, safe ingestion, project-state continuity, correction handling, and memory hygiene.

This repository contains the working Kontext V2 implementation and the supporting evidence, tests, hooks, dashboards, and migration tooling around it. Legacy Open Brain, OB1, and Mem0 material may still exist in this repo for compatibility, migration history, upstream provenance, or rollback evidence. They are not the primary product identity.

## Current Boundary

- Kontext V2 is the primary memory backend.
- Legacy Mem0 is backup, compatibility, and rollback only unless a task explicitly asks for parity or restore work.
- Open Brain/OB1 references are retained only where they describe upstream packages, historical logs, fixtures, or compatibility paths.
- VPS state at `/opt/kontext` is the deployment source of truth, but repo cleanup should not mutate the VPS unless a task explicitly requires it.

## Repo Map

| Path | Purpose |
| --- | --- |
| `tools/kontext-v2/` | Kontext V2 backend, MCP bridge, benchmark/evaluation tooling, retirement gates, and tests |
| `tools/kontext-v2/client_hooks/` | Codex/Claude hook and MCP client helpers |
| `docs/kontext-v2-benchmark-matrix.md` | Kontext benchmark and cutover evidence history |
| `project_log.md` | Append-only project activity log |
| `AGENTS.md` and `CLAUDE.md` | Agent operating rules for this repo |
| `resources/`, `recipes/`, `skills/`, `integrations/` | Retained upstream/compatibility material unless a specific task says otherwise |

## Local Setup

Use a Python virtual environment outside Git tracking:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r tools/kontext-v2/requirements.txt
```

Run focused checks from the repo root:

```bash
python -m pytest tools/kontext-v2/tests -q
python -m py_compile tools/kontext-v2/scripts/*.py
git diff --check
```

Some tests and scripts depend on local or VPS-only services. If a check requires private bundles, API keys, or `/opt/kontext`, do not fake the result; document what could not be verified.

## Safety Rules

- Do not commit `.env` files, API keys, tokens, private benchmark bundles, raw memory dumps, auth/session files, or SSH keys.
- Do not ingest raw chats, logs, transcripts, or secret-bearing files into Kontext.
- Do not bulk-delete memories or Archive/category rows. Cleanup must be exact-ID or benchmark-namespace scoped with backups.
- Do not treat transport success as quality success. Retirement/cutover claims require the relevant verification report.
- Keep docs and code clear about Kontext being primary; legacy Mem0 and Open Brain/OB1 are compatibility/provenance unless explicitly in scope.

## For A New Developer

Start with `tools/kontext-v2/README.md`, then read the latest entries in `project_log.md`. For code changes, prefer narrow tests around the touched script/module. For cleanup work, stage only intentional files and run a secret-shape scan before pushing.

## License

See `LICENSE.md`. Some retained upstream material may carry its original provenance and contribution context; do not remove that history unless the task is explicitly a legal/provenance cleanup.
