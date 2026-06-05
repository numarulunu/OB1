# OB1 Human Memory V2 Rescue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Claude cleanup dry run with a richer human-memory policy that preserves psychology, relationships, formative events, family-origin patterns, and AI philosophy while keeping generic technical clutter aggressively filtered.

**Architecture:** Add a local-only `human_memory_v2` layer beside `recipes/shadow-cleanup/second_layer_consolidate.py`. It reads the verified Claude snapshot, first-layer decision cache, and V1 dry-run artifacts; builds GPT-agent packs; validates node/card coverage; then writes a new local dry-run apply plan and Desktop review export without touching Supabase.

**Tech Stack:** Python 3.14, pytest, JSONL artifacts under `.local/open-brain-cleanup/`, existing `recipes/shadow-cleanup` patterns, GPT-5.5 CLI subagents for proposal generation, no OpenRouter for V2, no Supabase mutation.

---

## Inputs And Rules

Inputs:
- Claude snapshot: `.local/open-brain-cleanup/agent-compaction/claude-current-agent-20260505.jsonl`
- First-layer state: `.local/open-brain-cleanup/budgeted/waves/claude-sweep-state.json`
- First-layer caches: `.local/open-brain-cleanup/budgeted/cache/row-cache-claude-wave*.jsonl`
- V1 cards: `.local/open-brain-cleanup/second-layer/rollups/claude-second-layer-memory-cards-20260506.jsonl`
- V1 dry-run files: `.local/open-brain-cleanup/second-layer/dry-run/claude-dry-run-*.json*`

Safety:
- Do not read `.env*` or secret files.
- Do not call OpenRouter from the new V2 script.
- Do not mutate Supabase.
- Do not print raw memory/chat contents to terminal or final responses.
- Runtime packs may contain memory text only under `.local/open-brain-cleanup/human-v2/` and Desktop review exports.
- Every candidate row must be accounted for exactly once as `node`, `drop`, or `review`.
- Every final card must preserve source IDs and provenance.

Policy version: `human-memory-v2-formative-dossiers`.

Keep and enrich: psychology, relationships, family-origin patterns, one-time identity-shaping events, emotionally intense/raw memories, shadow motives without moral cleaning, important people, short exact phrases, old relationship experiences, AI philosophy/goals/workstyle, source provenance, currentness labels, uncertainty labels, contradiction labels, and signal scores.

Still filter hard: transcript-process clutter, diarization/speaker-labeling notes, generic debugging chatter, raw vocal lesson sludge, generic vocal anatomy, and duplicated project status noise already represented by stronger cards.

## File Structure

- Create: `recipes/shadow-cleanup/human_memory_v2.py`
- Create: `recipes/shadow-cleanup/test_human_memory_v2.py`
- Modify: `recipes/shadow-cleanup/README.md`
- Runtime: `.local/open-brain-cleanup/human-v2/{policy,candidates,packs,node-proposals,card-proposals,reports,dry-run}/`
- Review export: `~/Desktop/OB1-Claude-Human-Memory-V2-Review-<timestamp>/`

---

### Task 1: Policy Schema And Scoring

**Files:**
- Create: `recipes/shadow-cleanup/test_human_memory_v2.py`
- Create: `recipes/shadow-cleanup/human_memory_v2.py`

- [ ] Add tests for `POLICY_VERSION`, required psychology-card schema fields, psychology score weighting, and business/workflow score weighting.
- [ ] Implement `POLICY_VERSION = "human-memory-v2-formative-dossiers"`.
- [ ] Implement required fields: `content`, `memory_type`, `signal_strength`, `emotional_intensity`, `current_status`, `evidence_count`, `domains`, `people`, `source_ids`, `provenance`.
- [ ] Implement `psychology_signal_score()` with weights: identity impact 30%, emotional intensity 25%, future advice value 25%, recurrence 10%, source clarity 10%.
- [ ] Implement `business_signal_score()` with weights: usefulness/currentness 40%, project relevance 25%, actionability 20%, recurrence 10%, emotional intensity 5%.
- [ ] Verify: `python -m pytest recipes\shadow-cleanup\test_human_memory_v2.py -q`.

### Task 2: Candidate Routing

**Files:**
- Modify: `recipes/shadow-cleanup/human_memory_v2.py`
- Modify: `recipes/shadow-cleanup/test_human_memory_v2.py`

- [ ] Add tests showing family-origin/relationship content routes to `rescue_candidate` with `protection_level="maximum"`.
- [ ] Add tests showing AI second-brain/philosophy content routes to `rescue_candidate` with domain `ai`.
- [ ] Add tests showing speaker diarization/transcript-process clutter routes to `deterministic_drop`.
- [ ] Implement domain detection for `family_origin`, `relationships`, `psychology`, `shadow_motives`, `ai`, `opera`, `vocality`, `money_execution`, `business`, and `workflow`.
- [ ] Implement `classify_candidate(row, first_layer_decision)` returning `id`, `route`, `domains`, `protection_level`, and `first_layer_decision`.
- [ ] Verify: `python -m pytest recipes\shadow-cleanup\test_human_memory_v2.py -q`.

### Task 3: Candidate Artifacts

**Files:**
- Modify: `recipes/shadow-cleanup/human_memory_v2.py`
- Modify: `recipes/shadow-cleanup/test_human_memory_v2.py`

- [ ] Add tests that cache decision loading stores only `row_id -> decision`, not raw content.
- [ ] Add tests that `build_candidate_records()` preserves `id`, `content`, `metadata`, and `classification` for routed candidates.
- [ ] Implement JSONL helpers, `load_cache_decisions()`, and `build_candidate_records()`.
- [ ] Add CLI command `build-candidates --snapshot <snapshot.jsonl> --cache-files <cache files> --output <candidates.jsonl> --report <report.json>`.
- [ ] Terminal output must print counts only: `snapshot_rows`, `candidate_rows`, `route_counts`, `domain_counts`, `db_apply=False`.
- [ ] Verify: `python -m pytest recipes\shadow-cleanup\test_human_memory_v2.py -q` and `python recipes\shadow-cleanup\human_memory_v2.py --help`.

### Task 4: Node Packs

**Files:**
- Modify: `recipes/shadow-cleanup/human_memory_v2.py`
- Modify: `recipes/shadow-cleanup/test_human_memory_v2.py`

- [ ] Add tests for stable `node_key()` grouping by protected domain and first topic.
- [ ] Add tests that `build_node_packs()` honors `--max-rows-per-pack`.
- [ ] Implement `node_key()`, `build_node_packs()`, and `build-node-packs --candidates <candidates.jsonl> --max-rows-per-pack 35 --output <node-packs.jsonl> --report <report.json>`.
- [ ] Each pack must include `pack_id`, `policy_version`, `stage="node_classification"`, `node_key`, `row_count`, and `items`.
- [ ] Verify: `python -m pytest recipes\shadow-cleanup\test_human_memory_v2.py -q`.

### Task 5: GPT Node Contract And Validator

**Files:**
- Modify: `recipes/shadow-cleanup/human_memory_v2.py`
- Modify: `recipes/shadow-cleanup/test_human_memory_v2.py`

- [ ] Add tests that every input row appears exactly once across `nodes.source_ids`, `drop_source_ids`, or `review_source_ids`.
- [ ] Implement `node_policy_prompt(pack)` with V2 rules and strict JSON output.
- [ ] Implement `validate_node_output(pack, output)` with missing/extra/duplicate ID reporting.
- [ ] Add CLI `validate-nodes --packs <node-packs.jsonl> --outputs <node-proposals/*.json> --report <report.json>`.
- [ ] Verify: `python -m pytest recipes\shadow-cleanup\test_human_memory_v2.py -q`.

### Task 6: Card Synthesis Contract And Validator

**Files:**
- Modify: `recipes/shadow-cleanup/human_memory_v2.py`
- Modify: `recipes/shadow-cleanup/test_human_memory_v2.py`

- [ ] Add tests that every final card has `content`, `memory_type`, `signal_strength`, and `source_ids`.
- [ ] Implement `card_synthesis_prompt(node_pack)` with fixed schema fields: timeline, incidents, competing interpretations, AI guidance, domains, people, provenance, and review reason.
- [ ] Implement `validate_card_output(output)`.
- [ ] Add CLI `build-card-packs`, `validate-cards`.
- [ ] Verify: `python -m pytest recipes\shadow-cleanup\test_human_memory_v2.py -q`.

### Task 7: Local Dry-Run Plan

**Files:**
- Modify: `recipes/shadow-cleanup/human_memory_v2.py`
- Modify: `recipes/shadow-cleanup/test_human_memory_v2.py`

- [ ] Add tests proving V2 rescued source IDs block deletion from the V1 delete set.
- [ ] Implement `build_v2_dry_run_plan(v1_delete_ids, held_ids, v2_cards)`.
- [ ] Add CLI `dry-run-plan --v1-delete-candidates <jsonl> --v1-held-ids <jsonl> --v2-card-proposals <json files> --output-root <dry-run-dir>`.
- [ ] Output files: V2 plan JSON, V2 insert candidates JSONL, V2 delete candidates JSONL, V2 held/review IDs JSONL.
- [ ] Terminal output must print counts only and `db_apply=False`.
- [ ] Verify: `python -m pytest recipes\shadow-cleanup\test_human_memory_v2.py -q`.

### Task 8: Desktop Review Export

**Files:**
- Modify: `recipes/shadow-cleanup/human_memory_v2.py`
- Modify: `recipes/shadow-cleanup/test_human_memory_v2.py`

- [ ] Add tests that review Markdown contains card headings and `Keep/Edit/Drop` checkboxes.
- [ ] Implement `build_review_markdown(cards, summary)`.
- [ ] Add CLI `export-review --dry-run-plan <plan.json> --cards <insert-candidates.jsonl>`.
- [ ] Export README, Markdown, CSV, exact JSONL cards, and dry-run plan under `~/Desktop/OB1-Claude-Human-Memory-V2-Review-<timestamp>/`.
- [ ] Verify exported card count matches JSONL lines.

### Task 9: Documentation

**Files:**
- Modify: `recipes/shadow-cleanup/README.md`

- [ ] Add `Human Memory V2 Rescue/Rebuild` section.
- [ ] Document `build-candidates`, `build-node-packs`, `validate-nodes`, `build-card-packs`, `validate-cards`, `dry-run-plan`, and `export-review` commands.
- [ ] Verify: `rg -n "Human Memory V2|human_memory_v2|human-v2" recipes\shadow-cleanup\README.md`.

### Task 10: First Dry Run

**Files:**
- Runtime artifacts only.

- [ ] Run full tests: `python -m pytest recipes\shadow-cleanup -q`.
- [ ] Build candidates from `.local\open-brain-cleanup\agent-compaction\claude-current-agent-20260505.jsonl` and cache files.
- [ ] Build node packs with `--max-rows-per-pack 35`.
- [ ] Dispatch GPT-5.5 node-classification workers, each writing one JSON file under `.local/open-brain-cleanup/human-v2/node-proposals/`.
- [ ] Validate node outputs; repair only failed packs.
- [ ] Build card packs from validated nodes.
- [ ] Dispatch GPT-5.5 card-synthesis workers, each writing one JSON file under `.local/open-brain-cleanup/human-v2/card-proposals/`.
- [ ] Validate final cards; repair only failed files.
- [ ] Build V2 dry-run plan.
- [ ] Export Desktop review package.

## Self-Review

Spec coverage: policy, scoring, routing, node clustering, synthesis, validation, dry-run planning, and review export are covered.

Safety coverage: no secret reads, no OpenRouter, no Supabase mutation, no raw terminal output, no live delete.

Execution checkpoint: after this plan, implementation can start. Live apply is out of scope until the V2 review package is approved.
