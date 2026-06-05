from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "regression_canary.py"
spec = importlib.util.spec_from_file_location("regression_canary", SCRIPT_PATH)
regression_canary = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(regression_canary)


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_regression_canary_passes_sanitized_reports(tmp_path: Path) -> None:
    report = _write_json(
        tmp_path / "expanded-eval.json",
        {
            "ok": True,
            "mode": "expanded-mcp-eval",
            "metrics": {"first_satisfying_mrr": 1.0, "avg_latency_ms": 700},
            "non_default_flags": [],
        },
    )

    result = regression_canary.evaluate_reports(
        [("expanded", report)],
        min_mrr=0.99,
        max_latency_ms=900,
    )

    assert result["ok"] is True
    assert result["reports_checked"] == 1
    assert result["benchmark_flags"]["active"] is False
    assert result["blockers"] == []


def test_regression_canary_fails_thresholds_and_tags_benchmark_flags(tmp_path: Path) -> None:
    report = _write_json(
        tmp_path / "benchmark-report.json",
        {
            "ok": True,
            "mode": "predict-only-sweep",
            "metrics": {"mrr": 0.5, "avg_search_latency_ms": 1200},
            "flags": {"semantic_tail": True, "cross_encoder": False},
        },
    )

    result = regression_canary.evaluate_reports(
        [("beam", report)],
        min_mrr=0.99,
        max_latency_ms=900,
    )

    assert result["ok"] is False
    assert result["benchmark_flags"]["active"] is True
    assert result["benchmark_flags"]["labels"] == ["beam:semantic_tail"]
    assert "beam mrr 0.5000 below 0.9900" in result["blockers"]
    assert "beam latency 1200.0ms above 900.0ms" in result["blockers"]
