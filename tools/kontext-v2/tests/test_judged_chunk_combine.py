from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_chunk_combine.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_chunk_combine", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def run_report(path: Path, rows: list[tuple[str, bool, float]], *, cost: float = 0.01) -> None:
    questions = []
    for question_hash, passed, score in rows:
        questions.append(
            {
                "question_hash": question_hash,
                "category": "preference_following",
                "private_question": "raw private question must not leak",
                "cutoff_results": {
                    "20": {
                        "judgment": "PASS" if passed else "FAIL",
                        "score": score,
                        "judge_count": 3,
                        "judge_pass_count": 2 if passed else 1,
                        "generated_answer": "raw generated answer must not leak",
                        "generated_answer_hash": f"answer-{question_hash}",
                    }
                },
            }
        )
    write_json(
        path,
        {
            "ok": False,
            "mode": "openai-compatible-judged-benchmark-run-failed",
            "provider": "openai-compatible",
            "runs_model_calls": True,
            "question_offset": 6,
            "planned_questions": 6,
            "selected_questions": len(rows),
            "completed_questions": len(rows),
            "completed_calls": len(rows) * 5,
            "estimated_cost_usd": {"total_usd": cost},
            "summary": {"passed": sum(1 for _, passed, _ in rows), "total": len(rows)},
            "questions": questions,
        },
    )


def verification_report(path: Path, *, raw_payload_ok: bool = True, usage_ok: bool = True) -> None:
    write_json(
        path,
        {
            "ok": raw_payload_ok and usage_ok,
            "mode": "judged-benchmark-verification",
            "gates": {
                "raw_payload": {"ok": raw_payload_ok, "hit_count": 0 if raw_payload_ok else 1},
                "usage": {"ok": usage_ok, "required": True, "total_tokens": 123},
                "provider": {"ok": True, "expected": "openai-compatible", "actual": "openai-compatible"},
                "model_calls": {"ok": True, "required": True, "actual": True},
            },
        },
    )


def test_combines_partial_prefix_and_tail_with_cost_cap(tmp_path: Path):
    module = load_module()
    prefix = tmp_path / "prefix-run.json"
    tail = tmp_path / "tail-run.json"
    prefix_verify = tmp_path / "prefix-verify.json"
    tail_verify = tmp_path / "tail-verify.json"
    run_report(prefix, [("q1", True, 1.0), ("q2", True, 0.8), ("q3", True, 1.0), ("q4", False, 0.4)], cost=0.05)
    run_report(tail, [("q5", True, 0.9), ("q6", True, 1.0)], cost=0.03)
    verification_report(prefix_verify)
    verification_report(tail_verify)

    result = module.build_combined_report(
        [prefix, tail],
        verification_reports=[prefix_verify, tail_verify],
        cutoff="20",
        expected_questions=6,
        min_accuracy=0.8,
        max_cost_usd=0.2,
    )
    rendered = json.dumps(result)

    assert result["ok"] is True
    assert result["summary"]["total"] == 6
    assert result["summary"]["passed"] == 5
    assert result["summary"]["accuracy"] == 0.8333
    assert result["summary"]["total_cost_usd"] == 0.08
    assert "raw private question" not in rendered
    assert "raw generated answer" not in rendered
    assert "sk-" not in rendered


def test_rejects_duplicate_question_hash(tmp_path: Path):
    module = load_module()
    first = tmp_path / "first-run.json"
    second = tmp_path / "second-run.json"
    first_verify = tmp_path / "first-verify.json"
    second_verify = tmp_path / "second-verify.json"
    run_report(first, [("same", True, 1.0)])
    run_report(second, [("same", True, 1.0)])
    verification_report(first_verify)
    verification_report(second_verify)

    result = module.build_combined_report(
        [first, second],
        verification_reports=[first_verify, second_verify],
        cutoff="20",
        expected_questions=2,
        min_accuracy=0.8,
        max_cost_usd=0.2,
    )

    assert result["ok"] is False
    assert any(item.startswith("duplicate question:") for item in result["blocked_by"])


def test_rejects_failed_raw_payload_gate_even_for_partial_source(tmp_path: Path):
    module = load_module()
    report = tmp_path / "run.json"
    verify = tmp_path / "verify.json"
    run_report(report, [("q1", True, 1.0)])
    verification_report(verify, raw_payload_ok=False)

    result = module.build_combined_report(
        [report],
        verification_reports=[verify],
        cutoff="20",
        expected_questions=1,
        min_accuracy=0.8,
        max_cost_usd=0.2,
    )

    assert result["ok"] is False
    assert "raw payload gate failed: source 1" in result["blocked_by"]


def test_rejects_cost_over_cap(tmp_path: Path):
    module = load_module()
    report = tmp_path / "run.json"
    verify = tmp_path / "verify.json"
    run_report(report, [("q1", True, 1.0)], cost=0.25)
    verification_report(verify)

    result = module.build_combined_report(
        [report],
        verification_reports=[verify],
        cutoff="20",
        expected_questions=1,
        min_accuracy=0.8,
        max_cost_usd=0.2,
    )

    assert result["ok"] is False
    assert "cost 0.250000 exceeds cap 0.200000" in result["blocked_by"]
