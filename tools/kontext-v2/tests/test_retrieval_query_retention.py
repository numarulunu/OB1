from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "retrieval_query_retention.py"
spec = importlib.util.spec_from_file_location("retrieval_query_retention", SCRIPT_PATH)
retrieval_query_retention = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(retrieval_query_retention)


def test_build_report_is_dry_run_and_sanitized_by_default() -> None:
    report = retrieval_query_retention.build_report(
        keep_days=30,
        max_delete=5000,
        total_rows=12500,
        eligible_rows=750,
        deleted_rows=0,
        apply=False,
    )
    rendered = json.dumps(report, sort_keys=True)

    assert report["ok"] is True
    assert report["mode"] == "retrieval-query-retention"
    assert report["dry_run"] is True
    assert report["keep_days"] == 30
    assert report["max_delete"] == 5000
    assert report["total_rows"] == 12500
    assert report["eligible_rows"] == 750
    assert report["would_delete_rows"] == 750
    assert report["deleted_rows"] == 0
    assert "query_hash" not in rendered
    assert "result_external_ids" not in rendered
    assert "memory" not in rendered.lower()


def test_build_report_caps_planned_delete_count() -> None:
    report = retrieval_query_retention.build_report(
        keep_days=30,
        max_delete=100,
        total_rows=10000,
        eligible_rows=1200,
        deleted_rows=100,
        apply=True,
    )

    assert report["dry_run"] is False
    assert report["would_delete_rows"] == 100
    assert report["deleted_rows"] == 100
    assert report["attention"] == ["retention cap reached; rerun may be needed"]


def test_validate_args_rejects_dangerously_short_retention_window() -> None:
    try:
        retrieval_query_retention.validate_retention_args(keep_days=2, max_delete=100)
    except ValueError as exc:
        assert "keep_days" in str(exc)
    else:
        raise AssertionError("expected keep_days validation to fail")


def test_delete_old_rows_uses_bounded_id_subquery() -> None:
    sql, params = retrieval_query_retention.delete_sql(keep_days=45, max_delete=250)

    assert "WITH doomed AS" in sql
    assert "FROM retrieval_queries" in sql
    assert "ORDER BY created_at ASC" in sql
    assert "LIMIT %s" in sql
    assert "RETURNING 1" in sql
    assert params == (45, 250)
