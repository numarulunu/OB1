from kontext_v2.mcp_server import ingest_exchange_dry_run


def test_ingest_exchange_dry_run_returns_proposals_without_writing():
    payload = ingest_exchange_dry_run(
        messages=[
            {"role": "user", "content": "Kontext V2 should mirror Mem0 first."},
            {"role": "assistant", "content": "I will keep Mem0 live until parity passes."},
        ],
        origin="codex",
    )

    assert payload["ok"] is True
    assert payload["mode"] == "dry_run"
    assert payload["writes_applied"] == 0
    assert payload["proposals"]
    assert payload["proposals"][0]["action"] == "create_candidate"
