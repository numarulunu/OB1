from __future__ import annotations

import json

from kontext_v2.mcp_reliability_eval import (
    bounded_top_k,
    compare_services,
    evaluate_case_reports,
    load_cases,
    normalize_domain_values,
    parse_tool_text_payload,
    safe_result_row,
)


def test_parse_tool_text_payload_handles_mcp_text_content():
    response = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"results": [{"id": "mem-1", "memory": "private text"}]}),
                }
            ]
        }
    }

    payload = parse_tool_text_payload(response)

    assert payload["results"][0]["id"] == "mem-1"


def test_safe_result_row_omits_raw_memory_text():
    row = {
        "id": "mem-1",
        "memory": "private raw memory",
        "title": "safe title",
        "metadata": {"domains": ["ai"], "memory_type": "project_state"},
    }

    safe = safe_result_row(row)
    rendered = json.dumps(safe)

    assert safe == {
        "id": "mem-1",
        "title": "safe title",
        "domains": ["ai"],
        "memory_type": "project_state",
    }
    assert "private raw memory" not in rendered
    assert "memory" not in safe


def test_evaluate_case_reports_counts_domain_and_type_hits():
    cases = [
        {
            "name": "case-a",
            "expected_domains": ["ai"],
            "expected_memory_types": ["project_state"],
            "min_results": 1,
        },
        {
            "name": "case-b",
            "expected_domains": ["finance"],
            "expected_memory_types": ["finance_context"],
            "min_results": 1,
        },
    ]
    service_results = {
        "case-a": [{"id": "mem-1", "metadata": {"domains": ["ai"], "memory_type": "project_state"}}],
        "case-b": [{"id": "mem-2", "metadata": {"domains": ["business"], "memory_type": "decision"}}],
    }

    report = evaluate_case_reports(cases, service_results)

    assert report["summary"] == {
        "cases": 2,
        "passed": 1,
        "failed": 1,
        "pass_rate": 0.5,
        "domain_hits": 1,
        "memory_type_hits": 1,
        "first_satisfying_hits": 1,
        "first_satisfying_mrr": 0.5,
        "first_satisfying_median_rank": 1,
    }
    assert report["failed_cases"] == ["case-b"]
    assert report["cases"][0]["first_satisfying_rank"] == 1
    assert report["cases"][1]["first_satisfying_rank"] is None


def test_evaluate_case_reports_tracks_first_row_matching_domain_and_type_together():
    cases = [
        {
            "name": "case-a",
            "expected_domains": ["finance"],
            "expected_memory_types": ["project_state"],
            "min_results": 1,
        }
    ]
    service_results = {
        "case-a": [
            {"id": "domain-only", "metadata": {"domains": ["finance"], "memory_type": "workflow"}},
            {"id": "type-only", "metadata": {"domains": ["systems"], "memory_type": "project_state"}},
            {"id": "both", "metadata": {"domains": ["finance"], "memory_type": "project_state"}},
        ]
    }

    report = evaluate_case_reports(cases, service_results)

    assert report["cases"][0]["domain_hit"] is True
    assert report["cases"][0]["memory_type_hit"] is True
    assert report["cases"][0]["first_satisfying_rank"] == 3
    assert report["summary"]["first_satisfying_mrr"] == 0.3333


def test_domain_aliases_make_workflows_match_workflow_expectations():
    assert "workflow" in normalize_domain_values(["workflows"])

    report = evaluate_case_reports(
        [
            {
                "name": "case-a",
                "expected_domains": ["workflow"],
                "expected_memory_types": ["decision"],
                "min_results": 1,
            }
        ],
        {"case-a": [{"id": "mem-1", "metadata": {"domains": ["workflows"], "memory_type": "decision"}}]},
    )

    assert report["cases"][0]["first_satisfying_rank"] == 1


def test_compare_services_tracks_overlap_mrr_without_raw_rows():
    comparison = compare_services(
        {
            "kontext": {
                "eval": {
                    "cases": [
                        {"name": "case-a", "top_ids": ["a", "b", "c"]},
                        {"name": "case-b", "top_ids": ["d", "e"], "first_satisfying_rank": 2},
                        {"name": "case-c", "top_ids": ["f"], "first_satisfying_rank": 1},
                    ]
                }
            },
            "mem0": {
                "eval": {
                    "cases": [
                        {"name": "case-a", "top_ids": ["x", "b", "y"]},
                        {"name": "case-b", "top_ids": ["d", "z"], "first_satisfying_rank": 1},
                        {"name": "case-c", "top_ids": ["q"], "first_satisfying_rank": None},
                    ]
                }
            },
        }
    )

    assert comparison == {
        "pair": ["kontext", "mem0"],
        "cases": 3,
        "shared_any_cases": 2,
        "shared_first_cases": 1,
        "shared_any_rate": 0.6667,
        "shared_first_rate": 0.3333,
        "left_first_overlap_mrr": 0.5,
        "right_first_overlap_mrr": 0.5,
        "mean_first_overlap_mrr": 0.5,
        "left_first_satisfying_mrr": 0.5,
        "right_first_satisfying_mrr": 0.3333,
        "mean_first_satisfying_mrr": 0.4167,
        "left_better_satisfying_cases": ["case-c"],
        "right_better_satisfying_cases": ["case-b"],
        "equal_satisfying_cases": 1,
        "no_overlap_cases": ["case-c"],
    }

def test_vocality_eval_accepts_decision_memories_as_current_state_evidence():
    cases = {case["name"]: case for case in load_cases("tools/mem0-remote-mcp/retrieval_eval_cases.v1.13.json")}
    assert "decision" in cases["vocality-current-state"]["expected_memory_types"]


def test_expanded_eval_pack_has_broad_safe_coverage():
    cases = load_cases("tools/mem0-remote-mcp/retrieval_eval_cases.v1.14-expanded.json")
    names = [case["name"] for case in cases]
    domains = set().union(*(set(case["expected_domains"]) for case in cases))
    memory_types = set().union(*(set(case["expected_memory_types"]) for case in cases))

    assert len(cases) >= 30
    assert len(names) == len(set(names))
    assert {"ai", "systems", "workflow", "business", "vocality", "opera", "finance", "relationships", "psychology", "infrastructure"} <= domains
    assert {"project_state", "architecture_decision", "decision", "preference", "workflow", "relationship_pattern", "psychology_pattern", "finance_context", "career_context"} <= memory_types
    assert all(case["query_hash"] for case in cases)
    assert all(case["min_results"] >= 1 for case in cases)


def test_mcp_reliability_eval_allows_top_k_50():
    assert bounded_top_k(99) == 50
