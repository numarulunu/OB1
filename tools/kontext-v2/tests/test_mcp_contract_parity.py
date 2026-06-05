from kontext_v2.mcp_bridge import build_mcp_profiles
from kontext_v2.mcp_server import list_tools, normalize_search_args
from kontext_v2.mcp_bridge import _compact_category_memory_row, _compact_category_row


def test_read_only_tool_surface_matches_mem0_names():
    names = {tool["name"] for tool in list_tools(write_enabled=False)}

    assert {"search", "fetch", "ingestion_status", "project_search", "project_timeline", "project_fetch", "project_file_context"} <= names
    assert "ingest_exchange" not in names


def test_tool_definitions_include_input_schema():
    tools = {tool["name"]: tool for tool in list_tools(write_enabled=False)}

    assert tools["search"]["inputSchema"]["type"] == "object"
    assert "query" in tools["search"]["inputSchema"]["required"]
    assert tools["fetch"]["inputSchema"]["properties"]["id"]["type"] == "string"
    assert tools["ingestion_status"]["inputSchema"]["type"] == "object"


def test_search_args_match_mem0_filter_shape():
    args = normalize_search_args(
        {
            "query": "memory architecture",
            "top_k": 100,
            "domains": ["ai", "systems"],
            "memory_tiers": ["active"],
            "memory_types": ["project_state"],
            "current_statuses": ["active"],
        }
    )

    assert args.query == "memory architecture"
    assert args.top_k == 50
    assert args.domains == ["ai", "systems"]
    assert args.memory_tiers == ["active"]
    assert args.memory_types == ["project_state"]
    assert args.current_statuses == ["active"]


def test_top_k_zero_clamps_to_one():
    args = normalize_search_args({"query": "memory architecture", "top_k": 0})

    assert args.top_k == 1


def test_filter_normalization_handles_scalar_and_dict_inputs():
    args = normalize_search_args(
        {
            "query": "memory architecture",
            "domains": 3,
            "memory_tiers": {"tier": "active"},
            "memory_types": "project_state",
        }
    )

    assert args.domains == ["3"]
    assert args.memory_tiers == []
    assert args.memory_types == ["project_state"]


def test_write_tool_definitions_match_mem0_shape():
    tools = {tool["name"]: tool for tool in list_tools(write_enabled=True)}

    assert {"save", "update", "delete", "extract_memories", "ingest_exchange", "submit_memory_override", "flag_memory"} <= set(tools)
    assert {"upsert_category", "assign_category", "unassign_category", "delete_category"}.isdisjoint(tools)
    assert tools["save"]["inputSchema"]["properties"]["content"]["type"] == "string"
    assert tools["update"]["inputSchema"]["properties"]["reason"]["type"] == "string"
    assert tools["delete"]["inputSchema"]["properties"]["id"]["type"] == "string"
    assert tools["extract_memories"]["inputSchema"]["properties"]["messages"]["type"] == "array"
    assert tools["ingest_exchange"]["inputSchema"]["properties"]["context_messages"]["type"] == "array"
    assert tools["submit_memory_override"]["inputSchema"]["properties"]["content"]["type"] == "string"
    assert tools["flag_memory"]["inputSchema"]["properties"]["reason"]["type"] == "string"


def test_category_mutators_require_explicit_category_write_gate():
    tools = {
        tool["name"]
        for tool in list_tools(write_enabled=True, dry_run_write_enabled=True, category_write_enabled=True)
    }

    assert {"upsert_category", "assign_category", "unassign_category", "delete_category"} <= tools


def test_dry_run_write_tool_surface_exposes_mem0_writes_without_category_mutators():
    tools = {tool["name"]: tool for tool in list_tools(write_enabled=False, dry_run_write_enabled=True)}

    assert {"save", "update", "delete", "extract_memories", "ingest_exchange", "submit_memory_override", "flag_memory"} <= set(tools)
    assert {"upsert_category", "assign_category", "unassign_category", "delete_category"}.isdisjoint(tools)
    assert tools["save"]["inputSchema"]["properties"]["content"]["type"] == "string"
    assert tools["delete"]["inputSchema"]["properties"]["reason"]["type"] == "string"


def test_project_tool_definitions_include_input_schema():
    tools = {tool["name"]: tool for tool in list_tools(write_enabled=False)}

    assert tools["project_search"]["inputSchema"]["properties"]["query"]["type"] == "string"
    assert tools["project_search"]["inputSchema"]["properties"]["project"]["type"] == "string"
    assert tools["project_search"]["inputSchema"]["properties"]["project_root"]["type"] == "string"
    assert tools["project_search"]["inputSchema"]["properties"]["cwd"]["type"] == "string"
    assert tools["project_fetch"]["inputSchema"]["properties"]["id"]["type"] == "string"
    assert tools["project_timeline"]["inputSchema"]["properties"]["before"]["type"] == "integer"
    assert tools["project_timeline"]["inputSchema"]["properties"]["project"]["type"] == "string"
    assert tools["project_file_context"]["inputSchema"]["properties"]["file_path"]["type"] == "string"
    assert tools["project_file_context"]["inputSchema"]["properties"]["project_root"]["type"] == "string"


def test_category_tool_schema_limits_context_growth():
    tools = {tool["name"]: tool for tool in list_tools(write_enabled=False)}

    assert tools["list_category_memories"]["inputSchema"]["properties"]["limit"]["maximum"] == 50


def test_mcp_category_rows_are_compact_for_agent_clients():
    category = _compact_category_row(
        {
            "id": "cat-1",
            "slug": "systems",
            "name": "Systems",
            "description": "Infrastructure and memory systems.",
            "count": 1000,
            "entries": [f"memory-{idx}" for idx in range(1000)],
            "stale": 25,
            "uses": 7,
            "sources": {"auto": 1000},
        }
    )
    memory = _compact_category_memory_row(
        {
            "external_mem0_id": "memory-1",
            "title": "Compact header",
            "metadata": {"domains": ["ai", "systems"], "raw_notes": "x" * 5000},
            "memory_type": "project_state",
            "current_status": "active",
            "memory_tier": "active",
            "signal_strength": 9,
            "source": "auto",
        }
    )

    assert "entries" not in category
    assert category["sample_entries"] == ["memory-0", "memory-1", "memory-2", "memory-3", "memory-4"]
    assert category["entries_truncated"] == 995
    assert "metadata" not in memory
    assert memory["domains"] == ["ai", "systems"]


def test_mem0_compat_profile_can_be_configured_separately_for_cutover_canary():
    profiles = build_mcp_profiles(
        {
            "KONTEXT_MCP_MEM0_TOKEN": "mem0-cutover-token",
            "KONTEXT_MCP_CODEX_TOKEN": "codex-token",
        },
        write_enabled=True,
    )

    assert profiles["mem0-cutover-token"].name == "mem0"
    assert profiles["mem0-cutover-token"].can_write is True
    assert profiles["mem0-cutover-token"].can_dry_run_write is True
    assert profiles["codex-token"].name == "codex"


def test_profile_write_allowlist_enables_only_named_canary_profile():
    profiles = build_mcp_profiles(
        {
            "KONTEXT_MCP_MEM0_TOKEN": "mem0-canary-token",
            "KONTEXT_MCP_CODEX_TOKEN": "codex-token",
            "KONTEXT_MCP_CLAUDE_TOKEN": "claude-token",
            "KONTEXT_MCP_WRITE_PROFILES": "mem0",
        },
        write_enabled=False,
        dry_run_write_enabled=True,
    )

    assert profiles["mem0-canary-token"].name == "mem0"
    assert profiles["mem0-canary-token"].can_write is True
    assert profiles["mem0-canary-token"].can_dry_run_write is True
    assert profiles["mem0-canary-token"].can_write_categories is False
    assert profiles["codex-token"].can_write is False
    assert profiles["codex-token"].can_dry_run_write is True
    assert profiles["claude-token"].can_write is False
    assert profiles["claude-token"].can_dry_run_write is True


def test_profile_write_allowlist_implies_write_tools_for_only_that_profile():
    profiles = build_mcp_profiles(
        {
            "KONTEXT_MCP_MEM0_TOKEN": "mem0-canary-token",
            "KONTEXT_MCP_CODEX_TOKEN": "codex-token",
            "KONTEXT_MCP_WRITE_ENABLED_PROFILES": "mem0",
        },
        write_enabled=False,
        dry_run_write_enabled=True,
    )

    mem0_tools = {
        tool["name"]
        for tool in list_tools(
            write_enabled=profiles["mem0-canary-token"].can_write,
            dry_run_write_enabled=profiles["mem0-canary-token"].can_dry_run_write,
        )
    }
    codex_tools = {
        tool["name"]
        for tool in list_tools(
            write_enabled=profiles["codex-token"].can_write,
            dry_run_write_enabled=profiles["codex-token"].can_dry_run_write,
        )
    }

    assert "save" in mem0_tools
    assert "upsert_category" not in mem0_tools
    assert "save" in codex_tools
    assert "upsert_category" not in codex_tools


def test_mcp_category_write_allowlist_is_separate_from_memory_write_allowlist():
    profiles = build_mcp_profiles(
        {
            "KONTEXT_MCP_MEM0_TOKEN": "mem0-canary-token",
            "KONTEXT_MCP_CODEX_TOKEN": "codex-token",
            "KONTEXT_MCP_WRITE_PROFILES": "mem0,codex",
            "KONTEXT_MCP_CATEGORY_WRITE_PROFILES": "codex",
        },
        write_enabled=False,
        dry_run_write_enabled=True,
    )

    assert profiles["mem0-canary-token"].can_write is True
    assert profiles["mem0-canary-token"].can_write_categories is False
    assert profiles["codex-token"].can_write is True
    assert profiles["codex-token"].can_write_categories is True
