import ast
from pathlib import Path

from core import build_profile_specs
from server_status import audit_log_is_writable, health_payload


def test_profiles_are_split_by_token_and_permission():
    profiles = build_profile_specs(
        {
            'MEM0_API_KEY': 'shared-key',
            'MEM0_API_KEY_CODEX': 'codex-key',
            'MEM0_API_KEY_CLAUDE': 'claude-key',
            'MCP_READONLY_TOKEN': 'readonly-token',
            'MCP_CODEX_TOKEN': 'codex-token',
            'MCP_CLAUDE_TOKEN': 'claude-token',
        }
    )
    by_token = {profile.token: profile for profile in profiles}

    assert set(by_token) == {'readonly-token', 'codex-token', 'claude-token'}
    assert by_token['readonly-token'].can_write is True
    assert by_token['readonly-token'].can_ingest is True
    assert by_token['codex-token'].can_write is True
    assert by_token['codex-token'].can_ingest is True
    assert by_token['claude-token'].can_ingest is True
    assert by_token['claude-token'].api_key == 'claude-key'


def test_profile_tokens_must_be_unique():
    try:
        build_profile_specs(
            {
                'MEM0_API_KEY': 'shared-key',
                'MCP_READONLY_TOKEN': 'same-token',
                'MCP_CODEX_TOKEN': 'same-token',
            }
        )
    except RuntimeError as exc:
        assert 'tokens must be unique' in str(exc)
    else:
        raise AssertionError('expected duplicate token failure')


def test_chatgpt_profile_can_be_forced_read_only():
    profiles = build_profile_specs(
        {
            'MEM0_API_KEY': 'shared-key',
            'MCP_READONLY_TOKEN': 'readonly-token',
            'MCP_CHATGPT_CAN_WRITE': 'false',
        }
    )

    assert profiles[0].name == 'chatgpt'
    assert profiles[0].can_write is False
    assert profiles[0].can_ingest is False


def test_global_mem0_write_freeze_overrides_writer_profiles():
    profiles = build_profile_specs(
        {
            'MEM0_API_KEY': 'shared-key',
            'MCP_READONLY_TOKEN': 'readonly-token',
            'MCP_CODEX_TOKEN': 'codex-token',
            'MCP_CLAUDE_TOKEN': 'claude-token',
            'MCP_PERPLEXITY_TOKEN': 'perplexity-token',
            'MCP_PERPLEXITY_CAN_WRITE': 'true',
            'MEM0_MCP_WRITES_ENABLED': 'false',
        }
    )

    assert {profile.name for profile in profiles} == {'chatgpt', 'codex', 'claude', 'perplexity'}
    assert all(profile.can_write is False for profile in profiles)
    assert all(profile.can_ingest is False for profile in profiles)


def test_health_payload_reports_runtime_disable_without_secrets(tmp_path):
    payload = health_payload(
        profiles=['chatgpt', 'codex'],
        ingestion_enabled=True,
        audit_log=tmp_path / 'audit.jsonl',
        profile_permissions={'chatgpt': {'can_write': False}, 'codex': {'can_write': False}},
        runtime_enabled=False,
    )

    assert payload['runtime'] == {'enabled': False}
    rendered = str(payload).lower()
    assert 'token' not in rendered
    assert 'api_key' not in rendered


def test_perplexity_profile_is_split_and_read_only_by_default():
    profiles = build_profile_specs(
        {
            'MEM0_API_KEY': 'shared-key',
            'MEM0_API_KEY_PERPLEXITY': 'perplexity-key',
            'MCP_PERPLEXITY_TOKEN': 'perplexity-token',
        }
    )

    assert len(profiles) == 1
    assert profiles[0].name == 'perplexity'
    assert profiles[0].token == 'perplexity-token'
    assert profiles[0].api_key == 'perplexity-key'
    assert profiles[0].can_write is False
    assert profiles[0].can_ingest is False
    assert profiles[0].agent_id == 'perplexity-live-memory'


def test_perplexity_profile_can_be_explicitly_writer_enabled():
    profiles = build_profile_specs(
        {
            'MEM0_API_KEY': 'shared-key',
            'MCP_PERPLEXITY_TOKEN': 'perplexity-token',
            'MCP_PERPLEXITY_CAN_WRITE': 'true',
        }
    )

    assert profiles[0].name == 'perplexity'
    assert profiles[0].can_write is True
    assert profiles[0].can_ingest is True


def test_health_payload_includes_only_non_secret_monitoring_metadata(tmp_path):
    payload = health_payload(
        profiles=['chatgpt', 'codex'],
        ingestion_enabled=True,
        audit_log=tmp_path / 'audit.jsonl',
        profile_permissions={'chatgpt': {'can_write': False}, 'codex': {'can_write': False}},
    )

    assert payload['ok'] is True
    assert payload['service'] == 'ionut-memory-mcp'
    assert payload['profiles'] == ['chatgpt', 'codex']
    assert payload['ingestion']['enabled'] is True
    assert payload['writes'] == {'enabled': False}
    assert payload['profile_permissions'] == {
        'chatgpt': {'can_write': False},
        'codex': {'can_write': False},
    }
    assert payload['audit']['writable'] is True
    rendered = str(payload).lower()
    assert 'token' not in rendered
    assert 'api_key' not in rendered


def test_audit_log_is_writable_returns_false_for_directory_path(tmp_path):
    assert audit_log_is_writable(tmp_path) is False


def test_server_search_tool_exposes_retrieval_quality_filters():
    tree = ast.parse(Path(__file__).with_name('server.py').read_text(encoding='utf-8'))
    search_defs = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == 'search']

    assert len(search_defs) == 1
    arg_names = [arg.arg for arg in search_defs[0].args.args]
    assert arg_names == ['query', 'top_k', 'memory_tiers', 'domains', 'memory_types', 'current_statuses']


def test_server_guards_legacy_runtime_routes_when_disabled():
    text = Path('tools/mem0-remote-mcp/server.py').read_text(encoding='utf-8')
    tree = ast.parse(text)
    guarded = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        calls = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        guarded[node.name] = 'require_runtime_enabled' in calls

    assert guarded['handle_maintenance_status'] is True
    assert guarded['handle_hook_heartbeat'] is True
    assert guarded['handle_project_observation'] is True
    assert guarded['handle_streamable_http'] is True
