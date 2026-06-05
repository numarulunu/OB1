from __future__ import annotations

import json

import kontext_v2.live_mem0 as live_mem0
from kontext_v2.importer import ImportReport
from kontext_v2.live_mem0 import (
    Mem0ApiClient,
    collect_eval_seed_rows,
    safe_import_report,
)


class FakeMem0Client:
    def __init__(self):
        self.fetches = []

    def search(self, query: str, top_k: int = 5):
        return {
            "results": [
                {"id": "mem-1", "memory": "raw text one", "metadata": {"memory_type": "project_state"}},
                {"id": "mem-2", "title": "needs fetch", "metadata": {"domains": ["ai"]}},
            ]
        }

    def fetch(self, memory_id: str):
        self.fetches.append(memory_id)
        return {"id": memory_id, "memory": "fetched raw text", "metadata": {"domains": ["systems"]}}


def test_mem0_api_client_get_all_uses_api_key_without_exposing_it():
    calls = []

    def fake_transport(request, timeout):
        calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "api_key": request.headers.get("X-api-key"),
            }
        )
        return {"results": [{"id": "mem-1", "memory": "private memory", "metadata": {}}]}

    client = Mem0ApiClient(
        base_url="https://mem0.example.test",
        api_key="secret-key",
        user_id="ionut",
        transport=fake_transport,
    )

    payload = client.get_all()

    assert payload["results"][0]["id"] == "mem-1"
    assert calls == [
        {
            "url": "https://mem0.example.test/memories?user_id=ionut",
            "method": "GET",
            "api_key": "secret-key",
        }
    ]


def test_mem0_api_client_search_allows_top_k_50_for_parity_sweeps():
    calls = []

    def fake_transport(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append(payload)
        return {"results": []}

    client = Mem0ApiClient(
        base_url="https://mem0.example.test",
        api_key="secret-key",
        user_id="ionut",
        transport=fake_transport,
    )

    client.search("memory architecture", top_k=50)

    assert calls[0]["top_k"] == 50


def test_postgres_memory_rows_reads_payload_json_and_uses_database_id(monkeypatch):
    executed = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            executed.append((sql, params))

        def fetchall(self):
            return [
                (
                    "db-id-1",
                    json.dumps(
                        {
                            "id": "payload-id-should-not-win",
                            "data": "private memory",
                            "domains": ["ai"],
                            "memory_type": "project_state",
                            "current_status": "active",
                            "memory_tier": "active",
                            "signal_strength": 8,
                            "user_id": "ionut",
                        }
                    ),
                ),
                ("db-id-2", {"data": "second memory", "memory_type": "note"}),
            ]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

    class FakePsycopg:
        def connect(self, url):
            assert url == "postgres://mem0-db"
            return FakeConnection()

    monkeypatch.setattr(live_mem0, "import_module", lambda name: FakePsycopg(), raising=False)

    rows = live_mem0.postgres_memory_rows("postgres://mem0-db", user_id="ionut", limit=500)

    assert rows == [
        {
            "id": "db-id-1",
            "data": "private memory",
            "text": "private memory",
            "metadata": {
                "domains": ["ai"],
                "memory_type": "project_state",
                "signal_strength": 8,
                "current_status": "active",
                "memory_tier": "active",
            },
            "domains": ["ai"],
            "memory_type": "project_state",
            "current_status": "active",
            "memory_tier": "active",
            "signal_strength": 8,
            "user_id": "ionut",
        },
        {
            "id": "db-id-2",
            "data": "second memory",
            "text": "second memory",
            "metadata": {
                "domains": [],
                "memory_type": "note",
                "signal_strength": None,
                "current_status": "",
                "memory_tier": "active",
            },
            "memory_type": "note",
        },
    ]
    assert "from mem0_memories" in executed[0][0]
    assert executed[0][1] == ["ionut", 500]


def test_mem0_api_client_get_all_prefers_lexical_database_url(monkeypatch):
    http_calls = []

    def fake_transport(request, timeout):
        http_calls.append(request.full_url)
        raise AssertionError("HTTP fallback should not be used when lexical DB is configured")

    monkeypatch.setattr(
        live_mem0,
        "postgres_memory_rows",
        lambda lexical_database_url, user_id, limit: [
            {"id": "mem-db", "memory": "database memory", "metadata": {}}
        ],
        raising=False,
    )

    client = Mem0ApiClient(
        base_url="https://mem0.example.test",
        api_key="secret-key",
        user_id="ionut",
        lexical_database_url="postgres://mem0-db",
        transport=fake_transport,
    )

    payload = client.get_all(page_size=50, max_pages=2)

    assert payload == {"results": [{"id": "mem-db", "memory": "database memory", "metadata": {}}], "count": 1}
    assert http_calls == []


def test_collect_eval_seed_rows_fetches_missing_full_text_and_dedupes_ids():
    client = FakeMem0Client()
    rows = collect_eval_seed_rows(
        client,
        cases=[{"name": "case-a", "query": "memory architecture"}, {"name": "case-b", "query": "memory architecture"}],
        top_k=2,
        max_fetches=10,
    )

    assert [row["id"] for row in rows] == ["mem-1", "mem-2"]
    assert client.fetches == ["mem-2"]


def test_safe_import_report_omits_raw_memory_text():
    report = safe_import_report(
        ImportReport(created=1, updated=2, unchanged=3, skipped=4),
        source="live_seed",
        rows_seen=10,
    )

    rendered = json.dumps(report, sort_keys=True)
    assert report == {
        "source": "live_seed",
        "rows_seen": 10,
        "created": 1,
        "updated": 2,
        "unchanged": 3,
        "skipped": 4,
    }
    assert "raw text" not in rendered
    assert "memory" not in rendered.lower()
