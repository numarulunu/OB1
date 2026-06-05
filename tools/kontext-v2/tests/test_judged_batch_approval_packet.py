from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_batch_approval_packet.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_batch_approval_packet", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_readiness(path: Path, ok: bool = True) -> None:
    suites = {
        "locomo1": {"ready_for_paid_judged_run": ok, "public_raw_scan": {"hit_count": 0}},
        "longmemeval1": {"ready_for_paid_judged_run": ok, "public_raw_scan": {"hit_count": 0}},
        "beam1": {"ready_for_paid_judged_run": ok, "public_raw_scan": {"hit_count": 0}},
    }
    write_json(
        path,
        {
            "ok": ok,
            "mode": "judged-readiness-audit",
            "runs_model_calls": False,
            "summary": {
                "suites_total": 3,
                "suites_ready_for_paid_judged_run": 3 if ok else 2,
                "actual_judged_accuracy_proven": False,
                "blocked_by": ["explicit paid-run approval"] if ok else ["readiness gate failures"],
            },
            "suites": suites,
        },
    )


def write_packet(path: Path, suite: str, calls: dict, cost: float, max_cost: float, raw_suffix: str = "") -> None:
    write_json(
        path,
        {
            "ok": True,
            "mode": "judged-benchmark-approval-packet",
            "runs_model_calls": False,
            "approval_required": True,
            "dataset": suite,
            "run_id": f"{suite}-run",
            "selected_questions": 1,
            "top_k_values": [50],
            "estimated_llm_calls": calls,
            "estimated_tokens": {
                "answer_input_tokens": calls["answer_calls"] * 4000,
                "answer_output_tokens": calls["answer_calls"] * 300,
                "judge_input_tokens": calls["judge_calls"] * 1500,
                "judge_output_tokens": calls["judge_calls"] * 120,
                "total_input_tokens": calls["answer_calls"] * 4000 + calls["judge_calls"] * 1500,
                "total_output_tokens": calls["answer_calls"] * 300 + calls["judge_calls"] * 120,
                "total_tokens": calls["answer_calls"] * 4300 + calls["judge_calls"] * 1620,
            },
            "estimated_cost_usd": {"answerer_usd": cost / 2, "judge_usd": cost / 2, "total_usd": cost},
            "max_cost_usd": max_cost,
            "required_env_vars": ["OPENAI_API_KEY"],
            "command_template": (
                "python3 /opt/kontext/scripts/judged_benchmark_run.py "
                f"--input-bundle /opt/kontext/private/judged-bundles/{suite}.json "
                "--api-key-env OPENAI_API_KEY --approve-cost "
                f"--output /opt/kontext/reports/judged-plans/{suite}-paid-run.json{raw_suffix}"
            ),
        },
    )


def test_batch_packet_totals_ready_suites_without_raw_text_or_secrets(tmp_path):
    module = load_module()
    readiness = tmp_path / "readiness.json"
    write_readiness(readiness)
    write_packet(tmp_path / "locomo.json", "locomo1", {"answer_calls": 1, "judge_calls": 1, "total_calls": 2}, 0.001077, 0.01)
    write_packet(tmp_path / "longmem.json", "longmemeval1", {"answer_calls": 1, "judge_calls": 1, "total_calls": 2}, 0.001077, 0.01)
    write_packet(tmp_path / "beam.json", "beam1", {"answer_calls": 2, "judge_calls": 3, "total_calls": 5}, 0.002451, 0.05)

    packet = module.build_batch_packet(
        readiness,
        [
            f"locomo1={tmp_path / 'locomo.json'}",
            f"longmemeval1={tmp_path / 'longmem.json'}",
            f"beam1={tmp_path / 'beam.json'}",
        ],
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["approval_required"] is True
    assert packet["runs_model_calls"] is False
    assert packet["actual_judged_accuracy_proven"] is False
    assert packet["summary"]["suite_count"] == 3
    assert packet["summary"]["estimated_llm_calls"] == {"answer_calls": 4, "judge_calls": 5, "total_calls": 9}
    assert packet["summary"]["estimated_cost_usd"]["total_usd"] == 0.004605
    assert packet["summary"]["max_cost_usd_total"] == 0.07
    assert packet["required_env_vars"] == ["OPENAI_API_KEY"]
    assert len(packet["commands"]) == 3
    assert "private raw question" not in rendered
    assert "sk-" not in rendered


def test_batch_packet_refuses_unready_readiness_report(tmp_path):
    module = load_module()
    readiness = tmp_path / "readiness.json"
    write_readiness(readiness, ok=False)
    write_packet(tmp_path / "locomo.json", "locomo1", {"answer_calls": 1, "judge_calls": 1, "total_calls": 2}, 0.001, 0.01)

    packet = module.build_batch_packet(readiness, [f"locomo1={tmp_path / 'locomo.json'}"])

    assert packet["ok"] is False
    assert packet["approval_required"] is False
    assert packet["reason"] == "readiness report is not fully ready"


def test_batch_packet_rejects_secret_like_command(tmp_path):
    module = load_module()
    readiness = tmp_path / "readiness.json"
    write_readiness(readiness)
    write_packet(
        tmp_path / "locomo.json",
        "locomo1",
        {"answer_calls": 1, "judge_calls": 1, "total_calls": 2},
        0.001,
        0.01,
        raw_suffix=" --api-key sk-test-secret-value",
    )

    packet = module.build_batch_packet(readiness, [f"locomo1={tmp_path / 'locomo.json'}"])

    assert packet["ok"] is False
    assert packet["approval_required"] is False
    assert packet["reason"] == "approval packet contains unsafe command text"


def test_cli_writes_batch_packet(tmp_path, capsys):
    module = load_module()
    readiness = tmp_path / "readiness.json"
    output = tmp_path / "batch.json"
    write_readiness(readiness)
    write_packet(tmp_path / "locomo.json", "locomo1", {"answer_calls": 1, "judge_calls": 1, "total_calls": 2}, 0.001077, 0.01)

    exit_code = module.main(
        [
            "--readiness-report",
            str(readiness),
            "--packet",
            f"locomo1={tmp_path / 'locomo.json'}",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(capsys.readouterr().out)["ok"] is True
