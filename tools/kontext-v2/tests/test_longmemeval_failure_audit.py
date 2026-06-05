from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "longmemeval_failure_audit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("longmemeval_failure_audit", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def assert_public_report_has_no_raw_payload(value):
    forbidden_keys = {
        "question",
        "ground_truth_answer",
        "retrieved_memories_by_top_k",
        "memory",
        "messages",
        "content",
        "generated_answer",
        "judge_response",
        "judge_responses",
        "prompt",
    }

    def walk(node):
        if isinstance(node, dict):
            assert not (set(node) & forbidden_keys)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)


def test_failure_audit_classifies_misses_without_raw_payload(tmp_path):
    module = load_module()
    bundle_path = tmp_path / "long-private.json"
    run_path = tmp_path / "paid-run.json"
    verify_path = tmp_path / "verification.json"

    bundle_path.write_text(
        json.dumps(
            {
                "dataset": "longmemeval_s",
                "run_id": "private-long",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "q-present",
                        "category": "single-session-user",
                        "question": "private question present",
                        "ground_truth_answer": "blue archive folder",
                        "retrieved_memories_by_top_k": {
                            "20": [
                                {
                                    "memory": "private filler " * 150 + "blue archive folder",
                                    "metadata": {"session_id": "session_1"},
                                }
                            ]
                        },
                    },
                    {
                        "question_id": "q-absent",
                        "category": "multi-session-assistant",
                        "question": "private question absent",
                        "ground_truth_answer": "copper notebook",
                        "retrieved_memories_by_top_k": {
                            "20": [{"memory": "private unrelated context", "metadata": {"session_id": "session_2"}}]
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "mode": "openai-compatible-judged-benchmark-run",
                "dataset": "longmemeval_s",
                "retrieval_backend": "kontext",
                "estimated_cost_usd": {"total_usd": 0.01},
                "actual_usage": {"total_tokens": 1234},
                "questions": [
                    {
                        "question_id": "q-present",
                        "category": "single-session-user",
                        "question_hash": "hash-present",
                        "ground_truth_hash": "gt-present",
                        "cutoff_results": {"20": {"judgment": "FAIL", "score": 0.0, "memories_evaluated": 1}},
                    },
                    {
                        "question_id": "q-absent",
                        "category": "multi-session-assistant",
                        "question_hash": "hash-absent",
                        "ground_truth_hash": "gt-absent",
                        "cutoff_results": {"20": {"judgment": "FAIL", "score": 0.0, "memories_evaluated": 1}},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    verify_path.write_text(
        json.dumps({"ok": False, "summary": {"cutoff": 20, "total": 2, "passed": 0, "accuracy": 0.0}}),
        encoding="utf-8",
    )

    report = module.build_audit(
        run_report=json.loads(run_path.read_text(encoding="utf-8")),
        verification_report=json.loads(verify_path.read_text(encoding="utf-8")),
        private_bundle=json.loads(bundle_path.read_text(encoding="utf-8")),
        cutoff=20,
        memory_max_chars=220,
        total_max_chars=500,
    )
    rendered = json.dumps(report)

    assert report["ok"] is True
    assert report["classification_counts"]["evidence_present_truncated"] == 1
    assert report["classification_counts"]["evidence_absent_top20"] == 1
    assert report["selector_coverage"]["misses_full_has_answer_terms"] == 1
    assert report["selector_coverage"]["misses_selector_has_answer_terms"] == 0
    assert report["retrieval_coverage"]["misses_with_retrieved_top20"] == 2
    assert report["prompt_truncation"]["memory_max_chars"] == 220
    assert "private question" not in rendered
    assert "blue archive folder" not in rendered
    assert "copper notebook" not in rendered
    assert "private filler" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_cli_refuses_to_write_report_with_raw_payload_key(tmp_path, monkeypatch, capsys):
    module = load_module()
    run_path = tmp_path / "run.json"
    verify_path = tmp_path / "verify.json"
    bundle_path = tmp_path / "bundle.json"
    output_path = tmp_path / "audit.json"
    run_path.write_text("{}", encoding="utf-8")
    verify_path.write_text("{}", encoding="utf-8")
    bundle_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "build_audit", lambda *args, **kwargs: {"ok": True, "question": "private raw question"})

    code = module.main(
        [
            "--run-report",
            str(run_path),
            "--verification-report",
            str(verify_path),
            "--private-bundle",
            str(bundle_path),
            "--output",
            str(output_path),
        ]
    )
    rendered = capsys.readouterr().out

    assert code == 2
    assert not output_path.exists()
    assert "private raw question" not in rendered
    assert json.loads(rendered)["gates"]["raw_payload"]["hit_count"] == 1


def test_cli_refuses_to_write_report_with_secret_like_value(tmp_path, monkeypatch, capsys):
    module = load_module()
    run_path = tmp_path / "run.json"
    verify_path = tmp_path / "verify.json"
    bundle_path = tmp_path / "bundle.json"
    output_path = tmp_path / "audit.json"
    run_path.write_text("{}", encoding="utf-8")
    verify_path.write_text("{}", encoding="utf-8")
    bundle_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "build_audit", lambda *args, **kwargs: {"ok": True, "diagnostic": "Bearer abcdefghijklmnopqrstuvwxyz0123456789"})

    code = module.main(
        [
            "--run-report",
            str(run_path),
            "--verification-report",
            str(verify_path),
            "--private-bundle",
            str(bundle_path),
            "--output",
            str(output_path),
        ]
    )
    rendered = capsys.readouterr().out

    assert code == 2
    assert not output_path.exists()
    assert "Bearer abcdefghijklmnopqrstuvwxyz0123456789" not in rendered
    assert json.loads(rendered)["gates"]["raw_payload"]["hit_count"] == 1
