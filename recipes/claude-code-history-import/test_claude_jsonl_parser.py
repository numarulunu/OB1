import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


RECIPE_DIR = Path(__file__).resolve().parent


def load_module(name):
    sys.path.insert(0, str(RECIPE_DIR))
    spec = importlib.util.spec_from_file_location(name, RECIPE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parser = load_module("claude_jsonl_parser")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_read_session_keeps_dialogue_text_and_skips_tool_noise(tmp_path):
    path = tmp_path / "Project-A" / "session-123.jsonl"
    rows = [
        {
            "type": "user",
            "timestamp": "2026-04-28T10:00:00.000Z",
            "cwd": "C:/work/project-a",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "I prefer concise memory."},
                    {"type": "tool_result", "content": "tool payload"},
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-04-28T10:01:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hidden reasoning"},
                    {"type": "text", "text": "We decided to use Qwen."},
                    {"type": "tool_use", "input": {"command": "echo noisy"}},
                ],
            },
        },
        {"type": "attachment", "text": "startup hook output"},
        {"type": "file-history-snapshot", "content": "large snapshot"},
    ]
    write_jsonl(path, rows)

    session = parser.read_session(path, max_message_chars=500)
    dialogue = parser.extract_dialogue_text(session["messages"])

    assert session["session_id"] == "session-123"
    assert session["project"] == "Project-A"
    assert session["cwd"] == "C:/work/project-a"
    assert session["message_count"] == 2
    assert "User: I prefer concise memory." in dialogue
    assert "Assistant: We decided to use Qwen." in dialogue
    assert "tool payload" not in dialogue
    assert "hidden reasoning" not in dialogue
    assert "echo noisy" not in dialogue
    assert "startup hook" not in dialogue


def test_read_session_handles_string_content_and_message_caps(tmp_path):
    path = tmp_path / "Project-B" / "abc.jsonl"
    write_jsonl(path, [
        {
            "type": "user",
            "timestamp": "2026-04-28T11:00:00.000Z",
            "message": {"role": "user", "content": "x" * 80},
        },
        {
            "type": "assistant",
            "timestamp": "2026-04-28T11:01:00.000Z",
            "message": {"role": "assistant", "content": "Short answer"},
        },
    ])

    session = parser.read_session(path, max_message_chars=20)

    assert session["messages"][0]["text"] == "x" * 20 + "..."
    assert session["message_count"] == 2


def test_prepare_dialogue_for_extraction_caps_long_sessions():
    messages = [
        {"role": "user", "text": "A" * 90, "timestamp": "2026-04-28T10:00:00.000Z"},
        {"role": "assistant", "text": "B" * 90, "timestamp": "2026-04-28T10:01:00.000Z"},
    ]

    dialogue = parser.prepare_dialogue_for_extraction(messages, max_dialogue_chars=80)

    assert len(dialogue) <= 120
    assert "[... middle truncated ...]" in dialogue
    assert dialogue.startswith("User: A")
    assert dialogue.endswith("B" * 30)


def test_should_skip_uses_sync_log_and_signal_thresholds():
    session = {
        "session_hash": "abc123",
        "first_timestamp": "2026-04-28T10:00:00.000Z",
        "message_count": 1,
        "messages": [{"role": "user", "text": "tiny", "timestamp": "2026-04-28T10:00:00.000Z"}],
    }
    args = type("Args", (), {"after": None, "before": None, "min_messages": 2, "min_words": 50})()

    assert parser.should_skip(session, {"ingested_ids": {}}, args) == "single_turn"
    session["message_count"] = 2
    session["messages"].append({"role": "assistant", "text": "ok", "timestamp": "2026-04-28T10:01:00.000Z"})
    assert parser.should_skip(session, {"ingested_ids": {}}, args) == "too_little_text"
    session["messages"] = [{"role": "user", "text": "decision " * 80, "timestamp": "2026-04-28T10:00:00.000Z"}] * 10
    session["message_count"] = 10
    assert parser.should_skip(session, {"ingested_ids": {}}, args) is None
    assert parser.should_skip(session, {"ingested_ids": {"abc123": {}}}, args) == "already_imported"


def test_raw_dry_run_writes_report_without_sync_or_credentials(tmp_path, monkeypatch):
    importer = load_module("claude_history_importer")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    source = tmp_path / "claude"
    session_path = source / "Project-C" / "dry-run.jsonl"
    rows = []
    for i in range(10):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append({
            "type": role,
            "timestamp": f"2026-04-28T10:{i:02d}:00.000Z",
            "message": {"role": role, "content": f"Long project decision text {i} " * 10},
        })
    write_jsonl(session_path, rows)

    report = tmp_path / "report.md"
    sync_log = tmp_path / "sync.json"
    args = SimpleNamespace(
        source=str(source), dry_run=True, raw=True, report=str(report), sync_log=str(sync_log),
        after=None, before=None, limit=1, model="openrouter", ollama_model="qwen3",
        openrouter_model=importer.DEFAULT_OPENROUTER_MODEL, fallback_openrouter_model="openai/gpt-4o-mini",
        no_fallback=False, min_messages=2, min_words=50, max_words=50000,
        max_message_chars=12000, max_dialogue_chars=120000, verbose=False,
        ingest_endpoint=False, focus=None,
    )

    stats = importer.run_import(args)

    assert stats["processed"] == 1
    assert stats["thoughts_generated"] == 1
    assert report.exists()
    assert "[Claude: Project-C]" in report.read_text(encoding="utf-8")
    assert not sync_log.exists()


def test_limit_stops_before_parsing_entire_source_tree(tmp_path, monkeypatch):
    importer = load_module("claude_history_importer")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    source = tmp_path / "claude"
    for session_index in range(3):
        rows = []
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            rows.append({
                "type": role,
                "timestamp": f"2026-04-28T10:{i:02d}:00.000Z",
                "message": {"role": role, "content": f"Durable project decision {session_index}-{i} " * 10},
            })
        write_jsonl(source / f"Project-{session_index}" / f"session-{session_index}.jsonl", rows)

    args = SimpleNamespace(
        source=str(source), dry_run=True, raw=True, report=None, sync_log=str(tmp_path / "sync.json"),
        after=None, before=None, limit=1, model="openrouter", ollama_model="qwen3",
        openrouter_model=importer.DEFAULT_OPENROUTER_MODEL, fallback_openrouter_model="openai/gpt-4o-mini",
        no_fallback=False, min_messages=2, min_words=50, max_words=50000,
        max_message_chars=12000, max_dialogue_chars=120000, verbose=False,
        ingest_endpoint=False, focus=None,
    )

    stats = importer.run_import(args)

    assert stats["found"] == 3
    assert stats["processed"] == 1
    assert stats["parsed"] == 1


def test_build_prompt_handles_literal_json_example():
    importer = load_module("claude_history_importer")
    session = {
        "project": "Project-D",
        "cwd": "C:/work/project-d",
        "session_id": "abc",
        "first_timestamp": "2026-04-28T10:00:00.000Z",
        "message_count": 2,
    }

    prompt = importer.build_prompt(session, "User: remember this", importer.DEFAULT_OPENROUTER_MODEL)

    assert "Project-D" in prompt
    assert '"thoughts"' in prompt
    assert "User: remember this" in prompt


def test_normalize_supabase_url_accepts_project_or_rest_url():
    importer = load_module("claude_history_importer")

    assert importer.normalize_supabase_url("https://abc.supabase.co") == "https://abc.supabase.co"
    assert importer.normalize_supabase_url("https://abc.supabase.co/") == "https://abc.supabase.co"
    assert importer.normalize_supabase_url("https://abc.supabase.co/rest/v1") == "https://abc.supabase.co"
    assert importer.normalize_supabase_url("https://abc.supabase.co/rest/v1/") == "https://abc.supabase.co"


def test_extraction_cache_reuses_distilled_session(tmp_path, monkeypatch):
    importer = load_module("claude_history_importer")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    source = tmp_path / "claude"
    session_path = source / "Project-E" / "session.jsonl"
    rows = []
    for i in range(10):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append({
            "type": role,
            "timestamp": f"2026-04-28T10:{i:02d}:00.000Z",
            "message": {"role": role, "content": f"Reusable cache decision {i} " * 10},
        })
    write_jsonl(session_path, rows)

    cache_path = tmp_path / "cache.jsonl"
    args = SimpleNamespace(
        source=str(source), dry_run=True, raw=False, report=None, sync_log=str(tmp_path / "sync.json"),
        extraction_cache=str(cache_path), after=None, before=None, limit=1, model="openrouter",
        ollama_model="qwen3", openrouter_model=importer.DEFAULT_OPENROUTER_MODEL,
        fallback_openrouter_model="openai/gpt-4o-mini", no_fallback=False, min_messages=2,
        min_words=50, max_words=50000, max_message_chars=12000, max_dialogue_chars=120000,
        verbose=False, ingest_endpoint=False, focus=None,
    )

    def fake_summarize(session, dialogue, import_args):
        return {
            "thoughts": [{"content": "Cached durable memory", "type": "decision", "topics": ["cache"], "people": [], "confidence": "firm"}],
            "conversation_type": "test",
            "model": "fake",
        }

    monkeypatch.setattr(importer, "summarize", fake_summarize)
    first = importer.run_import(args)

    assert first["processed"] == 1
    assert first["thoughts_generated"] == 1
    assert cache_path.exists()

    def fail_summarize(session, dialogue, import_args):
        raise AssertionError("cache was not used")

    monkeypatch.setattr(importer, "summarize", fail_summarize)
    second = importer.run_import(args)

    assert second["processed"] == 1
    assert second["thoughts_generated"] == 1
