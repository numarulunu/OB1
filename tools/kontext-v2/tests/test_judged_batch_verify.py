from __future__ import annotations

import importlib.util
import json
import shlex
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_batch_verify.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_batch_verify", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def command_template(suite: str, verification_path: Path) -> str:
    return (
        "python3 /opt/kontext/scripts/judged_benchmark_run.py "
        f"--input-bundle /opt/kontext/private/judged-bundles/{suite}.json "
        f"--output /opt/kontext/reports/judged-plans/{suite}-paid-run.json "
        "--cutoffs 50 --max-questions 1 --execute --provider openai-compatible "
        "--approve-cost --max-cost-usd 0.01 --api-key-env OPENAI_API_KEY "
        f"--verification-output {shlex.quote(str(verification_path))} "
        "--verify-cutoff 50 --verify-min-accuracy 0.9 --verify-mem0-target-accuracy 0.9 "
        "--verify-max-cost-usd 0.01 --verify-require-model-calls --verify-require-usage "
        "--verify-expected-provider openai-compatible"
    )


def write_batch_packet(path: Path, verification_paths: dict[str, Path]) -> None:
    write_json(
        path,
        {
            "ok": True,
            "mode": "judged-batch-approval-packet",
            "runs_model_calls": False,
            "approval_required": True,
            "actual_judged_accuracy_proven": False,
            "summary": {"max_cost_usd_total": 0.03},
            "commands": [
                {"suite": suite, "command_template": command_template(suite, verify_path)}
                for suite, verify_path in verification_paths.items()
            ],
        },
    )


def verification_report(suite: str, total: int = 1, passed: int = 1, ok: bool = True) -> dict:
    accuracy = round(passed / total, 4) if total else 0.0
    gates = {
        "model_calls": {"ok": True, "required": True, "actual": True},
        "usage": {"ok": True, "required": True, "total_tokens": 120},
        "provider": {"ok": True, "expected": "openai-compatible", "actual": "openai-compatible"},
        "answerer_model": {"ok": True, "expected": "answer-model", "actual": "answer-model"},
        "judge_model": {"ok": True, "expected": "judge-model", "actual": "judge-model"},
        "question_count": {"ok": True, "min_questions": 1, "selected_questions": total},
        "accuracy": {"ok": accuracy >= 0.9, "min_accuracy": 0.9, "actual": accuracy},
        "cost": {"ok": True, "max_cost_usd": 0.01, "actual_cost_usd": 0.001},
        "mem0_target": {"ok": accuracy >= 0.9, "target_accuracy": 0.9, "actual": accuracy, "delta": round(accuracy - 0.9, 4)},
        "raw_payload": {"ok": True, "hit_count": 0, "hit_paths": []},
    }
    if not ok:
        gates["accuracy"]["ok"] = False
        gates["mem0_target"]["ok"] = False
    return {
        "ok": ok,
        "mode": "judged-benchmark-verification",
        "source_mode": "openai-compatible-judged-benchmark-run",
        "provider": "openai-compatible",
        "runs_model_calls": True,
        "dataset": f"{suite}-dataset",
        "run_id": f"{suite}-run",
        "summary": {
            "cutoff": 50,
            "total": total,
            "passed": passed,
            "accuracy": accuracy,
            "avg_score": accuracy,
            "mem0_target_accuracy": 0.9,
            "accuracy_delta_vs_mem0_target": round(accuracy - 0.9, 4),
        },
        "estimated_cost_usd": {"total_usd": 0.001},
        "actual_usage": {"total_tokens": 120},
        "gates": gates,
    }


def test_batch_verifier_accepts_all_strict_suite_verifications(tmp_path):
    module = load_module()
    locomo = tmp_path / "locomo-verify.json"
    longmem = tmp_path / "longmem-verify.json"
    packet = tmp_path / "batch.json"
    write_batch_packet(packet, {"locomo1": locomo, "longmemeval1": longmem})
    write_json(locomo, verification_report("locomo1", total=15, passed=15))
    write_json(longmem, verification_report("longmemeval1", total=15, passed=15))

    result = module.build_batch_verification(packet)
    rendered = json.dumps(result)

    assert result["ok"] is True
    assert result["mode"] == "judged-batch-verification"
    assert result["runs_model_calls"] is False
    assert result["actual_judged_accuracy_proven"] is True
    assert result["summary"]["suite_count"] == 2
    assert result["summary"]["suites_verified"] == 2
    assert result["summary"]["total_questions"] == 30
    assert result["summary"]["total_passed"] == 30
    assert result["summary"]["weighted_accuracy"] == 1.0
    assert result["summary"]["total_cost_usd"] == 0.002
    assert result["summary"]["total_usage_tokens"] == 240
    assert result["blocked_by"] == []
    assert "private raw question" not in rendered
    assert "sk-" not in rendered


def test_batch_verifier_rejects_tiny_micro_sample_by_default(tmp_path):
    module = load_module()
    locomo = tmp_path / "locomo-verify.json"
    longmem = tmp_path / "longmem-verify.json"
    beam = tmp_path / "beam-verify.json"
    packet = tmp_path / "batch.json"
    write_batch_packet(packet, {"locomo1": locomo, "longmemeval1": longmem, "beam1": beam})
    write_json(locomo, verification_report("locomo1", total=1, passed=1))
    write_json(longmem, verification_report("longmemeval1", total=1, passed=1))
    write_json(beam, verification_report("beam1", total=1, passed=1))

    result = module.build_batch_verification(packet)

    assert result["ok"] is False
    assert result["actual_judged_accuracy_proven"] is False
    assert "question_count" in result["blocked_by"]
    assert result["summary"]["total_questions"] == 3
    assert result["summary"]["min_batch_questions"] == 30


def test_batch_verifier_rejects_ten_question_micro_sample(tmp_path):
    module = load_module()
    locomo = tmp_path / "locomo-verify.json"
    longmem = tmp_path / "longmem-verify.json"
    packet = tmp_path / "batch.json"
    write_batch_packet(packet, {"locomo1": locomo, "longmemeval1": longmem})
    write_json(locomo, verification_report("locomo1", total=5, passed=5))
    write_json(longmem, verification_report("longmemeval1", total=5, passed=5))

    result = module.build_batch_verification(packet)

    assert result["ok"] is False
    assert result["actual_judged_accuracy_proven"] is False
    assert "question_count" in result["blocked_by"]
    assert result["summary"]["total_questions"] == 10


def test_batch_verifier_rejects_missing_verification_report(tmp_path):
    module = load_module()
    missing = tmp_path / "missing-verify.json"
    packet = tmp_path / "batch.json"
    write_batch_packet(packet, {"locomo1": missing})

    result = module.build_batch_verification(packet)

    assert result["ok"] is False
    assert result["actual_judged_accuracy_proven"] is False
    assert result["summary"]["suites_verified"] == 0
    assert "missing verification report: locomo1" in result["blocked_by"]


def test_batch_verifier_rejects_non_strict_verification_report(tmp_path):
    module = load_module()
    verify_path = tmp_path / "locomo-verify.json"
    packet = tmp_path / "batch.json"
    write_batch_packet(packet, {"locomo1": verify_path})
    report = verification_report("locomo1")
    del report["gates"]["model_calls"]
    write_json(verify_path, report)

    result = module.build_batch_verification(packet)

    assert result["ok"] is False
    assert result["actual_judged_accuracy_proven"] is False
    assert "missing strict verification gates: locomo1" in result["blocked_by"]


def test_batch_verifier_rejects_wrong_report_mode(tmp_path):
    module = load_module()
    verify_path = tmp_path / "locomo-verify.json"
    packet = tmp_path / "batch.json"
    write_batch_packet(packet, {"locomo1": verify_path})
    report = verification_report("locomo1")
    report["mode"] = "some-other-report"
    write_json(verify_path, report)

    result = module.build_batch_verification(packet)

    assert result["ok"] is False
    assert result["actual_judged_accuracy_proven"] is False
    assert "invalid verification report mode: locomo1" in result["blocked_by"]


def test_batch_verifier_rejects_failed_suite_verification(tmp_path):
    module = load_module()
    verify_path = tmp_path / "beam-verify.json"
    packet = tmp_path / "batch.json"
    write_batch_packet(packet, {"beam1": verify_path})
    write_json(verify_path, verification_report("beam1", total=2, passed=1, ok=False))

    result = module.build_batch_verification(packet)

    assert result["ok"] is False
    assert result["actual_judged_accuracy_proven"] is False
    assert result["summary"]["weighted_accuracy"] == 0.5
    assert "failed suite verification: beam1" in result["blocked_by"]
    assert result["suites"][0]["failed_gates"] == ["accuracy", "mem0_target"]


def test_cli_writes_batch_verification_report(tmp_path, capsys):
    module = load_module()
    verify_path = tmp_path / "locomo-verify.json"
    packet = tmp_path / "batch.json"
    output = tmp_path / "batch-verification.json"
    write_batch_packet(packet, {"locomo1": verify_path})
    write_json(verify_path, verification_report("locomo1", total=30, passed=30))

    code = module.main(["--batch-packet", str(packet), "--output", str(output)])

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(capsys.readouterr().out)["actual_judged_accuracy_proven"] is True
