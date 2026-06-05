from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_benchmark_verify.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_benchmark_verify", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def args(**overrides):
    defaults = {
        "report": "judged.json",
        "cutoff": 50,
        "min_accuracy": 0.9,
        "mem0_target_accuracy": 0.918,
        "max_cost_usd": 0.01,
        "min_questions": 30,
        "require_model_calls": True,
        "require_usage": True,
        "expected_provider": "openai-compatible",
        "expected_answerer_model": "answer-model",
        "expected_judge_model": "judge-model",
        "expected_questions": 30,
        "output": None,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def actual_judged_report(**overrides):
    report = {
        "ok": True,
        "mode": "openai-compatible-judged-benchmark-run",
        "runs_model_calls": True,
        "completed_calls": 2,
        "provider": "openai-compatible",
        "answerer_model": "answer-model",
        "judge_model": "judge-model",
        "dataset": "locomo10",
        "selected_questions": 30,
        "top_k_values": [50],
        "estimated_cost_usd": {"total_usd": 0.006},
        "actual_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "summary": {"50": {"total": 30, "passed": 30, "accuracy": 1.0, "avg_score": 1.0}},
        "questions": [
            {
                "question_id": "q1",
                "question_hash": "abc",
                "ground_truth_hash": "def",
                "cutoff_results": {"50": {"score": 1.0, "judgment": "PASS"}},
            }
        ],
    }
    report.update(overrides)
    return report


def test_verifier_accepts_actual_judged_report_against_cost_and_target():
    module = load_module()

    result = module.build_verification(actual_judged_report(), args())

    assert result["ok"] is True
    assert result["runs_model_calls"] is True
    assert result["summary"]["cutoff"] == 50
    assert result["summary"]["accuracy"] == 1.0
    assert result["summary"]["mem0_target_accuracy"] == 0.918
    assert result["summary"]["accuracy_delta_vs_mem0_target"] == 0.082
    assert all(gate["ok"] for gate in result["gates"].values())


def test_verifier_uses_actual_cost_before_estimated_cost():
    module = load_module()
    report = actual_judged_report(
        estimated_cost_usd={"total_usd": 0.001},
        actual_cost_usd={"method": "conservative_max_unit_price", "total_usd": 0.25},
    )

    result = module.build_verification(report, args(max_cost_usd=0.01))

    assert result["ok"] is False
    assert result["gates"]["cost"]["ok"] is False
    assert result["gates"]["cost"]["actual_cost_usd"] == 0.25


def test_verifier_rejects_mock_or_no_call_report_for_actual_gate():
    module = load_module()
    report = actual_judged_report(
        mode="mock-judged-benchmark-run",
        runs_model_calls=False,
        provider="mock",
        actual_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )

    result = module.build_verification(report, args())

    assert result["ok"] is False
    assert result["gates"]["model_calls"]["ok"] is False
    assert result["gates"]["usage"]["ok"] is False
    assert result["gates"]["provider"]["ok"] is False


def test_verifier_rejects_static_model_call_flag_without_completed_calls():
    module = load_module()
    report = actual_judged_report(completed_calls=0)

    result = module.build_verification(report, args())

    assert result["ok"] is False
    assert result["gates"]["model_calls"]["ok"] is False
    assert result["gates"]["model_calls"]["completed_calls"] == 0


def test_verifier_rejects_micro_sample_by_default():
    module = load_module()

    result = module.build_verification(actual_judged_report(selected_questions=3, summary={"50": {"total": 3, "passed": 3, "accuracy": 1.0}}), args())

    assert result["ok"] is False
    assert result["gates"]["question_count"]["ok"] is False
    assert result["gates"]["question_count"]["min_questions"] == 30


def test_parser_defaults_require_real_sample_floor():
    module = load_module()

    parsed = module.build_parser().parse_args(["--report", "judged.json"])

    assert parsed.min_questions == 30
    assert parsed.min_accuracy == 0.8


def test_verifier_rejects_raw_payload_keys_without_echoing_raw_values():
    module = load_module()
    report = actual_judged_report(
        questions=[
            {
                "question_id": "q1",
                "question": "private raw question",
                "ground_truth_answer": "private raw answer",
                "retrieved_memories_by_top_k": {"50": [{"memory": "private raw memory"}]},
                "cutoff_results": {"50": {"score": 1.0, "judgment": "PASS"}},
            }
        ]
    )

    result = module.build_verification(report, args())
    rendered = json.dumps(result)

    assert result["ok"] is False
    assert result["gates"]["raw_payload"]["ok"] is False
    assert result["gates"]["raw_payload"]["hit_count"] == 4
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered


def test_verifier_rejects_missing_strict_gate_requirements():
    module = load_module()

    result = module.build_verification(
        actual_judged_report(),
        args(
            max_cost_usd=None,
            mem0_target_accuracy=None,
            require_model_calls=False,
            require_usage=False,
            expected_provider=None,
            expected_answerer_model=None,
            expected_judge_model=None,
            expected_questions=None,
        ),
    )

    assert result["ok"] is False
    assert result["gates"]["model_calls"]["ok"] is False
    assert result["gates"]["usage"]["ok"] is False
    assert result["gates"]["provider"]["ok"] is False
    assert result["gates"]["answerer_model"]["ok"] is False
    assert result["gates"]["judge_model"]["ok"] is False
    assert result["gates"]["cost"]["ok"] is False
    assert result["gates"]["mem0_target"]["ok"] is False
    assert result["gates"]["question_count"]["ok"] is False


def test_verifier_rejects_model_identity_mismatch():
    module = load_module()

    result = module.build_verification(
        actual_judged_report(answerer_model="wrong-answer-model", judge_model="wrong-judge-model"),
        args(),
    )

    assert result["ok"] is False
    assert result["gates"]["answerer_model"]["ok"] is False
    assert result["gates"]["answerer_model"]["expected"] == "answer-model"
    assert result["gates"]["answerer_model"]["actual"] == "wrong-answer-model"
    assert result["gates"]["judge_model"]["ok"] is False
    assert result["gates"]["judge_model"]["expected"] == "judge-model"
    assert result["gates"]["judge_model"]["actual"] == "wrong-judge-model"


def test_verifier_rejects_value_side_secret_like_payload_without_echoing_value():
    module = load_module()
    report = actual_judged_report(
        sanitized_diagnostic={"marker": "Bearer abcdefghijklmnopqrstuvwxyz0123456789"},
    )

    result = module.build_verification(report, args())
    rendered = json.dumps(result)

    assert result["ok"] is False
    assert result["gates"]["raw_payload"]["ok"] is False
    assert result["gates"]["raw_payload"]["hit_count"] == 1
    assert "Bearer abcdefghijklmnopqrstuvwxyz0123456789" not in rendered


def test_cli_writes_sanitized_verification_report(tmp_path, capsys):
    module = load_module()
    report = tmp_path / "judged.json"
    output = tmp_path / "verification.json"
    report.write_text(json.dumps(actual_judged_report()), encoding="utf-8")

    code = module.main(
        [
            "--report",
            str(report),
            "--cutoff",
            "50",
            "--min-accuracy",
            "0.9",
            "--mem0-target-accuracy",
            "0.918",
            "--max-cost-usd",
            "0.01",
            "--require-model-calls",
            "--require-usage",
            "--expected-provider",
            "openai-compatible",
            "--expected-answerer-model",
            "answer-model",
            "--expected-judge-model",
            "judge-model",
            "--expected-questions",
            "30",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert json.loads(capsys.readouterr().out)["ok"] is True
