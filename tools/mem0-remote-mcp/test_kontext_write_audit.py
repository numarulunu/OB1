from __future__ import annotations

import json

from kontext_write_audit import (
    KontextWriteAuditConfig,
    build_write_audit_row,
    log_kontext_write_audit,
)


def test_build_write_audit_row_is_sanitized_and_tracks_apply_result():
    row = build_write_audit_row(
        origin="codex",
        tool="save",
        arguments={
            "content": "private memory text must not render",
            "reason": "private reason must not render",
            "domains": ["ai", "systems"],
            "memory_type": "project_state",
            "current_status": "active",
            "memory_tier": "active",
        },
        mem0_result={"response": {"id": "mem0-id"}},
        kontext_response={
            "mode": "apply",
            "writes_applied": 1,
            "results": [{"action": "save", "id": "kontext-id"}],
            "proposal": {"content_preview": "private memory text must not render"},
        },
        kontext_latency_ms=12.3456,
        kontext_error="",
        fetch_response={"id": "kontext-id"},
        fetch_error="",
    )
    rendered = json.dumps(row, sort_keys=True)

    assert row["event"] == "kontext_write_audit"
    assert row["origin"] == "codex"
    assert row["tool"] == "save"
    assert row["content_hash"]
    assert row["reason_hash"]
    assert row["mem0_id"] == "mem0-id"
    assert row["kontext_ids"] == ["kontext-id"]
    assert row["kontext_mode"] == "apply"
    assert row["writes_applied"] == 1
    assert row["read_after_write_checked"] is True
    assert row["read_after_write_ok"] is True
    assert row["metadata"] == {
        "domains": ["ai", "systems"],
        "memory_type": "project_state",
        "current_status": "active",
        "memory_tier": "active",
    }
    assert "private memory text" not in rendered
    assert "private reason" not in rendered
    assert "content_preview" not in rendered


def test_log_kontext_write_audit_writes_apply_row_and_fetches_written_id(tmp_path):
    log_path = tmp_path / "write-audit.jsonl"
    calls = []

    def fake_rpc(config, tool, arguments):
        calls.append((tool, dict(arguments)))
        if tool == "save":
            return {"mode": "apply", "writes_applied": 1, "results": [{"action": "save", "id": "kontext-id"}]}, 4.2, ""
        if tool == "fetch":
            return {"id": "kontext-id"}, 1.5, ""
        raise AssertionError(f"unexpected tool: {tool}")

    ok = log_kontext_write_audit(
        KontextWriteAuditConfig(
            enabled=True,
            mcp_url="https://kontext.example/mcp/{token}",
            token="secret-token",
            log_path=str(log_path),
        ),
        origin="codex",
        tool="save",
        arguments={"content": "private write content", "domains": ["ai"]},
        mem0_result={"response": {"id": "mem0-id"}},
        rpc_func=fake_rpc,
    )

    assert ok is True
    assert [call[0] for call in calls] == ["save", "fetch"]
    assert calls[1][1] == {"id": "kontext-id"}
    row = json.loads(log_path.read_text(encoding="utf-8"))
    rendered = json.dumps(row, sort_keys=True)
    assert row["tool"] == "save"
    assert row["kontext_ids"] == ["kontext-id"]
    assert row["read_after_write_ok"] is True
    assert "private write content" not in rendered
    assert "secret-token" not in rendered


def test_log_kontext_write_audit_dry_run_row_does_not_fetch(tmp_path):
    log_path = tmp_path / "write-audit.jsonl"
    calls = []

    def fake_rpc(config, tool, arguments):
        calls.append(tool)
        return {"mode": "dry_run", "writes_applied": 0, "results": [{"action": "save", "id": "dry-id"}]}, 2.0, ""

    ok = log_kontext_write_audit(
        KontextWriteAuditConfig(
            enabled=True,
            mcp_url="https://kontext.example/mcp/{token}",
            token="secret-token",
            log_path=str(log_path),
        ),
        origin="codex",
        tool="save",
        arguments={"content": "private dry-run content"},
        mem0_result={"response": {"id": "mem0-id"}},
        rpc_func=fake_rpc,
    )

    assert ok is True
    assert calls == ["save"]
    row = json.loads(log_path.read_text(encoding="utf-8"))
    assert row["kontext_mode"] == "dry_run"
    assert row["writes_applied"] == 0
    assert row["read_after_write_checked"] is False
    assert row["read_after_write_ok"] is False
