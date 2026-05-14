# Kontext V2 Benchmark Adapter Locomo Predict-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Milestone 1 of the Kontext benchmark system: a local benchmark adapter, tiny LoCoMo-style fixture, predict-only runner, and sanitized JSON/Markdown reports.

**Architecture:** Add a small `kontext_v2.benchmarks` package that writes benchmark memories through existing `MemoryRecord` and `KontextRepository`, searches through existing `search_memories`, and reports only aggregate/sanitized data. The first runner uses a synthetic local LoCoMo-style fixture and does not call answerer or judge models.

**Tech Stack:** Python 3, pytest, psycopg, existing Kontext V2 Postgres schema, existing `kontext_v2.retrieval.search_memories`, JSON and Markdown report files.

---

## Scope

This plan implements only Milestone 1 from `docs/superpowers/specs/2026-05-14-kontext-v2-benchmark-mem0-v3-parity-design.md`.

In scope: tiny local LoCoMo-style fixture; benchmark namespace metadata; adapter `add` and `search`; predict-only runner; sanitized JSON/Markdown reports; local tests and CLI smoke.

Out of scope: fused vector/full-text/entity scoring; decay/access scoring; judged evaluation; full LoCoMo download/run; dashboard changes; VPS deploy; live Mem0 writes.

Safety requirements:
- Do not print secrets, profile tokens, remote `.env`, raw chats, or raw memory text in normal output.
- Benchmark rows must carry `source=benchmark`, `benchmark_dataset`, `benchmark_run_id`, `profile=benchmark`, and `is_live_memory=false`.
- Generated benchmark reports must not contain raw fixture memory text.

## Files

Create:
- `tools/kontext-v2/kontext_v2/benchmarks/__init__.py`
- `tools/kontext-v2/kontext_v2/benchmarks/adapter.py`
- `tools/kontext-v2/kontext_v2/benchmarks/fixtures.py`
- `tools/kontext-v2/kontext_v2/benchmarks/reporting.py`
- `tools/kontext-v2/kontext_v2/benchmarks/locomo_predict.py`
- `tools/kontext-v2/tests/fixtures/locomo_tiny.json`
- `tools/kontext-v2/tests/test_benchmark_adapter.py`
- `tools/kontext-v2/tests/test_benchmark_locomo_predict.py`

Modify:
- `.gitignore`
- `tools/kontext-v2/README.md`
- `project_log.md`

---

### Task 1: Tiny Fixture And Loader

**Files:**
- Create: `tools/kontext-v2/kontext_v2/benchmarks/__init__.py`
- Create: `tools/kontext-v2/kontext_v2/benchmarks/fixtures.py`
- Create: `tools/kontext-v2/tests/fixtures/locomo_tiny.json`
- Test: `tools/kontext-v2/tests/test_benchmark_adapter.py`

- [ ] **Step 1: Write the failing fixture test**

Create `tools/kontext-v2/tests/test_benchmark_adapter.py`:

```python
from pathlib import Path

from kontext_v2.benchmarks.fixtures import load_locomo_tiny_fixture

FIXTURE = Path(__file__).parent / "fixtures" / "locomo_tiny.json"


def test_load_locomo_tiny_fixture_returns_conversations_and_questions():
    fixture = load_locomo_tiny_fixture(FIXTURE)
    assert fixture["dataset"] == "locomo_tiny"
    assert len(fixture["conversations"]) == 2
    assert fixture["conversations"][0]["conversation_id"] == "tiny-conv-1"
    assert fixture["questions"][0]["question"] == "Where is Alice planning to travel in June?"
    assert fixture["questions"][0]["expected_terms"] == ["berlin", "june"]
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
Set-Location -LiteralPath 'C:\Tools\OB1'
python -m pytest tools\kontext-v2\tests\test_benchmark_adapter.py::test_load_locomo_tiny_fixture_returns_conversations_and_questions -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'kontext_v2.benchmarks'`.

- [ ] **Step 3: Add `locomo_tiny.json`**

Create `tools/kontext-v2/tests/fixtures/locomo_tiny.json` with two synthetic conversations and three questions:

```json
{
  "dataset": "locomo_tiny",
  "conversations": [
    {"conversation_id": "tiny-conv-1", "user_id": "benchmark-locomo-tiny-1", "sessions": [
      {"session_id": "session_1", "date": "2024-06-01", "messages": [{"role": "user", "content": "Alice: I am planning a Berlin trip for June."}, {"role": "assistant", "content": "Bob: Berlin in June should be warm."}]},
      {"session_id": "session_2", "date": "2024-06-08", "messages": [{"role": "user", "content": "Alice: Please remember I booked the museum tour for Friday."}, {"role": "assistant", "content": "Bob: I will remember the Friday museum tour."}]}
    ]},
    {"conversation_id": "tiny-conv-2", "user_id": "benchmark-locomo-tiny-2", "sessions": [
      {"session_id": "session_1", "date": "2024-07-03", "messages": [{"role": "user", "content": "Mira: My cello recital is at the old library."}, {"role": "assistant", "content": "Noah: The recital location is the old library."}]}
    ]}
  ],
  "questions": [
    {"question_id": "tiny-q-1", "conversation_id": "tiny-conv-1", "user_id": "benchmark-locomo-tiny-1", "question": "Where is Alice planning to travel in June?", "category": "single-hop", "expected_terms": ["berlin", "june"]},
    {"question_id": "tiny-q-2", "conversation_id": "tiny-conv-1", "user_id": "benchmark-locomo-tiny-1", "question": "What tour did Alice book for Friday?", "category": "temporal", "expected_terms": ["museum", "friday"]},
    {"question_id": "tiny-q-3", "conversation_id": "tiny-conv-2", "user_id": "benchmark-locomo-tiny-2", "question": "Where is Mira's cello recital?", "category": "single-hop", "expected_terms": ["old", "library"]}
  ]
}
```

- [ ] **Step 4: Add loader implementation**

Create `tools/kontext-v2/kontext_v2/benchmarks/__init__.py`:

```python
from __future__ import annotations

from kontext_v2.benchmarks.fixtures import load_locomo_tiny_fixture

__all__ = ["load_locomo_tiny_fixture"]
```

Create `tools/kontext-v2/kontext_v2/benchmarks/fixtures.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_locomo_tiny_fixture(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("dataset") != "locomo_tiny":
        raise ValueError("Expected locomo_tiny fixture")
    if not isinstance(data.get("conversations"), list):
        raise ValueError("Fixture conversations must be a list")
    if not isinstance(data.get("questions"), list):
        raise ValueError("Fixture questions must be a list")
    return data
```

- [ ] **Step 5: Verify and commit**

Run:

```powershell
python -m pytest tools\kontext-v2\tests\test_benchmark_adapter.py -q
git add tools/kontext-v2/kontext_v2/benchmarks tools/kontext-v2/tests/fixtures/locomo_tiny.json tools/kontext-v2/tests/test_benchmark_adapter.py
git commit -m "test: add tiny locomo benchmark fixture"
```

Expected: tests pass and commit succeeds.

---

### Task 2: Kontext Benchmark Adapter

**Files:**
- Create: `tools/kontext-v2/kontext_v2/benchmarks/adapter.py`
- Modify: `tools/kontext-v2/kontext_v2/benchmarks/__init__.py`
- Modify: `tools/kontext-v2/tests/test_benchmark_adapter.py`

- [ ] **Step 1: Add failing adapter tests**

Append tests that:
- connect to `KONTEXT_V2_DATABASE_URL`;
- call `apply_schema(conn)`;
- instantiate `KontextBenchmarkAdapter(conn, dataset="locomo_tiny", run_id="unit-adapter-search")`;
- call `add([BenchmarkMessage("user", "Alice: Berlin trip in June.")], ...)`;
- assert the fetched memory metadata contains `source=benchmark`, `benchmark_run_id`, `profile=benchmark`, and `is_live_memory=false`;
- call `search("Where is Alice traveling in June?", "benchmark-locomo-tiny-1", top_k=5)`;
- assert results are Mem0-like dicts with `id`, `memory`, `score`, `metadata`, and no `query_debug`.

Expected test names:

```python
def test_adapter_add_messages_writes_isolated_benchmark_memories(): ...
def test_adapter_search_returns_mem0_like_results_without_raw_debug(): ...
```

- [ ] **Step 2: Run failing tests**

```powershell
python -m pytest tools\kontext-v2\tests\test_benchmark_adapter.py -q
```

Expected: FAIL with missing `kontext_v2.benchmarks.adapter`.

- [ ] **Step 3: Implement `adapter.py`**

Create `tools/kontext-v2/kontext_v2/benchmarks/adapter.py`:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import search_memories


@dataclass(frozen=True)
class BenchmarkMessage:
    role: str
    content: str


@dataclass(frozen=True)
class BenchmarkAddResult:
    results: list[dict[str, Any]]


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _message_text(messages: Iterable[BenchmarkMessage]) -> str:
    lines = []
    for message in messages:
        role = str(message.role or "unknown").strip().lower() or "unknown"
        content = " ".join(str(message.content or "").split())
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


class KontextBenchmarkAdapter:
    def __init__(self, conn: Any, dataset: str, run_id: str) -> None:
        self.conn = conn
        self.repo = KontextRepository(conn)
        self.dataset = dataset
        self.run_id = run_id

    def close(self) -> None:
        self.conn.close()

    def add(self, messages: list[BenchmarkMessage], user_id: str, conversation_id: str, session_id: str, timestamp: str | None = None) -> BenchmarkAddResult:
        text = _message_text(messages)
        digest = stable_hash("|".join([self.dataset, self.run_id, user_id, conversation_id, session_id, text]))
        external_id = f"benchmark:{self.dataset}:{self.run_id}:{digest[:16]}"
        metadata = {
            "source": "benchmark",
            "benchmark_dataset": self.dataset,
            "benchmark_run_id": self.run_id,
            "benchmark_user_id": user_id,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "timestamp": timestamp,
            "profile": "benchmark",
            "is_live_memory": False,
            "domains": ["benchmark"],
            "memory_type": "benchmark_observation",
            "current_status": "benchmark",
            "memory_tier": "cold",
            "signal_strength": 5,
        }
        record = MemoryRecord(
            external_mem0_id=external_id,
            title=f"{self.dataset} {conversation_id} {session_id}",
            text=text,
            metadata=metadata,
            memory_type="benchmark_observation",
            current_status="benchmark",
            memory_tier="cold",
            signal_strength=5,
            source_hash=digest,
        )
        self.repo.upsert_memory(record, version_source="benchmark")
        self.conn.commit()
        return BenchmarkAddResult(results=[{"id": external_id, "event": "ADD", "metadata": {"benchmark_dataset": self.dataset, "benchmark_run_id": self.run_id}}])

    def search(self, query: str, user_id: str, top_k: int = 200) -> list[dict[str, Any]]:
        rows = search_memories(self.repo, query=query, top_k=top_k, domains=[], memory_types=["benchmark_observation"], memory_tiers=["cold"], current_statuses=["benchmark"])
        results = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if metadata.get("source") != "benchmark" or metadata.get("benchmark_dataset") != self.dataset or metadata.get("benchmark_run_id") != self.run_id or metadata.get("benchmark_user_id") != user_id:
                continue
            results.append({
                "id": row.get("external_mem0_id") or row.get("id"),
                "memory": row.get("text") or row.get("memory") or "",
                "score": float(row.get("score") or 0.0),
                "metadata": {"benchmark_dataset": self.dataset, "benchmark_run_id": self.run_id, "conversation_id": metadata.get("conversation_id"), "session_id": metadata.get("session_id")},
            })
        return results[:top_k]
```

- [ ] **Step 4: Export adapter symbols**

Replace `tools/kontext-v2/kontext_v2/benchmarks/__init__.py` with:

```python
from __future__ import annotations

from kontext_v2.benchmarks.adapter import BenchmarkAddResult, BenchmarkMessage, KontextBenchmarkAdapter
from kontext_v2.benchmarks.fixtures import load_locomo_tiny_fixture

__all__ = ["BenchmarkAddResult", "BenchmarkMessage", "KontextBenchmarkAdapter", "load_locomo_tiny_fixture"]
```

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest tools\kontext-v2\tests\test_benchmark_adapter.py -q
git add tools/kontext-v2/kontext_v2/benchmarks tools/kontext-v2/tests/test_benchmark_adapter.py
git commit -m "feat: add kontext benchmark adapter"
```

Expected: tests pass and commit succeeds.

---

### Task 3: Report Writer And Predict-Only Runner

**Files:**
- Create: `tools/kontext-v2/kontext_v2/benchmarks/reporting.py`
- Create: `tools/kontext-v2/kontext_v2/benchmarks/locomo_predict.py`
- Create: `tools/kontext-v2/tests/test_benchmark_locomo_predict.py`

- [ ] **Step 1: Write failing tests**

Create `tools/kontext-v2/tests/test_benchmark_locomo_predict.py` with tests that:
- call `build_predict_only_report("locomo_tiny", "unit-report", 5, results)`;
- assert `matched_questions == 1`, category counts are present, and raw text like `Alice: Berlin trip` is absent from `str(report)`;
- call `write_report_files(report, tmp_path)` and assert `.json` and `.md` exist;
- call `run_locomo_predict_only(database_url, FIXTURE, tmp_path, "unit-runner", top_k=5)`;
- assert dataset is `locomo_tiny`, total questions are `3`, matched questions are `3`, and generated files do not include `Alice: Berlin` or `Mira: My cello`.

Use these test names:

```python
def test_report_uses_hashes_not_raw_memory(tmp_path): ...
def test_run_locomo_predict_only_writes_sanitized_report(tmp_path): ...
```

- [ ] **Step 2: Run failing tests**

```powershell
python -m pytest tools\kontext-v2\tests\test_benchmark_locomo_predict.py -q
```

Expected: FAIL with missing `reporting` or `locomo_predict` module.

- [ ] **Step 3: Implement `reporting.py`**

Create `tools/kontext-v2/kontext_v2/benchmarks/reporting.py` with:
- `build_predict_only_report(dataset, run_id, top_k, results) -> dict`
- `write_report_files(report, output_dir) -> {"json": Path, "markdown": Path}`

Required behavior:
- Store `dataset`, `run_id`, `mode="predict-only"`, `created_at`, `top_k`, `total_questions`, `matched_questions`, `match_rate`, `categories`, and `questions`.
- For each question, store `question_id`, `category`, `matched`, `search_latency_ms`, `result_ids`, and `result_hashes`.
- Compute `result_hashes` from memory text using `stable_hash(text)[:16]` but never store raw memory text in the report.
- Markdown report contains only aggregate counts and category counts.

- [ ] **Step 4: Implement `locomo_predict.py`**

Create `tools/kontext-v2/kontext_v2/benchmarks/locomo_predict.py` with:
- `_messages(raw_messages) -> list[BenchmarkMessage]`
- `_matched(search_results, expected_terms) -> bool`
- `run_locomo_predict_only(database_url, fixture_path, output_dir, run_id, top_k=200) -> dict`
- `main()` with args `--database-url`, `--fixture-path`, `--output-dir`, `--run-id`, and `--top-k`.

Required behavior:
- Load the tiny fixture.
- Apply schema.
- Ingest all sessions through `KontextBenchmarkAdapter.add`.
- For each question, search through the adapter.
- Mark a question matched only when all expected terms are present in retrieved memory text.
- Write sanitized reports.
- Print one aggregate line: `dataset=... run_id=... matched=X/Y json=...`.
- Do not print raw fixture memory text.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest tools\kontext-v2\tests\test_benchmark_locomo_predict.py -q
python -m kontext_v2.benchmarks.locomo_predict --database-url $env:KONTEXT_V2_DATABASE_URL --fixture-path tools\kontext-v2\tests\fixtures\locomo_tiny.json --output-dir tools\kontext-v2\benchmark-results --run-id local-smoke --top-k 5
git add tools/kontext-v2/kontext_v2/benchmarks/reporting.py tools/kontext-v2/kontext_v2/benchmarks/locomo_predict.py tools/kontext-v2/tests/test_benchmark_locomo_predict.py
git commit -m "feat: add tiny locomo predict-only runner"
```

Expected: tests pass, CLI prints `matched=3/3`, and commit succeeds.

---

### Task 4: Ignore Outputs, Document Usage, Verify

**Files:**
- Modify: `.gitignore`
- Modify: `tools/kontext-v2/README.md`
- Modify: `project_log.md`

- [ ] **Step 1: Ignore benchmark outputs**

Append to `.gitignore` if absent:

```gitignore
# Kontext benchmark outputs
tools/kontext-v2/benchmark-results/
benchmark-results/
```

- [ ] **Step 2: Document the smoke benchmark**

Append to `tools/kontext-v2/README.md`:

```markdown
## Tiny LoCoMo Predict-Only Benchmark

This smoke benchmark uses a synthetic local fixture and does not call answerer or judge models. It writes sanitized JSON and Markdown reports under `tools/kontext-v2/benchmark-results/`.

```powershell
python -m kontext_v2.benchmarks.locomo_predict `
  --database-url $env:KONTEXT_V2_DATABASE_URL `
  --fixture-path tools\kontext-v2\tests\fixtures\locomo_tiny.json `
  --output-dir tools\kontext-v2\benchmark-results `
  --run-id local-smoke `
  --top-k 5
```

Expected output is a one-line aggregate summary such as `matched=3/3`. The command must not print raw memory text or secrets.
```

- [ ] **Step 3: Run final verification**

```powershell
Set-Location -LiteralPath 'C:\Tools\OB1'
git check-ignore -v tools/kontext-v2/benchmark-results/example.json
python -m pytest tools\kontext-v2\tests\test_benchmark_adapter.py tools\kontext-v2\tests\test_benchmark_locomo_predict.py -q
python -m pytest tools\kontext-v2\tests -q
python -m kontext_v2.benchmarks.locomo_predict --database-url $env:KONTEXT_V2_DATABASE_URL --fixture-path tools\kontext-v2\tests\fixtures\locomo_tiny.json --output-dir tools\kontext-v2\benchmark-results --run-id final-smoke --top-k 5
rg -n "Alice:|Mira:|Berlin trip|cello recital" tools\kontext-v2\benchmark-results
```

Expected:
- `git check-ignore` prints the `.gitignore` rule.
- Focused tests pass.
- Full Kontext V2 tests pass.
- CLI prints `matched=3/3`.
- `rg` returns no matches in generated reports.

- [ ] **Step 4: Add project log entry and commit**

Append to `project_log.md` with actual verification results:

```markdown
## 2026-05-14 - Kontext V2 benchmark adapter milestone 1

- Summary: Added local benchmark adapter, tiny LoCoMo-style predict-only fixture, sanitized report writer, and CLI smoke runner for Kontext V2.
- Files touched: `tools/kontext-v2/kontext_v2/benchmarks/*`, benchmark tests, tiny fixture, `.gitignore`, `tools/kontext-v2/README.md`.
- Verification: focused benchmark tests passed; full Kontext V2 tests passed; CLI smoke produced `matched=3/3`; generated reports did not contain raw fixture text.
- Decisions: Kept benchmark data isolated with `source=benchmark`, `profile=benchmark`, and `is_live_memory=false`; no judged model calls; no live Mem0 writes; no VPS deploy.
- Next step: Add full LoCoMo adapter path against the upstream benchmark runner, then measure Kontext before changing retrieval scoring.
```

Run:

```powershell
git add .gitignore tools/kontext-v2/README.md project_log.md
git commit -m "docs: document kontext benchmark smoke run"
```

Expected: commit succeeds.

## Self-Review Checklist

- Spec coverage: Covers Milestone 1 from the design spec: adapter, tiny fixture, predict-only runner, sanitized report writer, isolation metadata, and local verification.
- Marker scan: The plan has no open-ended implementation markers.
- Type consistency: Tests and implementation use `BenchmarkMessage`, `BenchmarkAddResult`, `KontextBenchmarkAdapter.add`, `KontextBenchmarkAdapter.search`, `build_predict_only_report`, and `run_locomo_predict_only` consistently.
- Verification: Plan requires failing tests first, focused tests, full Kontext tests, CLI smoke, and a raw-text leakage scan.

