# Kontext V2 Goal Completion Audit

Date: 2026-05-21

Status: not complete yet. Kontext V2 is ahead of Mem0 on OB1 live retrieval, freshness, MCP canary safety, and the implemented predict-only benchmark mirrors. The remaining unproven part is official-style judged answer accuracy, because the official Mem0 benchmark pipeline includes answer generation and judge scoring, while the Kontext mirrors currently prove retrieval evidence only.

## Sources Checked

- Mem0 memory evaluation docs: https://docs.mem0.ai/core-concepts/memory-evaluation
- Mem0 memory-benchmarks repo: https://github.com/mem0ai/memory-benchmarks
- Current upstream repo HEAD from `git ls-remote`: `4b61c5d31b9c668a12b4f5e78064248a02c82d2b`
- Local matrix: `docs/kontext-v2-benchmark-matrix.md`
- Fresh VPS reports:
  - `/opt/kontext/reports/latest.json`
  - `/opt/kontext/reports/expanded-mcp-eval-fresh-20260521T133917Z.json`
  - `/opt/kontext/reports/kontext-mcp-canary-smoke-20260521T134344Z.json`
  - `/opt/kontext/reports/kontext-cutover-readiness-latest.json`

## Current Evidence Summary

| Area | Evidence | Status |
| --- | --- | --- |
| OB1 base live retrieval | Latest shadow report keeps Kontext `12/12`, Mem0 `12/12`; Kontext first-satisfying MRR `1.0` vs Mem0 `0.7389`; Kontext faster. | Pass |
| OB1 expanded live retrieval | Fresh 36-case eval: Kontext `36/36`, avg `739.29 ms`, first-satisfying MRR `1.0`; Mem0 `35/36`, avg `909.29 ms`, first-satisfying MRR `0.8519`. | Pass, Kontext better |
| Mirror freshness | Latest shadow report exact-ID freshness `250/250`, scan offset `2750 -> 3000`, total rows `4003`, stale `0`. | Pass |
| MCP canary safety | Public canary smoke initialized, listed 16 tools, passed search/fetch/project/category checks, all write-like tools returned `dry_run`, all `writes_applied=0`, delete returned `delete_candidate`. | Pass |
| Cutover-readiness audit | `/opt/kontext/reports/kontext-cutover-readiness-latest.json`: 7/7 gates passing, `ready_for_user_cutover_review=true`, `production_cutover_enabled=false`. | Pass, no cutover |
| LoCoMo mirror | Full predict-only sweep: top-k50 and top-k200 `1977/1977`, exceeding Mem0 published top-50/top-200 rates on retrieval evidence. | Pass as predict-only retrieval |
| LongMemEval mirror | Full predict-only sweep: top-k50 and top-k200 `500/500`, exceeding Mem0 published top-50/top-200 rates on retrieval evidence. | Pass as predict-only retrieval |
| BEAM mirror | Implemented and deployed. Available BEAM 10M tested slice top-k200 `176/176`; top-k50 `166/176`. Opt-in cross-encoder full instruction/preference validation top-k50 `33/40`, top-k200 `40/40`. | Pass on top-k200 evidence; top-k50 still the active quality frontier |

## Requirement Audit

| Requirement | Current evidence | Audit result |
| --- | --- | --- |
| Existing local and live OB1 evals | Local focused suites pass; fresh live base and expanded evals show Kontext equal or better than Mem0 on pass rate, latency, and first-satisfying rank. | Proven |
| Expanded OB1 retrieval/write/cutover evals | Expanded 36-case eval passes; canary smoke proves dry-run write safety; cutover-readiness audit passes while keeping `production_cutover_enabled=false`. | Proven for shadow/canary mode |
| Official/mirrored benchmark suites | LoCoMo, LongMemEval, and BEAM predict-only mirrors exist and are deployed. LoCoMo/LongMemEval exceed published retrieval-depth targets on evidence-hit rate. BEAM exceeds top-k200 on tested slices and remains weaker at top-k50 hard instruction/preference slices. | Mostly proven for predict-only retrieval; judged answer parity not proven |
| Real MCP behavior | Search/fetch/status/project/category/write-like tools are covered by public canary smoke and native client canary evidence. Save/update/delete/flag/ingest/extract/override remain dry-run in production; isolated apply-mode write tests previously passed. | Proven without production writes |
| Latency | Live OB1 reports consistently show Kontext faster than Mem0. | Proven |
| Metadata quality | First-satisfying rank uses domain and memory_type metadata; current expanded eval has Kontext MRR `1.0` vs Mem0 `0.8519`. | Proven for OB1 live eval metadata; benchmark metadata still evaluated indirectly |
| Freshness and decay | Exact-ID freshness windows pass; memory tiers/decay influence ranking and are covered by retrieval tests and live freshness reports. | Proven for mirror freshness; decay effect should remain in regression tests |
| Conflict handling | Dry-run update/delete candidate behavior and conflict candidate reporting exist; dream cleanup is CLI-first. | Partially proven; broad conflict-resolution eval is not yet formalized |
| Categories | Public MCP canary verifies `list_categories` and `list_category_memories`; payload budgets are enforced. | Proven for read surface |
| Project continuity | Project search/fetch/timeline/file-context are exposed and canary-smoked; compact project observations exist. | Proven for canary read surface |
| No production cutover | Health/readiness evidence shows shadow/read-only mode and writes disabled. | Proven |

## Official Benchmark Interpretation

Mem0 official docs and the memory-benchmarks repo define a full benchmark as ingestion, search, answer generation, and judge evaluation. The repo also exposes `--predict-only`, which stops after search. Kontext currently mirrors the official datasets and reports predict-only retrieval evidence. That is useful and cheaper, but it is not the same claim as judged answer accuracy.

Because of that, the active goal should not be marked complete yet unless the accepted success standard is explicitly limited to predict-only retrieval mirrors. The safer engineering conclusion is:

- Kontext is already better than Mem0 on OB1 live retrieval quality and latency.
- Kontext is operationally safe enough for a user cutover review, not for unapproved cutover.
- Kontext has strong official-suite retrieval evidence, but not full judged-answer parity evidence.
- The main remaining technical gap is BEAM top-k50 quality on hard instruction/preference slices and optional judged benchmark runs if cost is approved.

## Next Gate

The first actionable gate is now scaffolded: `tools/kontext-v2/scripts/judged_benchmark_plan.py` estimates the no-API cost/call shape for judged benchmark micro-slices. It was deployed to `/opt/kontext/scripts/judged_benchmark_plan.py` and smoke-tested without model calls. Sample remote outputs:

- `/opt/kontext/reports/judged-plans/locomo5-20260521.json`: 5 questions, 4 cutoffs, 40 estimated LLM calls, 118,400 estimated tokens.
- `/opt/kontext/reports/judged-plans/longmemeval5-20260521.json`: 5 questions, 4 cutoffs, 40 estimated LLM calls, 118,400 estimated tokens.
- `/opt/kontext/reports/judged-plans/beam4-20260521.json`: 4 questions, 4 cutoffs, BEAM rubric mode with 3 judge units per question, 64 estimated LLM calls, 146,560 estimated tokens.

Recommended next step: choose one of these gates:

1. Run one approved judged micro-slice, starting with LoCoMo or LongMemEval because the planner shows a smaller call shape than BEAM.
2. BEAM top-k50 improvement: continue the hard instruction/preference slice work with a stronger fair reranker or cheaper first-stage gate.
3. Conflict-resolution eval: add a compact OB1 conflict/staleness eval that verifies newer facts, memory_tier decay, delete-candidate behavior, and dream cleanup outputs.

Do not enable production writes or production cutover without explicit approval.
