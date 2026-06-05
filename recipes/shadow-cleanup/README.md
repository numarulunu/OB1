# Shadow Cleanup

Cleanup planning and guarded application for the OB1 `thoughts` table.

The default planning commands do not edit, delete, archive, or update live database rows. Runtime artifacts are created under `.local/open-brain-cleanup/`:

- `snapshots/` - JSONL exports from Supabase
- `packs/` - duplicate and topic cluster packs for review
- `proposals/` - Claude-generated consolidation proposals

The live Claude history importer can keep running while this recipe reads from Supabase. This recipe never touches the Claude sync log.

## Commands

Show local cleanup artifact status:

```powershell
python recipes\shadow-cleanup\shadow_cleanup.py status
```

Create a read-only Supabase snapshot:

```powershell
python recipes\shadow-cleanup\shadow_cleanup.py snapshot
```

Build cleanup packs from the newest snapshot:

```powershell
python recipes\shadow-cleanup\shadow_cleanup.py pack --topic-min-items 3 --max-items 40
```

Split packs into disjoint worker files:

```powershell
python recipes\shadow-cleanup\shadow_cleanup.py shard --shards 4
```

Run Claude CLI on the newest pack file:

```powershell
python recipes\shadow-cleanup\shadow_cleanup.py run-claude --model opus --max-budget-usd 10
```

Use `--limit-packs N` for a bounded batch. The proposal command appends to the output file and skips pack IDs already present in that same file, so it is safe to resume.

## Safety Rules

- No live database mutation unless an apply command is explicitly run with `--apply`.
- Destructive apply commands must write an exact JSONL backup first.
- No raw memory contents are printed to the terminal.
- Secrets are read only by the script when Supabase access is required; they are never printed.
- Failed Claude packs are logged by pack ID only in `_shadow-cleanup.log`.
- Proposal files are review artifacts, not instructions to apply automatically.

## Verification

Apply existing proposal files in guarded mode:

```powershell
python recipes\shadow-cleanup\apply_proposals.py .local\open-brain-cleanup\proposals\proposals-shard-*.jsonl
```

Add `--apply` to mutate Supabase. The apply command is non-destructive: it inserts canonical cleanup memories and marks originals as archive candidates in metadata; it does not delete rows.

Run aggressive DeepSeek cleanup planning without mutating Supabase:

```powershell
python recipes\shadow-cleanup\aggressive_cleanup.py snapshot --source claude_history
python recipes\shadow-cleanup\aggressive_cleanup.py snapshot --source all
python recipes\shadow-cleanup\aggressive_cleanup.py pack --limit-rows 500 --max-items 50 --project-filter temp-working-dir
python recipes\shadow-cleanup\aggressive_cleanup.py run-deepseek --model deepseek/deepseek-v4-pro --limit-packs 10
python recipes\shadow-cleanup\aggressive_cleanup.py summarize
python recipes\shadow-cleanup\aggressive_cleanup.py apply-drops --min-confidence 0.92 --max-delete 500
```

`aggressive_cleanup.py` writes local artifacts under `.local/open-brain-cleanup/aggressive/`. Proposal generation does not mutate Supabase. `apply-drops --apply` is destructive: it first writes an exact JSONL backup of rows to delete, skips goldlist hits by default, then hard-deletes only high-confidence `DROP_*` rows.

Run v3 budgeted canonicalization with verifier gating:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py snapshot --source claude_history --output .local\open-brain-cleanup\budgeted\snapshots\claude-current-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py pack --snapshot .local\open-brain-cleanup\budgeted\snapshots\claude-current-20260505.jsonl --source claude_history --limit-rows 500 --max-pack-rows 25 --output .local\open-brain-cleanup\budgeted\packs\claude-pilot-500-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py run --pack-file .local\open-brain-cleanup\budgeted\packs\claude-pilot-500-20260505.jsonl --output .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500-20260505.jsonl --model deepseek/deepseek-v4-pro --prompt-policy human --limit-packs 5 --max-tokens 4500 --temperature 0 --retries 2 --retry-delay 3
python recipes\shadow-cleanup\budgeted_canonicalize.py verify --pack-file .local\open-brain-cleanup\budgeted\packs\claude-pilot-500-20260505.jsonl .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500-20260505.jsonl --output .local\open-brain-cleanup\budgeted\verified\claude-pilot-500-verified-20260505.jsonl --limit-packs 5 --max-tokens 2500 --temperature 0 --retries 2 --retry-delay 3
python recipes\shadow-cleanup\budgeted_canonicalize.py repair --pack-file .local\open-brain-cleanup\budgeted\packs\claude-pilot-500-20260505.jsonl .local\open-brain-cleanup\budgeted\verified\claude-pilot-500-verified-20260505.jsonl --output .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500-repairs-20260505.jsonl --limit-packs 5 --max-tokens 4500 --temperature 0 --retries 2 --retry-delay 3
python recipes\shadow-cleanup\budgeted_canonicalize.py verify --pack-file .local\open-brain-cleanup\budgeted\packs\claude-pilot-500-20260505.jsonl .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500-repairs-20260505.jsonl --output .local\open-brain-cleanup\budgeted\verified\claude-pilot-500-repairs-verified-20260505.jsonl --limit-packs 5 --max-tokens 2500 --temperature 0 --retries 2 --retry-delay 3
python recipes\shadow-cleanup\budgeted_canonicalize.py summarize .local\open-brain-cleanup\budgeted\verified\claude-pilot-500-verified-20260505.jsonl .local\open-brain-cleanup\budgeted\verified\claude-pilot-500-repairs-verified-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py apply .local\open-brain-cleanup\budgeted\verified\claude-pilot-500-verified-20260505.jsonl --max-delete 500
```

`budgeted_canonicalize.py run` is the legacy OpenRouter-backed path. Do not use it for GPT-5.5 CLI subagent cleanup. The GPT path is to generate local pack files, dispatch Codex GPT-5.5 subagents in this CLI to produce proposal artifacts/review questions, then apply verified results from the main thread. `budgeted_canonicalize.py apply` is a dry run unless `--apply` is passed. It inserts canonical memories, writes an exact backup, then hard-deletes only verified source rows that are either explicitly junk or covered by a canonical memory. It blocks raw keeps, escalations, and verifier-unsafe IDs. Use `--max-pack-rows 25` when the model fails to reliably enumerate every source ID in 50-row packs; `run`, `repair`, and `verify` support bounded retries for transient OpenRouter or JSON failures. `repair` consumes verifier-rejected rows only and writes fresh proposals that must be verified before apply.

Build GPT-5.5 CLI subagent cache/waves without OpenRouter or Supabase access:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py cache-index --snapshot .local\open-brain-cleanup\agent-compaction\claude-current-agent-20260505.jsonl --policy-version human-memory-patterns-v2 --output .local\open-brain-cleanup\budgeted\cache\row-cache-gpt55-strict-pilot-20260505.jsonl .local\open-brain-cleanup\agent-compaction\gpt55-strict-pilot-pack1-repair-20260505.json .local\open-brain-cleanup\agent-compaction\gpt55-strict-pilot-a-20260505.json .local\open-brain-cleanup\agent-compaction\gpt55-strict-pilot-b-20260505.json
python recipes\shadow-cleanup\budgeted_canonicalize.py wave --snapshot .local\open-brain-cleanup\agent-compaction\claude-current-agent-20260505.jsonl --source claude_history --row-cache .local\open-brain-cleanup\budgeted\cache\row-cache-gpt55-strict-pilot-20260505.jsonl --policy-version human-memory-patterns-v2 --limit-rows 400 --max-pack-rows 40 --packs-output .local\open-brain-cleanup\budgeted\waves\claude-wave1-packs-400-20260505.jsonl --manifest-output .local\open-brain-cleanup\budgeted\waves\claude-wave1-manifest-400-20260505.json --junk-output .local\open-brain-cleanup\budgeted\waves\claude-wave1-junk-400-20260505.jsonl
```

`cache-index` stores only IDs, decisions, hashes, source, policy version, and pack IDs; it does not store raw memory content. `wave` skips cached rows, filters deterministic junk by default, and coalesces sparse rows into dense agent packs so 400 candidate rows can become about 10 GPT-5.5 subagent tasks instead of hundreds of tiny packs. Change `--policy-version` whenever the cleanup policy changes materially, so stale decisions are not reused.

Run deterministic no-LLM junk pruning:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py audit-junk --source all --min-confidence 0.98 --output .local\open-brain-cleanup\budgeted\junk\junk-all-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py apply-junk .local\open-brain-cleanup\budgeted\junk\junk-all-20260505.jsonl --min-confidence 0.98 --max-delete 500
python recipes\shadow-cleanup\budgeted_canonicalize.py apply-junk .local\open-brain-cleanup\budgeted\junk\junk-all-20260505.jsonl --min-confidence 0.98 --max-delete 500 --apply
```

`audit-junk` does not call OpenRouter and does not write raw memory content to the proposal file. `apply-junk` is a dry run unless `--apply` is passed; destructive apply writes an exact JSONL backup first.

Run the focused tests:

```powershell
python -m pytest recipes\shadow-cleanup -q
```

## Human Memory V2 Rescue/Rebuild

`human_memory_v2.py` is the local-only GPT-agent cleanup path for the richer human-memory policy. It is designed to rescue psychology, relationships, family-origin material, identity-shaping events, AI philosophy, and current project/workflow signal before any V1 delete set is considered. It does not read `.env*`, does not call OpenRouter, and does not mutate Supabase.

Build V2 rescue candidates from the Claude snapshot and first-layer cache decisions:

```powershell
python recipes\shadow-cleanup\human_memory_v2.py build-candidates --snapshot .local\open-brain-cleanup\agent-compaction\claude-current-agent-20260505.jsonl --cache-files .local\open-brain-cleanup\budgeted\cache\row-cache-claude-wave*.jsonl --output .local\open-brain-cleanup\human-v2\candidates\claude-human-v2-candidates.jsonl --report .local\open-brain-cleanup\human-v2\reports\claude-human-v2-candidates-report.json
```

Build node-classification packs for GPT-5.5 CLI subagents:

```powershell
python recipes\shadow-cleanup\human_memory_v2.py build-node-packs --candidates .local\open-brain-cleanup\human-v2\candidates\claude-human-v2-candidates.jsonl --max-rows-per-pack 35 --output .local\open-brain-cleanup\human-v2\packs\claude-human-v2-node-packs.jsonl --report .local\open-brain-cleanup\human-v2\reports\claude-human-v2-node-packs-report.json
```

After GPT subagents write one strict JSON node proposal per pack under `.local\open-brain-cleanup\human-v2\node-proposals\`, validate coverage:

```powershell
python recipes\shadow-cleanup\human_memory_v2.py validate-nodes --packs .local\open-brain-cleanup\human-v2\packs\claude-human-v2-node-packs.jsonl --outputs .local\open-brain-cleanup\human-v2\node-proposals\*.json --report .local\open-brain-cleanup\human-v2\reports\claude-human-v2-validate-nodes-report.json
```

Build card-synthesis packs from validated node proposals:

```powershell
python recipes\shadow-cleanup\human_memory_v2.py build-card-packs --node-packs .local\open-brain-cleanup\human-v2\packs\claude-human-v2-node-packs.jsonl --node-outputs .local\open-brain-cleanup\human-v2\node-proposals\*.json --output .local\open-brain-cleanup\human-v2\packs\claude-human-v2-card-packs.jsonl --report .local\open-brain-cleanup\human-v2\reports\claude-human-v2-card-packs-report.json
```

After GPT subagents write one strict JSON card proposal per card pack under `.local\open-brain-cleanup\human-v2\card-proposals\`, validate final card schema and source coverage:

```powershell
python recipes\shadow-cleanup\human_memory_v2.py validate-cards --packs .local\open-brain-cleanup\human-v2\packs\claude-human-v2-card-packs.jsonl --outputs .local\open-brain-cleanup\human-v2\card-proposals\*.json --report .local\open-brain-cleanup\human-v2\reports\claude-human-v2-validate-cards-report.json
```

Build the local V2 dry-run plan. V2 card `source_ids` rescue rows from the V1 delete candidate set:

```powershell
python recipes\shadow-cleanup\human_memory_v2.py dry-run-plan --v1-delete-candidates .local\open-brain-cleanup\second-layer\dry-run\claude-dry-run-delete-candidates-20260506.jsonl --v1-held-ids .local\open-brain-cleanup\second-layer\dry-run\claude-dry-run-hold-review-ids-20260506.jsonl --v2-card-proposals .local\open-brain-cleanup\human-v2\card-proposals\*.json --output-root .local\open-brain-cleanup\human-v2\dry-run
```

Export a Desktop review package with Markdown checkboxes, CSV, exact JSONL cards, and the dry-run plan:

```powershell
python recipes\shadow-cleanup\human_memory_v2.py export-review --dry-run-plan .local\open-brain-cleanup\human-v2\dry-run\v2-dry-run-plan.json --cards .local\open-brain-cleanup\human-v2\dry-run\v2-insert-candidates.jsonl
```

All commands print aggregate counts only. Runtime artifacts that contain memory text stay under `.local\open-brain-cleanup\human-v2\` or the Desktop review package.
## Mem0 Pilot Review/Export

`review_mem0_pipeline.py` is the local bridge between the curated Human Memory V2 cards and a possible Mem0 runtime. It does not read `.env*`, does not touch Supabase, and does not mutate a Mem0 server unless `import-mem0 --execute` is explicitly passed.

Export a self-contained local review dashboard:

```powershell
python recipes\shadow-cleanup\review_mem0_pipeline.py export-dashboard --cards .local\open-brain-cleanup\human-v2\dry-run\v2-insert-candidates.jsonl --dry-run-plan .local\open-brain-cleanup\human-v2\dry-run\v2-dry-run-plan.json --output-root "$env:USERPROFILE\Desktop\OB1-Mem0-Pilot-Review-20260507"
```

Export Mem0 OSS request JSONL for all curated cards:

```powershell
python recipes\shadow-cleanup\review_mem0_pipeline.py export-mem0 --cards .local\open-brain-cleanup\human-v2\dry-run\v2-insert-candidates.jsonl --output .local\open-brain-cleanup\human-v2\mem0\mem0-full-import-requests.jsonl --user-id ionut --target oss
```

Export a top-priority staging pilot subset:

```powershell
python recipes\shadow-cleanup\review_mem0_pipeline.py export-mem0 --cards .local\open-brain-cleanup\human-v2\dry-run\v2-insert-candidates.jsonl --output .local\open-brain-cleanup\human-v2\mem0\mem0-pilot-top300-requests.jsonl --user-id ionut --target oss --limit 300
```

Dry-run a Mem0 import without network writes:

```powershell
python recipes\shadow-cleanup\review_mem0_pipeline.py import-mem0 --requests .local\open-brain-cleanup\human-v2\mem0\mem0-pilot-top300-requests.jsonl --base-url https://mem0.example.com
```

Only run the importer with `--execute` after a staging Mem0 server exists and the remote command set has been approved. The importer logs request paths/statuses to `_mem0-import.log` and never logs raw card content.

For self-hosted Mem0 OSS with API-key auth enabled, pass `--api-key-env MEM0_API_KEY`; the importer sends that value through `X-API-Key` by default. Leave `--execute` off until the dry-run count matches the intended import batch.
