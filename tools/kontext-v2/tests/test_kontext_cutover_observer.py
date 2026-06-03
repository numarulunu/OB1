from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import time


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kontext_cutover_observer.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kontext_cutover_observer", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def scorecard_payload(ok: bool = True, production_cutover_enabled: bool = False) -> dict:
    return {
        "ok": ok,
        "mode": "kontext-goal-scorecard",
        "runs_model_calls": False,
        "goal_complete_candidate": ok,
        "production_cutover_enabled": production_cutover_enabled,
        "source_of_truth": "mem0",
        "summary": {"blocker_count": 0 if ok else 1, "quality_gap_count": 0},
        "blockers": [] if ok else ["expanded retrieval gate failed"],
        "quality_gaps": [],
        "gates": {
            "official_judged_accuracy": {
                "status": "pass" if ok else "fail",
                "actual_judged_accuracy_proven": ok,
            },
            "beam_hard_slice": {"status": "pass", "top_k50_rate": 1.0},
        },
        "private_raw_memory": "must not render",
    }


def readiness_payload(ok: bool = True) -> dict:
    return {
        "ok": ok,
        "ready_for_user_cutover_review": ok,
        "production_cutover_enabled": False,
        "status_counts": {"pass": 8 if ok else 7, "fail": 0 if ok else 1, "missing": 0},
        "gates": [
            {
                "name": "base_live_mcp_retrieval",
                "status": "pass",
                "evidence": {
                    "kontext": {"cases": 12, "passed": 12, "first_satisfying_mrr": 1.0, "avg_latency_ms": 715.9},
                    "mem0": {"cases": 12, "passed": 12, "first_satisfying_mrr": 0.7389, "avg_latency_ms": 872.68},
                    "private_case_text": "must not render",
                },
            },
            {
                "name": "expanded_live_mcp_retrieval",
                "status": "pass" if ok else "fail",
                "evidence": {
                    "kontext": {"cases": 36, "passed": 36, "first_satisfying_mrr": 1.0, "avg_latency_ms": 741.45},
                    "mem0": {"cases": 36, "passed": 35, "first_satisfying_mrr": 0.8565, "avg_latency_ms": 945.84},
                },
            },
            {"name": "public_mcp_canary_dry_run", "status": "pass", "evidence": {"writes_applied_total": 0}},
            {
                "name": "official_judged_accuracy",
                "status": "pass",
                "evidence": {
                    "actual_judged_accuracy_proven": True,
                    "weighted_accuracy": 1.0,
                    "total_questions": 3,
                    "total_cost_usd": 0.002253,
                },
            },
        ],
    }


def usage_payload(ok: bool = True, total_tokens: int = 1200) -> dict:
    return {
        "ok": ok,
        "summary": {"total_tokens": total_tokens, "malformed_rows": 0 if ok else 1},
        "origins": {
            "codex": {"searches": 8, "ingestion_rows": 2, "avg_search_latency_ms": 650.0, "total_tokens": total_tokens},
        },
        "logs": {"audit_log": "/data/audit.jsonl"},
        "raw_prompt": "must not render",
    }


def client_status_payload(codex_status: str = "healthy") -> dict:
    return {
        "ok": codex_status not in {"missing", "error"},
        "summary": {"status_counts": {codex_status: 1}},
        "origins": {
            "codex": {
                "status": codex_status,
                "hook_count": 3,
                "search_count": 8,
                "ingest_rows": 2,
                "errors": 0 if codex_status != "error" else 1,
            }
        },
    }


def shadow_rows() -> list[dict]:
    return [
        {
            "ts": "2026-05-22T19:00:00Z",
            "event": "kontext_shadow_compare",
            "origin": "codex",
            "mem0_count": 3,
            "kontext_count": 3,
            "shared_any": True,
            "shared_first": False,
            "shared_count": 1,
            "mem0_latency_ms": 900.0,
            "kontext_latency_ms": 700.0,
            "kontext_error": "",
        }
    ]


def many_shadow_rows(count: int, *, old_slow_rows: int = 0) -> list[dict]:
    rows = []
    for index in range(count):
        kontext_latency = 10000.0 if index < old_slow_rows else 900.0
        rows.append(
            {
                "ts": f"2026-05-22T19:{index:02d}:00Z",
                "event": "kontext_shadow_compare",
                "origin": "codex",
                "mem0_count": 5,
                "kontext_count": 5,
                "shared_any": True,
                "shared_first": index % 4 == 0,
                "shared_count": 2,
                "mem0_latency_ms": 1200.0,
                "kontext_latency_ms": kontext_latency,
                "kontext_error": "",
            }
        )
    return rows


def write_audit_rows() -> list[dict]:
    return [
        {
            "ts": "2026-05-23T09:00:00Z",
            "event": "kontext_write_audit",
            "origin": "codex",
            "tool": "save",
            "kontext_mode": "apply",
            "writes_applied": 1,
            "read_after_write_checked": True,
            "read_after_write_ok": True,
            "kontext_error": "",
        },
        {
            "ts": "2026-05-23T09:01:00Z",
            "event": "kontext_write_audit",
            "origin": "codex",
            "tool": "update",
            "kontext_mode": "apply",
            "writes_applied": 1,
            "read_after_write_checked": True,
            "read_after_write_ok": True,
            "kontext_error": "",
        },
        {
            "ts": "2026-05-23T09:02:00Z",
            "event": "kontext_write_audit",
            "origin": "codex",
            "tool": "flag_memory",
            "kontext_mode": "apply",
            "writes_applied": 1,
            "read_after_write_checked": False,
            "read_after_write_ok": False,
            "kontext_error": "",
        },
    ]


def rollback_drill_payload(status: str = "pass") -> dict:
    ok = status == "pass"
    return {
        "ok": ok,
        "mode": "kontext-rollback-drill",
        "runs_model_calls": False,
        "summary": {"pass": 5 if ok else 4, "warn": 0, "fail": 0 if ok else 1},
        "checks": {
            "mem0_primary_health": {"status": "pass"},
            "kontext_health": {"status": "pass"},
            "write_mode_disabled": {"status": "pass" if ok else "fail"},
            "rollback_artifacts": {"status": "pass"},
            "restore_sandbox": {"status": "pass"},
        },
        "attention_items": [] if ok else ["write mode is still enabled"],
    }


def ob1_quality_payload(ok: bool = True) -> dict:
    return {
        "ok": ok,
        "mode": "ob1-retrieval-quality-gate",
        "runs_model_calls": False,
        "summary": {"cases": 30, "passed": 30 if ok else 29, "failed": 0 if ok else 1, "pass_rate": 1.0 if ok else 0.9667},
        "groups": {
            "protected_history": {"cases": 12, "passed": 12, "failed": 0, "pass_rate": 1.0},
            "project_continuity": {"cases": 18, "passed": 18 if ok else 17, "failed": 0 if ok else 1, "pass_rate": 1.0 if ok else 0.9444},
        },
        "cases": [
            {
                "name": "safe-case-name",
                "query_hash": "abc123",
                "top_result_hashes": ["def456"],
                "private_raw_query": "must not render",
            }
        ],
    }


def retirement_gate_payload(ok: bool = False, monitor_hours: float = 0.44) -> dict:
    monitor_status = "pass" if monitor_hours >= 48 and ok else "fail"
    return {
        "ok": ok,
        "mode": "kontext-mem0-retirement-gate",
        "runs_model_calls": False,
        "decision": "mem0_runtime_can_be_disabled_after_final_delete_approval"
        if ok
        else "keep_mem0_available_fix_blockers",
        "status_counts": {"pass": 8, "fail": 0} if ok else {"pass": 7, "fail": 1},
        "gates": [
            {
                "name": "client_routing_kontext_only",
                "status": "pass",
                "evidence": {"kontext_clients": ["codex", "claude", "chatgpt_visible"]},
            },
            {"name": "kontext_health", "status": "pass", "evidence": {"mirror_count": 136934}},
            {
                "name": "legacy_mem0_frozen_and_unused",
                "status": monitor_status,
                "evidence": {
                    "writes_enabled": False,
                    "recent_read_count": 0,
                    "recent_write_count": 0,
                    "monitor_hours": monitor_hours,
                },
            },
        ],
        "blockers": [] if ok else ["legacy Mem0 is still writable, still receiving runtime traffic, or has not completed a 48h monitor"],
        "private_raw_memory": "must not render",
    }


def test_observer_passes_green_reports_without_raw_payloads():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        ob1_retrieval_quality=ob1_quality_payload(),
        shadow_comparison_rows=shadow_rows(),
        expected_origins=["codex"],
        label="codex-kontext-primary",
    )
    rendered = json.dumps(report)

    assert report["ok"] is True
    assert report["mode"] == "kontext-cutover-observer"
    assert report["recommendation"] == "continue_kontext_primary_canary"
    assert report["summary"]["fail"] == 0
    assert report["summary"]["warn"] == 0
    assert report["checks"]["scorecard"]["status"] == "pass"
    assert report["checks"]["ob1_retrieval_quality"]["status"] == "pass"
    assert report["checks"]["ob1_retrieval_quality"]["evidence"]["protected_history_passed"] == "12/12"
    assert report["checks"]["ob1_retrieval_quality"]["evidence"]["project_continuity_passed"] == "18/18"
    assert report["checks"]["real_world_shadow_compare"]["status"] == "pass"
    assert report["checks"]["real_world_shadow_compare"]["evidence"]["rows_seen"] == 1
    assert report["checks"]["expanded_live_mcp_retrieval"]["evidence"]["kontext_passed"] == "36/36"
    assert "must not render" not in rendered
    assert "/data/audit.jsonl" not in rendered


def test_observer_blocks_when_ob1_retrieval_quality_fails():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        ob1_retrieval_quality=ob1_quality_payload(ok=False),
        shadow_comparison_rows=many_shadow_rows(60),
        write_audit_rows=write_audit_rows(),
        rollback_drill=rollback_drill_payload(),
        expected_origins=["codex"],
    )

    assert report["ok"] is False
    assert report["checks"]["ob1_retrieval_quality"]["status"] == "fail"
    assert "ob1_retrieval_quality failed" in report["transition_assessment"]["blockers"]
    assert "OB1 retrieval quality gate failed" in report["attention_items"]


def test_shadow_comparison_reports_recent_windows_separately_from_polluted_history():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        shadow_comparison_rows=many_shadow_rows(25, old_slow_rows=5),
        expected_origins=["codex"],
    )

    evidence = report["checks"]["real_world_shadow_compare"]["evidence"]
    last_20 = evidence["recent_windows"]["last_20"]

    assert evidence["rows_seen"] == 25
    assert evidence["avg_kontext_latency_ms"] > last_20["avg_kontext_latency_ms"]
    assert last_20["rows_seen"] == 20
    assert last_20["avg_kontext_latency_ms"] == 900.0
    assert last_20["avg_mem0_latency_ms"] == 1200.0
    assert last_20["latency_winner"] == "kontext"


def test_transition_assessment_keeps_mem0_primary_until_write_canary_and_rollback_are_done():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        shadow_comparison_rows=many_shadow_rows(60),
        expected_origins=["codex"],
    )

    assessment = report["transition_assessment"]

    assert assessment["decision"] == "keep_mem0_primary_start_write_canary"
    assert assessment["recent_head_to_head_rows"] == 50
    assert "recent_head_to_head_rows" not in assessment["remaining_work_keys"]
    assert "write_canary" in assessment["remaining_work_keys"]
    assert "rollback_drill" in assessment["remaining_work_keys"]
    assert "production_cutover" in assessment["remaining_work_keys"]


def test_transition_assessment_removes_write_canary_after_apply_audit_passes():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        shadow_comparison_rows=many_shadow_rows(60),
        write_audit_rows=write_audit_rows(),
        expected_origins=["codex"],
    )

    assessment = report["transition_assessment"]

    assert assessment["decision"] == "keep_mem0_primary_run_rollback_drill"
    assert assessment["write_canary"]["status"] == "pass"
    assert assessment["write_canary"]["tool_status"] == {"flag_memory": "pass", "save": "pass", "update": "pass"}
    assert "write_canary" not in assessment["remaining_work_keys"]
    assert "rollback_drill" in assessment["remaining_work_keys"]
    assert "production_cutover" in assessment["remaining_work_keys"]


def test_transition_assessment_removes_rollback_drill_after_report_passes():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        shadow_comparison_rows=many_shadow_rows(60),
        write_audit_rows=write_audit_rows(),
        rollback_drill=rollback_drill_payload(),
        expected_origins=["codex"],
    )

    assessment = report["transition_assessment"]

    assert assessment["decision"] == "ready_for_production_cutover_review"
    assert assessment["rollback_drill"]["status"] == "pass"
    assert "write_canary" not in assessment["remaining_work_keys"]
    assert "rollback_drill" not in assessment["remaining_work_keys"]
    assert assessment["remaining_work_keys"] == ["production_cutover"]


def test_transition_assessment_blocks_when_input_freshness_fails():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        shadow_comparison_rows=many_shadow_rows(60),
        write_audit_rows=write_audit_rows(),
        rollback_drill=rollback_drill_payload(),
        expected_origins=["codex"],
        input_freshness={
            "scorecard": {"status": "fail", "summary": "scorecard input is stale"},
            "cutover_readiness": {"status": "pass", "summary": "readiness input is fresh"},
        },
    )

    assert report["ok"] is False
    assert report["recommendation"] == "rollback_or_hold_mem0_primary"
    assert report["checks"]["input_freshness"]["status"] == "fail"
    assert report["transition_assessment"]["decision"] == "hold_mem0_primary_fix_regressions"
    assert "input_freshness failed" in report["transition_assessment"]["blockers"]
    assert "scorecard input is stale" in report["attention_items"]


def test_transition_assessment_blocks_on_failed_rollback_drill():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        shadow_comparison_rows=many_shadow_rows(60),
        write_audit_rows=write_audit_rows(),
        rollback_drill=rollback_drill_payload(status="fail"),
        expected_origins=["codex"],
    )

    assessment = report["transition_assessment"]

    assert assessment["decision"] == "hold_mem0_primary_fix_regressions"
    assert "rollback_drill" in assessment["remaining_work_keys"]
    assert "rollback drill failed" in assessment["blockers"]


def test_observer_holds_or_rolls_back_on_failed_scorecard():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(ok=False),
        cutover_readiness=readiness_payload(ok=False),
        usage_report=usage_payload(),
        client_status=client_status_payload(),
        shadow_comparison_rows=shadow_rows(),
        expected_origins=["codex"],
    )

    assert report["ok"] is False
    assert report["recommendation"] == "rollback_or_hold_mem0_primary"
    assert report["summary"]["fail"] >= 1
    assert "scorecard is not green" in report["attention_items"]


def test_observer_warns_when_optional_usage_or_client_data_is_missing():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(),
        cutover_readiness=readiness_payload(),
        usage_report=None,
        client_status=None,
        shadow_comparison_rows=[],
        expected_origins=["codex"],
    )

    assert report["ok"] is True
    assert report["recommendation"] == "continue_canary_but_review_warnings"
    assert report["summary"]["warn"] == 3
    assert report["checks"]["usage_health"]["status"] == "warn"
    assert report["checks"]["client_activity"]["status"] == "warn"
    assert report["checks"]["real_world_shadow_compare"]["status"] == "warn"


def test_cli_writes_json_markdown_and_history(tmp_path, capsys):
    module = load_module()
    scorecard = tmp_path / "scorecard.json"
    readiness = tmp_path / "readiness.json"
    usage = tmp_path / "usage.json"
    clients = tmp_path / "clients.json"
    ob1_quality = tmp_path / "ob1-quality.json"
    output = tmp_path / "observer.json"
    markdown = tmp_path / "observer.md"
    history = tmp_path / "observer-history.jsonl"
    shadow = tmp_path / "shadow.jsonl"
    write_audit = tmp_path / "write-audit.jsonl"
    rollback = tmp_path / "rollback.json"
    write_json(scorecard, scorecard_payload())
    write_json(readiness, readiness_payload())
    write_json(usage, usage_payload())
    write_json(clients, client_status_payload())
    write_json(ob1_quality, ob1_quality_payload())
    shadow.write_text("\n".join(json.dumps(row) for row in shadow_rows()) + "\n", encoding="utf-8")
    write_audit.write_text("\n".join(json.dumps(row) for row in write_audit_rows()) + "\n", encoding="utf-8")
    write_json(rollback, rollback_drill_payload())

    code = module.main(
        [
            "--scorecard",
            str(scorecard),
            "--cutover-readiness",
            str(readiness),
            "--usage-report",
            str(usage),
            "--client-status",
            str(clients),
            "--ob1-retrieval-quality-report",
            str(ob1_quality),
            "--shadow-comparison-log",
            str(shadow),
            "--write-audit-log",
            str(write_audit),
            "--rollback-drill-report",
            str(rollback),
            "--expected-origin",
            "codex",
            "--output",
            str(output),
            "--markdown-output",
            str(markdown),
            "--history-log",
            str(history),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["checks"]["ob1_retrieval_quality"]["status"] == "pass"
    assert payload["checks"]["input_freshness"]["evidence"]["ob1_retrieval_quality"]["status"] == "pass"
    assert "Recommendation" in markdown.read_text(encoding="utf-8")
    history_rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    assert history_rows[-1]["ok"] is True
    assert history_rows[-1]["recommendation"] == "continue_kontext_primary_canary"
    assert json.loads(capsys.readouterr().out)["runs_model_calls"] is False


def test_cli_fails_when_scorecard_or_readiness_file_is_stale(tmp_path):
    module = load_module()
    scorecard = tmp_path / "scorecard.json"
    readiness = tmp_path / "readiness.json"
    usage = tmp_path / "usage.json"
    clients = tmp_path / "clients.json"
    output = tmp_path / "observer.json"
    write_json(scorecard, scorecard_payload())
    write_json(readiness, readiness_payload())
    write_json(usage, usage_payload())
    write_json(clients, client_status_payload())
    old = time.time() - 3 * 3600
    os.utime(scorecard, (old, old))

    code = module.main(
        [
            "--scorecard",
            str(scorecard),
            "--cutover-readiness",
            str(readiness),
            "--usage-report",
            str(usage),
            "--client-status",
            str(clients),
            "--max-input-age-hours",
            "1",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert code == 2
    assert payload["ok"] is False
    assert payload["checks"]["input_freshness"]["status"] == "fail"
    assert payload["checks"]["input_freshness"]["evidence"]["scorecard"]["status"] == "fail"


def test_observer_retirement_gate_replaces_stale_beam_cutover_blocker():
    module = load_module()

    report = module.build_observer_report(
        scorecard=scorecard_payload(ok=False),
        cutover_readiness=readiness_payload(ok=False),
        retirement_gate=retirement_gate_payload(ok=False, monitor_hours=0.44),
        label="kontext-mem0-retirement",
    )
    rendered = json.dumps(report)

    assert report["ok"] is False
    assert report["recommendation"] == "continue_mem0_frozen_monitor"
    assert report["checks"]["retirement_gate"]["status"] == "fail"
    assert "scorecard" not in report["checks"]
    assert "cutover_readiness" not in report["checks"]
    assert "official_judged_accuracy" not in report["checks"]
    assert report["transition_assessment"]["decision"] == "continue_mem0_frozen_monitor"
    assert report["transition_assessment"]["remaining_work_keys"] == ["legacy_mem0_monitor", "final_delete_approval"]
    assert "must not render" not in rendered


def test_cli_accepts_retirement_gate_without_scorecard_or_readiness(tmp_path, capsys):
    module = load_module()
    retirement = tmp_path / "retirement.json"
    output = tmp_path / "observer.json"
    markdown = tmp_path / "observer.md"
    write_json(retirement, retirement_gate_payload(ok=True, monitor_hours=72))

    code = module.main(
        [
            "--retirement-gate",
            str(retirement),
            "--label",
            "kontext-mem0-retirement",
            "--output",
            str(output),
            "--markdown-output",
            str(markdown),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["ok"] is True
    assert payload["recommendation"] == "mem0_runtime_can_be_disabled_after_final_delete_approval"
    assert payload["checks"]["retirement_gate"]["status"] == "pass"
    assert "Retirement Gate" in markdown.read_text(encoding="utf-8")
    assert json.loads(capsys.readouterr().out)["runs_model_calls"] is False


def test_cutover_decision_includes_operator_rollback_runbook():
    text = (Path(__file__).resolve().parents[1] / "cutover_decision.md").read_text(encoding="utf-8")

    assert "## Rollback Runbook" in text
    assert "Do not hard-delete Kontext data" in text
    assert "Rollback trigger" in text
    assert "Verification after rollback" in text
