import json
import subprocess
import sys

from stale_config_guard import check_config_files


def test_stale_config_guard_accepts_mem0_only_codex_and_claude_configs(tmp_path):
    codex_config = tmp_path / 'config.toml'
    codex_hooks = tmp_path / 'hooks.json'
    claude_mcp = tmp_path / '.mcp.json'
    claude_settings = tmp_path / 'settings.json'
    codex_config.write_text('[features]\nhooks = true\n\n[mcp_servers.mem0]\nurl = "https://example.test/mcp/token"\n', encoding='utf-8')
    codex_hooks.write_text(json.dumps({'hooks': {'SessionStart': [{'hooks': [{'command': 'python mem0_context_hook.py session_start'}]}]}}), encoding='utf-8')
    claude_mcp.write_text(json.dumps({'mcpServers': {'mem0': {'type': 'http', 'url': 'https://example.test/mcp/token'}}}), encoding='utf-8')
    claude_settings.write_text(json.dumps({'hooks': {'UserPromptSubmit': [{'hooks': [{'command': 'python mem0_context_hook.py user_prompt'}]}]}}), encoding='utf-8')

    report = check_config_files([codex_config, codex_hooks, claude_mcp, claude_settings])

    assert report['ok'] is True
    assert report['errors'] == []


def test_stale_config_guard_rejects_kontext_tokenomy_and_deprecated_hooks(tmp_path):
    codex_config = tmp_path / 'config.toml'
    claude_mcp = tmp_path / '.mcp.json'
    codex_config.write_text('[features]\ncodex_hooks = true\n\n[mcp_servers.kontext]\nurl = "https://example.test"\n', encoding='utf-8')
    claude_mcp.write_text(json.dumps({'mcpServers': {'tokenomy': {'type': 'http', 'url': 'https://example.test'}}}), encoding='utf-8')

    report = check_config_files([codex_config, claude_mcp])

    assert report['ok'] is False
    rendered = json.dumps(report, ensure_ascii=False).lower()
    assert 'codex_hooks' in rendered
    assert 'kontext' in rendered
    assert 'tokenomy' in rendered


def test_stale_config_guard_cli_json(tmp_path):
    codex_config = tmp_path / 'config.toml'
    codex_config.write_text('[features]\nhooks = true\n\n[mcp_servers.mem0]\nurl = "https://example.test/mcp/token"\n', encoding='utf-8')

    result = subprocess.run(
        [sys.executable, 'tools/mem0-remote-mcp/stale_config_guard.py', '--path', str(codex_config), '--json'],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload['ok'] is True
