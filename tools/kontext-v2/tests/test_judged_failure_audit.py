from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_failure_audit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_failure_audit", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample_report():
    return {
        "ok": True,
        "mode": "openai-compatible-judged-benchmark-run",
        "runs_model_calls": True,
        "provider": "openai-compatible",
        "dataset": "locomo10",
        "run_id": "run-1",
        "selected_questions": 2,
        "top_k_values": [10, 20, 200],
        "answerer_model": "gpt-5.4-nano",
        "judge_model": "gpt-5.4-nano",
        "actual_usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        "estimated_cost_usd": {"total_usd": 0.01},
        "summary": {
            "10": {"total": 2, "passed": 1, "accuracy": 0.5, "avg_score": 0.45},
            "20": {"total": 2, "passed": 2, "accuracy": 1.0, "avg_score": 0.85},
            "200": {"total": 2, "passed": 1, "accuracy": 0.5, "avg_score": 0.55},
        },
        "questions": [
            {
                "question_id": "q1",
                "question_hash": "hash-one-abcdef",
                "ground_truth_hash": "gt-one",
                "category": "temporal",
                "cutoff_results": {
                    "10": {"score": 0.4, "judgment": "FAIL", "memories_evaluated": 10},
                    "20": {"score": 0.9, "judgment": "PASS", "memories_evaluated": 20},
                    "200": {"score": 0.4, "judgment": "FAIL", "memories_evaluated": 200},
                },
            },
            {
                "question_id": "q2",
                "question_hash": "hash-two-abcdef",
                "ground_truth_hash": "gt-two",
                "category": "multi_hop",
                "cutoff_results": {
                    "10": {"score": 0.6, "judgment": "PASS", "memories_evaluated": 10},
                    "20": {"score": 0.8, "judgment": "PASS", "memories_evaluated": 20},
                    "200": {"score": 0.7, "judgment": "PASS", "memories_evaluated": 200},
                },
            },
        ],
    }


def test_build_audit_reports_aggregate_cutoff_patterns_without_raw_text():
    module = load_module()

    audit = module.build_audit(sample_report(), {"summary": {"accuracy": 0.5}})
    rendered = json.dumps(audit)

    assert audit["mode"] == "judged-failure-audit"
    assert audit["runs_model_calls"] is False
    assert audit["source"]["selected_questions"] == 2
    assert audit["best_cutoff_by_accuracy"] == "20"
    assert audit["oracle_best_per_question"] == {"passed": 2, "total": 2, "accuracy": 1.0}
    assert audit["cutoffs"]["20"]["accuracy"] == 1.0
    assert audit["cutoffs"]["200"]["failed_question_hashes"] == ["hash-one-abcdef"]
    assert audit["transition_counts"]["k10:fail|k20:pass|k200:fail"] == 1
    assert "private raw question" not in rendered
    assert "generated_answer" not in rendered
    assert "private raw" not in rendered


def test_audit_flags_raw_payload_keys_without_echoing_values():
    module = load_module()
    report = sample_report()
    report["questions"][0]["question"] = "private raw question"
    report["questions"][0]["retrieved_memories_by_top_k"] = {"20": [{"memory": "private raw memory"}]}

    audit = module.build_audit(report)
    rendered = json.dumps(audit)

    assert audit["raw_payload_key_hit_count"] == 2
    assert "$.questions[0].question" in audit["raw_payload_key_hit_paths"]
    assert "private raw question" not in rendered
    assert "private raw memory" not in rendered


def test_cli_writes_sanitized_audit(tmp_path, capsys):
    module = load_module()
    paid_run = tmp_path / "paid.json"
    output = tmp_path / "audit.json"
    paid_run.write_text(json.dumps(sample_report()), encoding="utf-8")

    code = module.main(["--paid-run", str(paid_run), "--output", str(output)])

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == "judged-failure-audit"
    assert json.loads(capsys.readouterr().out)["mode"] == "judged-failure-audit"
