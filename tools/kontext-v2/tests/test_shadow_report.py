
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kontext_shadow_report.py"


def load_shadow_report():
    spec = importlib.util.spec_from_file_location("kontext_shadow_report", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

def test_run_exact_id_freshness_exists():
    module = load_shadow_report()
    assert hasattr(module, "run_exact_id_freshness")

def test_main_report_includes_exact_id_freshness_in_ok_gate(tmp_path, monkeypatch, capsys):
    module = load_shadow_report()
    monkeypatch.setattr(module, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(module, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(module, "utc_now", lambda: datetime(2026, 5, 15, 1, 2, 3, tzinfo=timezone.utc))
    monkeypatch.setattr(module, "http_json", lambda url: {"ok": True, "status": 200, "latency_ms": 1, "payload": {"ok": True}})
    monkeypatch.setattr(module, "run_reliability", lambda env: {"ok": True, "profiles": {}})
    monkeypatch.setattr(module, "run_sync_dry_run", lambda env: {"ok": True, "report": {"unchanged": 20}})
    monkeypatch.setattr(module, "run_exact_id_freshness", lambda env: {"ok": True, "checked": 77, "fresh": 77, "stale": 0})
    assert module.main() == 0
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["ok"] is True
    assert latest["exact_id_freshness"] == {"ok": True, "checked": 77, "fresh": 77, "stale": 0}
    assert "exact_id_freshness=true" in capsys.readouterr().out
