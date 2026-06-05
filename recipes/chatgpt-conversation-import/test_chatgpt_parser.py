import chatgpt_parser


def make_message(role, text, create_time):
    return {
        "author": {"role": role},
        "content": {"content_type": "text", "parts": [text]},
        "create_time": create_time,
    }


def test_prepare_dialogue_splits_oversized_small_exports_by_session(monkeypatch):
    monkeypatch.setattr(chatgpt_parser, "MAX_CONTEXT_CHARS", 120)
    messages = [
        make_message("user", "first session " * 12, 1000),
        make_message("assistant", "first reply " * 12, 1010),
        make_message("user", "second session " * 12, 1000 + (5 * 60 * 60)),
        make_message("assistant", "second reply " * 12, 1000 + (5 * 60 * 60) + 10),
    ]

    chunks = chatgpt_parser.prepare_dialogue_for_extraction(messages, total_conversations=21)

    assert len(chunks) == 2
    assert "first session" in chunks[0]
    assert "second session" in chunks[1]
