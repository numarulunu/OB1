from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mcp_no_overlap_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("mcp_no_overlap_diagnostics", SCRIPT_PATH)
mcp_no_overlap_diagnostics = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(mcp_no_overlap_diagnostics)


def test_diagnose_no_overlap_is_sanitized_and_reports_rank_buckets():
    cases = [
        {
            "name": "case-a",
            "query": "AI memory architecture",
            "query_hash": "hash-a",
        }
    ]
    eval_payload = {
        "profiles": {
            "codex": {
                "services": {
                    "kontext": {"eval": {"cases": [{"name": "case-a", "top_ids": ["kontext-top"]}]}},
                    "mem0": {"eval": {"cases": [{"name": "case-a", "top_ids": ["mem0-top"]}]}},
                },
                "comparison": {"no_overlap_cases": ["case-a"]},
            }
        }
    }
    source_rows = [
        {
            "external_mem0_id": "kontext-top",
            "title": "AI memory architecture",
            "text": "private Kontext top memory text",
            "metadata": {"domains": ["ai", "systems"], "memory_type": "project_state", "signal_strength": 9},
            "memory_type": "project_state",
            "memory_tier": "active",
            "current_status": "active",
        },
        {
            "external_mem0_id": "mem0-top",
            "title": "AI memory",
            "text": "private Mem0 candidate memory text",
            "metadata": {"domains": ["ai"], "memory_type": "project_state", "signal_strength": 8},
            "memory_type": "project_state",
            "memory_tier": "active",
            "current_status": "active",
        },
    ]

    report = mcp_no_overlap_diagnostics.diagnose_no_overlap(
        cases=cases,
        eval_payload=eval_payload,
        source_rows=source_rows,
        profile="codex",
        top_mem0_ids=1,
    )
    rendered = json.dumps(report)

    assert report["ok"] is True
    assert report["no_overlap_cases"] == 1
    assert report["rank_buckets"]["top_10"] == 1
    assert report["reports"][0]["mem0_top_ids_mirrored"] == 1
    assert report["reports"][0]["best_mem0_rank_in_kontext"] == 2
    assert "private Kontext" not in rendered
    assert "private Mem0" not in rendered
    assert "mem0-top" not in rendered
    assert "kontext-top" not in rendered



def test_collect_missing_mem0_ids_dedupes_without_printing_content():
    eval_payload = {
        "profiles": {
            "codex": {
                "services": {
                    "kontext": {"eval": {"cases": [{"name": "case-a", "top_ids": ["kontext-top"]}]}},
                    "mem0": {"eval": {"cases": [{"name": "case-a", "top_ids": ["missing", "present", "missing"]}]}},
                },
                "comparison": {"no_overlap_cases": ["case-a"]},
            }
        }
    }
    source_rows = [{"external_mem0_id": "present", "text": "private present text"}]

    assert mcp_no_overlap_diagnostics.collect_missing_mem0_ids(
        eval_payload=eval_payload,
        source_rows=source_rows,
        top_mem0_ids=3,
    ) == ["missing"]
