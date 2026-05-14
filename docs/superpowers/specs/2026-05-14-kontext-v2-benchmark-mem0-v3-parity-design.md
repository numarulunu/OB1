# Kontext V2 Benchmark And Mem0 V3 Parity Design

## Purpose

Kontext V2 should become an owned memory system that can be tested against the same public benchmark style Mem0 uses, then improved with Mem0 v3-style retrieval features without cutting over clients prematurely.

The immediate goal is not to replace Mem0 live. Mem0 remains the source of truth while Kontext stays a shadow mirror and evaluation target. The work should answer two practical questions:

1. Can Kontext run the same benchmark families used to evaluate Mem0-style memory systems?
2. Which retrieval and ingestion features must Kontext add to match or beat Mem0 for Ionut's real workflows?

## Current Context

Kontext V2 already has the right base shape for this work:

- Postgres schema with memories, metadata, categories, relations, retrieval telemetry, project observations, and topic dossiers.
- pgvector extension and an embeddings table.
- Postgres full-text search through generated `TSVECTOR` columns.
- A Python retrieval layer with query expansion, metadata normalization, domain/tier hints, and local reranking.
- A Mem0-compatible MCP/HTTP surface in read-only mirror mode.
- A parity harness and live reliability evals already proving the current narrow 12-case suite can pass for both Kontext and Mem0.

The known gap is that Kontext does not yet have a benchmark adapter for Mem0's public benchmark suite, true fused vector/full-text/entity scoring, access-history decay, or benchmark-grade score reporting.

## External Benchmark Target

The primary external target is Mem0's public `memory-benchmarks` suite:

- Repository: `https://github.com/mem0ai/memory-benchmarks`
- License: Apache-2.0
- Pipeline: `Ingest -> Search -> Evaluate`
- Supported datasets: LoCoMo, LongMemEval, and BEAM
- Useful low-cost mode: `--predict-only`, which stops after search and skips answer/judge evaluation

Benchmark meaning:

- LoCoMo: first target. About 10 multi-session dialogues and roughly 300 questions. It tests factual recall, temporal reasoning, and multi-hop inference.
- LongMemEval: second target. 500 questions across 6 types. It tests long-term extraction, updates, temporal reasoning, and multi-session behavior.
- BEAM: third target. Much larger and more expensive. Run only after LoCoMo and LongMemEval reports show stable retrieval value.

Mem0's published v3 benchmark numbers should be treated as directional, not as a direct apples-to-apples target until Kontext uses comparable extraction, embedding, answerer, judge, and retrieval depth settings.

## Design Decision

Build a benchmark adapter and parity pack instead of copying Mem0 internals line by line.

Reasoning:

- Kontext's architecture is already Postgres/pgvector-owned, while Mem0 OSS and Platform have different internal assumptions.
- Some documented Mem0 features, such as memory decay, are platform-facing behavior, not necessarily reusable OSS implementation code.
- Reimplementing the ideas natively lets Kontext expose category, tier, dossier, dashboard, and manual cleanup controls that are more valuable for this personal system than exact internal clone behavior.

## Architecture

The work splits into four units.

### 1. Benchmark Adapter

Add a small compatibility layer that lets the benchmark runner treat Kontext as a memory backend.

Preferred shape:

- Keep the upstream benchmark repo mostly untouched.
- Add an adapter module under Kontext, for example `tools/kontext-v2/kontext_v2/benchmarks/`.
- The adapter exposes the operations the benchmark runner needs: add/ingest conversation chunks, search memories, fetch or format results, and write a run report.
- If upstream integration requires too much patching, add a thin Mem0-compatible local HTTP endpoint for benchmark use only.

The adapter must never print raw secrets, remote `.env` contents, raw chats, or raw memory text in normal logs. Reports should store IDs, hashes, counts, categories, question types, scores, and short sanitized previews only when explicitly needed for local debugging.

### 2. Benchmark Runner Wrapper

Add a local runner that can execute cost-controlled evaluations.

Run modes:

- `predict-only`: ingest and search, no answerer/judge calls. This is the default for development.
- `judged-subset`: answerer/judge only for a capped subset of questions.
- `full`: complete benchmark run, allowed only when explicitly requested because it can burn API budget.

The wrapper should support:

- dataset name: `locomo`, `longmemeval`, and `beam` when the first two datasets have passed predict-only smoke checks;
- backend: `kontext`, `mem0-oss`, `mem0-platform` if credentials are available;
- top-k values: `10`, `20`, `50`, `200`;
- resume/checkpoint behavior;
- output directory under a local ignored benchmark-results path.

### 3. Retrieval Parity Pack

Implement Mem0 v3-style retrieval in Kontext with explicit scoring components.

Signals:

- semantic score from pgvector embeddings;
- lexical score from Postgres full-text search / BM25-like ranking;
- entity/category/domain boost from metadata, categories, relations, and extracted names/topics;
- tier/status boost so active memories outrank historical/cold memories when relevance is otherwise close;
- recency/access decay factor from retrieval history;
- optional rerank hook, disabled by default because it costs tokens/API calls.

The search response should include a compact internal score explanation for dashboard/debugging:

- final score;
- semantic contribution;
- lexical contribution;
- entity/category contribution;
- tier/status contribution;
- decay/access contribution;
- matched categories/domains;
- reason codes without dumping memory text.

### 4. Benchmark Report And Dashboard Hooks

Store benchmark runs as local JSON and Markdown files first. Database-backed dashboard ingestion is a separate follow-up after the reports prove useful.

Report fields:

- run ID, date, backend, dataset, run mode, top-k cutoffs;
- model/provider settings when used, without keys;
- question counts by type;
- retrieval hit metrics when ground-truth evidence is available;
- judged accuracy when judge mode is enabled;
- latency and estimated cost;
- top failure categories;
- comparison against previous Kontext run and optional Mem0 run.

Dashboard integration is not part of the first implementation pass. The first pass should produce machine-readable JSON plus a compact Markdown summary.

## Decay Design

Memory decay in Kontext should be an access-history scoring factor, not deletion.

Data to track:

- last retrieved timestamp;
- capped recent retrieval timestamps, for example last 20 accesses;
- retrieval count;
- optional last positive-use timestamp in a future feedback feature; the first pass leaves this field absent.

Scoring behavior:

- recently useful memories get a small boost;
- stale memories are softly dampened only when competing with equally relevant fresher memories;
- exact high-relevance matches should not disappear just because they are old;
- `active`, `historical`, and `cold` tiers remain separate from decay.

This should be deterministic and testable. No LLM is needed for decay.

## Ingestion Position

Do not move full benchmark ingestion into the live memory path yet.

For benchmarks, Kontext can ingest benchmark conversations into an isolated benchmark namespace/profile so test data never pollutes Ionut's live memory mirror. The benchmark namespace may use the same schema with clear metadata such as:

- `source = benchmark`;
- `benchmark_dataset = locomo`;
- `benchmark_run_id = ...`;
- `profile = benchmark`;
- `is_live_memory = false`.

Live MCP profiles must keep using the existing mirror/source-of-truth policy until cutover gates are met.

## Test Strategy

### Local Unit Tests

Add tests for:

- score fusion math;
- decay factor behavior;
- access-history caps;
- entity/category boost behavior;
- benchmark namespace isolation;
- sanitized report output.

### Local Integration Tests

Add tests for:

- adapter can ingest a tiny benchmark fixture;
- adapter can search and return Mem0-like result objects;
- predict-only report writes JSON and Markdown summaries;
- benchmark data does not appear in normal live-profile search unless explicitly requested.

### Live/Cost-Controlled Tests

Run in this order:

1. LoCoMo predict-only with Kontext.
2. LoCoMo predict-only with Mem0 if safe credentials and endpoints are available.
3. LoCoMo judged subset with capped questions.
4. LongMemEval predict-only.
5. LongMemEval judged subset.
6. BEAM small slice only after the earlier runs show stable value.

Full judged runs require explicit approval because answerer and judge calls can become expensive.

## Success Criteria

First milestone success:

- Kontext can run LoCoMo in predict-only mode without modifying live memory data.
- The run produces a sanitized JSON report and Markdown summary.
- The adapter returns Mem0-like result objects for benchmark questions.
- Local tests pass.

Second milestone success:

- Kontext retrieval uses fused semantic, lexical, entity/category, tier/status, and decay/access scoring.
- Score explanations are available for debug/reporting.
- LoCoMo predict-only improves or exposes clear failure categories compared with the current retrieval layer.

Third milestone success:

- LongMemEval predict-only runs successfully.
- A capped judged subset can compare Kontext and Mem0 under the same answerer/judge settings.
- Results are good enough to decide which retrieval feature to improve next.

## Non-Goals

- No live cutover from Mem0 to Kontext.
- No automatic write enablement for live Codex/Claude profiles.
- No full BEAM run as the first step.
- No dashboard redesign in this benchmark pass.
- No raw memory/chat dumps in benchmark logs or reports.
- No platform-only Mem0 code copying unless the implementation source is actually available and worth reusing.

## Implementation Order

1. Inspect the `memory-benchmarks` backend interface and decide whether adapter or local HTTP compatibility is less invasive.
2. Add a tiny benchmark fixture and failing adapter tests.
3. Implement the Kontext benchmark adapter for add/search/report.
4. Add benchmark namespace isolation in schema/repository if current metadata filtering is insufficient.
5. Add the predict-only runner wrapper and sanitized report writer.
6. Run the tiny fixture locally.
7. Run LoCoMo predict-only locally or on VPS staging.
8. Add fused retrieval scoring behind a feature flag.
9. Add decay/access tracking behind the same feature flag.
10. Rerun LoCoMo predict-only and compare reports.
11. Run a capped judged subset only after retrieval-only behavior is stable.

## Safety Rules

- Preserve remote `/opt/mem0-remote-mcp/.env` and `/opt/mem0-remote-mcp/data` during any deploy.
- Do not print secrets, profile tokens, raw memory text, raw chats, remote `.env`, or benchmark raw conversations in normal output.
- Benchmark data must stay isolated from live memory data.
- Mem0 remains source of truth until repeated parity, freshness, write-policy, dashboard/category, and shadow evidence gates pass.
- Any full judged benchmark run needs explicit approval because it can spend API money.

## Recommended Next Step

Write the implementation plan for Milestone 1 only: benchmark adapter plus LoCoMo predict-only reporting. Do not implement fused retrieval or decay until the benchmark harness can measure whether those changes help.

