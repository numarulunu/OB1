from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kontext_ob1_retrieval_quality_gate.py"
spec = importlib.util.spec_from_file_location("kontext_ob1_retrieval_quality_gate", SCRIPT_PATH)
gate = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(gate)


class FakeRepo:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def list_memory_rows(self, limit: int = 100):
        return self.rows[:limit]


def row(memory_id: str, title: str, *, domains: list[str], memory_type: str, protected: bool = False) -> dict:
    return {
        "external_mem0_id": memory_id,
        "title": title,
        "text": title,
        "metadata": {
            "domains": domains,
            "memory_type": memory_type,
            "current_status": "active",
            "memory_tier": "active",
            "signal_strength": 9,
            "is_protected_autobiographical_history": protected,
        },
        "memory_type": memory_type,
        "current_status": "active",
        "memory_tier": "active",
        "signal_strength": 9,
        "rank": 0.1,
    }


def test_quality_gate_scores_protected_and_project_cases_without_raw_text() -> None:
    cases = [
        {
            "name": "protected-family-origin",
            "group": "protected_history",
            "query": "how did my father shape my trust pattern",
            "expected_domains": ["family", "psychology"],
            "expected_memory_types": ["formative_event"],
            "require_protected_history": True,
            "max_rank": 1,
        },
        {
            "name": "project-kontext-cutover",
            "group": "project_continuity",
            "query": "Kontext cutover status and Mem0 rollback decision",
            "expected_domains": ["ai", "systems", "infrastructure"],
            "expected_memory_types": ["project_state"],
            "max_rank": 2,
        },
    ]
    repo = FakeRepo(
        [
            row(
                "protected-1",
                "father attachment trust formative event",
                domains=["family", "psychology"],
                memory_type="formative_event",
                protected=True,
            ),
            row(
                "project-1",
                "Kontext cutover status Mem0 rollback decision",
                domains=["ai", "systems", "infrastructure"],
                memory_type="project_state",
            ),
        ]
    )

    report = gate.evaluate_cases(repo, cases, top_k=5)
    rendered = json.dumps(report, sort_keys=True).lower()

    assert report["ok"] is True
    assert report["summary"]["cases"] == 2
    assert report["summary"]["passed"] == 2
    assert report["groups"]["protected_history"]["passed"] == 1
    assert report["groups"]["project_continuity"]["passed"] == 1
    assert report["cases"][0]["query_hash"]
    assert report["cases"][0]["first_satisfying_rank"] == 1
    assert "how did my father" not in rendered
    assert "father attachment trust formative event" not in rendered
    assert "protected-1" not in rendered


def test_quality_gate_fails_when_protected_requirement_is_not_met() -> None:
    cases = [
        {
            "name": "protected-family-origin",
            "group": "protected_history",
            "query": "how did my father shape my trust pattern",
            "expected_domains": ["workflow"],
            "expected_memory_types": ["project_state"],
            "require_protected_history": True,
            "max_rank": 1,
        }
    ]
    repo = FakeRepo(
        [
            row(
                "unprotected-1",
                "workflow project state",
                domains=["workflow"],
                memory_type="project_state",
                protected=False,
            )
        ]
    )

    report = gate.evaluate_cases(repo, cases, top_k=5)

    assert report["ok"] is False
    assert report["summary"]["failed"] == 1
    assert report["cases"][0]["failed_reasons"] == ["protected_history_not_found"]


def test_default_case_file_has_minimum_stratified_coverage() -> None:
    cases = gate.load_cases(Path("tools/kontext-v2/eval/ob1_retrieval_quality_cases.json"))
    names = [case["name"] for case in cases]
    groups = [case["group"] for case in cases]

    assert len(cases) >= 30
    assert len(names) == len(set(names))
    assert groups.count("protected_history") >= 10
    assert groups.count("project_continuity") >= 15
    assert all(case["query_hash"] for case in cases)
