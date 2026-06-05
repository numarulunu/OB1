import json
import importlib.util
import subprocess
import sys


HOOK = 'tools/mem0-remote-mcp/client_hooks/mem0_context_hook.py'


def load_hook_module():
    spec = importlib.util.spec_from_file_location('mem0_context_hook_under_test', HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_hook(mode, stdin='{}'):
    result = subprocess.run(
        [sys.executable, HOOK, mode],
        input=stdin,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_session_start_hook_suppresses_output_to_avoid_context_clutter():
    payload = run_hook('session_start')

    assert payload == {'suppressOutput': True}


def test_compact_session_start_hook_suppresses_output_to_avoid_context_clutter():
    payload = run_hook('session_start', json.dumps({'source': 'compact'}))

    assert payload == {'suppressOutput': True}


def test_user_prompt_hook_suppresses_output_to_avoid_context_clutter():
    payload = run_hook('user_prompt')

    assert payload == {'suppressOutput': True}


def test_unknown_hook_mode_suppresses_output():
    payload = run_hook('unknown')

    assert payload == {'suppressOutput': True}


def test_heartbeat_url_is_derived_from_mcp_profile_url_without_printing_token():
    module = load_hook_module()

    assert module.heartbeat_url_from_mcp_url('https://memory.example/mcp/private-token') == 'https://memory.example/hook-heartbeat/private-token'
    assert module.heartbeat_url_from_mcp_url('https://memory.example/not-mcp/private-token') == ''


def test_codex_mcp_url_prefers_kontext_over_mem0_compatibility(tmp_path):
    module = load_hook_module()
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.mem0]
url = "https://legacy.example/mcp/legacy-token"

[mcp_servers.kontext]
url = "https://kontext.example/api/v2/mcp/kontext-token"
""",
        encoding="utf-8",
    )

    assert module.read_codex_mcp_url(config) == "https://kontext.example/api/v2/mcp/kontext-token"


def test_claude_mcp_url_prefers_kontext_from_claude_json_over_legacy_mem0(tmp_path):
    module = load_hook_module()
    config = tmp_path / ".claude.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mem0": {"url": "https://legacy.example/mcp/legacy-token"},
                    "kontext": {"url": "https://kontext.example/api/v2/mcp/kontext-token"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert module.read_claude_mcp_url(config) == "https://kontext.example/api/v2/mcp/kontext-token"


def test_project_observation_url_is_derived_from_mcp_profile_url_without_printing_token():
    module = load_hook_module()

    assert module.project_observation_url_from_mcp_url('https://memory.example/mcp/private-token') == 'https://memory.example/project-observation/private-token'
    assert module.project_observation_url_from_mcp_url('https://memory.example/not-mcp/private-token') == ''


def test_maintenance_status_url_is_derived_from_mcp_profile_url_without_printing_token():
    module = load_hook_module()

    assert module.maintenance_status_url_from_mcp_url('https://memory.example/mcp/private-token') == 'https://memory.example/maintenance-status/private-token'
    assert module.maintenance_status_url_from_mcp_url('https://memory.example/not-mcp/private-token') == ''


def test_hook_origin_is_inferred_from_home_config_path():
    module = load_hook_module()

    assert module.infer_origin_from_path('C:/Users/Gaming PC/.codex/mem0_context_hook.py') == 'codex'
    assert module.infer_origin_from_path('/root/.claude/mem0_context_hook.py') == 'claude'


def test_codex_hook_output_wraps_bounded_maintenance_reminder():
    module = load_hook_module()

    payload = module.wrap_context_for_client('session_start', {'additionalContext': 'Memory maintenance due'}, origin='codex')

    assert payload == {
        'hookSpecificOutput': {
            'hookEventName': 'SessionStart',
            'additionalContext': 'Memory maintenance due',
        }
    }


def test_claude_hook_output_wraps_bounded_maintenance_reminder():
    module = load_hook_module()

    payload = module.wrap_context_for_client('user_prompt', {'additionalContext': 'Memory maintenance due'}, origin='claude')

    assert payload == {'additionalContext': 'Memory maintenance due'}


def test_maintenance_reminder_from_status_is_compact_and_actionable():
    module = load_hook_module()

    reminder = module.maintenance_reminder_from_status(
        {
            'maintenance': {
                'due': True,
                'pending_flags': 12,
                'flag_types': {'stale_candidate': 7, 'delete_candidate': 5},
                'reasons': ['pending_flags_threshold'],
            }
        }
    )

    assert 'Memory maintenance due' in reminder
    assert '12 pending flags' in reminder
    assert 'ask Ionut before running dream cleanup' in reminder
    assert len(reminder) <= 360


def test_session_start_adds_reminder_only_when_maintenance_is_due(monkeypatch):
    module = load_hook_module()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return json.dumps({'maintenance': {'due': True, 'pending_flags': 11, 'flag_types': {'stale_candidate': 11}}}).encode('utf-8')

    def fake_urlopen(request, timeout):
        assert request.full_url == 'https://memory.example/maintenance-status/private-token'
        return FakeResponse()

    monkeypatch.setenv('MEM0_MAINTENANCE_STATUS_URL', 'https://memory.example/maintenance-status/private-token')
    monkeypatch.setattr(module.urllib.request, 'urlopen', fake_urlopen)

    payload = module.session_start({})

    assert '11 pending flags' in payload['additionalContext']
    assert 'private-token' not in payload['additionalContext']


def test_maintenance_status_falls_back_to_mcp_ingestion_status(monkeypatch):
    module = load_hook_module()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return json.dumps(
                {
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "maintenance": {
                                            "due": True,
                                            "pending_flags": 12,
                                            "flag_types": {"stale_candidate": 12},
                                        }
                                    }
                                ),
                            }
                        ]
                    }
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://kontext.example/api/v2/mcp/private-token"
        assert b'"ingestion_status"' in request.data
        return FakeResponse()

    monkeypatch.delenv("MEM0_MAINTENANCE_STATUS_URL", raising=False)
    monkeypatch.delenv("KONTEXT_MAINTENANCE_STATUS_URL", raising=False)
    monkeypatch.setenv("KONTEXT_MCP_URL", "https://kontext.example/api/v2/mcp/private-token")
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    status = module.fetch_maintenance_status("codex")

    assert status["maintenance"]["pending_flags"] == 12


def test_send_heartbeat_prefers_mcp_tool(tmp_path, monkeypatch):
    module = load_hook_module()
    calls = []

    def fake_call_mcp_tool(url, name, arguments):
        calls.append((url, name, arguments))
        return {"ok": True}

    monkeypatch.setenv("KONTEXT_MCP_URL", "https://kontext.example/api/v2/mcp/private-token")
    monkeypatch.setenv("KONTEXT_HOOK_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(module, "call_mcp_tool", fake_call_mcp_tool)

    module.send_heartbeat("session_start", {"source": "hook-test", "session_id": "private-marker"})

    assert calls == [
        (
            "https://kontext.example/api/v2/mcp/private-token",
            "hook_heartbeat",
            {"hook_type": "session_start", "source": "hook-test", "marker": "private-marker"},
        )
    ]


def test_heartbeat_is_throttled_per_origin_and_hook_type(tmp_path, monkeypatch):
    module = load_hook_module()
    monkeypatch.setenv("KONTEXT_HOOK_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KONTEXT_HOOK_HEARTBEAT_THROTTLE_SECONDS", "600")

    assert module.should_send_heartbeat("user_prompt", {"session_id": "one"}, "codex", now=1000.0) is True
    assert module.should_send_heartbeat("user_prompt", {"session_id": "two"}, "codex", now=1200.0) is False
    assert module.should_send_heartbeat("user_prompt", {"session_id": "three"}, "codex", now=1601.0) is True
    assert module.should_send_heartbeat("user_prompt", {"session_id": "four"}, "claude", now=1602.0) is True


def test_low_signal_post_tool_use_does_not_send_heartbeat_or_observation(tmp_path, monkeypatch):
    module = load_hook_module()
    monkeypatch.setenv("KONTEXT_HOOK_STATE_DIR", str(tmp_path))
    payload = {
        "tool_name": "shell_command",
        "tool_input": {"command": "Get-Content README.md", "workdir": "C:/Tools/OB1"},
        "tool_response": {"exit_code": 0},
    }

    assert module.should_send_heartbeat("post_tool_use", payload, "codex", now=1000.0) is False
    assert module.should_send_project_observation("post_tool_use", payload, "codex") is False


def test_significant_post_tool_use_still_sends_heartbeat_and_observation(tmp_path, monkeypatch):
    module = load_hook_module()
    monkeypatch.setenv("KONTEXT_HOOK_STATE_DIR", str(tmp_path))
    payload = {
        "tool_name": "shell_command",
        "tool_input": {"command": "python -m pytest tools\\mem0-remote-mcp -q", "workdir": "C:/Tools/OB1"},
        "tool_response": {"exit_code": 0},
    }

    assert module.should_send_heartbeat("post_tool_use", payload, "codex", now=1000.0) is True
    assert module.should_send_project_observation("post_tool_use", payload, "codex") is True


def test_post_tool_use_capture_is_compact_and_secret_safe():
    module = load_hook_module()

    payload = module.build_project_observation_payload(
        'post_tool_use',
        {
            'tool_name': 'shell_command',
            'cwd': 'C:/Tools/OB1',
            'tool_input': {'command': 'python -m pytest tools\\mem0-remote-mcp -q', 'workdir': 'C:/Tools/OB1'},
            'tool_response': {'exit_code': 0, 'stdout': 'raw output TOKEN=private-token', 'stderr': 'API_KEY=abc123'},
        },
        origin='codex',
    )
    rendered = json.dumps(payload, sort_keys=True)

    assert payload['event_type'] == 'post_tool_use'
    assert payload['tool_name'] == 'shell_command'
    assert payload['command_category'] == 'test'
    assert payload['status'] == 'passed'
    assert payload['commands'] == ['category:test']
    assert 'tools/mem0-remote-mcp' in payload['touched_paths']
    assert 'private-token' not in rendered
    assert 'abc123' not in rendered
    assert 'raw output' not in rendered


def test_post_tool_use_hook_suppresses_output_schema():
    payload = run_hook('post_tool_use', json.dumps({'tool_name': 'shell_command', 'tool_response': {'exit_code': 0}}))

    assert payload == {'suppressOutput': True}


def test_post_tool_use_hook_handles_empty_input():
    module = load_hook_module()

    payload_builder = module.build_project_observation_payload('post_tool_use', {}, origin='codex')
    payload = run_hook('post_tool_use', '{}')

    assert payload_builder['title'] == 'post_tool_use hook unknown'
    assert payload == {'suppressOutput': True}


def test_dockerfile_packages_client_hook_script():
    dockerfile = open('tools/mem0-remote-mcp/Dockerfile', encoding='utf-8').read()

    assert 'client_hooks' in dockerfile
