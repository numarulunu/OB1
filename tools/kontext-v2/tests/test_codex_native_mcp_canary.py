from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "codex_native_mcp_canary.py"
SPEC = importlib.util.spec_from_file_location("codex_native_mcp_canary", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
codex_native_mcp_canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex_native_mcp_canary)


def test_build_codex_command_uses_safe_noninteractive_shape(tmp_path):
    command = codex_native_mcp_canary.build_codex_command("codex", Path("C:/Tools/OB1"), tmp_path / "out.txt")

    assert command[:4] == ["codex", "-a", "never", "exec"]
    assert "--sandbox" in command
    assert "danger-full-access" in command
    assert "--ephemeral" in command
    assert command[-1] == "-"


def test_resolve_codex_bin_accepts_explicit_path(tmp_path):
    fake = tmp_path / "codex.cmd"
    fake.write_text("@echo off\n", encoding="utf-8")

    assert codex_native_mcp_canary.resolve_codex_bin(str(fake)) == str(fake)


def test_prompt_keeps_output_sanitized_and_requires_temp_cleanup():
    prompt = codex_native_mcp_canary.build_prompt("unit-nonce")

    assert "Do not print secrets" in prompt
    assert "mem0.delete" in prompt
    assert "KONTEXT_WRITES_DRY_RUN_OK" in prompt
    assert "unit-nonce" in prompt


def test_parse_canary_result_uses_last_json_object():
    text = "noise {bad json}\n{" + \
        '"MEM0_CANARY_OK": true, ' + \
        '"MEM0_TEMP_DELETED": true, ' + \
        '"KONTEXT_CANARY_OK": true, ' + \
        '"KONTEXT_WRITES_DRY_RUN_OK": false}'

    markers = codex_native_mcp_canary.parse_canary_result(text)

    assert markers == {
        "MEM0_CANARY_OK": True,
        "MEM0_TEMP_DELETED": True,
        "KONTEXT_CANARY_OK": True,
        "KONTEXT_WRITES_DRY_RUN_OK": False,
    }


def test_default_mode_is_dry_run(capsys, tmp_path):
    exit_code = codex_native_mcp_canary.main(["--workdir", str(tmp_path), "--output-dir", str(tmp_path)])

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"mode": "dry_run"' in captured
    assert "--execute" in captured
