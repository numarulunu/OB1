from __future__ import annotations

import importlib.util
import json
import sys
import types
from argparse import Namespace
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_n30_cutover_prepare.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_n30_cutover_prepare", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def args(tmp_path: Path, **overrides):
    defaults = {
        "reports_dir": str(tmp_path / "reports"),
        "private_dir": str(tmp_path / "private"),
        "database_url_env": "KONTEXT_V2_DATABASE_URL",
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url_env": "OPENROUTER_BASE_URL",
        "base_url": None,
        "answerer_model": "google/gemini-2.0-flash-001",
        "judge_model": "google/gemini-2.0-flash-001",
        "max_questions_per_suite": 30,
        "cutoffs": "10,20,50,200",
        "max_locomo_cost_usd": 0.12,
        "max_longmemeval_cost_usd": 0.12,
        "max_beam_cost_usd": 0.20,
        "max_total_cost_usd": 0.50,
        "min_accuracy": 0.80,
        "mem0_target_accuracy": 0.80,
        "answer_input_usd_per_1m": 0.10,
        "answer_output_usd_per_1m": 0.40,
        "judge_input_usd_per_1m": 0.10,
        "judge_output_usd_per_1m": 0.40,
        "locomo_dataset_url": "https://example.test/locomo10.json",
        "longmemeval_dataset_url": "https://example.test/longmem.json",
        "beam_size": "10M",
        "beam_offset": 0,
        "beam_length": 10,
        "beam_question_types": "information_extraction,knowledge_update,instruction_following,preference_following",
        "run_id_prefix": "judged-n30-cutover",
        "timestamp": "20260529T010203Z",
        "output": str(tmp_path / "reports" / "manifest.json"),
        "execute_paid": False,
        "approve_cost": False,
        "skip_generation": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def option_value(argv: list[str], name: str) -> str | None:
    try:
        index = argv.index(name)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


class FakeRunner:
    def __init__(self):
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str], *, allowed_returncodes: set[int] | None = None):
        self.commands.append(list(argv))
        output = option_value(argv, "--output")
        verification = option_value(argv, "--verification-output")
        bundle = option_value(argv, "--judged-bundle-output")
        if bundle:
            questions = [
                {
                    "question_id": f"q{i}",
                    "question": "private raw benchmark question",
                    "ground_truth_answer": "private raw benchmark answer",
                    "retrieved_memories_by_top_k": {
                        "50": [{"id": "benchmark:one", "memory": "private raw benchmark memory"}]
                    },
                }
                for i in range(30)
            ]
            Path(bundle).parent.mkdir(parents=True, exist_ok=True)
            Path(bundle).write_text(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "private-judged-input-bundle",
                        "runs_model_calls": False,
                        "contains_live_user_memory": False,
                        "dataset": "fixture",
                        "run_id": "fixture-run",
                        "top_k_values": [10, 20, 50, 200],
                        "questions": questions,
                    }
                ),
                encoding="utf-8",
            )
            if output:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "mode": "predict-only-sweep",
                            "runs_model_calls": False,
                            "dataset": "fixture",
                            "run_id": "fixture-run",
                            "top_k_values": [10, 20, 50, 200],
                            "total_questions": 30,
                        }
                    ),
                    encoding="utf-8",
                )
        elif "judged_benchmark_run.py" in " ".join(argv):
            if output:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "mode": "mock-judged-benchmark-run",
                            "provider": "mock",
                            "runs_model_calls": False,
                            "selected_questions": 30,
                            "dataset": "fixture",
                            "run_id": "fixture-run",
                        }
                    ),
                    encoding="utf-8",
                )
            if verification:
                Path(verification).parent.mkdir(parents=True, exist_ok=True)
                Path(verification).write_text(
                    json.dumps(
                        {
                            "ok": False,
                            "mode": "judged-benchmark-verification",
                            "runs_model_calls": False,
                            "source_mode": "mock-judged-benchmark-run",
                            "gates": {
                                "model_calls": {"ok": False},
                                "provider": {"ok": False},
                                "usage": {"ok": False},
                                "raw_payload": {"ok": True, "hit_count": 0, "hit_paths": []},
                            },
                        }
                    ),
                    encoding="utf-8",
                )
        else:
            if output:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_text(json.dumps({"ok": True, "runs_model_calls": False}), encoding="utf-8")
        return {"returncode": 0, "stdout_bytes": 0, "stderr_bytes": 0}


def fake_predict_runner(spec, _args, _database_url):
    questions = [
        {
            "question_id": f"q{i}",
            "question": "private raw benchmark question",
            "ground_truth_answer": "private raw benchmark answer",
            "retrieved_memories_by_top_k": {
                "50": [{"id": "benchmark:one", "memory": "private raw benchmark memory"}]
            },
        }
        for i in range(30)
    ]
    Path(spec.private_bundle).parent.mkdir(parents=True, exist_ok=True)
    Path(spec.private_bundle).write_text(
        json.dumps(
            {
                "ok": True,
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_live_user_memory": False,
                "dataset": spec.name,
                "run_id": spec.run_id,
                "top_k_values": [10, 20, 50, 200],
                "questions": questions,
            }
        ),
        encoding="utf-8",
    )
    Path(spec.predict_report).parent.mkdir(parents=True, exist_ok=True)
    Path(spec.predict_report).write_text(
        json.dumps(
            {
                "ok": True,
                "mode": "predict-only-sweep",
                "runs_model_calls": False,
                "dataset": spec.name,
                "run_id": spec.run_id,
                "top_k_values": [10, 20, 50, 200],
                "total_questions": 30,
            }
        ),
        encoding="utf-8",
    )
    return {"returncode": 0, "stdout_bytes": 0, "stderr_bytes": 0}


def test_build_plan_defaults_to_no_paid_calls_and_n30_suite_specs(tmp_path):
    module = load_module()

    plan = module.build_plan(
        args(tmp_path),
        environ={
            "KONTEXT_V2_DATABASE_URL": "postgresql://example",
            "OPENROUTER_API_KEY": "sk-test-secret-value",
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1/chat/completions",
        },
    )
    rendered = json.dumps(plan)

    assert plan["runs_model_calls"] is False
    assert plan["paid_execution_enabled"] is False
    assert plan["question_count_per_suite"] == 30
    assert plan["top_k_values"] == [10, 20, 50, 200]
    assert plan["estimated_max_total_cost_usd"] == 0.44
    assert [suite["suite"] for suite in plan["suites"]] == ["locomo30", "longmemeval30", "beam30"]
    assert all(suite["selected_questions"] == 30 for suite in plan["suites"])
    assert "sk-test-secret-value" not in rendered


def test_build_plan_blocks_paid_execution_without_all_required_gates(tmp_path):
    module = load_module()

    plan = module.build_plan(
        args(tmp_path, execute_paid=True, approve_cost=False, max_total_cost_usd=0.50),
        environ={"KONTEXT_V2_DATABASE_URL": "postgresql://example", "OPENROUTER_API_KEY": "present"},
    )

    assert plan["paid_execution_enabled"] is False
    assert "paid execution requires --approve-cost" in plan["blocked_by"]


def test_build_plan_blocks_cost_ceiling_above_one_dollar(tmp_path):
    module = load_module()

    plan = module.build_plan(
        args(tmp_path, execute_paid=True, approve_cost=True, max_total_cost_usd=1.01),
        environ={"KONTEXT_V2_DATABASE_URL": "postgresql://example", "OPENROUTER_API_KEY": "present"},
    )

    assert plan["paid_execution_enabled"] is False
    assert "max total cost ceiling must be <= 1.00 USD" in plan["blocked_by"]


def test_prepare_writes_private_artifacts_and_sanitized_public_manifest(tmp_path):
    module = load_module()
    runner = FakeRunner()

    manifest = module.prepare(
        args(tmp_path),
        runner=runner,
        predict_runner=fake_predict_runner,
        environ={
            "KONTEXT_V2_DATABASE_URL": "postgresql://example",
            "OPENROUTER_API_KEY": "sk-test-secret-value",
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1/chat/completions",
        },
    )
    rendered = json.dumps(manifest)

    assert manifest["ok"] is True
    assert manifest["runs_model_calls"] is False
    assert manifest["paid_execution_enabled"] is False
    assert manifest["stage_counts"]["predict_private_bundle"] == 3
    assert manifest["stage_counts"]["approval_packet"] == 3
    assert all(Path(suite["private_bundle"]).is_file() for suite in manifest["suites"])
    private_prefix = str(tmp_path / "private").replace("\\", "/")
    assert all(private_prefix in suite["private_bundle"] for suite in manifest["suites"])
    assert "private raw benchmark question" not in rendered
    assert "private raw benchmark answer" not in rendered
    assert "private raw benchmark memory" not in rendered
    assert "sk-test-secret-value" not in rendered


def test_beam_predict_generation_uses_turn_level_rows_for_judged_bundle(tmp_path, monkeypatch):
    module = load_module()
    captured = {}

    fake_beam_predict = types.ModuleType("kontext_v2.benchmarks.beam_predict")

    def fake_run_beam_predict_sweep(*args, **kwargs):
        captured.update(kwargs)
        return {
            "dataset": "beam_1M",
            "run_id": kwargs["run_id"],
            "mode": "predict-only-sweep",
            "runs_model_calls": False,
            "report_paths": {"json": str(tmp_path / "beam-predict.json")},
        }

    fake_beam_predict.run_beam_predict_sweep = fake_run_beam_predict_sweep
    monkeypatch.setitem(sys.modules, "kontext_v2.benchmarks.beam_predict", fake_beam_predict)
    (tmp_path / "beam-predict.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    spec = module.SuiteSpec(
        name="beam30",
        dataset_kind="beam",
        run_id="beam-run",
        max_cost_usd=0.20,
        private_bundle=tmp_path / "private" / "beam.json",
        predict_report=tmp_path / "reports" / "beam-predict.json",
        mock_run=tmp_path / "reports" / "beam-mock.json",
        mock_verification=tmp_path / "reports" / "beam-verify.json",
        approval_packet=tmp_path / "reports" / "beam-approval.json",
        paid_run=tmp_path / "reports" / "beam-paid.json",
        paid_verification=tmp_path / "reports" / "beam-paid-verify.json",
    )

    module.run_predict_suite(spec, args(tmp_path), "postgresql://example")

    assert captured["session_only"] is False
    assert captured["question_types"] == "information_extraction,knowledge_update,instruction_following,preference_following"
    assert captured["top_k_values"] == [10, 20, 50, 200]


def test_cli_requires_n30_questions(tmp_path, capsys):
    module = load_module()

    exit_code = module.main(
        [
            "--reports-dir",
            str(tmp_path / "reports"),
            "--private-dir",
            str(tmp_path / "private"),
            "--database-url-env",
            "KONTEXT_V2_DATABASE_URL",
            "--max-questions-per-suite",
            "29",
            "--output",
            str(tmp_path / "manifest.json"),
        ],
        runner=FakeRunner(),
        environ={"KONTEXT_V2_DATABASE_URL": "postgresql://example"},
    )

    assert exit_code == 2
    assert "max questions per suite must be at least 30" in capsys.readouterr().out
