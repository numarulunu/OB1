from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "beam_diagnostics_gate.py"


def load_module():
    spec = importlib.util.spec_from_file_location("beam_diagnostics_gate", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def report_payload(top_k50: float = 0.75, top_k200: float = 1.0, mrr50: float = 0.0522) -> dict:
    return {
        "dataset": "beam_10M",
        "dropped": True,
        "summary": {
            "question_count": 8,
            "missing_evidence_count": 0,
            "first_hit_ranks": [74, 11, 39, 37, 73, 31, 5, 24],
            "hits_at_k": {
                "50": {"passed": round(top_k50 * 8), "total": 8, "rate": top_k50, "mrr": mrr50},
                "200": {"passed": round(top_k200 * 8), "total": 8, "rate": top_k200, "mrr": 0.0556},
            },
        },
        "diagnostics": [{"question_id": "8-q-1-instruction_following", "rows": [{"features": {"lexical_own": 7.65}}]}],
    }


def test_beam_diagnostics_gate_accepts_current_cross_encoder_floor(tmp_path):
    module = load_module()
    report = tmp_path / "beam-cross.json"
    write_json(report, report_payload())

    result = module.build_gate(
        report,
        label="cross",
        min_top_k50=0.75,
        min_top_k200=1.0,
        min_mrr50=0.05,
        require_dropped=True,
    )

    assert result["ok"] is True
    assert result["mode"] == "beam-diagnostics-gate"
    assert result["runs_model_calls"] is False
    assert result["label"] == "cross"
    assert result["summary"]["top_k50_rate"] == 0.75
    assert result["gates"]["top_k50"]["ok"] is True
    assert result["gates"]["top_k200"]["ok"] is True
    assert result["gates"]["mrr50"]["ok"] is True
    assert result["gates"]["raw_payload"]["ok"] is True
    assert result["blocked_by"] == []


def test_beam_diagnostics_gate_rejects_regressed_top_k50(tmp_path):
    module = load_module()
    report = tmp_path / "beam-cross.json"
    write_json(report, report_payload(top_k50=0.5))

    result = module.build_gate(report, min_top_k50=0.75, min_top_k200=1.0, min_mrr50=0.05)

    assert result["ok"] is False
    assert "top_k50 below floor" in result["blocked_by"]
    assert result["gates"]["top_k50"]["actual"] == 0.5


def test_beam_diagnostics_gate_rejects_raw_payload_keys(tmp_path):
    module = load_module()
    report = tmp_path / "beam-cross.json"
    payload = report_payload()
    payload["diagnostics"][0]["raw_question"] = "hidden raw text"
    write_json(report, payload)

    result = module.build_gate(report, min_top_k50=0.75, min_top_k200=1.0, min_mrr50=0.05)

    assert result["ok"] is False
    assert "raw payload keys present" in result["blocked_by"]
    assert result["gates"]["raw_payload"]["hit_count"] == 1
    assert "hidden raw text" not in json.dumps(result)


def test_cli_writes_gate_report(tmp_path, capsys):
    module = load_module()
    report = tmp_path / "beam-cross.json"
    output = tmp_path / "gate.json"
    write_json(report, report_payload())

    code = module.main(
        [
            "--report",
            str(report),
            "--label",
            "cross",
            "--min-top-k50",
            "0.75",
            "--min-top-k200",
            "1.0",
            "--min-mrr50",
            "0.05",
            "--require-dropped",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(capsys.readouterr().out)["runs_model_calls"] is False
