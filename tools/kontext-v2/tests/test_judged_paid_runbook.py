from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_paid_runbook.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_paid_runbook", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_batch_packet(path: Path) -> None:
    write_json(
        path,
        {
            "ok": True,
            "mode": "judged-batch-approval-packet",
            "runs_model_calls": False,
            "approval_required": True,
            "actual_judged_accuracy_proven": False,
            "required_env_vars": ["OPENAI_API_KEY"],
            "summary": {
                "suite_count": 3,
                "estimated_llm_calls": {"answer_calls": 4, "judge_calls": 5, "total_calls": 9},
                "estimated_tokens": {"total_tokens": 25300},
                "estimated_cost_usd": {"total_usd": 0.004605},
                "max_cost_usd_total": 0.07,
            },
            "commands": [{"suite": "locomo1"}, {"suite": "longmemeval1"}, {"suite": "beam1"}],
        },
    )


def write_preflight(path: Path, ready: bool, missing_env_vars: list[str] | None = None) -> None:
    missing_env_vars = missing_env_vars or []
    write_json(
        path,
        {
            "ok": ready,
            "mode": "judged-batch-execution-plan",
            "runs_model_calls": False,
            "preflight": True,
            "preflight_ready": ready,
            "approval_required": True,
            "execution_enabled": False,
            "blocked_by": [] if ready else ["required env vars are missing"],
            "summary": {
                "command_count": 3,
                "max_cost_usd_total": 0.07,
                "required_env_vars": ["OPENAI_API_KEY"],
                "missing_env_vars": missing_env_vars,
            },
            "debug": "sk-test-secret-value-that-must-not-render",
        },
    )


def write_batch_verification(path: Path, proven: bool) -> None:
    write_json(
        path,
        {
            "ok": proven,
            "mode": "judged-batch-verification",
            "runs_model_calls": False,
            "actual_judged_accuracy_proven": proven,
            "blocked_by": [] if proven else ["missing verification report: locomo1"],
            "summary": {
                "suite_count": 3,
                "suites_verified": 3 if proven else 0,
                "total_questions": 3 if proven else 0,
                "total_passed": 3 if proven else 0,
                "weighted_accuracy": 1.0 if proven else 0.0,
                "total_cost_usd": 0.004,
                "total_usage_tokens": 1200,
            },
        },
    )


def write_cutover_readiness(path: Path, ready: bool) -> None:
    write_json(
        path,
        {
            "ok": ready,
            "ready_for_user_cutover_review": ready,
            "production_cutover_enabled": False,
            "status_counts": {"pass": 7 if not ready else 8, "fail": 1 if not ready else 0, "missing": 0},
            "gates": [
                {"name": "expanded_live_mcp_retrieval", "status": "pass", "ok": True},
                {
                    "name": "official_judged_accuracy",
                    "status": "pass" if ready else "fail",
                    "ok": ready,
                    "details": {"blocked_by": [] if ready else ["missing verification report: locomo1"]},
                },
            ],
        },
    )


def test_runbook_summarizes_current_blocked_state_without_secret_values(tmp_path):
    module = load_module()
    packet = tmp_path / "batch-packet.json"
    preflight = tmp_path / "preflight.json"
    verification = tmp_path / "verification.json"
    readiness = tmp_path / "readiness.json"
    write_batch_packet(packet)
    write_preflight(preflight, ready=False, missing_env_vars=["OPENAI_API_KEY"])
    write_batch_verification(verification, proven=False)
    write_cutover_readiness(readiness, ready=False)

    result = module.build_runbook(packet, preflight, verification, readiness)
    rendered = json.dumps(result)

    assert result["ok"] is True
    assert result["mode"] == "judged-paid-runbook"
    assert result["runs_model_calls"] is False
    assert result["actual_judged_accuracy_proven"] is False
    assert result["ready_to_run_paid_batch"] is False
    assert result["required_user_approval"] is True
    assert result["missing_env_vars"] == ["OPENAI_API_KEY"]
    assert "OPENAI_API_KEY missing from run environment" in result["blocking_items"]
    assert "explicit paid-run approval required" in result["blocking_items"]
    assert "paid verification reports missing" in result["blocking_items"]
    assert result["summary"]["estimated_llm_calls"]["total_calls"] == 9
    assert result["summary"]["max_cost_usd_total"] == 0.07
    assert "sk-test-secret-value-that-must-not-render" not in rendered
    assert result["commands"][0]["runs_model_calls"] is False


def test_runbook_is_ready_to_run_when_preflight_is_ready_but_still_requires_approval(tmp_path):
    module = load_module()
    packet = tmp_path / "batch-packet.json"
    preflight = tmp_path / "preflight.json"
    verification = tmp_path / "verification.json"
    readiness = tmp_path / "readiness.json"
    write_batch_packet(packet)
    write_preflight(preflight, ready=True)
    write_batch_verification(verification, proven=False)
    write_cutover_readiness(readiness, ready=False)

    result = module.build_runbook(packet, preflight, verification, readiness)

    assert result["ok"] is True
    assert result["ready_to_run_paid_batch"] is True
    assert result["required_user_approval"] is True
    assert result["missing_env_vars"] == []
    assert "OPENAI_API_KEY missing from run environment" not in result["blocking_items"]
    assert "explicit paid-run approval required" in result["blocking_items"]
    assert "paid verification reports missing" in result["blocking_items"]
    assert result["next_action"].startswith("Get explicit approval")


def test_cli_writes_sanitized_runbook(tmp_path, capsys):
    module = load_module()
    packet = tmp_path / "batch-packet.json"
    preflight = tmp_path / "preflight.json"
    verification = tmp_path / "verification.json"
    readiness = tmp_path / "readiness.json"
    output = tmp_path / "runbook.json"
    write_batch_packet(packet)
    write_preflight(preflight, ready=False, missing_env_vars=["OPENAI_API_KEY"])
    write_batch_verification(verification, proven=False)
    write_cutover_readiness(readiness, ready=False)

    code = module.main(
        [
            "--batch-packet",
            str(packet),
            "--preflight-report",
            str(preflight),
            "--batch-verification",
            str(verification),
            "--cutover-readiness",
            str(readiness),
            "--output",
            str(output),
        ]
    )

    written = json.loads(output.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert written["mode"] == "judged-paid-runbook"
    assert printed["runs_model_calls"] is False
    assert "sk-test-secret-value-that-must-not-render" not in output.read_text(encoding="utf-8")
