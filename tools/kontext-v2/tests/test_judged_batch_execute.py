from __future__ import annotations

import importlib.util
import json
from types import SimpleNamespace
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_batch_execute.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_batch_execute", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def command_template(suite: str, suffix: str = "") -> str:
    return (
        "python3 /opt/kontext/scripts/judged_benchmark_run.py "
        f"--input-bundle /opt/kontext/private/judged-bundles/{suite}.json "
        f"--output /opt/kontext/reports/judged-plans/{suite}-paid-run.json "
        "--cutoffs 50 --max-questions 1 --execute --provider openai-compatible "
        "--approve-cost --max-cost-usd 0.01 --answerer-model answer-model "
        "--judge-model judge-model --api-key-env OPENAI_API_KEY "
        "--base-url https://api.openai.com/v1/chat/completions "
        "--answer-input-usd-per-1m 0.15 --answer-output-usd-per-1m 0.6 "
        "--judge-input-usd-per-1m 0.15 --judge-output-usd-per-1m 0.6 "
        f"--verification-output /opt/kontext/reports/judged-plans/{suite}-paid-verification.json "
        "--verify-cutoff 50 --verify-min-accuracy 0.9 --verify-mem0-target-accuracy 0.9 "
        "--verify-max-cost-usd 0.01 --verify-require-model-calls --verify-require-usage "
        "--verify-expected-provider openai-compatible "
        "--verify-expected-answerer-model answer-model --verify-expected-judge-model judge-model "
        f"--verify-expected-questions 1{suffix}"
    )


def write_batch_packet(path: Path, command_suffix: str = "") -> None:
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
                "suite_count": 2,
                "estimated_llm_calls": {"answer_calls": 2, "judge_calls": 2, "total_calls": 4},
                "estimated_tokens": {"total_tokens": 11840},
                "estimated_cost_usd": {"total_usd": 0.002154},
                "max_cost_usd_total": 0.02,
                "blocked_by": ["explicit paid-run approval"],
            },
            "commands": [
                {"suite": "locomo1", "command_template": command_template("locomo1", command_suffix)},
                {"suite": "longmemeval1", "command_template": command_template("longmemeval1")},
            ],
        },
    )


def test_batch_execute_default_is_no_call_blocked_plan(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)

    plan = module.build_execution_plan(packet)

    assert plan["ok"] is True
    assert plan["mode"] == "judged-batch-execution-plan"
    assert plan["runs_model_calls"] is False
    assert plan["execution_enabled"] is False
    assert plan["approval_required"] is True
    assert plan["summary"]["command_count"] == 2
    assert plan["summary"]["max_cost_usd_total"] == 0.02
    assert plan["summary"]["required_env_vars"] == ["OPENAI_API_KEY"]
    assert plan["blocked_by"] == ["execution flag is required", "cost approval flag is required"]


def test_batch_execute_blocks_when_ceiling_is_too_low(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)

    plan = module.build_execution_plan(
        packet,
        execute=True,
        approve_cost=True,
        max_total_cost_usd=0.01,
        environ={"OPENAI_API_KEY": "present-but-not-rendered"},
        check_env=True,
    )

    rendered = json.dumps(plan)
    assert plan["ok"] is False
    assert plan["runs_model_calls"] is False
    assert plan["execution_enabled"] is False
    assert "batch max cost exceeds approved ceiling" in plan["blocked_by"]
    assert "present-but-not-rendered" not in rendered


def test_batch_execute_reports_missing_env_by_name_only(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)

    plan = module.build_execution_plan(
        packet,
        execute=True,
        approve_cost=True,
        max_total_cost_usd=0.02,
        environ={},
        check_env=True,
    )

    assert plan["ok"] is False
    assert plan["runs_model_calls"] is False
    assert plan["summary"]["missing_env_vars"] == ["OPENAI_API_KEY"]
    assert "required env vars are missing" in plan["blocked_by"]


def test_batch_execute_checks_env_when_execute_is_requested(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)

    plan = module.build_execution_plan(
        packet,
        execute=True,
        approve_cost=True,
        max_total_cost_usd=0.02,
        environ={},
    )

    assert plan["ok"] is False
    assert plan["execution_enabled"] is False
    assert plan["summary"]["missing_env_vars"] == ["OPENAI_API_KEY"]
    assert "required env vars are missing" in plan["blocked_by"]


def test_batch_execute_preflight_validates_without_enabling_execution(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)

    plan = module.build_execution_plan(
        packet,
        preflight=True,
        max_total_cost_usd=0.02,
        environ={"OPENAI_API_KEY": "present-but-not-rendered"},
    )
    rendered = json.dumps(plan)

    assert plan["ok"] is True
    assert plan["mode"] == "judged-batch-execution-plan"
    assert plan["preflight"] is True
    assert plan["preflight_ready"] is True
    assert plan["runs_model_calls"] is False
    assert plan["execution_enabled"] is False
    assert plan["approval_required"] is True
    assert plan["blocked_by"] == []
    assert plan["summary"]["missing_env_vars"] == []
    assert "present-but-not-rendered" not in rendered


def test_batch_execute_preflight_blocks_missing_env_without_running(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)

    plan = module.build_execution_plan(
        packet,
        preflight=True,
        max_total_cost_usd=0.02,
        environ={},
    )

    assert plan["ok"] is False
    assert plan["preflight_ready"] is False
    assert plan["runs_model_calls"] is False
    assert plan["execution_enabled"] is False
    assert plan["summary"]["missing_env_vars"] == ["OPENAI_API_KEY"]
    assert "required env vars are missing" in plan["blocked_by"]


def test_batch_execute_preflight_blocks_low_ceiling_without_running(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)

    plan = module.build_execution_plan(
        packet,
        preflight=True,
        max_total_cost_usd=0.01,
        environ={"OPENAI_API_KEY": "present-but-not-rendered"},
    )

    assert plan["ok"] is False
    assert plan["preflight_ready"] is False
    assert plan["runs_model_calls"] is False
    assert plan["execution_enabled"] is False
    assert "batch max cost exceeds approved ceiling" in plan["blocked_by"]


def test_batch_execute_rejects_secret_like_command_template(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet, command_suffix=" --leaked-key sk-test-secret-value")

    plan = module.build_execution_plan(packet)

    assert plan["ok"] is False
    assert plan["runs_model_calls"] is False
    assert plan["execution_enabled"] is False
    assert "unsafe command template" in plan["blocked_by"]


def test_batch_execute_rejects_unexpected_runner_path(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)
    payload = json.loads(packet.read_text(encoding="utf-8"))
    payload["commands"][0]["command_template"] = "python3 /tmp/not-the-runner.py --execute"
    packet.write_text(json.dumps(payload), encoding="utf-8")

    plan = module.build_execution_plan(packet)

    assert plan["ok"] is False
    assert plan["runs_model_calls"] is False
    assert "unexpected command runner" in plan["blocked_by"]


def test_batch_execute_rejects_command_without_max_questions(tmp_path):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)
    payload = json.loads(packet.read_text(encoding="utf-8"))
    payload["commands"][0]["command_template"] = payload["commands"][0]["command_template"].replace("--max-questions 1 ", "")
    packet.write_text(json.dumps(payload), encoding="utf-8")

    plan = module.build_execution_plan(packet)

    assert plan["ok"] is False
    assert "command is missing required judged-run flags" in plan["blocked_by"]


def test_batch_execute_times_out_command_and_skips_remaining(tmp_path, monkeypatch):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)
    plan = module.build_execution_plan(
        packet,
        execute=True,
        approve_cost=True,
        max_total_cost_usd=0.02,
        environ={"OPENAI_API_KEY": "present-but-not-rendered"},
    )

    def fake_run(argv, capture_output, text, check, timeout):
        assert 0 < timeout <= 3
        raise module.subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.execute_plan(plan, max_runtime_seconds=3)

    assert result["ok"] is False
    assert result["commands"][0]["status"] == "timeout"
    assert result["commands"][1]["status"] == "skipped"
    assert "command timed out: locomo1" in result["blocked_by"]


def test_batch_execute_stops_before_next_suite_when_actual_cost_would_exceed_ceiling(tmp_path, monkeypatch):
    module = load_module()
    packet = tmp_path / "batch.json"
    write_batch_packet(packet)
    payload = json.loads(packet.read_text(encoding="utf-8"))
    for row in payload["commands"]:
        suite = row["suite"]
        row["command_template"] = row["command_template"].replace(
            f"/opt/kontext/reports/judged-plans/{suite}-paid-verification.json",
            str(tmp_path / f"{suite}-verify.json"),
        )
    packet.write_text(json.dumps(payload), encoding="utf-8")
    plan = module.build_execution_plan(
        packet,
        execute=True,
        approve_cost=True,
        max_total_cost_usd=0.02,
        environ={"OPENAI_API_KEY": "present-but-not-rendered"},
    )

    def fake_run(argv, capture_output, text, check, timeout):
        verification_output = module.option_value(argv, "--verification-output")
        write_json(Path(verification_output), {"estimated_cost_usd": {"total_usd": 0.019}})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.execute_plan(plan, max_runtime_seconds=30)

    assert result["ok"] is False
    assert result["commands"][0]["status"] == "completed"
    assert result["commands"][1]["status"] == "skipped"
    assert result["summary"]["actual_cost_usd"] == 0.019
    assert "batch running cost would exceed approved ceiling before longmemeval1" in result["blocked_by"]


def test_cli_writes_blocked_execution_plan_without_running(tmp_path, capsys):
    module = load_module()
    packet = tmp_path / "batch.json"
    output = tmp_path / "plan.json"
    write_batch_packet(packet)

    exit_code = module.main(["--batch-packet", str(packet), "--output", str(output)])

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["execution_enabled"] is False
    assert json.loads(capsys.readouterr().out)["runs_model_calls"] is False


def test_cli_writes_no_call_preflight_report(tmp_path, capsys, monkeypatch):
    module = load_module()
    packet = tmp_path / "batch.json"
    output = tmp_path / "preflight.json"
    write_batch_packet(packet)
    monkeypatch.setenv("OPENAI_API_KEY", "present-but-not-rendered")

    exit_code = module.main(
        [
            "--batch-packet",
            str(packet),
            "--preflight",
            "--max-total-cost-usd",
            "0.02",
            "--output",
            str(output),
        ]
    )
    rendered = output.read_text(encoding="utf-8")

    assert exit_code == 0
    assert json.loads(rendered)["preflight_ready"] is True
    assert json.loads(capsys.readouterr().out)["runs_model_calls"] is False
    assert "present-but-not-rendered" not in rendered
