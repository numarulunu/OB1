from __future__ import annotations

import importlib.util
import json
import tarfile
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kontext_rollback_drill.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kontext_rollback_drill", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_archive(path: Path, compose_text: str = "services:\n  kontext:\n    image: kontext:latest\n") -> None:
    source = path.parent / "archive-src"
    source.mkdir()
    compose = source / "docker-compose.yml"
    compose.write_text(compose_text, encoding="utf-8")
    with tarfile.open(path, "w:gz") as archive:
        archive.add(compose, arcname="kontext/docker-compose.yml")


def test_rollback_drill_passes_with_health_artifacts_and_write_disabled(tmp_path):
    module = load_module()
    archive = tmp_path / "rollback.tgz"
    backup = tmp_path / "docker-compose.yml.bak"
    compose = tmp_path / "docker-compose.yml"
    make_archive(archive)
    backup.write_text("services:\n  kontext:\n    image: kontext:latest\n", encoding="utf-8")
    compose.write_text("services:\n  kontext:\n    image: kontext:latest\n", encoding="utf-8")

    report = module.build_rollback_drill_report(
        rollback_archive=archive,
        compose_backup=backup,
        current_compose=compose,
        mem0_health={"ok": True, "service": "ionut-memory-mcp", "secret": "must not render"},
        kontext_health={"ok": True, "mode": "mirror_read_only", "raw": "must not render"},
        mcp_status={"profile": "mem0", "write_enabled": False, "dry_run_write_enabled": True, "token": "must not render"},
        label="unit",
    )
    rendered = json.dumps(report, sort_keys=True)

    assert report["ok"] is True
    assert report["mode"] == "kontext-rollback-drill"
    assert report["runs_model_calls"] is False
    assert report["summary"] == {"pass": 6, "warn": 0, "fail": 0}
    assert report["checks"]["write_mode_disabled"]["status"] == "pass"
    assert report["checks"]["state_feature_flags_disabled"]["status"] == "pass"
    assert report["checks"]["rollback_artifacts"]["evidence"]["archive_readable"] is True
    assert "must not render" not in rendered
    assert str(tmp_path) not in rendered


def test_rollback_drill_fails_when_write_profile_is_still_enabled(tmp_path):
    module = load_module()
    archive = tmp_path / "rollback.tgz"
    backup = tmp_path / "docker-compose.yml.bak"
    compose = tmp_path / "docker-compose.yml"
    make_archive(archive)
    backup.write_text("services:\n  kontext:\n    image: kontext:latest\n", encoding="utf-8")
    compose.write_text("services:\n  kontext:\n    environment:\n      KONTEXT_MCP_WRITE_PROFILES: \"mem0\"\n", encoding="utf-8")

    report = module.build_rollback_drill_report(
        rollback_archive=archive,
        compose_backup=backup,
        current_compose=compose,
        mem0_health={"ok": True},
        kontext_health={"ok": True, "mode": "mirror_read_only"},
        mcp_status={"profile": "mem0", "write_enabled": True, "dry_run_write_enabled": True},
        label="unit",
    )

    assert report["ok"] is False
    assert report["checks"]["write_mode_disabled"]["status"] == "fail"
    assert "write mode is still enabled" in report["attention_items"]


def test_rollback_drill_fails_when_state_or_benchmark_flags_are_enabled(tmp_path):
    module = load_module()
    archive = tmp_path / "rollback.tgz"
    backup = tmp_path / "docker-compose.yml.bak"
    compose = tmp_path / "docker-compose.yml"
    make_archive(archive)
    backup.write_text("services:\n  kontext:\n    image: kontext:latest\n", encoding="utf-8")
    compose.write_text(
        "\n".join(
            [
                "services:",
                "  kontext:",
                "    environment:",
                "      KONTEXT_STATE_MODEL_ENABLED: \"1\"",
                "      KONTEXT_TYPED_STATE_V2: \"true\"",
                "      KONTEXT_BENCHMARK_STATE_MODEL_ENABLED: \"on\"",
                "",
            ]
        ),
        encoding="utf-8",
    )

    report = module.build_rollback_drill_report(
        rollback_archive=archive,
        compose_backup=backup,
        current_compose=compose,
        mem0_health={"ok": True},
        kontext_health={"ok": True, "mode": "mirror_read_only"},
        mcp_status={"profile": "mem0", "write_enabled": False, "dry_run_write_enabled": True},
        label="unit",
    )

    assert report["ok"] is False
    state_flags = report["checks"]["state_feature_flags_disabled"]
    assert state_flags["status"] == "fail"
    assert state_flags["evidence"]["enabled_flags"] == [
        "KONTEXT_BENCHMARK_STATE_MODEL_ENABLED",
        "KONTEXT_STATE_MODEL_ENABLED",
        "KONTEXT_TYPED_STATE_V2",
    ]
    assert "state, typed, or benchmark feature flags are still enabled" in report["attention_items"]


def test_rollback_drill_cli_writes_report(tmp_path, capsys):
    module = load_module()
    archive = tmp_path / "rollback.tgz"
    backup = tmp_path / "docker-compose.yml.bak"
    compose = tmp_path / "docker-compose.yml"
    output = tmp_path / "report.json"
    health = tmp_path / "health.json"
    mcp = tmp_path / "mcp.json"
    make_archive(archive)
    backup.write_text("services:\n  kontext:\n    image: kontext:latest\n", encoding="utf-8")
    compose.write_text("services:\n  kontext:\n    image: kontext:latest\n", encoding="utf-8")
    health.write_text(json.dumps({"mem0": {"ok": True}, "kontext": {"ok": True, "mode": "mirror_read_only"}}), encoding="utf-8")
    mcp.write_text(json.dumps({"profile": "mem0", "write_enabled": False, "dry_run_write_enabled": True}), encoding="utf-8")

    code = module.main(
        [
            "--rollback-archive",
            str(archive),
            "--compose-backup",
            str(backup),
            "--current-compose",
            str(compose),
            "--health-json",
            str(health),
            "--mcp-status-json",
            str(mcp),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(capsys.readouterr().out)["runs_model_calls"] is False
