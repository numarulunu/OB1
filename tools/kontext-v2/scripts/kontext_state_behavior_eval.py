from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kontext_v2.intake import apply_intake_proposals, normalize_action, normalize_proposals  # noqa: E402
from kontext_v2.retrieval import search_memories  # noqa: E402


def memory_row(memory_id: str, text: str, rank: float = 0.5, **metadata: Any) -> dict[str, Any]:
    return {
        "external_mem0_id": memory_id,
        "title": text[:90],
        "text": text,
        "rank": rank,
        "metadata": metadata,
        "memory_type": metadata.get("memory_type"),
        "current_status": metadata.get("current_status"),
        "memory_tier": metadata.get("memory_tier"),
        "signal_strength": metadata.get("signal_strength"),
    }


class FixtureRepo:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []
        self.requested_limits: list[int] = []

    def list_memory_rows(self, limit: int = 1000) -> list[dict[str, Any]]:
        self.requested_limits.append(limit)
        return self.rows[:limit]

    def find_exact_text_id(self, content: str) -> str | None:
        for row in self.rows:
            if row.get("text") == content:
                return str(row.get("external_mem0_id") or "")
        return None

    def fetch_by_external_id(self, memory_id: str | None) -> dict[str, Any] | None:
        if not memory_id:
            return None
        for row in self.rows:
            if row.get("external_mem0_id") == memory_id:
                return row
        return None

    def record_memory_flag(self, **_: Any) -> None:
        raise AssertionError("state behavior eval must not apply writes")


def compact_explanation(row: dict[str, Any] | None) -> dict[str, Any]:
    explanation = row.get("score_explanation") if isinstance(row, dict) else {}
    if not isinstance(explanation, dict):
        return {}
    features = []
    for feature in explanation.get("features") or []:
        if isinstance(feature, dict):
            features.append({"name": feature.get("name"), "value": feature.get("value")})
    return {
        "total": explanation.get("total"),
        "features": features,
        "tier": explanation.get("tier"),
        "status": explanation.get("status"),
        "superseded": explanation.get("superseded"),
    }


def sanitized_search(
    repo: FixtureRepo,
    query: str,
    *,
    top_k: int = 5,
    memory_tiers: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows = search_memories(
        repo,
        query=query,
        top_k=top_k,
        domains=[],
        memory_types=[],
        memory_tiers=memory_tiers or [],
        current_statuses=[],
        include_explanations=True,
    )
    return [
        {
            "id": row.get("external_mem0_id"),
            "memory_type": row.get("memory_type"),
            "current_status": row.get("current_status"),
            "memory_tier": row.get("memory_tier"),
            "score_explanation": compact_explanation(row),
        }
        for row in rows
    ]


def case_result(case_id: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"id": case_id, "passed": bool(passed), **details}


def newer_active_fact_precedence() -> dict[str, Any]:
    repo = FixtureRepo(
        [
            memory_row(
                "older-local-only",
                "Kontext V2 currently runs only on the local workstation and should not use the VPS deployment.",
                rank=0.95,
                domains=["ai", "systems", "infrastructure"],
                memory_type="project_state",
                signal_strength=9,
                current_status="outdated",
                memory_tier="historical",
                updated_at="2026-05-10T00:00:00Z",
                superseded_by="current-vps-shadow",
            ),
            memory_row(
                "current-vps-shadow",
                "Kontext V2 currently runs on the VPS in shadow mode while Mem0 remains source of truth.",
                rank=0.58,
                domains=["ai", "systems", "infrastructure"],
                memory_type="project_state",
                signal_strength=9,
                current_status="active",
                memory_tier="active",
                effective_at="2026-05-21T00:00:00Z",
                updated_at="2026-05-21T00:00:00Z",
            ),
        ]
    )
    results = sanitized_search(repo, "current Kontext V2 deployment source of truth VPS")
    got = results[0]["id"] if results else None
    return case_result("newer_active_fact_precedence", got == "current-vps-shadow", expected="current-vps-shadow", got=got, top_ids=[r["id"] for r in results])


def superseded_fact_demotion() -> dict[str, Any]:
    repo = FixtureRepo(
        [
            memory_row(
                "superseded-write-policy",
                "Kontext production writes are enabled globally for all clients and can be used as the source of truth.",
                rank=0.96,
                domains=["ai", "systems", "infrastructure"],
                memory_type="architecture_decision",
                signal_strength=9,
                current_status="active",
                memory_tier="active",
                superseded_by="current-write-policy",
                superseded_at="2026-05-21T00:00:00Z",
                updated_at="2026-05-20T00:00:00Z",
            ),
            memory_row(
                "current-write-policy",
                "Kontext remains shadow and canary only; write-like MCP calls are dry-run unless explicitly approved.",
                rank=0.52,
                domains=["ai", "systems", "infrastructure"],
                memory_type="architecture_decision",
                signal_strength=9,
                current_status="active",
                memory_tier="active",
                effective_at="2026-05-21T00:00:00Z",
                updated_at="2026-05-21T00:00:00Z",
            ),
        ]
    )
    results = sanitized_search(repo, "Kontext production writes source of truth dry-run policy")
    got = results[0]["id"] if results else None
    return case_result("superseded_fact_demotion", got == "current-write-policy", expected="current-write-policy", got=got, top_ids=[r["id"] for r in results])


def cold_tier_demoted_by_default() -> dict[str, Any]:
    repo = FixtureRepo(
        [
            memory_row(
                "archived-note",
                "Kontext dashboard category policy archived note: old draft category names should be kept as the main labels.",
                rank=0.99,
                domains=["ai", "systems"],
                memory_type="project_state",
                signal_strength=4,
                current_status="inactive",
                memory_tier="cold",
                updated_at="2026-05-01T00:00:00Z",
            ),
            memory_row(
                "active-category-policy",
                "Kontext dashboard categories should use simple category labels and keep advanced controls visually quiet.",
                rank=0.45,
                domains=["ai", "systems"],
                memory_type="project_state",
                signal_strength=8,
                current_status="active",
                memory_tier="active",
                updated_at="2026-05-21T00:00:00Z",
            ),
        ]
    )
    results = sanitized_search(repo, "Kontext dashboard category policy labels")
    got = results[0]["id"] if results else None
    return case_result("cold_tier_demoted_by_default", got == "active-category-policy", expected="active-category-policy", got=got, top_ids=[r["id"] for r in results])


def cold_tier_filter_is_explicit() -> dict[str, Any]:
    repo = FixtureRepo(
        [
            memory_row("active-note", "Kontext category labels are current and simple.", domains=["ai"], memory_type="project_state", current_status="active", memory_tier="active"),
            memory_row("cold-note", "Kontext category labels old archive note.", domains=["ai"], memory_type="project_state", current_status="inactive", memory_tier="cold"),
        ]
    )
    results = sanitized_search(repo, "Kontext category labels archive", memory_tiers=["cold"])
    got = [row["id"] for row in results]
    return case_result("cold_tier_filter_is_explicit", got == ["cold-note"], expected=["cold-note"], got=got)


def dry_run_delete_maps_to_delete_candidate() -> dict[str, Any]:
    action, flag_type = normalize_action("delete")
    proposals = normalize_proposals({"proposals": [{"action": "delete", "content": "synthetic stale item", "reason": "stale duplicate"}]})
    result = apply_intake_proposals(FixtureRepo(), proposals=proposals, source_hash="synthetic", origin="state-eval", apply=False)
    first = result["results"][0] if result.get("results") else {}
    passed = action == "flag" and flag_type == "delete_candidate" and first.get("flag_type") == "delete_candidate" and result.get("writes_applied") == 0
    return case_result("dry_run_delete_maps_to_delete_candidate", passed, action=action, flag_type=flag_type, writes_applied=result.get("writes_applied"))


def missing_update_maps_to_conflict_candidate() -> dict[str, Any]:
    proposals = normalize_proposals({"proposals": [{"action": "update", "existing_id": "missing", "content": "synthetic corrected item", "reason": "no exact match"}]})
    result = apply_intake_proposals(FixtureRepo(), proposals=proposals, source_hash="synthetic", origin="state-eval", apply=False)
    first = result["results"][0] if result.get("results") else {}
    passed = first.get("action") == "flag" and first.get("flag_type") == "conflict_candidate" and result.get("writes_applied") == 0
    return case_result("missing_update_maps_to_conflict_candidate", passed, action=first.get("action"), flag_type=first.get("flag_type"), writes_applied=result.get("writes_applied"))


def score_explanations_are_sanitized() -> dict[str, Any]:
    repo = FixtureRepo([memory_row("safe-row", "Kontext scoring explanation fixture with private text that must not leave the eval.", domains=["ai"], memory_type="project_state", current_status="active", memory_tier="active")])
    results = sanitized_search(repo, "Kontext scoring explanation fixture")
    serialized = json.dumps(results, sort_keys=True)
    passed = bool(results and results[0].get("score_explanation")) and "private text" not in serialized and "Kontext scoring explanation fixture" not in serialized
    return case_result("score_explanations_are_sanitized", passed, top_ids=[row["id"] for row in results], score_explanation=results[0].get("score_explanation") if results else {})


CASES: list[Callable[[], dict[str, Any]]] = [
    newer_active_fact_precedence,
    superseded_fact_demotion,
    cold_tier_demoted_by_default,
    cold_tier_filter_is_explicit,
    dry_run_delete_maps_to_delete_candidate,
    missing_update_maps_to_conflict_candidate,
    score_explanations_are_sanitized,
]


def run_eval() -> dict[str, Any]:
    cases = [case() for case in CASES]
    passed = sum(1 for case in cases if case.get("passed"))
    return {
        "ok": passed == len(cases),
        "name": "kontext-state-behavior-eval",
        "writes_applied": 0,
        "total": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "cases": cases,
    }


def write_report(output: str | Path) -> dict[str, Any]:
    report = run_eval()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sanitized Kontext state behavior gates.")
    parser.add_argument("--output", help="Optional JSON report path.")
    args = parser.parse_args(argv)
    report = write_report(args.output) if args.output else run_eval()
    print(f"kontext-state-behavior ok={str(report['ok']).lower()} passed={report['passed']}/{report['total']} writes_applied={report['writes_applied']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
