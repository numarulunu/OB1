from __future__ import annotations

import json

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "retrieval_shadow_report.py"
spec = importlib.util.spec_from_file_location("retrieval_shadow_report", SCRIPT_PATH)
retrieval_shadow_report = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(retrieval_shadow_report)


def test_build_report_summarizes_shadow_rows_without_raw_query_or_memory_text() -> None:
    raw_query = "private relationship query text"
    raw_memory = "private memory body text"
    rows = [
        {
            "query_hash": "hash-a",
            "profile": "codex",
            "service": "kontext",
            "filters": {
                "domains": ["ai"],
                "top_k": 3,
                "query_features": {
                    "char_len": len(raw_query),
                    "token_count": 4,
                    "length_bucket": "short",
                    "temporal": False,
                    "current_state": False,
                    "autobiographical": True,
                    "term_hashes": ["abc123def456"],
                },
            },
            "result_external_ids": ["memory-1"],
            "latency_ms": 50.0,
        },
        {
            "query_hash": "hash-a",
            "profile": "claude",
            "service": "kontext",
            "filters": {
                "domains": ["workflow"],
                "top_k": 5,
                "query_features": {
                    "char_len": 90,
                    "token_count": 11,
                    "length_bucket": "medium",
                    "temporal": True,
                    "current_state": True,
                    "autobiographical": False,
                    "term_hashes": [],
                },
            },
            "result_external_ids": [],
            "latency_ms": 150.0,
        },
    ]

    report = retrieval_shadow_report.build_report(rows)
    rendered = json.dumps(report, sort_keys=True)

    assert report["ok"] is True
    assert report["rows_seen"] == 2
    assert report["unique_query_hashes"] == 1
    assert report["duplicate_query_hashes"] == 1
    assert report["zero_result_count"] == 1
    assert report["profiles"] == {"claude": 1, "codex": 1}
    assert report["requested_domains"] == {"ai": 1, "workflow": 1}
    assert report["telemetry_coverage"] == {
        "rows_with_query_features": 2,
        "rows_with_top_k": 2,
    }
    assert report["query_features"]["autobiographical"] == 1
    assert report["query_features"]["current_state"] == 1
    assert report["latency_ms"]["avg"] == 100.0
    assert raw_query not in rendered
    assert raw_memory not in rendered
    assert "memory-1" not in rendered


def test_build_report_breaks_down_zero_result_high_latency_and_repeat_queries() -> None:
    """Shadow telemetry must surface WHAT KIND of queries are failing.

    With only aggregate counters, the user cannot tell whether zero-result
    or slow queries cluster around autobiographical, temporal, current_state,
    or length-bucket patterns. The report must expose:

      - ``zero_result_features``: per query-feature counts among rows
        whose ``result_external_ids`` is empty.
      - ``high_latency_features``: per query-feature counts among rows
        whose ``latency_ms`` is at or above the p95 threshold.
      - ``repeated_query_top_n``: top-5 most-frequent ``query_hash`` values
        with their repeat counts (hashes only — never raw query text).

    These three breakdowns let scorer/expansion changes be calibrated against
    real production query patterns instead of self-authored fixtures, and they
    never expose raw query/memory text.
    """

    rows = [
        # Zero-result, autobiographical, short — example: "trauma?"
        {
            "query_hash": "hash-zero-auto",
            "profile": "codex",
            "service": "kontext",
            "filters": {
                "domains": [],
                "top_k": 5,
                "query_features": {
                    "char_len": 24,
                    "token_count": 3,
                    "length_bucket": "short",
                    "temporal": False,
                    "current_state": False,
                    "autobiographical": True,
                    "term_hashes": ["aaaaaaaaaaaa"],
                },
            },
            "result_external_ids": [],
            "latency_ms": 30.0,
        },
        # Zero-result, temporal current_state, medium
        {
            "query_hash": "hash-zero-time",
            "profile": "claude",
            "service": "kontext",
            "filters": {
                "domains": ["workflow"],
                "top_k": 5,
                "query_features": {
                    "char_len": 110,
                    "token_count": 14,
                    "length_bucket": "medium",
                    "temporal": True,
                    "current_state": True,
                    "autobiographical": False,
                    "term_hashes": [],
                },
            },
            "result_external_ids": [],
            "latency_ms": 80.0,
        },
        # Hit, slow, autobiographical long — example explainer query
        {
            "query_hash": "hash-slow-auto",
            "profile": "codex",
            "service": "kontext",
            "filters": {
                "domains": ["psychology"],
                "top_k": 10,
                "query_features": {
                    "char_len": 220,
                    "token_count": 30,
                    "length_bucket": "long",
                    "temporal": False,
                    "current_state": False,
                    "autobiographical": True,
                    "term_hashes": [],
                },
            },
            "result_external_ids": ["memory-99"],
            "latency_ms": 1800.0,
        },
        # Hit, fast, not autobiographical
        {
            "query_hash": "hash-fast",
            "profile": "codex",
            "service": "kontext",
            "filters": {
                "domains": ["ai"],
                "top_k": 3,
                "query_features": {
                    "char_len": 40,
                    "token_count": 5,
                    "length_bucket": "short",
                    "temporal": False,
                    "current_state": False,
                    "autobiographical": False,
                    "term_hashes": [],
                },
            },
            "result_external_ids": ["memory-1"],
            "latency_ms": 45.0,
        },
        # Same query as the very first row — drives repeated-hash signal
        {
            "query_hash": "hash-zero-auto",
            "profile": "codex",
            "service": "kontext",
            "filters": {
                "domains": [],
                "top_k": 5,
                "query_features": {
                    "char_len": 24,
                    "token_count": 3,
                    "length_bucket": "short",
                    "temporal": False,
                    "current_state": False,
                    "autobiographical": True,
                    "term_hashes": ["aaaaaaaaaaaa"],
                },
            },
            "result_external_ids": [],
            "latency_ms": 35.0,
        },
    ]

    report = retrieval_shadow_report.build_report(rows)

    rendered = json.dumps(report, sort_keys=True)
    assert "memory-99" not in rendered
    assert "memory-1" not in rendered

    zero_features = report["zero_result_features"]
    assert zero_features["autobiographical"] == 2
    assert zero_features["temporal"] == 1
    assert zero_features["current_state"] == 1
    assert zero_features["length_buckets"]["short"] == 2
    assert zero_features["length_buckets"]["medium"] == 1
    assert zero_features["total"] == 3

    high_latency = report["high_latency_features"]
    assert high_latency["threshold_ms"] >= 1.0
    # Only the 1800ms row should clear the p95 threshold for this sample.
    assert high_latency["total"] >= 1
    assert high_latency["autobiographical"] >= 1

    repeated = report["repeated_query_top_n"]
    assert isinstance(repeated, list)
    assert any(item["query_hash"] == "hash-zero-auto" and item["count"] == 2 for item in repeated)
    assert all("count" in item and "query_hash" in item for item in repeated)
    assert all(item["count"] >= 2 for item in repeated)
