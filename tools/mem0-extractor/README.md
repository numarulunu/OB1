# Mem0 Extractor

Dry-run-first extraction layer for turning captured AI/session text into curated Mem0 memory proposals.

This tool is external to Mem0. It does not modify upstream Mem0 code, so Mem0 updates will not wipe this layer.

## What It Does

1. Reads a `.jsonl`, `.json`, or `.txt` session file.
2. Runs deterministic prefiltering based on the Human Memory V2 policy.
3. Drops obvious sludge such as transcript process artifacts, tool logs, generic vocal lesson/anatomy chatter, and low-signal rows.
4. Builds memory candidates with `domains`, `memory_type`, `signal_strength`, `memory_tier`, and source IDs.
5. Optionally sends candidates to an OpenAI-compatible LLM endpoint for distilled JSON proposals.
6. Uses a type decision tree and deterministic repair so `pattern` stays a last-resort memory type.
7. Caches LLM responses by prompt/model hash to avoid paying twice for the same extraction.
8. Writes a local report first with metadata-quality and domain-specific gates.
9. A separate apply command can plan save/update/skip actions and only mutates Mem0 with explicit `--execute`.
10. Optionally writes compact Markdown/CSV review exports with short previews instead of dumping full memory contents.
11. Audits reports for automated QA before apply: duplicate cards, junk leakage, missing/invalid provenance, weak metadata, suspicious proposal rates, and soft review risks.

## Commands

Deterministic smoke test:

```powershell
python tools\mem0-extractor\extract_session.py --input path\to\session.jsonl --dry-run --deterministic-only
```

LLM dry run through OpenRouter:

```powershell
python tools\mem0-extractor\extract_session.py --input path\to\session.jsonl --dry-run --env-file .local\model-bakeoff\.env --llm-provider openrouter --model qwen/qwen3-235b-a22b-2507
```

Include the exact prompt in the local report when debugging policy behavior:

```powershell
python tools\mem0-extractor\extract_session.py --input path\to\session.jsonl --dry-run --deterministic-only --include-prompt
```

Plan an apply pass without contacting Mem0:

```powershell
python tools\mem0-extractor\apply_report.py --report .local\mem0-extractor\dry-run.json --output .local\mem0-extractor\apply-plan.json --offline
```

Plan an apply pass and write compact review files:

```powershell
python tools\mem0-extractor\apply_report.py --report .local\mem0-extractor\dry-run.json --output .local\mem0-extractor\apply-plan.json --offline --review-dir .local\mem0-extractor\reviews --review-prefix batch-001
```

Audit a report before applying it:

```powershell
python tools\mem0-extractor\audit_report.py --report .local\mem0-extractor\dry-run.json --output .local\mem0-extractor\audit.json --markdown-output .local\mem0-extractor\reviews\audit.md
```

Repair model-shortened or hallucinated `source_ids` before apply:

```powershell
python tools\mem0-extractor\report_repair.py --report .local\mem0-extractor\dry-run.json --output .local\mem0-extractor\dry-run-repaired.json
```

Run resume-safe batches:

```powershell
python tools\mem0-extractor\batch_orchestrator.py --input .local\open-brain-cleanup\snapshots\thoughts-20260503-102322.jsonl --output-root .local\mem0-extractor\batch-runs\ob1-main --run-id ob1-main --batch-size 25 --llm-env-file .local\model-bakeoff\.env --model qwen/qwen3.6-flash --mem0-env-file .local\mem0-extractor\mem0.env --stop-after-batches 4
```

Add `--execute` only when the batch audit/dedupe results are trusted. Re-running the same command resumes by skipping completed batches in the manifest.

Plan with Mem0 dedupe/search but no writes:

```powershell
python tools\mem0-extractor\apply_report.py --report .local\mem0-extractor\dry-run.json --output .local\mem0-extractor\apply-plan.json --env-file path\to\mem0.env
```

Execute after reviewing the plan:

```powershell
python tools\mem0-extractor\apply_report.py --report .local\mem0-extractor\dry-run.json --output .local\mem0-extractor\apply-executed.json --env-file path\to\mem0.env --execute
```

## Type Decision Tree

`pattern` is a last-resort type. The extractor now prefers:

- `decision` for chosen architecture, policy, or tooling direction.
- `project_state` for current status, blockers, pending work, and active project facts.
- `workflow` for repeatable processes, pipelines, runbooks, and operating procedures.
- `preference` for user instructions, defaults, likes/dislikes, and taste.
- `lesson` for generalized takeaways.
- `person` for important people, mentors, family, partners, and influential contacts.
- `relationship` for attachment, trust, conflict, and interpersonal dynamics.
- `identity_shaping` for formative events and life context.
- `shadow_motive` for deep recurring drives, wounds, and raw motivation sources.
- `ai_breakthrough` for AI philosophy, memory architecture, and agent-system breakthroughs.

Reports fail metadata quality when more than 60% of proposals are still `pattern`, when save proposals have missing domains or very low signal, or when proposals contain obvious generic vocal/business/finance sludge. Low-scored psychology, relationship, or family-origin proposals are flagged for review without blocking by default. Failed reports can be reviewed, but `apply_report.py --execute` refuses them unless `--allow-quality-failed` is passed.

## Signal Calibration

- `9-10`: identity-shaping, emotionally intense, family-origin, critical relationship, current architecture, or important project context.
- `7-8`: useful recurring workflow, system, business, opera, money, own-voice, or AI-memory context.
- `5-6`: potentially useful but weaker context; keep only when clustered or clearly connected.
- `1-4`: drop or review unless protected psychology/relationship nuance says otherwise.

## Policy Notes

- Psychology, relationships, family-origin material, formative events, AI philosophy, systems, current projects, business/money, opera, and own voice/career context are protected.
- Generic transcript artifacts, raw logs, stale implementation trivia, duplicate wording, and generic vocal/student lesson sludge are dropped aggressively.
- `active` means current operating context.
- `historical` means older/formative context that still explains the present.
- `cold` means true but rarely needed background.
- Junk should be deleted, not hidden as `cold`.

## Current Boundary

`extract_session.py` produces proposals. `apply_report.py` plans and can apply those proposals, but live writes require `--execute`. Broad deletes are still intentionally out of scope.
