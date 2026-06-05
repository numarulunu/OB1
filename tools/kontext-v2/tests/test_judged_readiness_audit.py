from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_readiness_audit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_readiness_audit", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def args(private_dir: Path, report_dir: Path, output: Path | None = None):
    return Namespace(
        private_dir=str(private_dir),
        report_dir=str(report_dir),
        output=str(output) if output else None,
        suite=[
            "locomo1|locomo-private.json|locomo-predict.json|locomo-mock.json|locomo-verify.json|locomo-approval.json",
            "longmemeval1|longmem-private.json|longmem-predict.json|longmem-mock.json|longmem-verify.json|longmem-approval.json",
        ],
        require_private_permissions=False,
    )


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_suite(private_dir: Path, report_dir: Path, prefix: str, dataset: str) -> None:
    write_json(
        private_dir / f"{prefix}-private.json",
        {
            "ok": True,
            "mode": "private-judged-input-bundle",
            "dataset": dataset,
            "run_id": f"{prefix}-run",
            "runs_model_calls": False,
            "contains_live_user_memory": False,
            "questions": [
                {
                    "question_id": "q1",
                    "question": "private raw benchmark question",
                    "ground_truth_answer": "private raw benchmark answer",
                    "retrieved_memories_by_top_k": {"50": [{"memory": "private benchmark memory"}]},
                }
            ],
        },
    )
    write_json(
        report_dir / f"{prefix}-predict.json",
        {
            "ok": True,
            "mode": f"{dataset}-predict",
            "dataset": dataset,
            "run_id": f"{prefix}-run",
            "runs_model_calls": False,
            "top_k_values": [50],
            "summary": {"50": {"total": 1, "passed": 1, "accuracy": 1.0}},
        },
    )
    write_json(
        report_dir / f"{prefix}-mock.json",
        {
            "ok": True,
            "mode": "mock-judged-benchmark-run",
            "provider": "mock",
            "dataset": dataset,
            "run_id": f"{prefix}-run",
            "runs_model_calls": False,
            "selected_questions": 1,
            "summary": {"50": {"total": 1, "passed": 1, "accuracy": 1.0}},
        },
    )
    write_json(
        report_dir / f"{prefix}-verify.json",
        {
            "ok": False,
            "mode": "judged-benchmark-verification",
            "source_mode": "mock-judged-benchmark-run",
            "runs_model_calls": False,
            "dataset": dataset,
            "run_id": f"{prefix}-run",
            "gates": {
                "model_calls": {"ok": False},
                "provider": {"ok": False},
                "usage": {"ok": False},
                "raw_payload": {"ok": True, "hit_count": 0, "hit_paths": []},
            },
        },
    )
    write_json(
        report_dir / f"{prefix}-approval.json",
        {
            "ok": True,
            "mode": "judged-benchmark-approval-packet",
            "dataset": dataset,
            "run_id": f"{prefix}-run",
            "runs_model_calls": False,
            "approval_required": True,
            "selected_questions": 1,
            "top_k_values": [50],
            "estimated_llm_calls": {"answer_calls": 1, "judge_calls": 1, "total_calls": 2},
            "estimated_tokens": {"total": 5920},
            "estimated_cost_usd": 0.001077,
            "max_cost_usd": 0.01,
            "required_env_vars": ["OPENAI_API_KEY"],
            "command_template": "python /opt/kontext/scripts/judged_benchmark_run.py --api-key-env OPENAI_API_KEY",
        },
    )


def test_readiness_audit_accepts_staged_locomo_and_longmemeval_without_raw_output(tmp_path):
    module = load_module()
    private_dir = tmp_path / "private"
    report_dir = tmp_path / "reports"
    write_suite(private_dir, report_dir, "locomo", "locomo10")
    write_suite(private_dir, report_dir, "longmem", "longmemeval_s")

    audit = module.build_audit(args(private_dir, report_dir))
    rendered = json.dumps(audit)

    assert audit["ok"] is True
    assert audit["runs_model_calls"] is False
    assert audit["summary"]["suites_ready_for_paid_judged_run"] == 2
    assert audit["summary"]["actual_judged_accuracy_proven"] is False
    assert audit["summary"]["blocked_by"] == ["explicit paid-run approval"]
    assert audit["suites"]["locomo1"]["ready_for_paid_judged_run"] is True
    assert audit["suites"]["longmemeval1"]["ready_for_paid_judged_run"] is True
    assert audit["suites"]["locomo1"]["public_raw_scan"]["hit_count"] == 0
    assert "private raw benchmark question" not in rendered
    assert "private raw benchmark answer" not in rendered
    assert "private benchmark memory" not in rendered


def test_readiness_audit_blocks_public_raw_payload_leaks(tmp_path):
    module = load_module()
    private_dir = tmp_path / "private"
    report_dir = tmp_path / "reports"
    write_suite(private_dir, report_dir, "locomo", "locomo10")
    write_suite(private_dir, report_dir, "longmem", "longmemeval_s")

    leaked = json.loads((report_dir / "locomo-predict.json").read_text(encoding="utf-8"))
    leaked["question"] = "raw public question leak"
    write_json(report_dir / "locomo-predict.json", leaked)

    audit = module.build_audit(args(private_dir, report_dir))
    rendered = json.dumps(audit)

    assert audit["ok"] is False
    assert audit["suites"]["locomo1"]["ready_for_paid_judged_run"] is False
    assert audit["suites"]["locomo1"]["gates"]["public_raw_scan"]["ok"] is False
    assert audit["suites"]["locomo1"]["public_raw_scan"]["hit_count"] == 1
    assert "raw public question leak" not in rendered


def test_readiness_audit_accepts_deployed_predict_and_cost_shapes(tmp_path):
    module = load_module()
    private_dir = tmp_path / "private"
    report_dir = tmp_path / "reports"
    write_suite(private_dir, report_dir, "locomo", "locomo10")
    write_suite(private_dir, report_dir, "longmem", "longmemeval_s")

    for prefix in ("locomo", "longmem"):
        predict = json.loads((report_dir / f"{prefix}-predict.json").read_text(encoding="utf-8"))
        predict.pop("ok")
        predict.pop("runs_model_calls")
        predict["mode"] = "predict-only-sweep"
        write_json(report_dir / f"{prefix}-predict.json", predict)

        approval = json.loads((report_dir / f"{prefix}-approval.json").read_text(encoding="utf-8"))
        approval["estimated_cost_usd"] = {
            "answerer_usd": 0.00078,
            "judge_usd": 0.000297,
            "total_usd": 0.001077,
        }
        write_json(report_dir / f"{prefix}-approval.json", approval)

    audit = module.build_audit(args(private_dir, report_dir))

    assert audit["ok"] is True
    assert audit["suites"]["locomo1"]["gates"]["predict_report"]["ok"] is True
    assert audit["suites"]["locomo1"]["gates"]["approval_packet"]["ok"] is True


def test_readiness_audit_surfaces_beam_rubric_approval_metadata(tmp_path):
    module = load_module()
    private_dir = tmp_path / "private"
    report_dir = tmp_path / "reports"
    write_suite(private_dir, report_dir, "beam", "beam_1M")

    approval = json.loads((report_dir / "beam-approval.json").read_text(encoding="utf-8"))
    approval["benchmark_mode"] = "beam-rubric"
    approval["judge_units_total"] = 4
    approval["judge_units_per_question"] = 2.0
    approval["estimated_llm_calls"] = {"answer_calls": 2, "judge_calls": 4, "total_calls": 6}
    write_json(report_dir / "beam-approval.json", approval)

    audit = module.build_audit(
        Namespace(
            private_dir=str(private_dir),
            report_dir=str(report_dir),
            output=None,
            suite=["beam1|beam-private.json|beam-predict.json|beam-mock.json|beam-verify.json|beam-approval.json"],
            require_private_permissions=False,
        )
    )

    approval_gate = audit["suites"]["beam1"]["gates"]["approval_packet"]
    assert audit["ok"] is True
    assert approval_gate["benchmark_mode"] == "beam-rubric"
    assert approval_gate["judge_units_total"] == 4
    assert approval_gate["judge_units_per_question"] == 2.0


def test_cli_writes_sanitized_readiness_audit(tmp_path, capsys):
    module = load_module()
    private_dir = tmp_path / "private"
    report_dir = tmp_path / "reports"
    output = tmp_path / "audit.json"
    write_suite(private_dir, report_dir, "locomo", "locomo10")
    write_suite(private_dir, report_dir, "longmem", "longmemeval_s")

    exit_code = module.main(
        [
            "--private-dir",
            str(private_dir),
            "--report-dir",
            str(report_dir),
            "--suite",
            "locomo1|locomo-private.json|locomo-predict.json|locomo-mock.json|locomo-verify.json|locomo-approval.json",
            "--suite",
            "longmemeval1|longmem-private.json|longmem-predict.json|longmem-mock.json|longmem-verify.json|longmem-approval.json",
            "--skip-private-permissions",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(capsys.readouterr().out)["ok"] is True
