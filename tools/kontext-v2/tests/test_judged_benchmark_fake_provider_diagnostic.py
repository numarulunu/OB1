from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_benchmark_fake_provider_diagnostic.py"
SCAN_PATH = Path(__file__).resolve().parents[1] / "scripts" / "public_artifact_scan.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_bundle(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "private-beam-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [50],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "knowledge_update",
                        "question": "What is the current launch codename for the billing dashboard?",
                        "ground_truth_answer": "Harbor.",
                        "retrieved_memories_by_top_k": {
                            "50": [
                                {
                                    "memory": (
                                        "User: The current launch codename for the billing dashboard is Harbor. "
                                        "Assistant: Noted."
                                    ),
                                    "metadata": {"source_ids": ["turn-1"]},
                                }
                            ]
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def write_approval(path: Path) -> None:
    command = " ".join(
        [
            "python3",
            "/opt/kontext/scripts/judged_benchmark_run.py",
            "--input-bundle",
            "PRIVATE_PATH_REDACTED",
            "--output",
            "/opt/kontext/reports/judged-plans/fake-run.json",
            "--cutoffs",
            "50",
            "--max-questions",
            "1",
            "--question-offset",
            "0",
            "--execute",
            "--provider",
            "openai-compatible",
            "--approve-cost",
            "--max-cost-usd",
            "1.0",
            "--answerer-model",
            "answer-model",
            "--judge-model",
            "judge-model",
            "--api-key-env",
            "OPENAI_API_KEY",
            "--base-url",
            "https://example.test/v1/chat/completions",
            "--answer-input-usd-per-1m",
            "1",
            "--answer-output-usd-per-1m",
            "1",
            "--judge-input-usd-per-1m",
            "1",
            "--judge-output-usd-per-1m",
            "1",
            "--judge-units-per-question",
            "3",
            "--answer-max-memories",
            "10",
            "--answer-memory-max-chars",
            "1200",
            "--answer-total-max-chars",
            "18000",
            "--beam-state-reducer",
            "--beam-state-ledger",
            "--beam-answer-candidate-selector",
            "--beam-direct-span-candidate",
            "--beam-typed-projection-candidate",
            "--omit-temperature",
            "--reasoning-effort",
            "minimal",
            "--verification-output",
            "/opt/kontext/reports/judged-plans/fake-verification.json",
            "--verify-cutoff",
            "50",
            "--verify-min-accuracy",
            "0.8",
            "--verify-min-questions",
            "1",
            "--verify-mem0-target-accuracy",
            "0.8",
            "--verify-max-cost-usd",
            "1.0",
            "--verify-require-model-calls",
            "--verify-require-usage",
            "--verify-expected-provider",
            "openai-compatible",
            "--verify-expected-answerer-model",
            "answer-model",
            "--verify-expected-judge-model",
            "judge-model",
            "--verify-expected-questions",
            "1",
        ]
    )
    path.write_text(
        json.dumps(
            {
                "ok": True,
                "mode": "judged-benchmark-approval-packet",
                "runs_model_calls": False,
                "input_bundle": "PRIVATE_PATH_REDACTED",
                "input_bundle_private_path_redacted": True,
                "selected_questions": 1,
                "top_k_values": [50],
                "judge_units_per_question": 3,
                "max_cost_usd": 1.0,
                "omit_temperature": True,
                "reasoning_effort": "minimal",
                "beam_state_reducer": True,
                "beam_state_ledger": True,
                "beam_answer_candidate_selector": True,
                "beam_direct_span_candidate": True,
                "beam_typed_projection_candidate": True,
                "command_template": command,
            }
        ),
        encoding="utf-8",
    )


def test_fake_provider_diagnostic_writes_sanitized_count_only_summary(tmp_path):
    module = load_module(SCRIPT_PATH, "judged_benchmark_fake_provider_diagnostic")
    scanner = load_module(SCAN_PATH, "public_artifact_scan")
    bundle = tmp_path / "private-bundle.json"
    approval = tmp_path / "approval.json"
    output = tmp_path / "fake-summary.json"
    write_bundle(bundle)
    write_approval(approval)

    code = module.main(["--approval", str(approval), "--input-bundle", str(bundle), "--output", str(output)])
    report = json.loads(output.read_text(encoding="utf-8"))
    scan = scanner.build_report([str(output)])
    rendered = json.dumps(report)

    assert code == 0
    assert report["ok"] is True
    assert report["runs_model_calls"] is False
    assert report["selected_questions"] == 1
    assert report["synthetic_calls"] >= 1
    assert report["selector_prompts"] == 1
    assert report["selector_metric_prompts"] == 1
    assert report["selector_metric_rule_prompts"] == 1
    assert report["raw_payload_hits"] == 0
    assert report["private_path_hits"] == 0
    assert report["secret_shaped_hits"] == 0
    assert scan["ok"] is True
    assert "billing dashboard" not in rendered
    assert "Harbor" not in rendered
    assert str(bundle) not in rendered
    assert "PRIVATE_PATH_REDACTED" not in rendered
