from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_provider_cooldown_guard.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_provider_cooldown_guard", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_report(path: Path, **overrides):
    payload = {
        "ok": False,
        "mode": "openai-compatible-judged-benchmark-run-failed",
        "reason": "provider request failed",
        "error_status": 429,
        "completed_calls": 0,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_guard_blocks_recent_zero_call_provider_429(tmp_path):
    module = load_module()
    write_report(tmp_path / "beam-paid-run.failed-429.json")

    report = module.build_report(tmp_path, 60, "NO_SUCH_OVERRIDE")

    assert report["ok"] is False
    assert report["runs_model_calls"] is False
    assert report["recent_429_failures"] == 1
    assert report["latest_failures"][0]["path_name"] == "beam-paid-run.failed-429.json"
    assert report["latest_failures"][0]["completed_calls"] == 0


def test_guard_ignores_success_non_429_and_completed_call_reports(tmp_path):
    module = load_module()
    write_report(tmp_path / "success.json", ok=True, error_status=429)
    write_report(tmp_path / "tls.json", reason="provider request failed", error_status=0)
    write_report(tmp_path / "partial.json", reason="provider request failed", error_status=429, completed_calls=3)

    report = module.build_report(tmp_path, 60, "NO_SUCH_OVERRIDE")

    assert report["ok"] is True
    assert report["recent_429_failures"] == 0


def test_guard_allows_explicit_override(tmp_path, monkeypatch):
    module = load_module()
    write_report(tmp_path / "beam-paid-run.failed-429.json")
    monkeypatch.setenv("KONTEXT_OVERRIDE_PROVIDER_429_COOLDOWN", "YES")

    report = module.build_report(tmp_path, 60, "KONTEXT_OVERRIDE_PROVIDER_429_COOLDOWN")

    assert report["ok"] is True
    assert report["override_active"] is True
    assert report["recent_429_failures"] == 0


def test_guard_cli_writes_report_and_exits_blocked(tmp_path, capsys):
    module = load_module()
    write_report(tmp_path / "beam-paid-run.failed-429.json")
    output = tmp_path / "guard.json"

    code = module.main(["--reports-dir", str(tmp_path), "--cooldown-minutes", "60", "--output", str(output)])
    rendered = output.read_text(encoding="utf-8")

    assert code == 2
    assert json.loads(rendered)["ok"] is False
    assert "beam-paid-run.failed-429.json" in rendered
    assert os.environ.get("OPENAI_API_KEY") is None or "sk-" not in rendered
