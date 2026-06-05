from __future__ import annotations

import os

import psycopg
from fastapi.testclient import TestClient

from kontext_v2.categories import infer_category_slugs
from kontext_v2.http_api import build_app
from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.schema import apply_schema


def _memory(
    external_id: str,
    *,
    title: str,
    text: str,
    metadata: dict,
    memory_type: str,
    memory_tier: str = "active",
    current_status: str = "category_test",
) -> MemoryRecord:
    return MemoryRecord(
        external_mem0_id=external_id,
        title=title,
        text=text,
        metadata=metadata,
        memory_type=memory_type,
        current_status=current_status,
        memory_tier=memory_tier,
        signal_strength=8.0,
        source_hash=f"{external_id}-hash",
    )


def test_category_inference_uses_metadata_and_tier_without_database():
    memory = _memory(
        "category-unit-1",
        title="Kontext V2 workflow deployment",
        text="The VPS memory system mirrors Mem0 and supports Codex hooks.",
        metadata={"domains": ["ai", "systems", "workflow"]},
        memory_type="project_state",
        memory_tier="historical",
    )

    slugs = infer_category_slugs(memory)

    assert {"projects", "systems", "workflow", "archive"}.issubset(set(slugs))


def test_upsert_memory_persists_default_category_assignments():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["category-auto-1"],),
            )
        conn.commit()

        repo = KontextRepository(conn)
        repo.upsert_memory(
            _memory(
                "category-auto-1",
                title="Kontext V2 category backend",
                text="Kontext mirrors Mem0 on the VPS and needs workflow categories.",
                metadata={"domains": ["ai", "systems", "workflow"]},
                memory_type="project_state",
                memory_tier="historical",
            )
        )

        slugs = repo.list_memory_category_slugs("category-auto-1")

    assert {"projects", "systems", "workflow", "archive"}.issubset(set(slugs))


def test_repository_supports_category_crud_and_manual_assignment():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM categories WHERE slug = %s", ("category-test-manual",))
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["category-manual-1"],),
            )
        conn.commit()

        repo = KontextRepository(conn)
        repo.upsert_memory(
            _memory(
                "category-manual-1",
                title="Manual category target",
                text="This memory should be assigned to a custom dashboard folder.",
                metadata={"domains": ["custom"]},
                memory_type="preference",
            )
        )

        category = repo.upsert_category(
            slug="category-test-manual",
            name="Manual Category",
            description="Created by the category backend tests.",
        )
        assigned = repo.assign_memory_category(
            external_mem0_id="category-manual-1",
            category_slug="category-test-manual",
            source="manual_test",
        )
        memories = repo.list_category_memories("category-test-manual")
        categories = repo.list_categories()
        deleted = repo.delete_category("category-test-manual")

    custom = next(item for item in categories if item["slug"] == "category-test-manual")
    assert category["slug"] == "category-test-manual"
    assert assigned is True
    assert memories[0]["external_mem0_id"] == "category-manual-1"
    assert memories[0]["title"] == "Manual category target"
    assert "text" not in memories[0]
    assert custom["count"] == 1
    assert custom["entries"] == ["category-manual-1"]
    assert deleted is True



def test_repository_refreshes_auto_category_assignments_for_existing_rows():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["category-refresh-1"],),
            )
        conn.commit()

        repo = KontextRepository(conn)
        repo.upsert_memory(
            _memory(
                "category-refresh-1",
                title="Design system category backfill",
                text="Frontend dashboard design belongs in design and project categories.",
                metadata={"domains": ["design"]},
                memory_type="project_state",
            )
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM memory_categories mc
                USING memories m
                WHERE mc.memory_id = m.id AND m.external_mem0_id = %s
                """,
                ("category-refresh-1",),
            )
        conn.commit()

        assert repo.list_memory_category_slugs("category-refresh-1") == []
        result = repo.refresh_auto_category_assignments(limit=100)
        slugs = repo.list_memory_category_slugs("category-refresh-1")

    assert result["memories"] >= 1
    assert {"design", "projects"}.issubset(set(slugs))

def test_http_api_exposes_categories_and_gates_writes():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM categories WHERE slug = %s", ("category-test-http",))
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["category-http-1"],),
            )
        conn.commit()
        repo = KontextRepository(conn)
        repo.upsert_memory(
            _memory(
                "category-http-1",
                title="HTTP category endpoint target",
                text="Dashboard category endpoints should expose this without raw memory text.",
                metadata={"domains": ["design"]},
                memory_type="project_state",
            )
        )

    readonly_client = TestClient(build_app(database_url, write_enabled=False))
    write_client = TestClient(build_app(database_url, write_enabled=True))

    blocked = readonly_client.post(
        "/categories",
        json={"slug": "category-test-http", "name": "HTTP Category"},
    )
    created = write_client.post(
        "/categories",
        json={"slug": "category-test-http", "name": "HTTP Category"},
    )
    assigned = write_client.post(
        "/categories/category-test-http/assign",
        json={"memory_id": "category-http-1"},
    )
    listed = readonly_client.get("/categories")
    memories = readonly_client.get("/categories/category-test-http/memories")

    assert blocked.status_code == 403
    assert created.status_code == 200
    assert created.json()["category"]["slug"] == "category-test-http"
    assert assigned.status_code == 200
    assert assigned.json()["assigned"] is True
    assert listed.status_code == 200
    assert any(item["slug"] == "category-test-http" for item in listed.json()["categories"])
    assert memories.status_code == 200
    assert memories.json()["memories"][0]["external_mem0_id"] == "category-http-1"
    assert "Dashboard category endpoints" not in memories.text



def test_http_api_allows_category_writes_without_memory_write_tools():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM categories WHERE slug = %s", ("category-write-only",))
        conn.commit()

    client = TestClient(build_app(database_url, write_enabled=False, category_write_enabled=True))

    created = client.post(
        "/categories",
        json={"slug": "category-write-only", "name": "Category Write Only"},
    )
    tools = client.get("/tools")
    deleted = client.delete("/categories/category-write-only")

    tool_names = {tool["name"] for tool in tools.json()["tools"]}
    assert created.status_code == 200
    assert created.json()["category"]["slug"] == "category-write-only"
    assert deleted.status_code == 200
    assert "submit_memory_override" not in tool_names
    assert "ingest_exchange" not in tool_names
