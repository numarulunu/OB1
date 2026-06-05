
from __future__ import annotations

import importlib.util
import json
from types import SimpleNamespace
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


def test_summarize_reliability_keeps_first_satisfying_metrics_without_raw_rows():
    module = load_shadow_report()
    payload = {
        "ok": True,
        "case_count": 2,
        "top_k": 5,
        "profiles": {
            "codex": {
                "ok": True,
                "services": {
                    "kontext": {
                        "init_ok": True,
                        "tools": ["search"],
                        "latency": {"avg_ms": 10},
                        "errors": [],
                        "eval": {
                            "summary": {"passed": 2, "first_satisfying_mrr": 1.0},
                            "cases": [{"top_rows": [{"memory": "private raw text"}]}],
                            "failed_cases": [],
                        },
                    }
                },
                "comparison": {
                    "pair": ["kontext", "mem0"],
                    "cases": 2,
                    "left_first_satisfying_mrr": 1.0,
                    "right_first_satisfying_mrr": 0.5,
                    "mean_first_satisfying_mrr": 0.75,
                    "left_better_satisfying_cases": ["case-a"],
                    "right_better_satisfying_cases": [],
                    "equal_satisfying_cases": 1,
                    "no_overlap_cases": [],
                },
            }
        },
    }

    summary = module.summarize_reliability(payload)

    comparison = summary["profiles"]["codex"]["comparison"]
    assert comparison["left_first_satisfying_mrr"] == 1.0
    assert comparison["right_first_satisfying_mrr"] == 0.5
    assert comparison["left_better_satisfying_cases"] == ["case-a"]
    assert "cases" not in summary["profiles"]["codex"]["services"]["kontext"]
    assert "private raw text" not in json.dumps(summary)

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


def test_run_sync_dry_run_uses_public_api_not_mem0_lexical_db(tmp_path, monkeypatch):
    module = load_shadow_report()
    captured: dict[str, object] = {}

    def fake_run(cmd, cwd, env, text, capture_output, timeout):
        captured["cmd"] = cmd
        captured["env"] = env
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "report": {"unchanged": 3},
                    "sync": {
                        "dry_run": True,
                        "metadata": {
                            "refresh_existing_limit": 250,
                            "refresh_existing_offset": 0,
                            "refreshed_existing_rows": 250,
                            "refreshed_stale_rows": 2,
                        },
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    payload = module.run_sync_dry_run(
        {
            "MEM0_API_KEY": "test-key",
            "MEM0_USER_ID": "ionut",
            "MEM0_BASE_URL": "https://mem0-api.example",
            "MEM0_LEXICAL_DATABASE_URL": "postgresql://postgres/not-visible-from-kontext",
        }
    )

    assert payload["ok"] is True
    assert captured["env"]["MEM0_LEXICAL_DATABASE_URL"] == ""
    cmd = captured["cmd"]
    assert "MEM0_LEXICAL_DATABASE_URL" in cmd
    assert cmd[-4:] == ["--refresh-existing-limit", "250", "--refresh-existing-offset", "0"]
    assert payload["refresh_existing"] == {
        "limit": 250,
        "offset": 0,
        "fetched": 250,
        "stale": 2,
    }


def test_run_exact_id_freshness_uses_bounded_cli_args(tmp_path, monkeypatch):
    module = load_shadow_report()
    captured: dict[str, object] = {}

    def fake_run(cmd, cwd, env, text, capture_output, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "checked": 25}), stderr="")

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "EXACT_ID_FRESHNESS_LIMIT", 25)
    monkeypatch.setattr(module, "EXACT_ID_FETCH_TIMEOUT", 9)

    payload = module.run_exact_id_freshness(
        {"MEM0_API_KEY": "test-key", "MEM0_USER_ID": "ionut", "MEM0_BASE_URL": "https://mem0-api.example"}
    )

    assert payload["ok"] is True
    assert captured["timeout"] == module.EXACT_ID_PROCESS_TIMEOUT
    cmd = captured["cmd"]
    assert cmd[-7:] == [
        "kontext_v2.exact_id_freshness_cli",
        "--limit",
        "25",
        "--offset",
        "0",
        "--fetch-timeout",
        "9",
    ]


def test_run_exact_id_freshness_uses_and_updates_rolling_offset_state(tmp_path, monkeypatch):
    module = load_shadow_report()
    captured: dict[str, object] = {}
    state_file = tmp_path / "exact-id-freshness-state.json"
    state_file.write_text(json.dumps({"next_scan_offset": 50}), encoding="utf-8")

    def fake_run(cmd, cwd, env, text, capture_output, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "checked": 25,
                    "scan_offset": 50,
                    "next_scan_offset": 75,
                    "total_rows": 100,
                    "scan_limit": 25,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "EXACT_ID_FRESHNESS_STATE_FILE", state_file, raising=False)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "EXACT_ID_FRESHNESS_LIMIT", 25)
    monkeypatch.setattr(module, "EXACT_ID_FETCH_TIMEOUT", 9)

    payload = module.run_exact_id_freshness(
        {"MEM0_API_KEY": "test-key", "MEM0_USER_ID": "ionut", "MEM0_BASE_URL": "https://mem0-api.example"}
    )

    cmd = captured["cmd"]
    assert cmd[cmd.index("--offset") + 1] == "50"
    assert payload["scan_offset"] == 50
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_scan_offset"] == 50
    assert state["next_scan_offset"] == 75
    assert state["limit"] == 25
    assert state["checked"] == 25
    assert state["total_rows"] == 100
