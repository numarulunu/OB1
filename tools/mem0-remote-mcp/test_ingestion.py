import json

from ingestion import (
    build_extraction_prompt,
    build_sanitized_preview,
    normalize_messages,
    parse_extraction_response,
    should_call_llm,
    source_hash,
)


def test_source_hash_is_stable_for_same_exchange():
    messages = [{'role': 'user', 'content': 'We decided Mem0 ingestion auto-writes after every exchange.'}]

    assert source_hash(normalize_messages(messages)) == source_hash(normalize_messages(messages))


def test_source_hash_changes_when_content_changes():
    first = normalize_messages([{'role': 'user', 'content': 'Use Qwen for ingestion.'}])
    second = normalize_messages([{'role': 'user', 'content': 'Use OpenAI for ingestion.'}])

    assert source_hash(first) != source_hash(second)


def test_preview_is_single_line_and_truncated():
    messages = [{'role': 'user', 'content': 'line one\nline two ' + ('x' * 600)}]

    preview = build_sanitized_preview(normalize_messages(messages), max_chars=120)

    assert '\n' not in preview
    assert len(preview) <= 120
    assert 'line one' not in preview
    assert 'x' * 12 not in preview
    assert 'hash=' in preview


def test_gate_skips_tiny_chatter():
    decision = should_call_llm(normalize_messages([{'role': 'user', 'content': 'ok thanks'}]))

    assert decision.keep is False
    assert decision.reason == 'low_signal_chatter'


def test_gate_skips_tool_and_transcript_junk():
    text = 'The speaker diarization was inferred from conversation cues and canonical names.'

    decision = should_call_llm(normalize_messages([{'role': 'assistant', 'content': text}]))

    assert decision.keep is False
    assert decision.reason == 'transcript_or_tool_junk'


def test_gate_keeps_mem0_architecture_decision():
    text = 'We decided the Mem0 hosted MCP ingestion layer should auto-write by default using Qwen.'

    decision = should_call_llm(normalize_messages([{'role': 'user', 'content': text}]))

    assert decision.keep is True
    assert 'ai' in decision.domains


def test_gate_keeps_relationship_psychology_signal():
    text = 'My relationship with my mother shaped my nervous system and attachment patterns.'

    decision = should_call_llm(normalize_messages([{'role': 'user', 'content': text}]))

    assert decision.keep is True
    assert 'relationships' in decision.domains
    assert 'psychology' in decision.domains


def test_prompt_contains_core_policy():
    prompt = build_extraction_prompt(
        normalize_messages([{'role': 'user', 'content': 'Remember this workflow decision.'}]),
        source='codex',
    )
    joined = '\n'.join(message['content'] for message in prompt)

    assert 'User messages are primary truth' in joined
    assert 'Assistant messages count only for accepted decisions' in joined
    assert 'No hard-delete in V1' in joined
    assert 'JSON only' in joined


def test_parser_converts_delete_to_flag():
    raw = json.dumps(
        {
            'proposals': [
                {
                    'action': 'delete',
                    'content': 'duplicate',
                    'reason': 'duplicate memory',
                    'confidence': 0.9,
                }
            ]
        }
    )

    proposals = parse_extraction_response(raw)

    assert proposals[0].action == 'flag'
    assert proposals[0].flag_type == 'delete_candidate'


def test_parser_clamps_scores_and_normalizes_unknown_action():
    raw = json.dumps(
        {
            'proposals': [
                {
                    'action': 'rewrite_everything',
                    'content': 'Durable memory.',
                    'domains': ['AI', ' systems '],
                    'memory_type': 'decision',
                    'signal_strength': 99,
                    'current_status': 'active',
                    'memory_tier': 'archive',
                    'confidence': 2,
                    'reason': 'bad action',
                }
            ]
        }
    )

    proposal = parse_extraction_response(raw)[0]

    assert proposal.action == 'skip'
    assert proposal.domains == ['ai', 'systems']
    assert proposal.signal_strength == 10
    assert proposal.memory_tier == 'active'
    assert proposal.confidence == 1.0


def test_parser_rejects_missing_proposals():
    try:
        parse_extraction_response('{}')
    except ValueError as exc:
        assert 'proposals field is required' in str(exc)
    else:
        raise AssertionError('expected parser failure')
