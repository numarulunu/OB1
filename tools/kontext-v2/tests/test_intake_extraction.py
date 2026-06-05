import json

from kontext_v2.intake import (
    gate_exchange,
    normalize_messages,
    normalize_proposals,
    preview_messages,
    source_hash,
)
from kontext_v2.mcp_server import extract_memories_dry_run


def test_source_hash_is_stable_for_same_exchange():
    messages = normalize_messages(
        [{"role": "user", "content": "We decided Kontext V2 should mirror Mem0 before cutover."}]
    )

    assert source_hash(messages) == source_hash(messages)


def test_source_hash_changes_when_content_changes():
    first = normalize_messages([{"role": "user", "content": "Use Qwen for ingestion."}])
    second = normalize_messages([{"role": "user", "content": "Use OpenAI for ingestion."}])

    assert source_hash(first) != source_hash(second)


def test_preview_is_single_line_and_truncated():
    messages = normalize_messages([{"role": "user", "content": "line one\nline two " + ("x" * 600)}])

    preview = preview_messages(messages, max_chars=120)

    assert "\n" not in preview
    assert len(preview) <= 120
    assert preview.endswith("...")


def test_gate_skips_tiny_chatter():
    decision = gate_exchange(normalize_messages([{"role": "user", "content": "ok thanks"}]))

    assert decision.keep is False
    assert decision.reason == "low_signal_chatter"


def test_gate_skips_tool_and_transcript_junk():
    text = "The speaker diarization was inferred from conversation cues and canonical names."

    decision = gate_exchange(normalize_messages([{"role": "assistant", "content": text}]))

    assert decision.keep is False
    assert decision.reason == "transcript_or_tool_junk"


def test_gate_keeps_ai_systems_project_signal():
    text = "We decided Kontext V2 should mirror Mem0 with a compatible MCP write path."

    decision = gate_exchange(normalize_messages([{"role": "user", "content": text}]))

    assert decision.keep is True
    assert decision.reason == "domain_signal"
    assert "ai" in decision.domains
    assert "systems" in decision.domains


def test_gate_keeps_relationship_psychology_signal():
    text = "My relationship with my mother shaped my nervous system and attachment patterns."

    decision = gate_exchange(normalize_messages([{"role": "user", "content": text}]))

    assert decision.keep is True
    assert "relationships" in decision.domains
    assert "psychology" in decision.domains


def test_normalize_proposals_converts_destructive_actions_to_flags():
    raw = json.dumps(
        {
            "proposals": [
                {"action": "delete", "content": "duplicate", "reason": "delete duplicate", "confidence": 0.9},
                {"action": "merge", "content": "overlap", "reason": "merge duplicate", "confidence": 0.8},
                {"action": "stale", "content": "old fact", "reason": "stale memory", "confidence": 0.7},
                {"action": "conflict", "content": "conflict", "reason": "conflicting memory", "confidence": 0.6},
            ]
        }
    )

    proposals = normalize_proposals(raw)

    assert [proposal.action for proposal in proposals] == ["flag", "flag", "flag", "flag"]
    assert [proposal.flag_type for proposal in proposals] == [
        "delete_candidate",
        "merge_candidate",
        "stale_candidate",
        "conflict_candidate",
    ]


def test_normalize_proposals_clamps_scores_and_unknown_action():
    raw = json.dumps(
        {
            "proposals": [
                {
                    "action": "rewrite_everything",
                    "content": "Durable memory.",
                    "domains": ["AI", " systems "],
                    "memory_type": "decision",
                    "signal_strength": 99,
                    "current_status": "active",
                    "memory_tier": "archive",
                    "confidence": 2,
                    "reason": "bad action",
                }
            ]
        }
    )

    proposal = normalize_proposals(raw)[0]

    assert proposal.action == "skip"
    assert proposal.domains == ["ai", "systems"]
    assert proposal.signal_strength == 10
    assert proposal.memory_tier == "active"
    assert proposal.confidence == 1.0


def test_extract_memories_dry_run_uses_gate_and_sanitized_preview():
    payload = extract_memories_dry_run(
        messages=[{"role": "user", "content": "Kontext V2 should mirror Mem0. TOKEN=secret-token-123"}],
        origin="kontext-v2-test",
    )

    assert payload["mode"] == "dry_run"
    assert payload["writes_applied"] == 0
    assert payload["gate"]["keep"] is True
    assert payload["source_hash"]
