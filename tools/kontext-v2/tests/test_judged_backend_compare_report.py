from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_backend_compare_report.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_backend_compare_report", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_verification(
    path: Path,
    *,
    suite: str,
    backend: str,
    model: str = "gpt-5-mini",
    cutoff: int = 20,
    total: int = 30,
    passed: int = 24,
) -> None:
    path.write_text(
        json.dumps(
            {
                "ok": True,
                "mode": "judged-benchmark-verification",
                "provider": "openai-compatible",
                "dataset": suite,
                "run_id": f"{suite}-{backend}",
                "retrieval_backend": backend,
                "answerer_model": model,
                "judge_model": model,
                "summary": {
                    "cutoff": cutoff,
                    "total": total,
                    "passed": passed,
                    "accuracy": round(passed / total, 4),
                    "avg_score": round(passed / total, 4),
                },
                "estimated_cost_usd": {"total_usd": 0.01},
                "actual_usage": {"total_tokens": 1234},
                "gates": {"raw_payload": {"ok": True, "hit_count": 0}},
            }
        ),
        encoding="utf-8",
    )


def test_backend_compare_report_rejects_mixed_models_without_override(tmp_path: Path):
    module = load_module()
    left = tmp_path / "locomo-kontext.json"
    right = tmp_path / "locomo-mem0.json"
    write_verification(left, suite="locomo30", backend="kontext", model="gpt-5-mini")
    write_verification(right, suite="locomo30", backend="legacy-mem0-offline", model="gpt-5.1")

    try:
        module.build_comparison_report([left, right], allow_mixed=False)
    except ValueError as exc:
        assert "mixed answerer/judge models" in str(exc)
    else:
        raise AssertionError("expected mixed models to be rejected")


def test_backend_compare_report_outputs_sanitized_side_by_side_rows(tmp_path: Path):
    module = load_module()
    locomo_k = tmp_path / "locomo-kontext.json"
    locomo_m = tmp_path / "locomo-mem0.json"
    write_verification(locomo_k, suite="locomo30", backend="kontext", passed=27)
    write_verification(locomo_m, suite="locomo30", backend="legacy-mem0-offline", passed=24)

    report = module.build_comparison_report([locomo_k, locomo_m], allow_mixed=False)
    rendered = json.dumps(report)

    assert report["ok"] is True
    assert report["mode"] == "judged-backend-comparison"
    assert report["suites"]["locomo30"]["delta_kontext_minus_mem0"] == 0.1
    assert report["rows"][0]["confidence"] in {"high", "medium-high"}
    assert "retrieved_memories_by_top_k" not in rendered
    assert "ground_truth_answer" not in rendered
    assert "sk-" not in rendered
