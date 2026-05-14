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
