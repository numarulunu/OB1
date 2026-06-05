from __future__ import annotations

import json

from kontext_v2.retrieval_shadow import safe_query_features, shadow_search_filters


def test_safe_query_features_are_bounded_and_do_not_include_raw_query_words() -> None:
    query = "When did my relationship pattern around trust change after the family-origin work?"

    features = safe_query_features(query)
    rendered = json.dumps(features, sort_keys=True).lower()

    assert features["char_len"] == len(query)
    assert features["token_count"] >= 8
    assert features["length_bucket"] == "medium"
    assert features["temporal"] is True
    assert features["autobiographical"] is True
    assert features["current_state"] is False
    assert 1 <= len(features["term_hashes"]) <= 16
    assert all(len(item) == 12 for item in features["term_hashes"])
    assert "relationship" not in rendered
    assert "family-origin" not in rendered
    assert query.lower() not in rendered


def test_shadow_search_filters_keep_requested_filters_and_add_safe_features() -> None:
    filters = shadow_search_filters(
        query="Current Kontext memory retrieval status?",
        top_k=7,
        domains=["ai"],
        memory_types=["project_state"],
        memory_tiers=["active"],
        current_statuses=["active"],
    )

    assert filters["top_k"] == 7
    assert filters["domains"] == ["ai"]
    assert filters["memory_types"] == ["project_state"]
    assert filters["memory_tiers"] == ["active"]
    assert filters["current_statuses"] == ["active"]
    assert filters["scorer_version"]
    assert filters["result_metrics"]["top_score"] is None
    assert filters["result_metrics"]["result_count"] == 0
    assert filters["query_features"]["current_state"] is True
    assert "Current Kontext" not in json.dumps(filters, sort_keys=True)
