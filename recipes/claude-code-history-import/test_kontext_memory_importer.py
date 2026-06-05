import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace


RECIPE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(RECIPE_DIR))


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, RECIPE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        create table entries (
            id integer primary key,
            file text,
            fact text,
            source text,
            grade real,
            tier text,
            created_at text,
            updated_at text,
            memory_type text
        )
        """
    )
    conn.execute(
        "insert into entries values (1, 'project_ob1.md', 'OB1 import decision', 'manual', 8, 'active', '2026-04-28', '2026-04-29', 'project')"
    )
    conn.execute(
        "insert into entries values (2, 'user_preferences.md', 'User wants direct answers', 'digest', 9, 'active', '2026-04-27', '2026-04-29', 'preference')"
    )
    return conn


def test_rows_from_connection_maps_kontext_entries():
    importer = load_module("kontext_memory_importer")

    rows = list(importer.rows_from_connection(build_conn(), limit=1))

    assert len(rows) == 1
    assert rows[0]["content"] == "[Kontext: project_ob1.md] OB1 import decision"
    assert rows[0]["metadata"]["source"] == "kontext"
    assert rows[0]["metadata"]["kontext_entry_id"] == 1


def test_export_dry_run_writes_jsonl_without_credentials(tmp_path, monkeypatch):
    importer = load_module("kontext_memory_importer")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    export_path = tmp_path / "kontext.jsonl"
    args = SimpleNamespace(dry_run=True, export=str(export_path), limit=2, sync_log=str(tmp_path / "sync.json"), ingest_endpoint=False)

    stats = importer.run_import(args, conn=build_conn())

    assert stats["found"] == 2
    assert stats["exported"] == 2
    assert stats["ingested"] == 0
    lines = export_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["metadata"]["source"] == "kontext"
