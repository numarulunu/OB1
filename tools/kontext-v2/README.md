# Kontext V2

Local package skeleton for the Kontext V2 Mem0 mirror work.

## Safety

- Default write mode is `dry_run`.
- Local development uses a dedicated Postgres database on host port `55434`.
- Do not point this package at production Mem0, VPS services, remote `.env` files, or secrets.
- Keep credentials local and non-sensitive; the compose defaults are for a disposable local test database only.

## Native Codex MCP Canary

Use this when checking whether the actual Codex client can reach both Mem0 and the remote Kontext canary MCP. The script prints sanitized aggregate markers only. By default it is a dry run and only prints the nested Codex command.

```powershell
python tools\kontext-v2\scripts\codex_native_mcp_canary.py
```

To run the live canary, use `--execute`. This creates one temporary low-priority Mem0 memory, fetches it, deletes that exact ID, and verifies the Kontext canary write path stays dry-run with `writes_applied=0`.

```powershell
python tools\kontext-v2\scripts\codex_native_mcp_canary.py --execute --workdir C:\Tools\OB1
```

The known safe Codex invocation shape is `codex -a never exec --sandbox danger-full-access --ephemeral -C <repo> -o <out> -`. Older read-only `codex exec` probes may cancel MCP calls before the tool request reaches the server.

Run the live canary from a normal shell that has Codex provider credentials available. Sandboxed command runners may not inherit that environment and can fail before MCP startup.

## Local Test Database

Start the database:

```powershell
docker compose -f tools\kontext-v2\docker-compose.yml up -d
```

Check readiness:

```powershell
docker compose -f tools\kontext-v2\docker-compose.yml ps
```

Stop the database:

```powershell
docker compose -f tools\kontext-v2\docker-compose.yml down
```

Run the bootstrap test:

```powershell
python -m pytest tools\kontext-v2\tests\test_package_bootstrap.py -q
```

## Tiny LoCoMo Predict-Only Benchmark

This smoke benchmark uses a synthetic local fixture and does not call answerer or judge models. It writes sanitized JSON and Markdown reports under `tools/kontext-v2/benchmark-results/`.

From the repo root:

```powershell
$env:PYTHONPATH = "tools\kontext-v2"
python -m kontext_v2.benchmarks.locomo_predict `
  --database-url "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2" `
  --fixture-path tools\kontext-v2\tests\fixtures\locomo_tiny.json `
  --output-dir tools\kontext-v2\benchmark-results `
  --run-id local-smoke `
  --top-k 5
```

Expected output is a one-line aggregate summary such as `matched=3/3 retrieval=3/3 skipped=0`. The command must not print raw memory text or secrets.
## Real LoCoMo Predict-Only Benchmark

This mode downloads the public LoCoMo `locomo10.json` fixture into ignored local cache `tools/kontext-v2/benchmark-data/` and writes sanitized reports under `tools/kontext-v2/benchmark-results/`. It ingests benchmark rows only with `source=benchmark`, `profile=benchmark`, and `is_live_memory=false`.

Run a small one-conversation smoke:

```powershell
$env:PYTHONPATH = "tools\kontext-v2"
python -m kontext_v2.benchmarks.locomo_predict `
  --database-url "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2" `
  --dataset-url "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json" `
  --output-dir tools\kontext-v2\benchmark-results `
  --run-id real-locomo-smoke `
  --top-k 20 `
  --conversations 0 `
  --max-questions 5
```

Reports contain question IDs, categories, match counts, result IDs, and result hashes. They intentionally do not include raw conversation text, raw question text, raw answers, or raw memory text.
Top-k sweep and miss analysis:

```powershell
$env:PYTHONPATH = "tools\kontext-v2"
python -m kontext_v2.benchmarks.locomo_predict `
  --database-url "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2" `
  --dataset-url "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json" `
  --output-dir tools\kontext-v2\benchmark-results `
  --run-id real-locomo-sweep `
  --top-k-sweep 5,10,20,50 `
  --conversations 0 `
  --max-questions 20
```

Sweep reports compare several retrieval cutoffs from one max-top-k search. Miss analysis is aggregate-only and classifies misses as evidence below cutoff, evidence not retrieved, expected terms below cutoff, expected terms not retrieved, or no match rule.

The benchmark adapter now keeps per-turn evidence rows plus one session context row for sourced sessions, then uses session-context-aware ranking, a small session-observation boost, and temporal/proper-noun boosts for multi-hop evidence. Each benchmark run clears prior benchmark rows for the same run ID before ingesting fresh data, which keeps repeat runs deterministic.

## LongMemEval Predict-Only Benchmark

This mode mirrors the public Mem0 `memory-benchmarks` LongMemEval shape without calling answerer or judge models. Each question gets an isolated benchmark user namespace, and reports stay sanitized.

Tiny local fixture smoke:

```powershell
$env:PYTHONPATH = "tools\kontext-v2"
python -m kontext_v2.benchmarks.longmemeval_predict `
  --database-url "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2" `
  --fixture-path tools\kontext-v2\tests\fixtures\longmemeval_real_shape.json `
  --output-dir tools\kontext-v2\benchmark-results `
  --run-id longmemeval-smoke `
  --top-k 5 `
  --max-questions 2
```

Capped real dataset smoke:

```powershell
$env:PYTHONPATH = "tools\kontext-v2"
python -m kontext_v2.benchmarks.longmemeval_predict `
  --database-url "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2" `
  --dataset-url "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json" `
  --output-dir tools\kontext-v2\benchmark-results `
  --run-id real-longmemeval-smoke `
  --top-k-sweep 10,20,50,200 `
  --max-questions 30
```

Full judged LongMemEval runs need explicit approval because answerer and judge calls can spend API money. Predict-only runs are the default safety gate before any judged benchmark.

## BEAM Predict-Only Benchmark

This mode mirrors the public Mem0 `memory-benchmarks` BEAM row shape without running answerer or rubric judge calls. It uses the Hugging Face datasets-server rows API for capped slices, normalizes BEAM chat batches into isolated benchmark sessions, and scores retrieval by whether source chat evidence IDs appear in the returned memories.

Tiny local fixture smoke:

```powershell
$env:PYTHONPATH = "tools\kontext-v2"
python -m kontext_v2.benchmarks.beam_predict `
  --database-url "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2" `
  --fixture-path tools\kontext-v2\tests\fixtures\beam_real_shape.json `
  --output-dir tools\kontext-v2\benchmark-results `
  --run-id beam-smoke `
  --top-k 10 `
  --question-types information_extraction,knowledge_update
```

Capped real BEAM rows smoke:

```powershell
$env:PYTHONPATH = "tools\kontext-v2"
python -m kontext_v2.benchmarks.beam_predict `
  --database-url "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2" `
  --dataset-path tools\kontext-v2\benchmark-data\beam_100K_rows_0_1.json `
  --output-dir tools\kontext-v2\benchmark-results `
  --run-id real-beam-smoke `
  --top-k-sweep 10,20,50,200 `
  --beam-size 100K `
  --offset 0 `
  --length 1 `
  --max-questions 4 `
  --question-types information_extraction,knowledge_update
```

Full judged BEAM runs need explicit approval because answerer and per-nugget judge calls can spend API money. Abstention questions are not a useful retrieval-only evidence-hit gate unless they are judged by an answerer/judge path, so the first safe slices should focus on evidence-backed question types.

For VPS-only isolated smoke/diagnostic work, use the helper scripts in `tools/kontext-v2/scripts/` from inside the Kontext container or copy them to `/tmp` and run with `PYTHONPATH=/app`:

```powershell
python tools\kontext-v2\scripts\beam_isolated_smoke.py --help
python tools\kontext-v2\scripts\beam_rank_diagnostics.py --help
```

Both helpers create a temporary PostgreSQL schema, print sanitized aggregate/rank metrics only, and drop the schema before exit.
