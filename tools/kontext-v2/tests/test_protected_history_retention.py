from __future__ import annotations

import json
from datetime import datetime, timezone

from kontext_v2.models import MemoryRecord


def test_protected_history_is_not_decayed_into_stale_dashboard_candidate() -> None:
    from kontext_v2.dashboard_snapshot import _decay

    old_stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    row = {
        "metadata": {"domains": ["relationships", "psychology"]},
        "memory_type": "formative_event",
        "current_status": "dormant",
        "memory_tier": "cold",
        "signal_strength": 9,
        "updated_at": old_stamp,
        "imported_at": old_stamp,
    }

    assert _decay(row, datetime(2026, 5, 26, tzinfo=timezone.utc)) < 0.3


def test_mcp_delete_and_cleanup_flags_do_not_apply_to_protected_history() -> None:
    from kontext_v2.mcp_bridge import McpProfile, _call_tool
    from test_mcp_bridge_write_tools import _FakeRepo, _FakeService

    repo = _FakeRepo()
    service = _FakeService(repo)
    profile = McpProfile(name="codex", token="token", can_write=True, can_dry_run_write=True)
    repo.memories["protected-history"] = MemoryRecord(
        external_mem0_id="protected-history",
        title="Protected historical context",
        text="Historical autobiographical context retained for explanation.",
        metadata={"domains": ["relationships", "psychology"]},
        memory_type="relationship_pattern",
        current_status="active",
        memory_tier="historical",
        signal_strength=9,
        source_hash="hash",
    )

    delete = json.loads(
        _call_tool(
            service,
            profile,
            "delete",
            {"id": "protected-history", "reason": "old unused memory"},
        )["content"][0]["text"]
    )
    flag = json.loads(
        _call_tool(
            service,
            profile,
            "flag_memory",
            {"id": "protected-history", "flag_type": "stale_candidate", "reason": "low use"},
        )["content"][0]["text"]
    )

    assert delete["mode"] == "protected"
    assert delete["writes_applied"] == 0
    assert delete["protected"] is True
    assert flag["mode"] == "protected"
    assert flag["writes_applied"] == 0
    assert flag["protected"] is True
    assert repo.flags == []
