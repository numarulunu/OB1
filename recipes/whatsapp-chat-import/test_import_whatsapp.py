import importlib.util
import json
from pathlib import Path


def load_importer():
    module_path = Path(__file__).with_name("import-whatsapp.py")
    spec = importlib.util.spec_from_file_location("import_whatsapp", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_bracketed_dot_timestamps_and_multiline_messages():
    importer = load_importer()
    text = "\n".join(
        [
            "[01.05.2026, 09:00:01] Alice: First line",
            "continued line",
            "[01.05.2026, 09:01:02] Bob: Reply",
            "[01.05.2026, 09:02:03] Messages and calls are end-to-end encrypted.",
        ]
    )

    messages = importer.parse_whatsapp_messages(text)
    user_messages = importer.filter_importable_messages(messages)

    assert len(messages) == 3
    assert messages[0].sender == "Alice"
    assert messages[0].text == "First line\ncontinued line"
    assert messages[1].sender == "Bob"
    assert messages[2].is_system
    assert len(user_messages) == 2


def test_chunk_messages_splits_by_time_window_and_keeps_context():
    importer = load_importer()
    messages = importer.parse_whatsapp_messages(
        "\n".join(
            [
                "[01.05.2026, 09:00:00] Alice: Start",
                "[01.05.2026, 10:00:00] Bob: Same day",
                "[10.05.2026, 09:00:00] Alice: Later",
            ]
        )
    )

    chunks = importer.chunk_messages("Family", messages, max_chars=500, max_days=3)

    assert len(chunks) == 2
    assert chunks[0].chat_name == "Family"
    assert chunks[0].message_count == 2
    assert chunks[0].start_date == "2026-05-01"
    assert chunks[1].start_date == "2026-05-10"
    assert "Alice" in chunks[0].text


def test_scan_sources_includes_processed_summaries_and_raw_exports(tmp_path):
    importer = load_importer()
    processed = tmp_path / "Processed"
    raw = tmp_path / "Raw Exports" / "Family"
    processed.mkdir(parents=True)
    raw.mkdir(parents=True)
    (processed / "Family-1.txt").write_text("Durable summary text", encoding="utf-8")
    (raw / "_chat.txt").write_text(
        "[01.05.2026, 09:00:00] Alice: Hello\n[01.05.2026, 09:01:00] Bob: Hi",
        encoding="utf-8",
    )

    records = importer.scan_whatsapp_sources([tmp_path], include_processed=True, include_raw=True)

    assert {record.source_kind for record in records} == {"processed_summary", "raw_export"}
    assert {record.chat_name for record in records} == {"Family"}


def test_build_chunks_uses_processed_and_raw_strategies(tmp_path):
    importer = load_importer()
    processed = importer.WhatsAppRecord(
        record_id="processed1",
        chat_name="Family",
        text="Processed memory text",
        source_name="Family-1.txt",
        source_kind="processed_summary",
        source_path="/tmp/Family-1.txt",
        byte_count=20,
    )
    raw = importer.WhatsAppRecord(
        record_id="raw1",
        chat_name="Family",
        text="[01.05.2026, 09:00:00] Alice: Hello",
        source_name="_chat.txt",
        source_kind="raw_export",
        source_path="/tmp/_chat.txt",
        byte_count=40,
    )

    chunks = importer.build_chunks([processed, raw], max_chars=1000, max_days=14)

    assert len(chunks) == 2
    assert chunks[0].source_kind == "processed_summary"
    assert chunks[1].source_kind == "raw_export"
    assert chunks[1].message_count == 1


def test_chunk_messages_formats_finished_groups_only(monkeypatch):
    importer = load_importer()
    lines = [f"[01.05.2026, 09:{index:02d}:00] Alice: Message {index}" for index in range(20)]
    messages = importer.parse_whatsapp_messages("\n".join(lines))
    calls = []
    original = importer.format_messages

    def counted_format(items):
        calls.append(len(items))
        return original(items)

    monkeypatch.setattr(importer, "format_messages", counted_format)

    chunks = importer.chunk_messages("Family", messages, max_chars=10000, max_days=30)

    assert len(chunks) == 1
    assert calls == [20]


def test_parse_extraction_response_caps_and_normalizes_memories():
    importer = load_importer()
    payload = {
        "thoughts": [
            {"content": f"Memory {index}", "type": "context", "topics": "family", "people": None, "confidence": "firm"}
            for index in range(8)
        ],
        "conversation_type": "relationship_pattern",
    }

    parsed = importer.parse_extraction_response(json.dumps(payload))

    assert len(parsed["thoughts"]) == 5
    assert parsed["thoughts"][0]["topics"] == ["family"]
    assert parsed["thoughts"][0]["people"] == []
    assert parsed["conversation_type"] == "relationship_pattern"
