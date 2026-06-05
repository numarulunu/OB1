from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient

from kontext_v2.http_api import build_app


def _rpc(client: TestClient, token: str, method: str, params: dict | None = None, request_id: int = 1):
    return client.post(
        f"/mcp/{token}",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def _tool_payload(response):
    assert response.status_code == 200
    return json.loads(response.json()["result"]["content"][0]["text"])


def test_write_enabled_bridge_applies_mem0_direct_write_tools_and_keeps_ingest_dry_run():
    token = "kontext-write-token"
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            write_enabled=True,
            mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token},
        )
    )
    secret = "secret-token-123"

    save = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {
                "name": "save",
                "arguments": {
                    "content": f"Kontext V2 should mirror Mem0 safely. TOKEN={secret}",
                    "domains": ["ai", "systems"],
                    "memory_type": "project_state",
                },
            },
        )
    )
    update = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {
                "name": "update",
                "arguments": {
                    "id": "memory-1",
                    "content": f"Kontext V2 update should stay hashed. TOKEN={secret}",
                    "reason": f"manual review TOKEN={secret}",
                },
            },
            request_id=2,
        )
    )
    delete = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {
                "name": "delete",
                "arguments": {
                    "id": "memory-2",
                    "reason": f"duplicate TOKEN={secret}",
                },
            },
            request_id=3,
        )
    )
    extract = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {
                "name": "extract_memories",
                "arguments": {
                    "messages": [{"role": "user", "content": f"Kontext V2 should mirror Mem0 safely. TOKEN={secret}"}],
                    "context_messages": [{"role": "assistant", "content": "Mem0 remains source of truth."}],
                },
            },
            request_id=4,
        )
    )
    ingest = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {"name": "ingest_exchange", "arguments": {"messages": [{"role": "user", "content": "Kontext V2 write path stays dry-run."}]}},
            request_id=5,
        )
    )
    override = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {
                "name": "submit_memory_override",
                "arguments": {
                    "content": f"Kontext V2 override should hash content. TOKEN={secret}",
                    "reason": f"manual review TOKEN={secret}",
                    "memory_type": "project_state",
                    "domains": ["ai", "systems"],
                },
            },
            request_id=6,
        )
    )
    flag = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {
                "name": "flag_memory",
                "arguments": {"id": "memory-1", "flag_type": "stale", "reason": f"bad TOKEN={secret}", "confidence": 2},
            },
            request_id=7,
        )
    )

    assert save["tool"] == "save"
    assert save["mode"] == "apply"
    assert save["writes_applied"] == 1
    assert update["tool"] == "update"
    assert update["mode"] == "apply"
    assert update["writes_applied"] == 1
    assert delete["tool"] == "delete"
    assert delete["mode"] == "apply"
    assert delete["writes_applied"] == 1
    assert extract["tool"] == "extract_memories"
    assert extract["mode"] == "dry_run"
    assert ingest["tool"] == "ingest_exchange"
    assert ingest["writes_applied"] == 0
    assert override["tool"] == "submit_memory_override"
    assert override["proposal"]["content_hash"]
    assert override["mode"] == "apply"
    assert override["writes_applied"] == 1
    assert flag["tool"] == "flag_memory"
    assert flag["flag"]["id"] == "memory-1"
    assert flag["flag"]["confidence"] == 1.0
    assert flag["mode"] == "apply"
    assert flag["writes_applied"] == 1

    rendered = json.dumps([save, update, delete, extract, ingest, override, flag], sort_keys=True)
    assert secret not in rendered


def test_dry_run_write_bridge_exposes_mem0_writes_without_category_mutators():
    token = "kontext-dry-run-token"
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            write_enabled=False,
            dry_run_write_enabled=True,
            mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token},
        )
    )

    listed = _rpc(client, token, "tools/list", {}, request_id=1)
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}

    assert {"save", "update", "delete", "extract_memories", "ingest_exchange", "submit_memory_override", "flag_memory"} <= names
    assert {"upsert_category", "assign_category", "unassign_category", "delete_category"}.isdisjoint(names)

    save = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {"name": "save", "arguments": {"content": "Dry-run only write exposure", "memory_type": "project_state"}},
            request_id=2,
        )
    )
    category = _rpc(
        client,
        token,
        "tools/call",
        {"name": "upsert_category", "arguments": {"slug": "dry-run-test", "name": "Dry Run Test"}},
        request_id=3,
    )

    assert save["tool"] == "save"
    assert save["mode"] == "dry_run"
    assert save["writes_applied"] == 0
    assert category.json()["error"]["code"] == -32602
    assert "unsupported tool" in category.json()["error"]["message"]


def test_read_only_bridge_dispatches_project_tools_as_safe_empty_results():
    token = "kontext-read-token"
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token},
        )
    )

    search = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {"name": "project_search", "arguments": {"query": "zzzz-not-found-bridge-test", "limit": 5}},
        )
    )
    timeline = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {"name": "project_timeline", "arguments": {"anchor_id": "missing-anchor", "before": 2, "after": 2}},
            request_id=2,
        )
    )
    fetch = _tool_payload(
        _rpc(client, token, "tools/call", {"name": "project_fetch", "arguments": {"id": "obs-1"}}, request_id=3)
    )
    file_context = _tool_payload(
        _rpc(
            client,
            token,
            "tools/call",
            {"name": "project_file_context", "arguments": {"file_path": "tools/kontext-v2/kontext_v2/no-such-bridge-file.py", "limit": 3}},
            request_id=4,
        )
    )

    assert search == {"rows": [], "count": 0}
    assert timeline == {"anchor_id": "missing-anchor", "rows": []}
    assert fetch == {"found": False, "id": "obs-1"}
    assert file_context == {"file_path": "tools/kontext-v2/kontext_v2/no-such-bridge-file.py", "titles": [], "rows": [], "recommend_full_file_read": True}


def test_read_only_bridge_rejects_write_tools():
    token = "kontext-read-token-2"
    client = TestClient(
        build_app(
            os.environ["KONTEXT_V2_DATABASE_URL"],
            mcp_env={"KONTEXT_MCP_CODEX_TOKEN": token},
        )
    )

    response = _rpc(
        client,
        token,
        "tools/call",
        {"name": "ingest_exchange", "arguments": {"messages": [{"role": "user", "content": "Kontext V2"}]}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "unsupported tool: ingest_exchange"


class _FakeRepo:
    def __init__(self):
        self.memories = {}
        self.flags = []
        self.audit = []
        self.project_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def record_intake_audit(self, **kwargs):
        self.audit.append(kwargs)
        return kwargs

    def find_exact_text_id(self, text):
        for memory_id, memory in self.memories.items():
            if " ".join(memory.text.split()) == " ".join(str(text or "").strip().split()):
                return memory_id
        return ""

    def fetch_by_external_id(self, external_id):
        return self.memories.get(external_id)

    def upsert_memory(self, memory, version_source="mem0_import"):
        self.memories[memory.external_mem0_id] = memory

    def record_memory_flag(self, **kwargs):
        self.flags.append(kwargs)
        return kwargs

    def project_search(self, **kwargs):
        self.project_calls.append(("project_search", kwargs))
        return {"rows": [], "count": 0}

    def project_timeline(self, **kwargs):
        self.project_calls.append(("project_timeline", kwargs))
        return {"anchor_id": kwargs.get("anchor_id") or "", "rows": []}

    def project_fetch(self, observation_id):
        self.project_calls.append(("project_fetch", {"observation_id": observation_id}))
        return {"found": False, "id": observation_id}

    def project_file_context(self, **kwargs):
        self.project_calls.append(("project_file_context", kwargs))
        return {
            "file_path": kwargs.get("file_path") or "",
            "titles": [],
            "rows": [],
            "recommend_full_file_read": True,
        }


class _FakeService:
    def __init__(self, repo):
        self._repo = repo

    def repo(self):
        return self._repo


def test_write_enabled_bridge_applies_direct_save_update_override_and_flags_without_raw_content():
    from kontext_v2.mcp_bridge import McpProfile, _call_tool

    repo = _FakeRepo()
    service = _FakeService(repo)
    profile = McpProfile(name="codex", token="token", can_write=True, can_dry_run_write=True)
    secret = "secret-token-123"

    save = json.loads(
        _call_tool(
            service,
            profile,
            "save",
            {
                "content": f"Kontext direct save applies when explicitly write-enabled. TOKEN={secret}",
                "domains": ["ai", "systems"],
                "memory_type": "project_state",
                "signal_strength": 8,
            },
        )["content"][0]["text"]
    )
    saved_id = save["results"][0]["id"]
    update = json.loads(
        _call_tool(
            service,
            profile,
            "update",
            {
                "id": saved_id,
                "content": f"Kontext direct update applies behind write-enabled. TOKEN={secret}",
                "reason": f"exact correction TOKEN={secret}",
                "memory_type": "project_state",
                "signal_strength": 9,
            },
        )["content"][0]["text"]
    )
    override = json.loads(
        _call_tool(
            service,
            profile,
            "submit_memory_override",
            {
                "content": f"Kontext override applies as a reviewed save. TOKEN={secret}",
                "reason": f"manual exact override TOKEN={secret}",
                "memory_type": "project_state",
                "domains": ["ai"],
            },
        )["content"][0]["text"]
    )
    flag = json.loads(
        _call_tool(
            service,
            profile,
            "flag_memory",
            {"id": saved_id, "flag_type": "stale_candidate", "reason": f"outdated TOKEN={secret}", "confidence": 2},
        )["content"][0]["text"]
    )
    delete = json.loads(
        _call_tool(
            service,
            profile,
            "delete",
            {"id": saved_id, "reason": f"duplicate TOKEN={secret}"},
        )["content"][0]["text"]
    )

    assert save["mode"] == "apply"
    assert save["writes_applied"] == 1
    assert saved_id in repo.memories
    assert update["mode"] == "apply"
    assert update["writes_applied"] == 1
    assert repo.memories[saved_id].text.startswith("Kontext direct update applies")
    assert override["mode"] == "apply"
    assert override["writes_applied"] == 1
    assert flag["mode"] == "apply"
    assert flag["writes_applied"] == 1
    assert flag["flag"]["confidence"] == 1.0
    assert repo.flags[-1]["status"] == "pending"
    assert delete["mode"] == "apply"
    assert delete["writes_applied"] == 1
    assert repo.memories[saved_id] is not None
    assert repo.flags[-1]["flag_type"] == "delete_candidate"

    rendered = json.dumps([save, update, override, flag, delete], sort_keys=True)
    assert secret not in rendered


def test_dry_run_enabled_bridge_still_does_not_apply_direct_save_or_flag():
    from kontext_v2.mcp_bridge import McpProfile, _call_tool

    repo = _FakeRepo()
    service = _FakeService(repo)
    profile = McpProfile(name="codex", token="token", can_write=False, can_dry_run_write=True)

    save = json.loads(
        _call_tool(service, profile, "save", {"content": "Kontext dry-run write should not apply."})["content"][0]["text"]
    )
    flag = json.loads(
        _call_tool(
            service,
            profile,
            "flag_memory",
            {"id": "dry-run-target", "flag_type": "stale_candidate", "reason": "dry-run only"},
        )["content"][0]["text"]
    )

    assert save["mode"] == "dry_run"
    assert save["writes_applied"] == 0
    assert flag["mode"] == "dry_run"
    assert flag["writes_applied"] == 0
    assert repo.memories == {}
    assert repo.flags == []


def test_bridge_passes_project_scope_into_project_tools():
    from kontext_v2.mcp_bridge import McpProfile, _call_tool

    repo = _FakeRepo()
    service = _FakeService(repo)
    profile = McpProfile(name="codex", token="token")

    _call_tool(
        service,
        profile,
        "project_search",
        {"query": "Kontext continuity", "limit": 5, "project": "OB1", "project_root": "C:/Tools/OB1"},
    )
    _call_tool(
        service,
        profile,
        "project_timeline",
        {"query": "Kontext continuity", "before": 1, "after": 2, "project": "OB1", "cwd": "C:/Tools/OB1"},
    )
    _call_tool(
        service,
        profile,
        "project_file_context",
        {"file_path": "tools/kontext-v2/kontext_v2/repository.py", "project": "OB1", "project_root": "C:/Tools/OB1"},
    )

    assert repo.project_calls[0] == (
        "project_search",
        {"query": "Kontext continuity", "limit": 5, "project": "OB1", "project_root": "C:/Tools/OB1"},
    )
    assert repo.project_calls[1][0] == "project_timeline"
    assert repo.project_calls[1][1]["project"] == "OB1"
    assert repo.project_calls[1][1]["project_root"] == "C:/Tools/OB1"
    assert repo.project_calls[2][0] == "project_file_context"
    assert repo.project_calls[2][1]["project"] == "OB1"
