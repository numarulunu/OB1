from __future__ import annotations

from datetime import datetime, timezone
import json


class EmptyCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        if "flag_type IN ('stale_candidate', 'decay')" in sql:
            self.rows = [{"count": 1}]
        elif "SELECT count(*) FROM memory_flags WHERE status = 'pending'" in sql:
            self.rows = [{"count": 0}]
        elif "SELECT count(*) FROM relations" in sql:
            self.rows = [{"count": 0}]
        elif "SELECT max(" in sql:
            self.rows = [{"max": None}]
        else:
            self.rows = []
        return self

    def fetchall(self):
        return self.rows


class EmptyConnection:
    def cursor(self, *args, **kwargs):
        return EmptyCursor()


class FakeDashboardRepo:
    conn = EmptyConnection()

    def count_memories(self) -> int:
        return 11752

    def list_memory_rows(self, limit: int = 1000, offset: int = 0) -> list[dict]:
        assert limit == 2
        assert offset == 0
        stamp = datetime(2026, 5, 25, 14, 19, 54, tzinfo=timezone.utc)
        return [
            {
                "external_mem0_id": "real-1",
                "title": "Real Kontext project state",
                "text": "Kontext V2 dashboard must read the live Postgres mirror.",
                "metadata": {"domains": ["ai", "systems"], "source": "kontext-v2-codex"},
                "memory_type": "project_state",
                "current_status": "active",
                "memory_tier": "active",
                "signal_strength": 10.0,
                "created_at": stamp,
                "updated_at": stamp,
                "imported_at": stamp,
            },
            {
                "external_mem0_id": "real-2",
                "title": "Cold stale cleanup candidate",
                "text": "This old memory is retained as cold context.",
                "metadata": {"domains": ["workflow"], "source": "kontext-v2-claude"},
                "memory_type": "workflow_state",
                "current_status": "dormant",
                "memory_tier": "cold",
                "signal_strength": 5.0,
                "created_at": stamp,
                "updated_at": stamp,
                "imported_at": stamp,
            },
        ]

    def list_categories(self) -> list[dict]:
        return [
            {
                "slug": "systems",
                "name": "Systems",
                "description": "Systems context",
                "count": 2,
                "entries": ["real-1", "real-2"],
                "stale": 1,
                "uses": 0,
                "sources": {"auto": 2},
            }
        ]

    def list_category_memories(self, slug: str, limit: int = 100) -> list[dict]:
        return []

    def list_dry_run_write_audit(self, limit: int = 30) -> dict:
        return {
            "count": 1,
            "summary": {"by_action": {"save": 1}, "by_origin": {"codex": 1}},
            "items": [
                {
                    "id": 1,
                    "action": "save",
                    "status": "dry_run",
                    "origin": "codex",
                    "source_hash_short": "abc123",
                    "created_at": "2026-05-25T14:00:00+00:00",
                }
            ],
        }

    def latest_mirror_sync_run(self) -> dict:
        return {
            "status": "ok",
            "source": "mem0",
            "mode": "live_snapshot",
            "finished_at": "2026-05-25T14:11:45+00:00",
            "rows_seen": 20,
            "error_count": 0,
        }

    def maintenance_status(self, recent_limit: int = 500) -> dict:
        return {
            "due": False,
            "pending_flags": 5,
            "flag_types": {"stale_candidate": 5},
        }


def test_dashboard_snapshot_uses_v2_repo_totals_and_real_entries():
    from kontext_v2.dashboard_snapshot import build_dashboard_snapshot

    payload = build_dashboard_snapshot(FakeDashboardRepo(), entry_limit=2)

    assert payload["meta"]["data_source"] == "kontext_v2"
    assert payload["meta"]["total"] == 11752
    assert payload["meta"]["loaded"] == 2
    assert payload["meta"]["last_sync"] == "2026-05-25T14:11:45+00:00"
    assert payload["meta"]["legacy_mem0_sync"] == "2026-05-25T14:11:45+00:00"
    assert payload["meta"]["by_tier"]["active"] == 11751
    assert payload["meta"]["by_tier"]["archive"] == 1
    assert payload["meta"]["stale"] == 1
    assert payload["meta"]["pending_flags"] == 0
    assert payload["meta"]["sync_drift"] == 0
    assert payload["meta"]["sync_drift_known"] is True
    assert payload["meta"]["relations"] == 1
    assert payload["meta"]["relations_synthetic"] is True
    assert payload["meta"]["relations_source"] == "derived"
    assert 0 <= payload["meta"]["relation_coverage"] <= 1
    assert payload["meta"]["fresh_pct_scope"] == "all_memories_pending_flags"
    assert payload["meta"]["errors"] == []
    assert set(payload["meta"]["health_components"]) >= {"freshness", "maintenance", "sync", "errors"}
    assert payload["totals"]["entries"] == 11752
    assert "canonical" not in payload["totals"]
    assert "devices" not in payload["totals"]
    assert "histOps" not in payload["totals"]
    assert len(payload["entries"]) == 2
    assert payload["entries"][0]["id"] == "real-1"
    assert payload["entries"][1]["tier"] == "C"
    assert "body" not in payload["entries"][0]
    assert payload["entries"][0]["body_len"] == len("Kontext V2 dashboard must read the live Postgres mirror.")
    assert len(payload["entries"][0]["body_hash"]) == 8
    assert payload["relations"][0]["source"] == "real-1"
    assert payload["relations"][0]["target"] == "real-2"
    assert payload["relations"][0]["kind"] == "shared_context"
    assert payload["relations"][0]["synthetic"] is True
    assert payload["relations"][0]["strength"] > 0.5
    assert payload["entries"][0]["relations"] == ["real-2"]
    assert payload["categories"][0]["slug"] == "systems"
    assert payload["dryRunWrites"]["count"] == 1
    assert payload["config"]["memory"]["system"] == "Kontext V2 primary"
    assert payload["config"]["source"] == "static_defaults"
    assert payload["config"]["decay"]["half_life_days"] == 23

    serialized = json.dumps(payload)
    assert "Kontext V2 dashboard must read the live Postgres mirror." not in serialized
    assert "This old memory is retained as cold context." not in serialized
    assert "Core identity" not in serialized
    assert "Evernote" not in serialized
    assert "Mem0 synced" not in serialized


def test_dashboard_activity_counts_only_cleanup_age_flags_as_decay():
    from datetime import date

    from kontext_v2.dashboard_snapshot import _activity

    class ActivityCursor:
        def __init__(self):
            self.executed = []
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            self.executed.append(" ".join(sql.split()))
            if "memory_flags" in sql:
                self.rows = [{"d": date(2026, 5, 25), "c": 2}]
            else:
                self.rows = []
            return self

        def fetchall(self):
            return self.rows

    class ActivityConnection:
        def __init__(self):
            self.cursor_obj = ActivityCursor()

        def cursor(self, *args, **kwargs):
            return self.cursor_obj

    class ActivityRepo:
        def __init__(self):
            self.conn = ActivityConnection()

    repo = ActivityRepo()
    rows = _activity(repo, datetime(2026, 5, 25, tzinfo=timezone.utc))

    assert any("flag_type IN ('stale_candidate', 'decay')" in sql for sql in repo.conn.cursor_obj.executed)
    assert rows[-1]["decayed"] == 2


def test_dashboard_snapshot_records_query_errors_without_silent_zeroes():
    from kontext_v2.dashboard_snapshot import build_dashboard_snapshot

    class BrokenCursor(EmptyCursor):
        def execute(self, sql, params=()):
            if "memory_flags WHERE status = 'pending'" in sql:
                raise RuntimeError("synthetic db failure")
            return super().execute(sql, params)

    class BrokenConnection:
        def cursor(self, *args, **kwargs):
            return BrokenCursor()

    class BrokenRepo(FakeDashboardRepo):
        conn = BrokenConnection()

    payload = build_dashboard_snapshot(BrokenRepo(), entry_limit=2)

    assert payload["meta"]["pending_flags"] is None
    assert len(payload["meta"]["errors"]) == 1
    error = payload["meta"]["errors"][0]
    assert "memory_flags" in error["sql_head"]
    assert error["error"] == "RuntimeError"
    assert "synthetic db failure" not in json.dumps(payload["meta"]["errors"])
    assert payload["meta"]["health_components"]["errors"] == 0.0


def test_dashboard_snapshot_marks_sync_drift_unknown_without_sync_run():
    from kontext_v2.dashboard_snapshot import build_dashboard_snapshot

    class NoSyncRepo(FakeDashboardRepo):
        def latest_mirror_sync_run(self) -> dict:
            return {}

    payload = build_dashboard_snapshot(NoSyncRepo(), entry_limit=2)

    assert payload["meta"]["sync_status"] == "unknown"
    assert payload["meta"]["sync_drift"] is None
    assert payload["meta"]["sync_drift_known"] is False


def test_dashboard_snapshot_does_not_promote_body_text_to_missing_title():
    from kontext_v2.dashboard_snapshot import _entry

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    private_body = "Private body text must not become a dashboard title when Mem0 did not provide one."

    item = _entry(
        {
            "external_mem0_id": "memory-without-title",
            "title": "",
            "text": private_body,
            "metadata": {},
            "memory_type": "relationship_history",
            "current_status": "active",
            "memory_tier": "historical",
            "signal_strength": 8,
            "created_at": now,
            "updated_at": now,
            "imported_at": now,
        },
        now,
    )

    rendered = json.dumps(item)
    assert item["title"] == "Untitled relationship history memory"
    assert item["desc"] == "Untitled relationship history memory"
    assert private_body not in rendered
    assert "Private body text" not in rendered


def test_dashboard_snapshot_exposes_context_mode_integration_when_enabled(monkeypatch):
    from kontext_v2.dashboard_snapshot import build_dashboard_snapshot

    monkeypatch.setenv("KONTEXT_CONTEXT_MODE_ENABLED", "1")
    monkeypatch.setenv("KONTEXT_CONTEXT_MODE_INSIGHT_URL", "http://localhost:4747")

    payload = build_dashboard_snapshot(FakeDashboardRepo(), entry_limit=2)

    integration = payload["integrations"]["context_mode"]
    assert integration == {
        "available": True,
        "label": "Context Mode",
        "insight_url": "http://localhost:4747",
    }


def test_recent_activity_contains_only_memory_write_and_retrieval_events(monkeypatch):
    from kontext_v2 import dashboard_snapshot

    def fake_query(_repo, sql, _params=()):
        if "memory_intake_audit" in sql:
            assert "status = 'apply'" in sql
            return [
                {
                    "origin": "kontext-v2-codex",
                    "action": "save",
                    "status": "apply",
                    "created_at": datetime(2026, 5, 25, 20, 5, tzinfo=timezone.utc),
                }
            ]
        if "retrieval_queries" in sql:
            return [
                {
                    "origin": "kontext-v2-codex",
                    "service": "kontext",
                    "profile": "codex",
                    "result_count": 3,
                    "latency_ms": 42,
                    "created_at": datetime(2026, 5, 25, 20, 6, tzinfo=timezone.utc),
                }
            ]
        for internal_table in (
            "hook_heartbeats",
            "project_observations",
            "mirror_sync_runs",
            "memory_flags",
        ):
            assert internal_table not in sql
        return []

    monkeypatch.setattr(dashboard_snapshot, "_query", fake_query)

    events = dashboard_snapshot._events(object())

    assert [event["kind"] for event in events] == ["read", "write"]
    assert [event["label"] for event in events] == ["Memory pulled", "Memory ingested"]
    rendered = " ".join(f"{event['label']} {event['detail']} {event['kind']}" for event in events)
    assert "Hook heartbeat" not in rendered
    assert "Project observation" not in rendered
    assert "Mirror sync" not in rendered
    assert "Memory flag" not in rendered
