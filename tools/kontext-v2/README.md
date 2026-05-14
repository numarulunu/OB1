# Kontext V2

Local package skeleton for the Kontext V2 Mem0 mirror work.

## Safety

- Default write mode is `dry_run`.
- Local development uses a dedicated Postgres database on host port `55434`.
- Do not point this package at production Mem0, VPS services, remote `.env` files, or secrets.
- Keep credentials local and non-sensitive; the compose defaults are for a disposable local test database only.

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

Expected output is a one-line aggregate summary such as `matched=3/3`. The command must not print raw memory text or secrets.
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
