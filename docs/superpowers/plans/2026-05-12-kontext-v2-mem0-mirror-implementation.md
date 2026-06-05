# Kontext V2 Mem0 Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build Kontext V2 as a VPS-ready Postgres + pgvector memory core that initially mirrors Mem0 and proves parity through import, MCP contract, retrieval, fetch, write-dry-run, and dashboard/category projection tests.

**Architecture:** Kontext V2 is a new local package under `tools/kontext-v2` with a Postgres repository layer, Mem0 mirror importer, read-only Mem0-compatible MCP service, and parity harness. Mem0 remains live source of truth until Kontext V2 matches or beats Mem0 retrieval quality. No destructive Mem0 writes are allowed in this plan.

**Tech Stack:** Python 3.11+, pytest, FastAPI, psycopg, Postgres, pgvector, JSONB, Postgres full-text search, Docker Compose for local Postgres, existing Mem0 V1.13 retrieval eval cases.

---

## File Structure

- Create: `tools/kontext-v2/README.md` - local developer guide and safety rules.
- Create: `tools/kontext-v2/requirements.txt` - Python dependencies for Kontext V2.
- Create: `tools/kontext-v2/docker-compose.yml` - local Postgres + pgvector for tests and development.
- Create: `tools/kontext-v2/kontext_v2/__init__.py` - package marker.
- Create: `tools/kontext-v2/kontext_v2/config.py` - environment/config parsing.
- Create: `tools/kontext-v2/kontext_v2/models.py` - typed dataclasses for memories, search filters, and parity reports.
- Create: `tools/kontext-v2/kontext_v2/schema.py` - SQL schema and migration helpers.
- Create: `tools/kontext-v2/kontext_v2/repository.py` - Postgres read/write repository.
- Create: `tools/kontext-v2/kontext_v2/importer.py` - idempotent Mem0 mirror importer.
- Create: `tools/kontext-v2/kontext_v2/retrieval.py` - hybrid retrieval over metadata, FTS, and vector fields.
- Create: `tools/kontext-v2/kontext_v2/mcp_server.py` - Mem0-compatible MCP server surface.
- Create: `tools/kontext-v2/kontext_v2/parity.py` - Mem0/Kontext side-by-side eval logic.
- Create: `tools/kontext-v2/kontext_v2/dossiers.py` - category/topic dossier projection and patch parsing.
- Create: `tools/kontext-v2/tests/conftest.py` - test DB fixtures and deterministic vectors.
- Create: `tools/kontext-v2/tests/fixtures/mem0_sanitized_export.json` - no-secret fixture with representative Mem0-shaped rows.
- Create: `tools/kontext-v2/tests/test_schema.py` - schema tests.
- Create: `tools/kontext-v2/tests/test_mem0_import_parity.py` - import/idempotency tests.
- Create: `tools/kontext-v2/tests/test_mcp_contract_parity.py` - tool shape tests.
- Create: `tools/kontext-v2/tests/test_retrieval_eval_parity.py` - retrieval eval tests using existing Mem0 V1.13 cases.
- Create: `tools/kontext-v2/tests/test_side_by_side_diff.py` - safe diff tests.
- Create: `tools/kontext-v2/tests/test_fetch_parity.py` - exact-ID fetch tests.
- Create: `tools/kontext-v2/tests/test_write_path_dry_run.py` - dry-run proposal tests.
- Create: `tools/kontext-v2/tests/test_dossiers.py` - category/dossier projection tests.
- Modify: `project_log.md` - add a concise entry after local plan execution is complete.

---

### Task 1: Create Kontext V2 Package Skeleton

**Files:**
- Create: `tools/kontext-v2/README.md`
- Create: `tools/kontext-v2/requirements.txt`
- Create: `tools/kontext-v2/docker-compose.yml`
- Create: `tools/kontext-v2/kontext_v2/__init__.py`
- Create: `tools/kontext-v2/kontext_v2/config.py`
- Create: `tools/kontext-v2/tests/conftest.py`

- [x] **Step 1: Write the failing package import test**

Create `tools/kontext-v2/tests/test_package_bootstrap.py`:

```python
from kontext_v2.config import KontextV2Config


def test_default_config_is_local_and_read_only():
    cfg = KontextV2Config.from_env({})

    assert cfg.service_name == "kontext-v2"
    assert cfg.write_mode == "dry_run"
    assert cfg.database_url.startswith("postgresql://")
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_package_bootstrap.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'kontext_v2'`.

- [x] **Step 3: Create the minimal package files**

Create `tools/kontext-v2/kontext_v2/__init__.py`:

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

Create `tools/kontext-v2/kontext_v2/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class KontextV2Config:
    service_name: str
    database_url: str
    write_mode: str
    embedding_dimension: int
    embedding_model: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "KontextV2Config":
        return cls(
            service_name=env.get("KONTEXT_V2_SERVICE_NAME", "kontext-v2"),
            database_url=env.get(
                "KONTEXT_V2_DATABASE_URL",
                "postgresql://kontext_v2:kontext_v2@localhost:55432/kontext_v2",
            ),
            write_mode=env.get("KONTEXT_V2_WRITE_MODE", "dry_run"),
            embedding_dimension=int(env.get("KONTEXT_V2_EMBEDDING_DIM", "16")),
            embedding_model=env.get("KONTEXT_V2_EMBEDDING_MODEL", "deterministic-test-v1"),
        )
```

Create `tools/kontext-v2/requirements.txt`:

```text
fastapi
httpx
psycopg[binary]
pytest
pytest-asyncio
uvicorn
```

Create `tools/kontext-v2/docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: kontext_v2
      POSTGRES_USER: kontext_v2
      POSTGRES_PASSWORD: kontext_v2
    ports:
      - "55432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U kontext_v2 -d kontext_v2"]
      interval: 5s
      timeout: 5s
      retries: 20
```

Create `tools/kontext-v2/README.md`:

```markdown
# Kontext V2

Kontext V2 is a VPS-hosted owned memory core. V0 mirrors Mem0 into Postgres + pgvector and proves parity before any client switch.

Safety rules:

- Mem0 remains live source of truth during V0.
- Do not destructively write to Mem0.
- Do not print secrets, profile tokens, raw chats, raw memory dumps, or remote `.env` contents.
- Parity reports may include aggregate metrics, IDs, metadata hits, and rank deltas.

Local test DB:

```powershell
docker compose -f tools\kontext-v2\docker-compose.yml up -d
python -m pytest tools\kontext-v2 -q
```
```

Create `tools/kontext-v2/tests/conftest.py`:

```python
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure():
    os.environ.setdefault(
        "KONTEXT_V2_DATABASE_URL",
        "postgresql://kontext_v2:kontext_v2@localhost:55432/kontext_v2",
    )
```

- [x] **Step 4: Run package bootstrap test to verify it passes**

Run: `python -m pytest tools\kontext-v2\tests\test_package_bootstrap.py -q`

Expected: `1 passed`.

- [x] **Step 5: Commit**

```bash
git add tools/kontext-v2
git commit -m "feat: scaffold kontext v2 package"
```

---

### Task 2: Implement Postgres Schema And Repository

**Files:**
- Create: `tools/kontext-v2/kontext_v2/models.py`
- Create: `tools/kontext-v2/kontext_v2/schema.py`
- Create: `tools/kontext-v2/kontext_v2/repository.py`
- Create: `tools/kontext-v2/tests/test_schema.py`

- [x] **Step 1: Write failing schema test**

Create `tools/kontext-v2/tests/test_schema.py`:

```python
import os

import psycopg

from kontext_v2.schema import apply_schema, list_tables


def test_schema_creates_core_tables():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        tables = set(list_tables(conn))

    assert {
        "memories",
        "memory_versions",
        "categories",
        "memory_categories",
        "relations",
        "embeddings",
        "retrieval_queries",
        "ingestion_audit",
        "topic_dossiers",
    } <= tables
```

- [x] **Step 2: Start local Postgres and verify test fails**

Run: `docker compose -f tools\kontext-v2\docker-compose.yml up -d`

Run: `python -m pytest tools\kontext-v2\tests\test_schema.py::test_schema_creates_core_tables -q`

Expected: FAIL with `ImportError` for `kontext_v2.schema`.

- [x] **Step 3: Implement schema helper**

Create `tools/kontext-v2/kontext_v2/schema.py`:

```python
from __future__ import annotations

import psycopg


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_mem0_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    memory_type TEXT NOT NULL DEFAULT '',
    current_status TEXT NOT NULL DEFAULT 'active',
    memory_tier TEXT NOT NULL DEFAULT 'active',
    signal_strength DOUBLE PRECISION,
    source_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_document TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(text, '')), 'B')
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_memories_external_mem0_id ON memories(external_mem0_id);
CREATE INDEX IF NOT EXISTS idx_memories_metadata ON memories USING GIN(metadata);
CREATE INDEX IF NOT EXISTS idx_memories_search_document ON memories USING GIN(search_document);

CREATE TABLE IF NOT EXISTS memory_versions (
    id BIGSERIAL PRIMARY KEY,
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    external_mem0_id TEXT NOT NULL,
    version_source TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    source_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_categories (
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'mem0_domain',
    PRIMARY KEY(memory_id, category_id)
);

CREATE TABLE IF NOT EXISTS relations (
    id BIGSERIAL PRIMARY KEY,
    source_memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
    target_memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embeddings (
    memory_id UUID PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding VECTOR(16) NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS retrieval_queries (
    id BIGSERIAL PRIMARY KEY,
    query_hash TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_external_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_audit (
    id BIGSERIAL PRIMARY KEY,
    source_hash TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS topic_dossiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id UUID REFERENCES categories(id) ON DELETE CASCADE,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    markdown TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_SQL)
    conn.commit()


def list_tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    ).fetchall()
    return [row[0] for row in rows]
```

- [x] **Step 4: Run schema test**

Run: `python -m pytest tools\kontext-v2\tests\test_schema.py -q`

Expected: `1 passed`.

- [x] **Step 5: Add repository insert/fetch test**

Append to `tools/kontext-v2/tests/test_schema.py`:

```python
from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository


def test_repository_upserts_and_fetches_memory():
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        repo.upsert_memory(
            MemoryRecord(
                external_mem0_id="mem-test-1",
                title="Test memory",
                text="Ionut prefers owned memory infrastructure.",
                metadata={"domains": ["ai", "systems"], "memory_type": "decision"},
                memory_type="decision",
                current_status="active",
                memory_tier="active",
                signal_strength=8.0,
                source_hash="hash-1",
            )
        )
        fetched = repo.fetch_by_external_id("mem-test-1")

    assert fetched is not None
    assert fetched.external_mem0_id == "mem-test-1"
    assert fetched.metadata["domains"] == ["ai", "systems"]
```

- [x] **Step 6: Verify repository test fails**

Run: `python -m pytest tools\kontext-v2\tests\test_schema.py::test_repository_upserts_and_fetches_memory -q`

Expected: FAIL with `ImportError` for `kontext_v2.models` or `kontext_v2.repository`.

- [x] **Step 7: Implement models and repository**

Create `tools/kontext-v2/kontext_v2/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryRecord:
    external_mem0_id: str
    title: str
    text: str
    metadata: dict[str, Any]
    memory_type: str
    current_status: str
    memory_tier: str
    signal_strength: float | None
    source_hash: str
```

Create `tools/kontext-v2/kontext_v2/repository.py`:

```python
from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from .models import MemoryRecord


class KontextRepository:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def upsert_memory(self, memory: MemoryRecord) -> None:
        row = self.conn.execute(
            """
            INSERT INTO memories (
                external_mem0_id, title, text, metadata, memory_type,
                current_status, memory_tier, signal_strength, source_hash
            ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
            ON CONFLICT (external_mem0_id) DO UPDATE SET
                title = excluded.title,
                text = excluded.text,
                metadata = excluded.metadata,
                memory_type = excluded.memory_type,
                current_status = excluded.current_status,
                memory_tier = excluded.memory_tier,
                signal_strength = excluded.signal_strength,
                source_hash = excluded.source_hash,
                updated_at = now()
            RETURNING id
            """,
            (
                memory.external_mem0_id,
                memory.title,
                memory.text,
                json.dumps(memory.metadata, sort_keys=True),
                memory.memory_type,
                memory.current_status,
                memory.memory_tier,
                memory.signal_strength,
                memory.source_hash,
            ),
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO memory_versions (memory_id, external_mem0_id, version_source, snapshot, source_hash)
            VALUES (%s, %s, 'mem0_import', %s::jsonb, %s)
            """,
            (row[0], memory.external_mem0_id, json.dumps(memory.__dict__, sort_keys=True), memory.source_hash),
        )
        self.conn.commit()

    def fetch_by_external_id(self, external_id: str) -> MemoryRecord | None:
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                SELECT external_mem0_id, title, text, metadata, memory_type,
                       current_status, memory_tier, signal_strength, source_hash
                FROM memories WHERE external_mem0_id = %s
                """,
                (external_id,),
            ).fetchone()
        if not row:
            return None
        return MemoryRecord(**row)
```

- [x] **Step 8: Run schema and repository tests**

Run: `python -m pytest tools\kontext-v2\tests\test_schema.py -q`

Expected: `2 passed`.

- [x] **Step 9: Commit**

```bash
git add tools/kontext-v2
git commit -m "feat: add kontext v2 postgres schema"
```

---

### Task 3: Implement Sanitized Mem0 Mirror Importer

**Files:**
- Create: `tools/kontext-v2/tests/fixtures/mem0_sanitized_export.json`
- Create: `tools/kontext-v2/tests/test_mem0_import_parity.py`
- Create: `tools/kontext-v2/kontext_v2/importer.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`

- [x] **Step 1: Create sanitized fixture**

Create `tools/kontext-v2/tests/fixtures/mem0_sanitized_export.json`:

```json
{
  "results": [
    {
      "id": "mem-ai-architecture-1",
      "memory": "OB1 memory architecture uses Mem0 as live source and Kontext V2 as planned mirror.",
      "metadata": {
        "title": "OB1 memory architecture",
        "domains": ["ai", "systems", "infrastructure"],
        "memory_type": "project_state",
        "current_status": "active",
        "memory_tier": "active",
        "signal_strength": 9
      }
    },
    {
      "id": "mem-vocality-1",
      "memory": "Vocality work should prioritize premium offer and proof library over generic AI tooling.",
      "metadata": {
        "title": "Vocality focus doctrine",
        "domains": ["business", "vocality", "workflow"],
        "memory_type": "decision",
        "current_status": "active",
        "memory_tier": "active",
        "signal_strength": 10
      }
    }
  ]
}
```

- [x] **Step 2: Write failing import parity test**

Create `tools/kontext-v2/tests/test_mem0_import_parity.py`:

```python
import json
import os
from pathlib import Path

import psycopg

from kontext_v2.importer import import_mem0_export
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "mem0_sanitized_export.json"


def test_import_preserves_ids_metadata_and_is_idempotent():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        first = import_mem0_export(repo, payload)
        second = import_mem0_export(repo, payload)
        memory = repo.fetch_by_external_id("mem-ai-architecture-1")

    assert first.created == 2
    assert first.updated == 0
    assert second.created == 0
    assert second.unchanged == 2
    assert memory is not None
    assert memory.memory_type == "project_state"
    assert memory.metadata["domains"] == ["ai", "systems", "infrastructure"]
```

- [x] **Step 3: Run import test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_mem0_import_parity.py -q`

Expected: FAIL with `ImportError` for `kontext_v2.importer`.

- [x] **Step 4: Implement importer**

Create `tools/kontext-v2/kontext_v2/importer.py`:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .models import MemoryRecord
from .repository import KontextRepository


@dataclass(frozen=True)
class ImportReport:
    created: int
    updated: int
    unchanged: int
    skipped: int


def _source_hash(row: dict[str, Any]) -> str:
    raw = json.dumps(row, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _as_memory_record(row: dict[str, Any]) -> MemoryRecord | None:
    external_id = str(row.get("id") or "").strip()
    text = str(row.get("memory") or row.get("text") or "").strip()
    if not external_id or not text:
        return None
    metadata = dict(row.get("metadata") or {})
    return MemoryRecord(
        external_mem0_id=external_id,
        title=str(metadata.get("title") or row.get("title") or ""),
        text=text,
        metadata=metadata,
        memory_type=str(metadata.get("memory_type") or ""),
        current_status=str(metadata.get("current_status") or "active"),
        memory_tier=str(metadata.get("memory_tier") or "active"),
        signal_strength=float(metadata["signal_strength"]) if metadata.get("signal_strength") is not None else None,
        source_hash=_source_hash(row),
    )


def import_mem0_export(repo: KontextRepository, payload: dict[str, Any]) -> ImportReport:
    created = updated = unchanged = skipped = 0
    for row in payload.get("results") or []:
        memory = _as_memory_record(row)
        if memory is None:
            skipped += 1
            continue
        existing = repo.fetch_by_external_id(memory.external_mem0_id)
        if existing and existing.source_hash == memory.source_hash:
            unchanged += 1
            continue
        repo.upsert_memory(memory)
        if existing:
            updated += 1
        else:
            created += 1
    return ImportReport(created=created, updated=updated, unchanged=unchanged, skipped=skipped)
```

- [x] **Step 5: Run import test**

Run: `python -m pytest tools\kontext-v2\tests\test_mem0_import_parity.py -q`

Expected: `1 passed`.

- [x] **Step 6: Commit**

```bash
git add tools/kontext-v2
git commit -m "feat: add mem0 mirror importer"
```

---

### Task 4: Implement Read-Only MCP Contract Parity

**Files:**
- Create: `tools/kontext-v2/tests/test_mcp_contract_parity.py`
- Create: `tools/kontext-v2/kontext_v2/mcp_server.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`

- [x] **Step 1: Write failing MCP contract test**

Create `tools/kontext-v2/tests/test_mcp_contract_parity.py`:

```python
from kontext_v2.mcp_server import list_tools, normalize_search_args


def test_read_only_tool_surface_matches_mem0_names():
    names = {tool["name"] for tool in list_tools(write_enabled=False)}

    assert {"search", "fetch", "ingestion_status"} <= names
    assert "ingest_exchange" not in names


def test_search_args_match_mem0_filter_shape():
    args = normalize_search_args(
        {
            "query": "memory architecture",
            "top_k": 100,
            "domains": ["ai", "systems"],
            "memory_tiers": ["active"],
            "memory_types": ["project_state"],
            "current_statuses": ["active"],
        }
    )

    assert args.query == "memory architecture"
    assert args.top_k == 20
    assert args.domains == ["ai", "systems"]
    assert args.memory_tiers == ["active"]
    assert args.memory_types == ["project_state"]
    assert args.current_statuses == ["active"]
```

- [x] **Step 2: Run MCP contract test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_mcp_contract_parity.py -q`

Expected: FAIL with `ImportError` for `kontext_v2.mcp_server`.

- [x] **Step 3: Implement read-only MCP helpers**

Create `tools/kontext-v2/kontext_v2/mcp_server.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchArgs:
    query: str
    top_k: int
    domains: list[str]
    memory_tiers: list[str]
    memory_types: list[str]
    current_statuses: list[str]


def list_tools(write_enabled: bool = False) -> list[dict[str, Any]]:
    tools = [
        {"name": "search", "description": "Search mirrored Kontext V2 memories."},
        {"name": "fetch", "description": "Fetch one mirrored memory by exact ID."},
        {"name": "ingestion_status", "description": "Return safe aggregate Kontext V2 status."},
    ]
    if write_enabled:
        tools.extend(
            [
                {"name": "extract_memories", "description": "Dry-run extract candidate memories."},
                {"name": "ingest_exchange", "description": "Dry-run ingest exchange proposals."},
                {"name": "submit_memory_override", "description": "Submit exact reviewed memory proposal."},
                {"name": "flag_memory", "description": "Flag exact memory for maintenance."},
            ]
        )
    return tools


def _list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if str(item).strip()]


def normalize_search_args(payload: dict[str, Any]) -> SearchArgs:
    query = str(payload.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    top_k = min(max(int(payload.get("top_k") or 10), 1), 20)
    return SearchArgs(
        query=query,
        top_k=top_k,
        domains=_list(payload.get("domains")),
        memory_tiers=_list(payload.get("memory_tiers")),
        memory_types=_list(payload.get("memory_types")),
        current_statuses=_list(payload.get("current_statuses")),
    )
```

- [x] **Step 4: Run MCP contract test**

Run: `python -m pytest tools\kontext-v2\tests\test_mcp_contract_parity.py -q`

Expected: `2 passed`.

- [x] **Step 5: Commit**

```bash
git add tools/kontext-v2
git commit -m "feat: add kontext v2 read-only mcp contract"
```

---

### Task 5: Implement Hybrid Retrieval And Eval Parity

**Files:**
- Create: `tools/kontext-v2/tests/test_retrieval_eval_parity.py`
- Create: `tools/kontext-v2/kontext_v2/retrieval.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`

- [x] **Step 1: Write failing retrieval test**

Create `tools/kontext-v2/tests/test_retrieval_eval_parity.py`:

```python
import json
import os
from pathlib import Path

import psycopg

from kontext_v2.importer import import_mem0_export
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import search_memories
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "mem0_sanitized_export.json"


def test_search_finds_domain_and_memory_type_hits():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        import_mem0_export(repo, payload)
        results = search_memories(
            repo,
            query="current architecture of my AI memory system",
            top_k=5,
            domains=["ai", "systems", "infrastructure"],
            memory_types=["project_state"],
            memory_tiers=[],
            current_statuses=["active"],
        )

    assert results
    assert results[0]["external_mem0_id"] == "mem-ai-architecture-1"
    assert "ai" in results[0]["metadata"]["domains"]
    assert results[0]["memory_type"] == "project_state"
```

- [x] **Step 2: Run retrieval test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_retrieval_eval_parity.py -q`

Expected: FAIL with `ImportError` for `kontext_v2.retrieval`.

- [x] **Step 3: Implement repository search and retrieval wrapper**

Append to `tools/kontext-v2/kontext_v2/repository.py`:

```python
    def search_rows(
        self,
        query: str,
        top_k: int,
        domains: list[str],
        memory_types: list[str],
        memory_tiers: list[str],
        current_statuses: list[str],
    ) -> list[dict]:
        where = ["search_document @@ plainto_tsquery('simple', %s)"]
        params: list[object] = [query]
        if domains:
            where.append("metadata->'domains' ?| %s")
            params.append(domains)
        if memory_types:
            where.append("memory_type = ANY(%s)")
            params.append(memory_types)
        if memory_tiers:
            where.append("memory_tier = ANY(%s)")
            params.append(memory_tiers)
        if current_statuses:
            where.append("current_status = ANY(%s)")
            params.append(current_statuses)
        params.append(top_k)
        sql = f"""
            SELECT external_mem0_id, title, text, metadata, memory_type,
                   current_status, memory_tier, signal_strength,
                   ts_rank(search_document, plainto_tsquery('simple', %s)) AS rank
            FROM memories
            WHERE {' AND '.join(where)}
            ORDER BY rank DESC, signal_strength DESC NULLS LAST, updated_at DESC
            LIMIT %s
        """
        params = [query] + params
        with self.conn.cursor(row_factory=dict_row) as cur:
            return list(cur.execute(sql, params).fetchall())
```

Create `tools/kontext-v2/kontext_v2/retrieval.py`:

```python
from __future__ import annotations

from .repository import KontextRepository


def search_memories(
    repo: KontextRepository,
    query: str,
    top_k: int,
    domains: list[str],
    memory_types: list[str],
    memory_tiers: list[str],
    current_statuses: list[str],
) -> list[dict]:
    return repo.search_rows(
        query=query,
        top_k=top_k,
        domains=domains,
        memory_types=memory_types,
        memory_tiers=memory_tiers,
        current_statuses=current_statuses,
    )
```

- [x] **Step 4: Run retrieval test**

Run: `python -m pytest tools\kontext-v2\tests\test_retrieval_eval_parity.py -q`

Expected: `1 passed`.

- [x] **Step 5: Wire existing Mem0 eval case format**

Add a second test to `tools/kontext-v2/tests/test_retrieval_eval_parity.py`:

```python
from tools.mem0_remote_mcp.retrieval_eval import evaluate_retrieval_cases, load_eval_cases


def test_existing_mem0_eval_case_file_is_reusable():
    cases = load_eval_cases("tools/mem0-remote-mcp/retrieval_eval_cases.v1.13.json")

    assert len(cases) >= 10
    assert {case.name for case in cases} >= {"ai-memory-architecture", "mem0-ingestion-pipeline"}
```

If Python cannot import from `tools.mem0_remote_mcp` because the existing directory contains a dash, replace that import with an `importlib.util.spec_from_file_location` loader inside the test.

- [x] **Step 6: Run retrieval eval tests**

Run: `python -m pytest tools\kontext-v2\tests\test_retrieval_eval_parity.py -q`

Expected: `2 passed`.

- [x] **Step 7: Commit**

```bash
git add tools/kontext-v2
git commit -m "feat: add kontext v2 retrieval parity foundation"
```

---

### Task 6: Implement Side-By-Side Parity Reports

**Files:**
- Create: `tools/kontext-v2/tests/test_side_by_side_diff.py`
- Create: `tools/kontext-v2/kontext_v2/parity.py`

- [x] **Step 1: Write failing side-by-side diff test**

Create `tools/kontext-v2/tests/test_side_by_side_diff.py`:

```python
from kontext_v2.parity import compare_search_results


def test_side_by_side_diff_uses_ids_and_metadata_without_memory_text():
    mem0 = [
        {"id": "mem-1", "memory": "secret raw text", "metadata": {"domains": ["ai"], "memory_type": "project_state"}}
    ]
    kontext = [
        {"external_mem0_id": "mem-1", "text": "secret raw text", "metadata": {"domains": ["ai"]}, "memory_type": "project_state"}
    ]

    report = compare_search_results("query-hash", mem0, kontext)
    rendered = str(report)

    assert report["mem0_count"] == 1
    assert report["kontext_count"] == 1
    assert report["shared_external_ids"] == ["mem-1"]
    assert "secret raw text" not in rendered
```

- [x] **Step 2: Run side-by-side diff test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_side_by_side_diff.py -q`

Expected: FAIL with `ImportError` for `kontext_v2.parity`.

- [x] **Step 3: Implement safe diff report**

Create `tools/kontext-v2/kontext_v2/parity.py`:

```python
from __future__ import annotations

from typing import Any


def _mem0_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("external_mem0_id") or "")


def _domain_set(row: dict[str, Any]) -> set[str]:
    metadata = row.get("metadata") or {}
    return {str(value) for value in metadata.get("domains") or []}


def _memory_type(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("memory_type") or row.get("memory_type") or "")


def compare_search_results(query_hash: str, mem0_rows: list[dict[str, Any]], kontext_rows: list[dict[str, Any]]) -> dict[str, Any]:
    mem0_ids = [_mem0_id(row) for row in mem0_rows if _mem0_id(row)]
    kontext_ids = [_mem0_id(row) for row in kontext_rows if _mem0_id(row)]
    shared = [memory_id for memory_id in mem0_ids if memory_id in set(kontext_ids)]
    return {
        "query_hash": query_hash,
        "mem0_count": len(mem0_rows),
        "kontext_count": len(kontext_rows),
        "shared_external_ids": shared,
        "mem0_domains": sorted(set().union(*[_domain_set(row) for row in mem0_rows]) if mem0_rows else set()),
        "kontext_domains": sorted(set().union(*[_domain_set(row) for row in kontext_rows]) if kontext_rows else set()),
        "mem0_memory_types": sorted({_memory_type(row) for row in mem0_rows if _memory_type(row)}),
        "kontext_memory_types": sorted({_memory_type(row) for row in kontext_rows if _memory_type(row)}),
    }
```

- [x] **Step 4: Run side-by-side diff test**

Run: `python -m pytest tools\kontext-v2\tests\test_side_by_side_diff.py -q`

Expected: `1 passed`.

- [x] **Step 5: Commit**

```bash
git add tools/kontext-v2
git commit -m "feat: add safe parity diff reports"
```

---

### Task 7: Implement Fetch Parity And Ingestion Status

**Files:**
- Create: `tools/kontext-v2/tests/test_fetch_parity.py`
- Modify: `tools/kontext-v2/kontext_v2/mcp_server.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`

- [x] **Step 1: Write failing fetch/status test**

Create `tools/kontext-v2/tests/test_fetch_parity.py`:

```python
import json
import os
from pathlib import Path

import psycopg

from kontext_v2.importer import import_mem0_export
from kontext_v2.mcp_server import fetch_tool, ingestion_status_tool
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "mem0_sanitized_export.json"


def test_fetch_and_status_are_mem0_compatible():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"]) as conn:
        apply_schema(conn)
        repo = KontextRepository(conn)
        import_mem0_export(repo, payload)
        fetched = fetch_tool(repo, "mem-ai-architecture-1")
        status = ingestion_status_tool(repo, recent_limit=5)

    assert fetched["id"] == "mem-ai-architecture-1"
    assert fetched["metadata"]["memory_type"] == "project_state"
    assert status["ok"] is True
    assert status["service"] == "kontext-v2"
    assert status["mirror"]["memories"] >= 2
```

- [x] **Step 2: Run fetch/status test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_fetch_parity.py -q`

Expected: FAIL with missing `fetch_tool` or `ingestion_status_tool`.

- [x] **Step 3: Implement repository count and MCP helpers**

Append to `tools/kontext-v2/kontext_v2/repository.py`:

```python
    def count_memories(self) -> int:
        return int(self.conn.execute("SELECT count(*) FROM memories").fetchone()[0])
```

Append to `tools/kontext-v2/kontext_v2/mcp_server.py`:

```python
from .repository import KontextRepository


def fetch_tool(repo: KontextRepository, memory_id: str) -> dict[str, Any]:
    memory = repo.fetch_by_external_id(memory_id)
    if memory is None:
        return {"ok": False, "error": "not_found", "id": memory_id}
    return {
        "id": memory.external_mem0_id,
        "memory": memory.text,
        "title": memory.title,
        "metadata": memory.metadata,
    }


def ingestion_status_tool(repo: KontextRepository, recent_limit: int = 10) -> dict[str, Any]:
    return {
        "ok": True,
        "service": "kontext-v2",
        "mode": "mirror_read_only",
        "recent_limit": min(max(int(recent_limit), 1), 100),
        "mirror": {"memories": repo.count_memories()},
        "writes": {"enabled": False},
    }
```

- [x] **Step 4: Run fetch/status test**

Run: `python -m pytest tools\kontext-v2\tests\test_fetch_parity.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit** *(not run; no commit requested)*

```bash
git add tools/kontext-v2
git commit -m "feat: add kontext v2 fetch and status parity"
```

---

### Task 8: Implement Write-Path Dry Run Proposals

**Files:**
- Create: `tools/kontext-v2/tests/test_write_path_dry_run.py`
- Modify: `tools/kontext-v2/kontext_v2/mcp_server.py`

- [x] **Step 1: Write failing dry-run test**

Create `tools/kontext-v2/tests/test_write_path_dry_run.py`:

```python
from kontext_v2.mcp_server import ingest_exchange_dry_run


def test_ingest_exchange_dry_run_returns_proposals_without_writing():
    payload = ingest_exchange_dry_run(
        messages=[
            {"role": "user", "content": "Kontext V2 should mirror Mem0 first."},
            {"role": "assistant", "content": "I will keep Mem0 live until parity passes."},
        ],
        origin="codex",
    )

    assert payload["ok"] is True
    assert payload["mode"] == "dry_run"
    assert payload["writes_applied"] == 0
    assert payload["proposals"]
    assert payload["proposals"][0]["action"] == "create_candidate"
```

- [x] **Step 2: Run dry-run test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_write_path_dry_run.py -q`

Expected: FAIL with missing `ingest_exchange_dry_run`.

- [x] **Step 3: Implement dry-run proposal helper**

Append to `tools/kontext-v2/kontext_v2/mcp_server.py`:

```python
import hashlib


def ingest_exchange_dry_run(messages: list[dict[str, str]], origin: str) -> dict[str, Any]:
    joined = "\n".join(str(message.get("content") or "") for message in messages)
    source_hash = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    keep = "Kontext V2" in joined or "Mem0" in joined
    proposals = []
    if keep:
        proposals.append(
            {
                "action": "create_candidate",
                "source_hash": source_hash,
                "origin": origin,
                "reason": "domain_signal",
                "domains": ["ai", "systems"],
            }
        )
    return {
        "ok": True,
        "mode": "dry_run",
        "writes_applied": 0,
        "source_hash": source_hash,
        "proposals": proposals,
    }
```

- [x] **Step 4: Run dry-run test**

Run: `python -m pytest tools\kontext-v2\tests\test_write_path_dry_run.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit** *(not run; no commit requested)*

```bash
git add tools/kontext-v2
git commit -m "feat: add kontext v2 write dry-run proposals"
```

---

### Task 9: Implement Category Dossiers As Database Projections

**Files:**
- Create: `tools/kontext-v2/tests/test_dossiers.py`
- Create: `tools/kontext-v2/kontext_v2/dossiers.py`
- Modify: `tools/kontext-v2/kontext_v2/repository.py`

- [x] **Step 1: Write failing dossier test**

Create `tools/kontext-v2/tests/test_dossiers.py`:

```python
from kontext_v2.dossiers import parse_dossier_edit, render_category_dossier


def test_dossier_is_projection_and_edits_become_patch_proposals():
    memories = [
        {"external_mem0_id": "mem-1", "title": "Architecture", "text": "Kontext V2 mirrors Mem0.", "memory_type": "project_state"}
    ]
    markdown = render_category_dossier("ai-systems", "AI Systems", memories)
    proposals = parse_dossier_edit(
        original_markdown=markdown,
        edited_markdown=markdown + "\n\n<!-- propose:update mem-1 memory_tier=active -->\n",
    )

    assert "# AI Systems" in markdown
    assert "mem-1" in markdown
    assert proposals == [{"action": "update", "external_mem0_id": "mem-1", "field": "memory_tier", "value": "active"}]
```

- [x] **Step 2: Run dossier test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_dossiers.py -q`

Expected: FAIL with `ImportError` for `kontext_v2.dossiers`.

- [x] **Step 3: Implement dossier projection helper**

Create `tools/kontext-v2/kontext_v2/dossiers.py`:

```python
from __future__ import annotations

import re
from typing import Any


def render_category_dossier(slug: str, name: str, memories: list[dict[str, Any]]) -> str:
    lines = [f"# {name}", "", f"Category: `{slug}`", "", "## Memories"]
    for memory in memories:
        lines.extend(
            [
                "",
                f"### {memory.get('title') or memory.get('external_mem0_id')}",
                f"- ID: `{memory.get('external_mem0_id')}`",
                f"- Type: `{memory.get('memory_type', '')}`",
                f"- Text: {memory.get('text', '')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Edit Protocol",
            "Add HTML comments like `<!-- propose:update mem-id field=value -->` to request database patches.",
        ]
    )
    return "\n".join(lines)


def parse_dossier_edit(original_markdown: str, edited_markdown: str) -> list[dict[str, str]]:
    del original_markdown
    proposals = []
    pattern = re.compile(r"<!--\s*propose:update\s+(\S+)\s+([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)\s*-->")
    for match in pattern.finditer(edited_markdown):
        proposals.append(
            {
                "action": "update",
                "external_mem0_id": match.group(1),
                "field": match.group(2),
                "value": match.group(3),
            }
        )
    return proposals
```

- [x] **Step 4: Run dossier test**

Run: `python -m pytest tools\kontext-v2\tests\test_dossiers.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit** *(not run; no commit requested)*

```bash
git add tools/kontext-v2
git commit -m "feat: add kontext v2 category dossiers"
```

---

### Task 10: Build Local Parity CLI

**Files:**
- Create: `tools/kontext-v2/parity_eval.py`
- Create: `tools/kontext-v2/tests/test_parity_cli.py`

- [x] **Step 1: Write failing CLI smoke test**

Create `tools/kontext-v2/tests/test_parity_cli.py`:

```python
import subprocess
import sys


def test_parity_cli_help_runs():
    result = subprocess.run(
        [sys.executable, "tools/kontext-v2/parity_eval.py", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Kontext V2 parity eval" in result.stdout
    assert "--cases" in result.stdout
    assert "--kontext-database-url" in result.stdout
```

- [x] **Step 2: Run CLI test to verify it fails**

Run: `python -m pytest tools\kontext-v2\tests\test_parity_cli.py -q`

Expected: FAIL because `parity_eval.py` does not exist.

- [x] **Step 3: Implement CLI help and dry-run shell**

Create `tools/kontext-v2/parity_eval.py`:

```python
from __future__ import annotations

import argparse
import json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kontext V2 parity eval")
    parser.add_argument("--cases", default="tools/mem0-remote-mcp/retrieval_eval_cases.v1.13.json")
    parser.add_argument("--kontext-database-url", required=False)
    parser.add_argument("--mem0-profile-url", required=False)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", required=False)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    report = {
        "ok": True,
        "mode": "dry_run" if args.dry_run else "configured",
        "cases": args.cases,
        "top_k": args.top_k,
    }
    text = json.dumps(report, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: Run CLI test**

Run: `python -m pytest tools\kontext-v2\tests\test_parity_cli.py -q`

Expected: `1 passed`.

- [x] **Step 5: Run all Kontext V2 local tests**

Run: `python -m pytest tools\kontext-v2 -q`

Expected: all Kontext V2 tests pass.

- [ ] **Step 6: Commit** *(not run; no commit requested)*

```bash
git add tools/kontext-v2
git commit -m "feat: add kontext v2 parity cli"
```

---

### Task 11: Project Log And Safety Review

**Files:**
- Modify: `project_log.md`

- [x] **Step 1: Run full relevant local tests**

Run: `python -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q`

Expected: Kontext V2 tests pass and existing Mem0 MCP tests still pass.

- [x] **Step 2: Check no secret/log/raw dump files were added**

Run: `git status --short`

Expected: only source, tests, docs, and fixture files for Kontext V2 are newly added or modified.

- [x] **Step 3: Add project log entry**

Append to `project_log.md`:

```markdown
## 2026-05-12 - Kontext V2 Mem0 Mirror Local Foundation

- Built the initial Kontext V2 local package under `tools/kontext-v2`.
- Added Postgres + pgvector schema, Mem0 mirror importer, read-only MCP contract helpers, retrieval parity foundation, side-by-side safe diff reports, fetch/status helpers, write dry-run proposals, dossier projections, and parity CLI shell.
- Mem0 remains live source of truth; no destructive Mem0 writes were performed.
- Verification: `python -m pytest tools\kontext-v2 tools\mem0-remote-mcp -q` passed.
- Next step: implement live Mem0 export/import against a private staging database, then run side-by-side retrieval evals without printing raw memory text.
```

- [ ] **Step 4: Commit project log** *(not run; no commit requested)*

```bash
git add project_log.md
git commit -m "docs: log kontext v2 mirror foundation"
```

---

## Deployment Gate

Do not deploy Kontext V2 to the VPS in this plan without a fresh user approval message.

Before a VPS deploy, prepare a separate deployment plan covering:

- target host and directory;
- Docker Compose service names;
- Postgres volume path;
- rollback archive path;
- backup command;
- health check command;
- private MCP smoke command;
- rollback command.

---

## Plan Self-Review

- Spec coverage: schema/import parity, MCP contract parity, retrieval parity, side-by-side diff, fetch parity, dry-run write path, and dossier projection tests are each mapped to a task.
- Placeholder scan: no `TBD`, `TODO`, `FIXME`, or vague implementation-only tasks are intentionally present.
- Type consistency: `external_mem0_id`, `memory_type`, `current_status`, `memory_tier`, `signal_strength`, `source_hash`, and `metadata` are used consistently across models, repository, importer, retrieval, MCP, and parity tests.
- Scope control: dashboard UI and live VPS deployment are intentionally excluded from V0 local foundation until retrieval parity exists.
