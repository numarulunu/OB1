from __future__ import annotations

import json

from kontext_v2.mcp_reliability_eval import (
    evaluate_case_reports,
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
    }
    assert report["failed_cases"] == ["case-b"]

def test_vocality_eval_accepts_decision_memories_as_current_state_evidence():
    from kontext_v2.mcp_reliability_eval import load_cases

    cases = {case["name"]: case for case in load_cases("tools/mem0-remote-mcp/retrieval_eval_cases.v1.13.json")}
    assert "decision" in cases["vocality-current-state"]["expected_memory_types"]
