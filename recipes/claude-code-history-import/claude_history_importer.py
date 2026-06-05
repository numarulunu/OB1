"""Claude Code history importer for Open Brain."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_jsonl_parser import (
    iter_session_files,
    prepare_dialogue_for_extraction,
    read_session,
    session_word_count,
    should_skip,
)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = Path.home() / ".claude" / "projects"
DEFAULT_SYNC_LOG_PATH = SCRIPT_DIR / "claude-history-sync-log.json"
LOG_PATH = SCRIPT_DIR / "_import-claude-history.log"

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OLLAMA_BASE = "http://localhost:11434"
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3-235b-a22b-2507"
DEFAULT_FALLBACK_OPENROUTER_MODEL = "openai/gpt-4o-mini"

FOCUS_PRESETS = {
    "tech": "technology, software architecture, engineering decisions, system design, code patterns, infrastructure, APIs, databases, and DevOps",
    "strategy": "business strategy, product decisions, career planning, market analysis, leadership, and professional growth",
    "personal": "family, health, relationships, values, home, personal finance, and life decisions",
    "creative": "writing, design, art direction, content strategy, storytelling, and creative process",
}

KNOWLEDGE_EXTRACTION_PROMPT = """\
You are extracting lasting knowledge from a Claude or Claude Code conversation.
The goal is to capture decisions, preferences, project context, goals, lessons,
constraints, and durable personal context that would be useful months later.

Return a JSON object with a "thoughts" array. Each thought must have:
- "content": a clear, self-contained memory in 2-4 sentences with specifics.
- "type": one of "decision", "preference", "learning", "context", "brainstorm", "reference".
- "topics": 1-3 short topic tags.
- "people": names of people mentioned, or an empty array.
- "confidence": "firm", "tentative", or "exploring".

Rules:
- Extract 0-5 thoughts per session. Zero is fine for low-value sessions.
- Skip tool logs, transient debugging output, generated code, command output, and generic Q&A.
- For coding sessions, capture why the user chose an approach, what project state changed,
  and what constraints matter. Do not summarize the code itself.
- Capture durable user preferences and goals even when they appear inside a technical session.
- Write in the same language as the conversation.

Return ONLY JSON like:
{{"thoughts": [{{"content": "...", "type": "decision", "topics": ["..."], "people": [], "confidence": "firm"}}], "conversation_type": "...", "skip_reason": "..."}}

Project: {project}
Working directory: {cwd}
Session ID: {session_id}
Date: {date}
Message count: {message_count}
Model: {model}

Conversation:
{dialogue_text}"""


def log_line(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{timestamp} {message}\n")


def load_sync_log(path: str | Path | None = None) -> dict[str, Any]:
    sync_path = Path(path) if path else DEFAULT_SYNC_LOG_PATH
    try:
        with sync_path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"ingested_ids": {}, "last_sync": ""}


def save_sync_log(log: dict[str, Any], path: str | Path | None = None) -> None:
    sync_path = Path(path) if path else DEFAULT_SYNC_LOG_PATH
    sync_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(log, handle, indent=2)


def load_extraction_cache(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    cache_path = Path(path)
    cache: dict[str, dict[str, Any]] = {}
    try:
        with cache_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_hash = item.get("session_hash")
                if session_hash:
                    cache[session_hash] = item
    except FileNotFoundError:
        pass
    return cache


def append_extraction_cache(path: str | Path | None, session: dict[str, Any], extraction: dict[str, Any]) -> None:
    if not path:
        return
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "session_hash": session.get("session_hash"),
        "session_id": session.get("session_id"),
        "project": session.get("project"),
        "path": session.get("path"),
        "first_timestamp": session.get("first_timestamp"),
        "last_timestamp": session.get("last_timestamp"),
        "message_count": session.get("message_count"),
        "extraction": extraction,
    }
    with cache_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("Use date format YYYY-MM-DD")


def _requests():
    try:
        import requests
    except ImportError as exc:
        raise SystemExit("Missing dependency: requests. Install with: pip install -r requirements.txt") from exc
    return requests


def http_post_with_retry(url: str, headers: dict[str, str], body: dict[str, Any], retries: int = 2):
    requests = _requests()
    for attempt in range(retries + 1):
        try:
            response = requests.post(url, headers=headers, json=body, timeout=120)
            if response.status_code >= 500 and attempt < retries:
                time.sleep(attempt + 1)
                continue
            return response
        except requests.RequestException as exc:
            if attempt < retries:
                time.sleep(attempt + 1)
                continue
            log_line(f"HTTP request failed: {exc}")
            return None
    return None


def parse_extraction_response(raw_content: str) -> dict[str, Any]:
    text = (raw_content or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            result = json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            return {"thoughts": [], "conversation_type": "", "skip_reason": "parse_error"}

    thoughts = []
    for item in result.get("thoughts", []):
        if isinstance(item, dict) and item.get("content"):
            thoughts.append({
                "content": str(item["content"]).strip(),
                "type": item.get("type", "learning"),
                "topics": item.get("topics", []),
                "people": item.get("people", []),
                "confidence": item.get("confidence", "firm"),
            })
        elif isinstance(item, str) and item.strip():
            thoughts.append({"content": item.strip(), "type": "learning", "topics": [], "people": [], "confidence": "firm"})

    return {
        "thoughts": thoughts,
        "conversation_type": result.get("conversation_type", ""),
        "skip_reason": result.get("skip_reason"),
    }


def build_focus_instruction(focus: str | None) -> str:
    if not focus or focus.lower() == "all":
        return ""
    focus_text = FOCUS_PRESETS.get(focus.lower(), focus)
    return (
        "\n\nIMPORTANT FOCUS FILTER: Only extract thoughts directly related to "
        f"{focus_text}. If the session is mainly about something else, return "
        '{"thoughts": [], "skip_reason": "off-topic"}.'
    )


def build_prompt(session: dict[str, Any], dialogue_text: str, model_name: str, focus: str | None = None) -> str:
    date = (session.get("first_timestamp") or "unknown")[:10]
    prompt = KNOWLEDGE_EXTRACTION_PROMPT.format(
        project=session.get("project", "unknown"),
        cwd=session.get("cwd", ""),
        session_id=session.get("session_id", ""),
        date=date,
        message_count=session.get("message_count", 0),
        model=model_name,
        dialogue_text=dialogue_text,
    )
    focus_instruction = build_focus_instruction(focus)
    if focus_instruction:
        prompt = prompt.replace("\nProject:", f"{focus_instruction}\n\nProject:")
    return prompt


def summarize_openrouter(session: dict[str, Any], dialogue_text: str, args: Any) -> dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required for OpenRouter extraction. Use --raw or --model ollama to avoid it.")

    models = [args.openrouter_model]
    fallback = getattr(args, "fallback_openrouter_model", DEFAULT_FALLBACK_OPENROUTER_MODEL)
    if fallback and not getattr(args, "no_fallback", False) and fallback not in models:
        models.append(fallback)

    for model_name in models:
        prompt = build_prompt(session, dialogue_text, model_name, getattr(args, "focus", None))
        response = http_post_with_retry(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            body={
                "model": model_name,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
        )
        if not response or response.status_code != 200:
            status = response.status_code if response else "no response"
            log_line(f"OpenRouter extraction failed for {model_name}: {status}")
            continue
        try:
            raw_content = response.json()["choices"][0]["message"]["content"]
            extraction = parse_extraction_response(raw_content)
            extraction["model"] = model_name
            return extraction
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            log_line(f"OpenRouter response parse failed for {model_name}: {exc}")
            continue

    return {"thoughts": [], "conversation_type": "", "skip_reason": "api_error"}


def summarize_ollama(session: dict[str, Any], dialogue_text: str, args: Any) -> dict[str, Any]:
    requests = _requests()
    model_name = getattr(args, "ollama_model", "qwen3")
    prompt = build_prompt(session, dialogue_text, model_name, getattr(args, "focus", None))
    try:
        response = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": model_name, "prompt": prompt, "stream": False, "format": "json"},
            timeout=120,
        )
    except requests.RequestException as exc:
        log_line(f"Ollama request failed: {exc}")
        return {"thoughts": [], "conversation_type": "", "skip_reason": "api_error"}
    if response.status_code != 200:
        log_line(f"Ollama returned HTTP {response.status_code}")
        return {"thoughts": [], "conversation_type": "", "skip_reason": "api_error"}
    try:
        extraction = parse_extraction_response(response.json().get("response", ""))
        extraction["model"] = model_name
        return extraction
    except json.JSONDecodeError:
        return {"thoughts": [], "conversation_type": "", "skip_reason": "parse_error"}


def summarize(session: dict[str, Any], dialogue_text: str, args: Any) -> dict[str, Any]:
    if args.raw:
        return {
            "thoughts": [{"content": dialogue_text, "type": "reference", "topics": [], "people": [], "confidence": "firm"}],
            "conversation_type": "raw_dialogue",
            "model": "raw",
        }
    if args.model == "ollama":
        return summarize_ollama(session, dialogue_text, args)
    return summarize_openrouter(session, dialogue_text, args)


def generate_embedding(text: str):
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None
    response = http_post_with_retry(
        f"{OPENROUTER_BASE}/embeddings",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        body={"model": "openai/text-embedding-3-small", "input": text[:8000]},
    )
    if not response or response.status_code != 200:
        return None
    try:
        return response.json()["data"][0]["embedding"]
    except (KeyError, IndexError, json.JSONDecodeError):
        return None


def normalize_supabase_url(value: str) -> str:
    """Accept either a project URL or the Data API /rest/v1 URL."""
    url = (value or "").strip().rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return url


def check_semantic_duplicate(thought_text: str, threshold: float = 0.92) -> bool:
    supabase_url = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    embedding = generate_embedding(thought_text)
    if not (supabase_url and service_key and embedding):
        return False
    response = http_post_with_retry(
        f"{supabase_url}/rest/v1/rpc/match_thoughts",
        headers={"Content-Type": "application/json", "apikey": service_key, "Authorization": f"Bearer {service_key}"},
        body={"query_embedding": embedding, "match_threshold": threshold, "match_count": 1, "filter": {"source": "claude_history"}},
    )
    if not response or response.status_code != 200:
        return False
    try:
        return len(response.json()) > 0
    except (json.JSONDecodeError, TypeError):
        return False


def ingest_thought_supabase(content: str, metadata: dict[str, Any], embed_text: str | None = None) -> dict[str, Any]:
    supabase_url = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    embedding = generate_embedding(embed_text or content)
    if not embedding:
        return {"ok": False, "error": "Failed to generate embedding"}
    response = http_post_with_retry(
        f"{supabase_url}/rest/v1/thoughts",
        headers={
            "Content-Type": "application/json",
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Prefer": "return=minimal",
        },
        body={"content": content, "embedding": embedding, "metadata": metadata},
    )
    if not response:
        return {"ok": False, "error": "No response from Supabase"}
    if response.status_code not in (200, 201):
        return {"ok": False, "error": f"HTTP {response.status_code}: {response.text[:300]}"}
    return {"ok": True}


def ingest_thought_endpoint(content: str, metadata: dict[str, Any]) -> dict[str, Any]:
    ingest_url = os.environ.get("INGEST_URL", "").strip()
    ingest_key = os.environ.get("INGEST_KEY", "").strip()
    response = http_post_with_retry(
        ingest_url,
        headers={"Content-Type": "application/json", "x-ingest-key": ingest_key},
        body={"content": content, "source": "claude_history", "extra_metadata": metadata},
    )
    if not response:
        return {"ok": False, "error": "No response from ingest endpoint"}
    try:
        return response.json()
    except json.JSONDecodeError:
        return {"ok": response.status_code in (200, 201), "error": response.text[:300]}


def build_thought_content(session: dict[str, Any], thought: dict[str, Any]) -> str:
    project = session.get("project") or "unknown"
    return f"[Claude: {project}] {thought.get('content', '').strip()}"


def build_metadata(session: dict[str, Any], thought: dict[str, Any], extraction: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "claude_history",
        "type": thought.get("type", "learning"),
        "topics": thought.get("topics", []),
        "people": thought.get("people", []),
        "confidence": thought.get("confidence", "firm"),
        "claude_project": session.get("project"),
        "claude_session_id": session.get("session_id"),
        "source_path": session.get("path"),
        "cwd": session.get("cwd"),
        "first_timestamp": session.get("first_timestamp"),
        "last_timestamp": session.get("last_timestamp"),
        "message_count": session.get("message_count"),
        "conversation_type": extraction.get("conversation_type", ""),
        "extractor_model": extraction.get("model", ""),
    }


def validate_live_environment(args: Any) -> None:
    if args.ingest_endpoint:
        if not os.environ.get("INGEST_URL"):
            raise SystemExit("INGEST_URL is required with --ingest-endpoint.")
        if not os.environ.get("INGEST_KEY"):
            raise SystemExit("INGEST_KEY is required with --ingest-endpoint.")
    else:
        if not os.environ.get("SUPABASE_URL"):
            raise SystemExit("SUPABASE_URL is required for live Supabase import.")
        if not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
            raise SystemExit("SUPABASE_SERVICE_ROLE_KEY is required for live Supabase import.")
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required for live import embeddings.")


def run_import(args: Any) -> dict[str, int]:
    source = Path(args.source).expanduser()
    if not source.exists():
        raise SystemExit(f"Path not found: {source}")
    if not args.dry_run:
        validate_live_environment(args)
    log_line(f"start source={source} dry_run={args.dry_run} raw={args.raw}")
    sync_log = load_sync_log(getattr(args, "sync_log", None))
    extraction_cache_path = getattr(args, "extraction_cache", None)
    extraction_cache = load_extraction_cache(extraction_cache_path)
    files = iter_session_files(source)
    stats = {"found": len(files), "parsed": 0, "processed": 0, "filtered": 0, "thoughts_generated": 0, "ingested": 0, "errors": 0}
    report_entries: list[dict[str, Any]] = []

    for session_path in files:
        if args.limit and stats["processed"] >= args.limit:
            break
        session = read_session(session_path, args.max_message_chars)
        if not session:
            continue
        stats["parsed"] += 1
        reason = should_skip(session, sync_log, args)
        if reason:
            stats["filtered"] += 1
            continue
        word_count = session_word_count(session)
        if args.max_words and word_count > args.max_words:
            stats["filtered"] += 1
            continue

        cached = extraction_cache.get(session.get("session_hash", ""))
        if cached:
            extraction = cached.get("extraction", {"thoughts": [], "conversation_type": "", "skip_reason": "cache_error"})
        else:
            dialogue = prepare_dialogue_for_extraction(session["messages"], args.max_dialogue_chars)
            extraction = summarize(session, dialogue, args)
            append_extraction_cache(extraction_cache_path, session, extraction)
            if session.get("session_hash"):
                extraction_cache[session["session_hash"]] = {"extraction": extraction}
        thoughts = extraction.get("thoughts", [])
        stats["processed"] += 1
        stats["thoughts_generated"] += len(thoughts)

        report_entry = {"session": session, "word_count": word_count, "thoughts": thoughts, "extraction": extraction}
        report_entries.append(report_entry)

        if args.verbose or args.dry_run:
            print(f"{session['project']} / {session['session_id']}: {len(thoughts)} thoughts")

        if args.dry_run:
            continue

        for thought in thoughts:
            content = build_thought_content(session, thought)
            metadata = build_metadata(session, thought, extraction)
            if check_semantic_duplicate(thought.get("content", "")):
                continue
            result = ingest_thought_endpoint(content, metadata) if args.ingest_endpoint else ingest_thought_supabase(content, metadata, thought.get("content"))
            if result.get("ok"):
                stats["ingested"] += 1
            else:
                stats["errors"] += 1
                log_line(f"ingest failed session={session.get('session_id')} error={result.get('error')}")

        sync_log["ingested_ids"][session["session_hash"]] = {
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "path": session.get("path"),
            "thoughts": len(thoughts),
        }
        sync_log["last_sync"] = datetime.now(timezone.utc).isoformat()
        save_sync_log(sync_log, getattr(args, "sync_log", None))

    if getattr(args, "report", None):
        write_report(Path(args.report), report_entries, stats)
    log_line(f"finish stats={stats}")
    return stats


def write_report(path: Path, entries: list[dict[str, Any]], stats: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Claude History Import Report", "", "## Stats", ""]
    for key, value in stats.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Sessions", ""])
    for entry in entries:
        session = entry["session"]
        lines.append(f"### [Claude: {session.get('project')}] {session.get('session_id')}")
        lines.append(f"- path: {session.get('path')}")
        lines.append(f"- date: {(session.get('first_timestamp') or 'unknown')[:10]}")
        lines.append(f"- messages: {session.get('message_count')}")
        lines.append("")
        for thought in entry.get("thoughts", []):
            lines.append(f"- ({thought.get('type', 'learning')}) {thought.get('content', '')}")
        if not entry.get("thoughts"):
            lines.append("- No thoughts extracted.")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import Claude Code JSONL history into Open Brain")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE), help="Claude projects folder or one .jsonl file")
    parser.add_argument("--dry-run", action="store_true", help="Parse and extract but do not write to Open Brain")
    parser.add_argument("--raw", action="store_true", help="Skip LLM extraction and import each session dialogue as one reference thought")
    parser.add_argument("--after", type=parse_date, help="Only sessions on or after YYYY-MM-DD")
    parser.add_argument("--before", type=parse_date, help="Only sessions on or before YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=0, help="Max sessions to process")
    parser.add_argument("--model", choices=["openrouter", "ollama"], default="openrouter", help="Extraction backend")
    parser.add_argument("--openrouter-model", default=DEFAULT_OPENROUTER_MODEL, help="OpenRouter model for extraction")
    parser.add_argument("--fallback-openrouter-model", default=DEFAULT_FALLBACK_OPENROUTER_MODEL, help="Fallback model when primary extraction fails")
    parser.add_argument("--no-fallback", action="store_true", help="Disable fallback extraction model")
    parser.add_argument("--ollama-model", default="qwen3", help="Ollama model name")
    parser.add_argument("--min-messages", type=int, default=2, help="Minimum extracted messages")
    parser.add_argument("--min-words", type=int, default=50, help="Minimum words for sessions below 10 messages")
    parser.add_argument("--max-words", type=int, default=50000, help="Skip sessions over this word count")
    parser.add_argument("--max-message-chars", type=int, default=12000, help="Cap each message before extraction")
    parser.add_argument("--max-dialogue-chars", type=int, default=120000, help="Cap each session prompt before extraction")
    parser.add_argument("--focus", default=None, help="Preset or free-text focus filter")
    parser.add_argument("--report", help="Write markdown report")
    parser.add_argument("--sync-log", default=str(DEFAULT_SYNC_LOG_PATH), help="Sync log path")
    parser.add_argument("--extraction-cache", help="JSONL cache for extracted session memories; enables resumable distillation")
    parser.add_argument("--verbose", action="store_true", help="Print session progress")
    parser.add_argument("--ingest-endpoint", action="store_true", help="Use INGEST_URL/INGEST_KEY instead of Supabase direct insert")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    stats = run_import(args)
    print(f"Processed {stats['processed']} sessions; generated {stats['thoughts_generated']} thoughts; ingested {stats['ingested']}.")


if __name__ == "__main__":
    main()
