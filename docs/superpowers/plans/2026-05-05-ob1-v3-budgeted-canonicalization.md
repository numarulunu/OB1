# OB1 V3 Budgeted Canonicalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the OB1 Supabase `thoughts` table from roughly 14,385 rows toward 3,000-4,500 durable memories by replacing raw clusters with strict-budget canonical memories.

**Architecture:** Build a separate budgeted canonicalization recipe rather than extending the broad deletion pass. The new pass snapshots live rows, groups them into source/project/topic buckets, asks the model for a small capped set of canonical memories plus explicit delete/keep/escalate IDs, then backs up and deletes originals only after canonical inserts succeed.

**Tech Stack:** Python 3.14, pytest, requests, Supabase REST, OpenRouter chat completions, OpenRouter embeddings (`openai/text-embedding-3-small`), existing OB1 `.env.local` and `.local/model-bakeoff/.env` loading patterns.

---

## Current Constraints And Targets

Current known DB count after phase 1 + compression apply:

| Source | Current rows | V3 target |
| --- | ---: | ---: |
| `claude_history` | 10,069 | 1,500-2,000 |
| `shadow_cleanup` | 1,296 | 500-700 |
| `kontext` | 1,146 | 700-900 |
| `gemini` | 738 | 150-300 |
| `whatsapp` | 795 | 100-250 |
| `chatgpt` | 341 | 100-200 |
| **Total** | **14,385** | **3,000-4,500** |

Rules:

- Do not print secrets or raw memory contents.
- Every destructive operation must write an exact JSONL backup first.
- Never delete a row unless it is either explicitly in `delete_source_ids` or explicitly covered by a canonical memory and the proposal says the bucket is complete.
- Keep/error/escalate IDs must never be deleted.
- Store malformed model raw responses only in local `.local/open-brain-cleanup/budgeted/raw-failures/`; never print them.
- The remaining OpenRouter budget is limited. Pilot first, then scale only if the reduction ratio is good.

## File Structure

- Create: `recipes/shadow-cleanup/budgeted_canonicalize.py`
  - Owns v3 snapshot reading, bucket building, pack generation, model prompts, proposal parsing, dry-run planning, backup, canonical insertion, and source deletion.
- Create: `recipes/shadow-cleanup/test_budgeted_canonicalize.py`
  - Unit tests for bucket keys, budget calculation, proposal parsing, apply planning, and safety rules.
- Modify: `recipes/shadow-cleanup/README.md`
  - Add v3 commands and safety notes.
- Modify: `tool_registry.md`
  - Bump Shadow Cleanup entry to mention budgeted canonicalization.
- Runtime artifacts:
  - `.local/open-brain-cleanup/budgeted/snapshots/`
  - `.local/open-brain-cleanup/budgeted/packs/`
  - `.local/open-brain-cleanup/budgeted/proposals/`
  - `.local/open-brain-cleanup/budgeted/backups/`
  - `.local/open-brain-cleanup/budgeted/raw-failures/`
  - `.local/open-brain-cleanup/budgeted/apply/budgeted-sync-log.json`

---

### Task 1: Add Pure Budget And Bucket Tests

**Files:**
- Create: `recipes/shadow-cleanup/test_budgeted_canonicalize.py`

- [ ] **Step 1: Write tests for source budgets, bucket keys, and canonical caps**

Create `recipes/shadow-cleanup/test_budgeted_canonicalize.py` with:

```python
import importlib.util
from pathlib import Path

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"


def load_budgeted():
    module_path = Path(__file__).with_name("budgeted_canonicalize.py")
    spec = importlib.util.spec_from_file_location("budgeted_canonicalize", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def thought(record_id, content, metadata=None):
    return {"id": record_id, "content": content, "metadata": metadata or {}}


def test_bucket_key_uses_source_project_type_and_topic_family():
    budgeted = load_budgeted()
    row = thought(
        UUID_A,
        "Parser status should not become durable memory.",
        {"source": "claude_history", "type": "context", "cwd": "C:/Tools/OB1", "topics": ["parser", "cleanup"]},
    )

    key = budgeted.bucket_key(row)

    assert key == "source:claude_history|project:c-tools-ob1|type:context|topic:parser"


def test_bucket_key_defaults_missing_metadata_safely():
    budgeted = load_budgeted()
    row = thought(UUID_A, "A row with no useful metadata.")

    key = budgeted.bucket_key(row)

    assert key == "source:missing|project:missing|type:missing|topic:missing"


def test_canonical_limit_is_strict_for_large_claude_buckets():
    budgeted = load_budgeted()

    assert budgeted.canonical_limit_for_bucket("claude_history", 50) == 3
    assert budgeted.canonical_limit_for_bucket("claude_history", 25) == 2
    assert budgeted.canonical_limit_for_bucket("claude_history", 5) == 1


def test_canonical_limit_preserves_more_kontext_than_claude():
    budgeted = load_budgeted()

    assert budgeted.canonical_limit_for_bucket("kontext", 50) == 8
    assert budgeted.canonical_limit_for_bucket("gemini", 50) == 4
```

- [ ] **Step 2: Run tests and verify they fail because the module does not exist**

Run:

```powershell
python -m pytest recipes\shadow-cleanup\test_budgeted_canonicalize.py -q
```

Expected: FAIL with `FileNotFoundError` or import failure for `budgeted_canonicalize.py`.

---

### Task 2: Implement Budget Policy And Bucket Helpers

**Files:**
- Create: `recipes/shadow-cleanup/budgeted_canonicalize.py`

- [ ] **Step 1: Create the module with constants and pure helpers**

Create `recipes/shadow-cleanup/budgeted_canonicalize.py` with this starting implementation:

```python
#!/usr/bin/env python3
"""Budgeted canonical rewrite pass for OB1 thoughts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

VERSION = "0.1"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / ".local" / "open-brain-cleanup" / "budgeted"
LOG_PATH = Path(__file__).resolve().parent / "_budgeted-canonicalize.log"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

SUPABASE_URL = ""
SUPABASE_SERVICE_ROLE_KEY = ""
OPENROUTER_API_KEY = ""


@dataclass(frozen=True)
class SourceBudget:
    target_rows: int
    max_pack_rows: int
    max_canonicals_per_full_pack: int
    min_delete_ratio: float


SOURCE_BUDGETS: dict[str, SourceBudget] = {
    "claude_history": SourceBudget(target_rows=1800, max_pack_rows=50, max_canonicals_per_full_pack=3, min_delete_ratio=0.70),
    "shadow_cleanup": SourceBudget(target_rows=600, max_pack_rows=50, max_canonicals_per_full_pack=5, min_delete_ratio=0.55),
    "kontext": SourceBudget(target_rows=850, max_pack_rows=50, max_canonicals_per_full_pack=8, min_delete_ratio=0.30),
    "gemini": SourceBudget(target_rows=250, max_pack_rows=40, max_canonicals_per_full_pack=4, min_delete_ratio=0.60),
    "whatsapp": SourceBudget(target_rows=200, max_pack_rows=40, max_canonicals_per_full_pack=3, min_delete_ratio=0.70),
    "chatgpt": SourceBudget(target_rows=150, max_pack_rows=40, max_canonicals_per_full_pack=3, min_delete_ratio=0.60),
    "missing": SourceBudget(target_rows=100, max_pack_rows=40, max_canonicals_per_full_pack=2, min_delete_ratio=0.75),
}

TEMP_MARKERS = ("\\temp", "/tmp", "appdata\\local\\temp", "codex", "claude-code")


class BudgetedCanonicalizeError(RuntimeError):
    """Plain-language CLI error."""


def clean_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return cleaned[:80] or "missing"


def metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata")
    return value if isinstance(value, dict) else {}


def source_of(record: dict[str, Any]) -> str:
    return clean_key(str(metadata(record).get("source") or "missing"))


def project_key(record: dict[str, Any]) -> str:
    meta = metadata(record)
    value = str(meta.get("claude_project") or meta.get("cwd") or meta.get("project") or "missing")
    lower = value.lower()
    if any(marker in lower for marker in TEMP_MARKERS):
        return "temp-working-dir"
    return clean_key(value)


def topic_key(record: dict[str, Any]) -> str:
    topics = metadata(record).get("topics")
    if isinstance(topics, list) and topics:
        return clean_key(str(topics[0]))
    return "missing"


def bucket_key(record: dict[str, Any]) -> str:
    meta = metadata(record)
    return (
        f"source:{source_of(record)}|"
        f"project:{project_key(record)}|"
        f"type:{clean_key(str(meta.get('type') or 'missing'))}|"
        f"topic:{topic_key(record)}"
    )


def canonical_limit_for_bucket(source: str, row_count: int) -> int:
    budget = SOURCE_BUDGETS.get(clean_key(source), SOURCE_BUDGETS["missing"])
    if row_count <= 0:
        return 0
    scaled = math.ceil(row_count / max(1, budget.max_pack_rows) * budget.max_canonicals_per_full_pack)
    return max(1, min(budget.max_canonicals_per_full_pack, scaled))
```

- [ ] **Step 2: Run pure helper tests**

Run:

```powershell
python -m pytest recipes\shadow-cleanup\test_budgeted_canonicalize.py -q
```

Expected: PASS for the four tests from Task 1.

---

### Task 3: Add Snapshot Reading And Rewrite Pack Building

**Files:**
- Modify: `recipes/shadow-cleanup/budgeted_canonicalize.py`
- Modify: `recipes/shadow-cleanup/test_budgeted_canonicalize.py`

- [ ] **Step 1: Add tests for pack generation and budget caps**

Append to `test_budgeted_canonicalize.py`:

```python
def test_build_rewrite_packs_enforces_canonical_limit_and_pack_size():
    budgeted = load_budgeted()
    rows = [
        thought(str(i).zfill(8) + "-1111-4111-8111-111111111111", f"Claude row {i}", {"source": "claude_history", "type": "context", "cwd": "C:/Tools/OB1", "topics": ["parser"]})
        for i in range(55)
    ]

    packs = budgeted.build_rewrite_packs(rows, source="claude_history", limit_rows=0)

    assert len(packs) == 2
    assert packs[0]["canonical_limit"] == 3
    assert packs[0]["record_count"] == 50
    assert packs[1]["canonical_limit"] == 1
    assert packs[1]["record_count"] == 5


def test_build_rewrite_packs_can_limit_rows_for_pilot():
    budgeted = load_budgeted()
    rows = [
        thought(str(i).zfill(8) + "-1111-4111-8111-111111111111", f"Claude row {i}", {"source": "claude_history", "type": "context"})
        for i in range(20)
    ]

    packs = budgeted.build_rewrite_packs(rows, source="claude_history", limit_rows=7)

    assert sum(pack["record_count"] for pack in packs) == 7
```

- [ ] **Step 2: Implement pack helpers**

Add to `budgeted_canonicalize.py`:

```python
def ensure_output_dirs(root: Path) -> None:
    for name in ("snapshots", "packs", "proposals", "backups", "raw-failures", "apply", "reports"):
        (root / name).mkdir(parents=True, exist_ok=True)


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def pack_id_for(cluster_key: str, items: list[dict[str, Any]], canonical_limit: int) -> str:
    ids = [str(item.get("id") or "") for item in items]
    raw = json.dumps([cluster_key, ids, canonical_limit], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def compact_item(record: dict[str, Any]) -> dict[str, Any]:
    meta = metadata(record)
    return {
        "id": record.get("id"),
        "content": record.get("content") or "",
        "source": meta.get("source"),
        "type": meta.get("type"),
        "topics": meta.get("topics") if isinstance(meta.get("topics"), list) else [],
        "project": meta.get("claude_project") or meta.get("cwd") or meta.get("project"),
    }


def build_rewrite_packs(records: list[dict[str, Any]], source: str = "all", limit_rows: int = 0) -> list[dict[str, Any]]:
    selected = [row for row in records if source == "all" or source_of(row) == clean_key(source)]
    selected.sort(key=lambda row: bucket_key(row))
    if limit_rows:
        selected = selected[:limit_rows]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in selected:
        grouped.setdefault(bucket_key(row), []).append(row)

    packs: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        source_name = source_of(rows[0]) if rows else "missing"
        budget = SOURCE_BUDGETS.get(source_name, SOURCE_BUDGETS["missing"])
        for start in range(0, len(rows), budget.max_pack_rows):
            chunk = rows[start : start + budget.max_pack_rows]
            canonical_limit = canonical_limit_for_bucket(source_name, len(chunk))
            items = [compact_item(row) for row in chunk]
            packs.append(
                {
                    "pack_id": pack_id_for(key, items, canonical_limit),
                    "kind": "budgeted_canonicalize_v1",
                    "cluster_key": key,
                    "source": source_name,
                    "record_count": len(items),
                    "canonical_limit": canonical_limit,
                    "min_delete_ratio": budget.min_delete_ratio,
                    "items": items,
                }
            )
    return packs
```

- [ ] **Step 3: Run tests**

Run:

```powershell
python -m pytest recipes\shadow-cleanup\test_budgeted_canonicalize.py -q
```

Expected: PASS.

---

### Task 4: Add Strict Budgeted Prompt And Model Runner

**Files:**
- Modify: `recipes/shadow-cleanup/budgeted_canonicalize.py`
- Modify: `recipes/shadow-cleanup/test_budgeted_canonicalize.py`

- [ ] **Step 1: Add prompt tests that enforce hard caps and every-ID accounting**

Append:

```python
def test_build_prompt_contains_hard_canonical_limit_and_every_id_rule():
    budgeted = load_budgeted()
    pack = {
        "pack_id": "pack1",
        "source": "claude_history",
        "canonical_limit": 3,
        "min_delete_ratio": 0.7,
        "items": [{"id": UUID_A, "content": "row a"}, {"id": UUID_B, "content": "row b"}],
    }

    prompt = budgeted.build_budgeted_prompt(pack)

    assert "at most 3 canonical_memories" in prompt
    assert "Every input id must appear exactly once" in prompt
    assert "delete_source_ids" in prompt
    assert "keep_source_ids" in prompt
    assert "escalate_source_ids" in prompt
```

- [ ] **Step 2: Implement strict prompt and parser**

Add:

```python
def build_budgeted_prompt(pack: dict[str, Any]) -> str:
    public_pack = {
        "pack_id": pack.get("pack_id"),
        "source": pack.get("source"),
        "cluster_key": pack.get("cluster_key"),
        "canonical_limit": pack.get("canonical_limit"),
        "min_delete_ratio": pack.get("min_delete_ratio"),
        "items": pack.get("items") or [],
    }
    limit = int(pack.get("canonical_limit") or 1)
    return f"""
You are rebuilding a second-brain memory database under a hard row budget.

Goal: replace raw, redundant, stale, or implementation-level rows with a tiny set of durable canonical memories.

Return strict JSON only. Do not include prose.

Hard rules:
- Create at most {limit} canonical_memories.
- Every input id must appear exactly once across canonical_memories.source_ids, delete_source_ids, keep_source_ids, or escalate_source_ids.
- Prefer canonical memories over raw keeps.
- Keep max 2 raw rows unless the pack contains critical current identity, money, legal/fiscal, relationship, or active project constraints.
- Escalate max 1 row unless the pack is genuinely unsafe.
- Delete implementation detail, process noise, obsolete choices, repeated facts, and raw chatter.
- For code/project/workflow material, keep the purpose and current architecture, not function names, line numbers, test status, flags, or logs.
- For psychology/relationships, keep stable current patterns and important close/influential people, not one-off incidents.
- For voice/vocal material, keep only current Vocality/brand/method IP or reusable teaching assets; delete raw lesson chatter.
- If a row is important, compress it into a canonical memory and delete the raw original unless the exact wording must remain as a reference.

Schema:
{{
  "pack_id": "same pack id",
  "bucket_status": "complete|partial|unsafe",
  "canonical_memories": [
    {{"content": "current durable memory", "source_ids": ["ids"], "type": "decision|preference|context|learning|reference|goal|project|workflow|pattern", "topics": ["short topics"], "confidence": 0.0, "reason": "short non-private reason"}}
  ],
  "delete_source_ids": ["ids safe to delete after canonicals are inserted"],
  "keep_source_ids": ["ids that must remain raw"],
  "escalate_source_ids": ["ids too risky for automated handling"],
  "risk_notes": ["short notes, no raw quotes"]
}}

Pack:
{json.dumps(public_pack, ensure_ascii=False)}
""".strip()


def parse_model_json(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise BudgetedCanonicalizeError("Model response was not valid JSON.")
        parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise BudgetedCanonicalizeError("Model response had an unexpected shape.")
    return parsed
```

- [ ] **Step 3: Implement OpenRouter runner with local raw-failure capture**

Add functions:

```python
def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalize_supabase_url(url: str) -> str:
    value = str(url or "").strip().rstrip("/")
    if value.endswith("/rest/v1"):
        value = value[: -len("/rest/v1")]
    return value.rstrip("/")


def refresh_env() -> None:
    global SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENROUTER_API_KEY
    SUPABASE_URL = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


def require_openrouter_env() -> None:
    if not OPENROUTER_API_KEY:
        raise BudgetedCanonicalizeError("Missing required environment variable: OPENROUTER_API_KEY")


def openrouter_chat(model: str, prompt: str, max_tokens: int, temperature: float) -> str:
    response = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "Return strict JSON only for database canonicalization."},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        },
        timeout=240,
    )
    if response.status_code != 200:
        raise BudgetedCanonicalizeError(f"OpenRouter request failed with HTTP {response.status_code}.")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise BudgetedCanonicalizeError("OpenRouter response had an unexpected shape.") from exc
    if not isinstance(content, str) or not content.strip():
        raise BudgetedCanonicalizeError("OpenRouter response was empty.")
    return content
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest recipes\shadow-cleanup\test_budgeted_canonicalize.py -q
```

Expected: PASS.

---

### Task 5: Add Proposal Validation And Apply Planning

**Files:**
- Modify: `recipes/shadow-cleanup/budgeted_canonicalize.py`
- Modify: `recipes/shadow-cleanup/test_budgeted_canonicalize.py`

- [ ] **Step 1: Add validation tests**

Append:

```python
def test_validate_proposal_requires_every_id_once():
    budgeted = load_budgeted()
    pack = {"pack_id": "p", "canonical_limit": 1, "items": [{"id": UUID_A}, {"id": UUID_B}]}
    proposal = {
        "pack_id": "p",
        "bucket_status": "complete",
        "canonical_memories": [{"content": "summary", "source_ids": [UUID_A], "confidence": 0.9}],
        "delete_source_ids": [],
        "keep_source_ids": [],
        "escalate_source_ids": [],
    }

    result = budgeted.validate_proposal(pack, proposal)

    assert result["ok"] is False
    assert "missing_ids" in result


def test_validate_proposal_rejects_canonical_limit_overflow():
    budgeted = load_budgeted()
    pack = {"pack_id": "p", "canonical_limit": 1, "items": [{"id": UUID_A}, {"id": UUID_B}]}
    proposal = {
        "pack_id": "p",
        "bucket_status": "complete",
        "canonical_memories": [
            {"content": "summary a", "source_ids": [UUID_A], "confidence": 0.9},
            {"content": "summary b", "source_ids": [UUID_B], "confidence": 0.9},
        ],
        "delete_source_ids": [],
        "keep_source_ids": [],
        "escalate_source_ids": [],
    }

    result = budgeted.validate_proposal(pack, proposal)

    assert result["ok"] is False
    assert result["canonical_limit_exceeded"] is True
```

- [ ] **Step 2: Implement validation**

Add:

```python
def valid_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if UUID_RE.match(str(item).strip())]


def validate_proposal(pack: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    input_ids = [str(item.get("id") or "") for item in pack.get("items") or []]
    input_set = set(input_ids)
    canonical_ids: list[str] = []
    memories = proposal.get("canonical_memories") or []
    if not isinstance(memories, list):
        memories = []
    for memory in memories:
        if isinstance(memory, dict):
            canonical_ids.extend(valid_id_list(memory.get("source_ids")))
    delete_ids = valid_id_list(proposal.get("delete_source_ids"))
    keep_ids = valid_id_list(proposal.get("keep_source_ids"))
    escalate_ids = valid_id_list(proposal.get("escalate_source_ids"))
    all_ids = canonical_ids + delete_ids + keep_ids + escalate_ids
    counts: dict[str, int] = {}
    for thought_id in all_ids:
        counts[thought_id] = counts.get(thought_id, 0) + 1
    duplicate_ids = sorted([thought_id for thought_id, count in counts.items() if count > 1])
    unknown_ids = sorted([thought_id for thought_id in counts if thought_id not in input_set])
    missing_ids = sorted([thought_id for thought_id in input_set if thought_id not in counts])
    canonical_limit = int(pack.get("canonical_limit") or 0)
    canonical_limit_exceeded = len(memories) > canonical_limit
    ok = not duplicate_ids and not unknown_ids and not missing_ids and not canonical_limit_exceeded
    return {
        "ok": ok,
        "duplicate_ids": duplicate_ids,
        "unknown_ids": unknown_ids,
        "missing_ids": missing_ids,
        "canonical_limit_exceeded": canonical_limit_exceeded,
        "canonical_count": len(memories),
        "delete_count": len(delete_ids),
        "keep_count": len(keep_ids),
        "escalate_count": len(escalate_ids),
    }
```

- [ ] **Step 3: Run tests**

Run:

```powershell
python -m pytest recipes\shadow-cleanup\test_budgeted_canonicalize.py -q
```

Expected: PASS.

---

### Task 6: Add CLI Commands For Snapshot, Pack, Run, Summarize

**Files:**
- Modify: `recipes/shadow-cleanup/budgeted_canonicalize.py`

- [ ] **Step 1: Implement Supabase snapshot helpers**

Add:

```python
def supabase_headers(prefer: str = "return=representation") -> dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def require_supabase_env() -> None:
    missing = [name for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not os.environ.get(name)]
    if missing:
        raise BudgetedCanonicalizeError("Missing required environment variables: " + ", ".join(missing))


def fetch_thoughts(source: str, page_size: int, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    offset = 0
    while True:
        if limit is not None and len(rows) >= limit:
            break
        take = page_size if limit is None else min(page_size, limit - len(rows))
        params = {"select": "id,content,metadata,created_at,updated_at"}
        if source != "all":
            params["metadata->>source"] = f"eq.{source}"
        response = requests.get(base, headers={**supabase_headers(), "Range": f"{offset}-{offset + take - 1}"}, params=params, timeout=120)
        if response.status_code not in (200, 206):
            raise BudgetedCanonicalizeError(f"Supabase snapshot request failed with HTTP {response.status_code}.")
        batch = response.json()
        if not isinstance(batch, list):
            raise BudgetedCanonicalizeError("Supabase snapshot response had an unexpected shape.")
        rows.extend(batch)
        if len(batch) < take:
            break
        offset += take
    return rows
```

- [ ] **Step 2: Implement CLI command bodies**

Add:

```python
def cmd_snapshot(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    rows = fetch_thoughts(args.source, args.page_size, args.limit_rows)
    output = args.output or args.output_root / "snapshots" / f"{args.source}-{utc_stamp()}.jsonl"
    count = write_jsonl(output, rows)
    print(f"snapshot_rows={count}")
    print(f"snapshot_file={output}")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    records = read_jsonl(args.snapshot)
    packs = build_rewrite_packs(records, source=args.source, limit_rows=args.limit_rows)
    output = args.output or args.output_root / "packs" / f"packs-{args.source}-{utc_stamp()}.jsonl"
    count = write_jsonl(output, packs)
    print(f"pack_count={count}")
    print(f"pack_file={output}")
    return 0


def processed_pack_ids(path: Path) -> set[str]:
    processed: set[str] = set()
    if not path.exists():
        return processed
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row.get("pack_id"), str) and not row.get("error"):
            processed.add(row["pack_id"])
    return processed


def cmd_run(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    packs = read_jsonl(args.pack_file)
    output = args.output or args.output_root / "proposals" / f"proposals-{args.model.replace('/', '-')}-{utc_stamp()}.jsonl"
    processed = processed_pack_ids(output)
    written = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for pack in packs:
            if args.limit_packs and written >= args.limit_packs:
                break
            pack_id = str(pack.get("pack_id") or "")
            if pack_id in processed:
                continue
            try:
                content = openrouter_chat(args.model, build_budgeted_prompt(pack), args.max_tokens, args.temperature)
                proposal = parse_model_json(content)
                validation = validate_proposal(pack, proposal)
                if not validation["ok"]:
                    raise BudgetedCanonicalizeError("Proposal failed validation: " + json.dumps(validation, sort_keys=True))
                row = {"pack_id": pack_id, "pack_kind": pack.get("kind"), "source": pack.get("source"), "cluster_key": pack.get("cluster_key"), "model": args.model, "created_at": dt.datetime.now(dt.UTC).isoformat(), "proposal": proposal}
            except Exception as exc:
                row = {"pack_id": pack_id, "pack_kind": pack.get("kind"), "source": pack.get("source"), "cluster_key": pack.get("cluster_key"), "model": args.model, "created_at": dt.datetime.now(dt.UTC).isoformat(), "error": str(exc)}
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            written += 1
            time.sleep(args.sleep_seconds)
    print(f"proposal_records_written={written}")
    print(f"proposal_file={output}")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    rows = []
    errors = 0
    for path in args.proposals:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("error"):
                errors += 1
            elif isinstance(row.get("proposal"), dict):
                rows.append(row)
    canonical = sum(len(row["proposal"].get("canonical_memories") or []) for row in rows)
    delete_ids = len(set(source_id for row in rows for source_id in valid_id_list(row["proposal"].get("delete_source_ids"))))
    keep_ids = len(set(source_id for row in rows for source_id in valid_id_list(row["proposal"].get("keep_source_ids"))))
    escalate_ids = len(set(source_id for row in rows for source_id in valid_id_list(row["proposal"].get("escalate_source_ids"))))
    print(f"summary proposal_rows={len(rows)} errors={errors} canonical_memories={canonical} delete_ids={delete_ids} keep_ids={keep_ids} escalate_ids={escalate_ids}")
    return 0
```

- [ ] **Step 3: Add `build_parser()` and `main()`**

Add:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Budgeted OB1 canonical rewrite pipeline.")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--version", action="version", version=f"budgeted-canonicalize {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--source", default="all")
    snapshot.add_argument("--limit-rows", type=int)
    snapshot.add_argument("--page-size", type=int, default=1000)
    snapshot.add_argument("--output", type=Path)
    snapshot.set_defaults(func=cmd_snapshot)

    pack = subparsers.add_parser("pack")
    pack.add_argument("--snapshot", required=True, type=Path)
    pack.add_argument("--source", default="all")
    pack.add_argument("--limit-rows", type=int, default=0)
    pack.add_argument("--output", type=Path)
    pack.set_defaults(func=cmd_pack)

    run = subparsers.add_parser("run")
    run.add_argument("--pack-file", required=True, type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--limit-packs", type=int, default=0)
    run.add_argument("--max-tokens", type=int, default=3500)
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--sleep-seconds", type=float, default=0.5)
    run.set_defaults(func=cmd_run)

    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("proposals", nargs="+", type=Path)
    summarize.set_defaults(func=cmd_summarize)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
    load_env_file(REPO_ROOT / ".env.local")
    load_env_file(REPO_ROOT / ".local" / "model-bakeoff" / ".env")
    refresh_env()
    if args.command == "snapshot":
        require_supabase_env()
    if args.command == "run":
        require_openrouter_env()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BudgetedCanonicalizeError as exc:
        logging.error("failed %s", exc)
        print(f"error={exc}", file=sys.stderr)
        raise SystemExit(1)
```

- [ ] **Step 4: Verify CLI help**

Run:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py --help
python recipes\shadow-cleanup\budgeted_canonicalize.py run --help
```

Expected: both commands print help and exit 0.

---

### Task 7: Add Safe Apply Command

**Files:**
- Modify: `recipes/shadow-cleanup/budgeted_canonicalize.py`
- Modify: `recipes/shadow-cleanup/test_budgeted_canonicalize.py`

- [ ] **Step 1: Add tests for apply planning**

Append:

```python
def test_apply_plan_never_deletes_keep_or_escalate_ids():
    budgeted = load_budgeted()
    proposal_rows = [
        {
            "pack_id": "p",
            "proposal": {
                "canonical_memories": [{"content": "summary", "source_ids": [UUID_A], "confidence": 0.9}],
                "delete_source_ids": [UUID_A, UUID_B],
                "keep_source_ids": [UUID_B],
                "escalate_source_ids": [UUID_C],
            },
        }
    ]

    plan = budgeted.build_apply_plan(proposal_rows)

    assert plan["canonical_count"] == 1
    assert plan["delete_ids"] == [UUID_A]
    assert plan["blocked_keep_or_escalate_ids"] == [UUID_B]
```

- [ ] **Step 2: Implement apply planner and DB mutation helpers**

Add:

```python
def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, dict) else default


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(path)


def text_hash(text: str) -> str:
    normalized = " ".join(str(text or "").lower().strip().split())
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def build_apply_plan(proposal_rows: list[dict[str, Any]]) -> dict[str, Any]:
    canonicals: list[dict[str, Any]] = []
    delete_ids: list[str] = []
    keep_or_escalate: set[str] = set()
    for row in proposal_rows:
        proposal = row.get("proposal") or {}
        keep_or_escalate.update(valid_id_list(proposal.get("keep_source_ids")))
        keep_or_escalate.update(valid_id_list(proposal.get("escalate_source_ids")))
    for row in proposal_rows:
        proposal = row.get("proposal") or {}
        for memory in proposal.get("canonical_memories") or []:
            if isinstance(memory, dict) and str(memory.get("content") or "").strip():
                canonicals.append({"row": row, "memory": memory})
        for thought_id in valid_id_list(proposal.get("delete_source_ids")):
            if thought_id not in keep_or_escalate and thought_id not in delete_ids:
                delete_ids.append(thought_id)
    blocked = sorted([thought_id for row in proposal_rows for thought_id in valid_id_list((row.get("proposal") or {}).get("delete_source_ids")) if thought_id in keep_or_escalate])
    return {"canonical_count": len(canonicals), "canonicals": canonicals, "delete_ids": delete_ids, "blocked_keep_or_escalate_ids": blocked}
```

- [ ] **Step 3: Implement Supabase fetch/delete/insert helpers**

Use the same pattern as `aggressive_cleanup.py`:

```python
def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_thoughts_by_ids(ids: list[str], chunk_size: int = 100) -> list[dict[str, Any]]:
    valid_ids = [thought_id for thought_id in dict.fromkeys(ids) if UUID_RE.match(thought_id)]
    rows: list[dict[str, Any]] = []
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    for batch_ids in chunked(valid_ids, chunk_size):
        response = requests.get(base, headers=supabase_headers(), params={"select": "id,content,metadata,created_at,updated_at", "id": f"in.({','.join(batch_ids)})"}, timeout=120)
        if response.status_code != 200:
            raise BudgetedCanonicalizeError(f"Supabase fetch-by-id failed with HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, list):
            raise BudgetedCanonicalizeError("Supabase fetch-by-id response had an unexpected shape.")
        rows.extend(payload)
    return rows


def delete_thoughts_by_ids(ids: list[str], chunk_size: int = 100) -> int:
    valid_ids = [thought_id for thought_id in dict.fromkeys(ids) if UUID_RE.match(thought_id)]
    deleted = 0
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    for batch_ids in chunked(valid_ids, chunk_size):
        response = requests.delete(base, headers=supabase_headers(prefer="return=representation"), params={"select": "id", "id": f"in.({','.join(batch_ids)})"}, timeout=120)
        if response.status_code not in (200, 204):
            raise BudgetedCanonicalizeError(f"Supabase delete failed with HTTP {response.status_code}.")
        if response.status_code == 204 or not response.text.strip():
            deleted += len(batch_ids)
        else:
            payload = response.json()
            deleted += len(payload) if isinstance(payload, list) else 0
    return deleted


def generate_embedding(text: str) -> list[float]:
    response = requests.post(f"{OPENROUTER_BASE}/embeddings", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}, json={"model": EMBEDDING_MODEL, "input": str(text or "")[:8000]}, timeout=120)
    if response.status_code != 200:
        raise BudgetedCanonicalizeError(f"Embedding request failed with HTTP {response.status_code}.")
    return response.json()["data"][0]["embedding"]


def insert_canonical_memory(content: str, metadata: dict[str, Any]) -> str:
    embedding = generate_embedding(content)
    response = requests.post(f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts?select=id", headers=supabase_headers(prefer="return=representation"), json={"content": content, "embedding": embedding, "metadata": metadata}, timeout=120)
    if response.status_code not in (200, 201):
        raise BudgetedCanonicalizeError(f"Canonical insert failed with HTTP {response.status_code}.")
    return str(response.json()[0]["id"])
```

- [ ] **Step 4: Implement `apply` command**

Add:

```python
def load_valid_proposal_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            pack_id = row.get("pack_id")
            if row.get("error") or not isinstance(row.get("proposal"), dict) or not isinstance(pack_id, str) or pack_id in seen:
                continue
            seen.add(pack_id)
            rows.append(row)
    return rows


def canonical_metadata(row: dict[str, Any], memory: dict[str, Any], batch: str) -> dict[str, Any]:
    return {
        "source": "shadow_cleanup",
        "import_mode": "budgeted_canonicalize_v1",
        "type": memory.get("type") or "context",
        "topics": memory.get("topics") if isinstance(memory.get("topics"), list) else [],
        "budgeted_pack_id": row.get("pack_id"),
        "budgeted_source": row.get("source"),
        "budgeted_cluster_key": row.get("cluster_key"),
        "budgeted_source_ids": valid_id_list(memory.get("source_ids")),
        "budgeted_confidence": memory.get("confidence"),
        "budgeted_reason": memory.get("reason", ""),
        "budgeted_batch": batch,
        "created_by": "budgeted_canonicalize_v1",
    }


def cmd_apply(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    rows = load_valid_proposal_rows(args.proposals)
    plan = build_apply_plan(rows)
    if args.limit_packs:
        allowed_pack_ids = {row["pack_id"] for row in rows[: args.limit_packs]}
        rows = [row for row in rows if row["pack_id"] in allowed_pack_ids]
        plan = build_apply_plan(rows)
    fetched = fetch_thoughts_by_ids(plan["delete_ids"])
    fetched_ids = {str(row.get("id")) for row in fetched}
    live_delete_ids = [thought_id for thought_id in plan["delete_ids"] if thought_id in fetched_ids]
    backup_file = args.backup_output or args.output_root / "backups" / f"budgeted-backup-{utc_stamp()}.jsonl"
    if args.max_delete and len(live_delete_ids) > args.max_delete:
        raise BudgetedCanonicalizeError(f"Apply plan has {len(live_delete_ids)} live deletes, above --max-delete {args.max_delete}.")
    inserted = 0
    reused = 0
    deleted = 0
    if args.apply:
        backed_up = write_jsonl(backup_file, fetched)
        if backed_up != len(fetched):
            raise BudgetedCanonicalizeError("Backup row count did not match fetched delete rows.")
        sync = read_json(args.sync_log, {"version": VERSION, "canonical_hashes": {}, "batches": {}})
        hashes = sync.setdefault("canonical_hashes", {})
        batch = utc_stamp()
        for item in plan["canonicals"]:
            row = item["row"]
            memory = item["memory"]
            content = str(memory.get("content") or "").strip()
            digest = text_hash(content)
            if digest in hashes:
                reused += 1
                continue
            thought_id = insert_canonical_memory(content, canonical_metadata(row, memory, batch))
            hashes[digest] = thought_id
            inserted += 1
            write_json(args.sync_log, sync)
        deleted = delete_thoughts_by_ids(live_delete_ids)
        sync.setdefault("batches", {})[batch] = {"canonical_inserted": inserted, "canonical_reused": reused, "source_deleted": deleted, "backup_file": str(backup_file), "applied_at": dt.datetime.now(dt.UTC).isoformat()}
        write_json(args.sync_log, sync)
    print(f"apply_plan proposal_rows={len(rows)} canonicals={plan['canonical_count']} live_delete_ids={len(live_delete_ids)} blocked_keep_or_escalate={len(plan['blocked_keep_or_escalate_ids'])} inserted={inserted} reused={reused} deleted={deleted} apply={args.apply} backup_file={backup_file if args.apply else 'none'}")
    return 0
```

- [ ] **Step 5: Register parser subcommand**

Add in `build_parser()`:

```python
    apply = subparsers.add_parser("apply")
    apply.add_argument("proposals", nargs="+", type=Path)
    apply.add_argument("--apply", action="store_true")
    apply.add_argument("--limit-packs", type=int, default=0)
    apply.add_argument("--max-delete", type=int, default=1000)
    apply.add_argument("--backup-output", type=Path)
    apply.add_argument("--sync-log", type=Path, default=OUTPUT_ROOT / "apply" / "budgeted-sync-log.json")
    apply.set_defaults(func=cmd_apply)
```

Update `main()` so `apply` requires both Supabase and OpenRouter env:

```python
    if args.command in {"snapshot", "apply"}:
        require_supabase_env()
    if args.command in {"run", "apply"}:
        require_openrouter_env()
```

- [ ] **Step 6: Run tests**

Run:

```powershell
python -m pytest recipes\shadow-cleanup\test_budgeted_canonicalize.py -q
```

Expected: PASS.

---

### Task 8: Document Commands And Tool Registry

**Files:**
- Modify: `recipes/shadow-cleanup/README.md`
- Modify: `tool_registry.md`

- [ ] **Step 1: Add README section**

Append to `recipes/shadow-cleanup/README.md`:

```markdown
## V3 Budgeted Canonicalization

This is the high-impact cleanup pass. It does not ask the model whether each row is good. It gives each bucket a hard canonical memory budget and requires every input id to be accounted for as compressed, deleted, kept raw, or escalated.

Pilot Claude cleanup:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py snapshot --source claude_history --output .local\open-brain-cleanup\budgeted\snapshots\claude-current.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py pack --snapshot .local\open-brain-cleanup\budgeted\snapshots\claude-current.jsonl --source claude_history --limit-rows 500 --output .local\open-brain-cleanup\budgeted\packs\claude-pilot-500.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py run --pack-file .local\open-brain-cleanup\budgeted\packs\claude-pilot-500.jsonl --output .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500.jsonl --limit-packs 10
python recipes\shadow-cleanup\budgeted_canonicalize.py summarize .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py apply .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500.jsonl --max-delete 500
```

Only add `--apply` after the dry-run numbers are acceptable. Apply writes an exact JSONL backup before deleting originals.
```

- [ ] **Step 2: Update `tool_registry.md`**

Change the Shadow Cleanup row to version `v1.4` and purpose:

```markdown
OB1 thoughts snapshot, clustering, proposal generation, guarded application, aggressive DeepSeek cleanup labels, backup-first hard deletes, and budgeted canonicalization.
```

- [ ] **Step 3: Run markdown-neutral verification**

Run:

```powershell
rg -n "budgeted_canonicalize|V3 Budgeted|v1.4" recipes\shadow-cleanup\README.md tool_registry.md
```

Expected: shows the new command references and registry entry.

---

### Task 9: Run A Low-Cost Pilot Before Spending Remaining Budget

**Files:**
- Runtime artifacts under `.local/open-brain-cleanup/budgeted/`

- [ ] **Step 1: Snapshot current Claude rows**

Run:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py snapshot --source claude_history --output .local\open-brain-cleanup\budgeted\snapshots\claude-current-20260505.jsonl
```

Expected output includes:

```text
snapshot_rows=<around 10069>
snapshot_file=.local\open-brain-cleanup\budgeted\snapshots\claude-current-20260505.jsonl
```

- [ ] **Step 2: Build a 500-row pilot pack**

Run:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py pack --snapshot .local\open-brain-cleanup\budgeted\snapshots\claude-current-20260505.jsonl --source claude_history --limit-rows 500 --output .local\open-brain-cleanup\budgeted\packs\claude-pilot-500-20260505.jsonl
```

Expected: pack count around 10-25 depending on buckets.

- [ ] **Step 3: Run only 5 packs first**

Run:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py run --pack-file .local\open-brain-cleanup\budgeted\packs\claude-pilot-500-20260505.jsonl --output .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500-20260505.jsonl --limit-packs 5 --max-tokens 3500 --temperature 0
```

Expected: `proposal_records_written=5`.

- [ ] **Step 4: Summarize pilot**

Run:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py summarize .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500-20260505.jsonl
```

Acceptance gate:

- Error rate below 20%.
- Delete IDs plus canonical coverage should imply at least 60% net reduction.
- Escalate IDs below 5%.

- [ ] **Step 5: Dry-run apply pilot**

Run:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py apply .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500-20260505.jsonl --max-delete 500
```

Expected output should show:

```text
apply_plan proposal_rows=<valid> canonicals=<small> live_delete_ids=<large> blocked_keep_or_escalate=<small> inserted=0 reused=0 deleted=0 apply=False backup_file=none
```

- [ ] **Step 6: Apply pilot only if the ratio is good**

Run only if dry-run shows a real reduction:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py apply .local\open-brain-cleanup\budgeted\proposals\claude-pilot-500-20260505.jsonl --max-delete 500 --apply
```

Expected:

- Backup file under `.local/open-brain-cleanup/budgeted/backups/`.
- Canonical rows inserted.
- Source rows deleted.
- Net row reduction should be `live_delete_ids - inserted`.

---

### Task 10: Scale Only After Pilot Metrics Pass

**Files:**
- Runtime artifacts under `.local/open-brain-cleanup/budgeted/`

- [ ] **Step 1: Continue Claude in chunks of 1,000 rows**

Run one chunk at a time:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py pack --snapshot .local\open-brain-cleanup\budgeted\snapshots\claude-current-20260505.jsonl --source claude_history --limit-rows 1000 --output .local\open-brain-cleanup\budgeted\packs\claude-1000-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py run --pack-file .local\open-brain-cleanup\budgeted\packs\claude-1000-20260505.jsonl --output .local\open-brain-cleanup\budgeted\proposals\claude-1000-20260505.jsonl --max-tokens 3500 --temperature 0
python recipes\shadow-cleanup\budgeted_canonicalize.py summarize .local\open-brain-cleanup\budgeted\proposals\claude-1000-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py apply .local\open-brain-cleanup\budgeted\proposals\claude-1000-20260505.jsonl --max-delete 1000
```

Add `--apply` only if the dry-run ratio remains good.

- [ ] **Step 2: Re-snapshot after each applied chunk**

Run:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py snapshot --source claude_history --output .local\open-brain-cleanup\budgeted\snapshots\claude-after-chunk-YYYYMMDD-HHMMSS.jsonl
```

Expected: Claude row count falls sharply after each chunk.

- [ ] **Step 3: Move to other sources in priority order**

Order:

1. `claude_history`
2. `shadow_cleanup`
3. `whatsapp`
4. `gemini`
5. `kontext`
6. `chatgpt`

Use source-specific commands:

```powershell
python recipes\shadow-cleanup\budgeted_canonicalize.py snapshot --source whatsapp --output .local\open-brain-cleanup\budgeted\snapshots\whatsapp-current-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py pack --snapshot .local\open-brain-cleanup\budgeted\snapshots\whatsapp-current-20260505.jsonl --source whatsapp --output .local\open-brain-cleanup\budgeted\packs\whatsapp-current-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py run --pack-file .local\open-brain-cleanup\budgeted\packs\whatsapp-current-20260505.jsonl --output .local\open-brain-cleanup\budgeted\proposals\whatsapp-current-20260505.jsonl --max-tokens 3500 --temperature 0
python recipes\shadow-cleanup\budgeted_canonicalize.py summarize .local\open-brain-cleanup\budgeted\proposals\whatsapp-current-20260505.jsonl
python recipes\shadow-cleanup\budgeted_canonicalize.py apply .local\open-brain-cleanup\budgeted\proposals\whatsapp-current-20260505.jsonl --max-delete 1000
```

---

## Self-Review

Spec coverage:

- Hard row budgets are implemented by `SOURCE_BUDGETS`, `canonical_limit_for_bucket`, and `build_budgeted_prompt`.
- Compression and deletion are implemented by canonical insertion plus `delete_source_ids` deletion.
- Safety requirements are implemented by validation, exact backups, dry-run mode, sync log, and keep/escalate blocking.
- Cost control is implemented by pilot-first commands, `--limit-packs`, source-specific chunks, and smaller pack sizes.

Placeholder scan:

- No TBD/TODO placeholders remain.
- Each task has concrete files, commands, and expected outputs.

Type consistency:

- Proposal fields are consistent across prompt, validation, summarize, and apply: `canonical_memories`, `delete_source_ids`, `keep_source_ids`, `escalate_source_ids`.
- CLI command names are consistent: `snapshot`, `pack`, `run`, `summarize`, `apply`.

Execution checkpoint:

- Do not run Task 9 until Tasks 1-8 pass locally.
- Do not apply pilot proposals until dry-run shows strong reduction and low escalation.
