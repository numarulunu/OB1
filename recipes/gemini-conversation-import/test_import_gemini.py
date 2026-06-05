import importlib.util
import json
import zipfile
from pathlib import Path


def load_importer():
    module_path = Path(__file__).with_name("import-gemini.py")
    spec = importlib.util.spec_from_file_location("import_gemini", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)


def test_scan_sources_dedupes_normalized_text_and_excludes_merged(tmp_path):
    importer = load_importer()
    folder = tmp_path / "Gemini Chats"
    folder.mkdir()
    (folder / "same.txt").write_text("Title\r\n\r\nHello from Gemini\r\n", encoding="utf-8")
    (folder / "folder-only.txt").write_text("Folder only memory", encoding="utf-8")
    (tmp_path / "Gemini-Chats-Ionutowscky-Merged.txt").write_text("Merged should not be imported", encoding="utf-8")
    archive = tmp_path / "archive.zip"
    write_zip(
        archive,
        {
            "same.txt": "Title\n\nHello from Gemini\n",
            "zip-only.txt": "Zip only memory",
        },
    )

    records = importer.scan_gemini_sources([folder, archive, tmp_path / "Gemini-Chats-Ionutowscky-Merged.txt"])

    assert len(records) == 3
    assert {record.title for record in records} == {"same", "folder-only", "zip-only"}
    assert all("Merged" not in record.source_name for record in records)


def test_scan_sources_can_include_merged_when_requested(tmp_path):
    importer = load_importer()
    merged = tmp_path / "Gemini-Chats-Ionutowscky-Merged.txt"
    merged.write_text("Merged corpus", encoding="utf-8")

    records = importer.scan_gemini_sources([merged], include_merged=True)

    assert len(records) == 1
    assert records[0].title == "Gemini-Chats-Ionutowscky-Merged"


def test_split_record_chunks_keeps_chunks_under_limit():
    importer = load_importer()
    text = "alpha " * 40 + "\n\n" + "beta " * 40 + "\n\n" + "gamma " * 40

    chunks = importer.split_text_chunks(text, max_chars=130)

    assert len(chunks) >= 3
    assert all(len(chunk) <= 130 for chunk in chunks)
    assert "alpha" in chunks[0]
    assert "gamma" in chunks[-1]


def test_parse_extraction_response_caps_and_normalizes_thoughts():
    importer = load_importer()
    payload = {
        "thoughts": [
            {"content": f"Memory {i}", "type": "context", "topics": "ai", "people": None, "confidence": "firm"}
            for i in range(7)
        ],
        "conversation_type": "strategy",
    }

    parsed = importer.parse_extraction_response(json.dumps(payload))

    assert len(parsed["thoughts"]) == 5
    assert parsed["thoughts"][0]["topics"] == ["ai"]
    assert parsed["thoughts"][0]["people"] == []
    assert parsed["conversation_type"] == "strategy"
