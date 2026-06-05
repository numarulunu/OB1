import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shadow_cleanup import (  # noqa: E402
    build_exact_duplicate_packs,
    build_topic_packs,
    canonical_text,
    build_claude_command,
    claude_cli_error_text,
    normalize_supabase_url,
    is_rate_limit_error,
    parse_claude_json,
    reached_consecutive_error_limit,
    read_processed_pack_ids,
    shard_records,
)


def thought(record_id, content, metadata=None):
    return {
        "id": record_id,
        "content": content,
        "metadata": metadata or {},
        "created_at": "2026-05-02T00:00:00Z",
    }


def test_canonical_text_collapses_whitespace_and_case():
    assert canonical_text("  Hello\n  WORLD\t") == "hello world"


def test_normalize_supabase_url_accepts_project_or_rest_url():
    assert normalize_supabase_url("https://abc.supabase.co") == "https://abc.supabase.co"
    assert normalize_supabase_url("https://abc.supabase.co/") == "https://abc.supabase.co"
    assert normalize_supabase_url("https://abc.supabase.co/rest/v1") == "https://abc.supabase.co"
    assert normalize_supabase_url("https://abc.supabase.co/rest/v1/") == "https://abc.supabase.co"


def test_build_exact_duplicate_packs_groups_same_content():
    records = [
        thought("a", "Use Supabase for OB1."),
        thought("b", " use   supabase for ob1. "),
        thought("c", "Different memory"),
    ]

    packs = build_exact_duplicate_packs(records, max_items=10)

    assert len(packs) == 1
    assert packs[0]["kind"] == "exact_duplicate"
    assert packs[0]["record_count"] == 2
    assert [item["id"] for item in packs[0]["items"]] == ["a", "b"]


def test_build_topic_packs_uses_project_and_topics():
    records = [
        thought("a", "OB1 cleanup", {"claude_project": "OB1", "topics": ["memory"]}),
        thought("b", "OB1 import", {"claude_project": "OB1", "topics": ["memory"]}),
        thought("c", "Vocality note", {"claude_project": "Vocality", "topics": ["teaching"]}),
    ]

    packs = build_topic_packs(records, min_items=2, max_items=10)

    assert len(packs) == 1
    assert packs[0]["kind"] == "topic_cluster"
    assert packs[0]["cluster_key"] == "project:ob1|topic:memory"
    assert [item["id"] for item in packs[0]["items"]] == ["a", "b"]


def test_shard_records_balances_by_round_robin():
    records = [{"pack_id": str(index)} for index in range(5)]

    shards = shard_records(records, 2)

    assert [[item["pack_id"] for item in shard] for shard in shards] == [["0", "2", "4"], ["1", "3"]]


def test_is_rate_limit_error_detects_usage_and_429_messages():
    assert is_rate_limit_error("HTTP 429 Too Many Requests")
    assert is_rate_limit_error("Claude usage limit reached for Opus")
    assert not is_rate_limit_error("JSON parse failed")


def test_claude_cli_error_text_ignores_successful_generated_content():
    stdout = json.dumps(
        {
            "is_error": False,
            "api_error_status": None,
            "terminal_reason": "completed",
            "structured_output": {"risk_notes": ["This memory mentions rate limit handling."]},
        }
    )

    assert claude_cli_error_text(stdout, "", 0) == ""


def test_claude_cli_error_text_reports_error_fields():
    stdout = json.dumps(
        {
            "is_error": True,
            "api_error_status": "429",
            "terminal_reason": "rate limit",
            "result": "Usage limit reached",
        }
    )

    assert "Usage limit reached" in claude_cli_error_text(stdout, "", 0)


def test_reached_consecutive_error_limit():
    assert reached_consecutive_error_limit(3, 3)
    assert not reached_consecutive_error_limit(2, 3)
    assert not reached_consecutive_error_limit(50, 0)


def test_parse_claude_json_extracts_result_wrappers():
    wrapped = json.dumps({"result": '{"canonical_memories": [], "archive_candidates": []}'})

    parsed = parse_claude_json(wrapped)

    assert parsed == {"canonical_memories": [], "archive_candidates": []}


def test_parse_claude_json_prefers_structured_output():
    wrapped = json.dumps(
        {
            "result": "{}",
            "structured_output": {"canonical_memories": [], "archive_candidates": [], "risk_notes": []},
        }
    )

    parsed = parse_claude_json(wrapped)

    assert parsed == {"canonical_memories": [], "archive_candidates": [], "risk_notes": []}


def test_read_processed_pack_ids_skips_error_records(tmp_path):
    path = tmp_path / "proposals.jsonl"
    path.write_text(
        json.dumps({"pack_id": "bad", "error": "failed"})
        + "\n"
        + json.dumps({"pack_id": "good", "proposal": {}})
        + "\n",
        encoding="utf-8",
    )

    assert read_processed_pack_ids(path) == {"good"}


def test_build_claude_command_uses_structured_non_persistent_print_mode():
    command = build_claude_command("claude", "opus", 1.25)

    assert command[:4] == ["claude", "-p", "--model", "opus"]
    assert "--output-format" in command
    assert "json" in command
    assert "--json-schema" in command
    assert "--permission-mode" in command
    assert "dontAsk" in command
    assert "--no-session-persistence" in command
    assert "--max-budget-usd" in command
    assert "1.25" in command
