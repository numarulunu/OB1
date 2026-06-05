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
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-ai-architecture-1", "mem-vocality-1"],),
            )
        conn.commit()
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


def test_import_skips_malformed_rows_without_aborting():
    payload = {
        "results": [
            {
                "id": "mem-valid-robustness-1",
                "memory": "Valid sanitized memory row.",
                "metadata": {
                    "title": "Valid robustness row",
                    "memory_type": "project_state",
                    "current_status": "active",
                    "memory_tier": "active",
                    "signal_strength": 7,
                },
            },
            {
                "id": "mem-bad-metadata",
                "memory": "Bad metadata row.",
                "metadata": "not a dict",
            },
            {
                "id": "mem-bad-signal-strength",
                "memory": "Bad signal strength row.",
                "metadata": {"signal_strength": "high"},
            },
            {
                "id": "mem-nonfinite-signal-strength",
                "memory": "Non-finite signal strength row.",
                "metadata": {"signal_strength": "NaN"},
            },
            {"id": "", "memory": "Missing external id.", "metadata": {}},
            {"id": "mem-missing-text", "metadata": {}},
        ]
    }
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]

    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memories WHERE external_mem0_id = ANY(%s)",
                (["mem-valid-robustness-1"],),
            )
        conn.commit()
        repo = KontextRepository(conn)

        report = import_mem0_export(repo, payload)
        memory = repo.fetch_by_external_id("mem-valid-robustness-1")

    assert report.created == 1
    assert report.skipped == 5
    assert memory is not None
    assert memory.signal_strength == 7.0
