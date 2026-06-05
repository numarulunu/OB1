"""Claude Code JSONL parsing and deterministic noise filtering."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKIP_BLOCK_TYPES = {
    "tool_use",
    "tool_result",
    "server_tool_use",
    "web_search_tool_result",
    "code_execution_tool_result",
    "thinking",
    "redacted_thinking",
    "image",
}

TEXT_BLOCK_TYPES = {"text", "document"}


def normalize_text(text: str) -> str:
    """Collapse whitespace so tool logs and pasted JSON do not dominate prompts."""
    return re.sub(r"\s+", " ", text).strip()


def extract_message_text(event: dict[str, Any]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        return normalize_text(content)
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in SKIP_BLOCK_TYPES:
            continue
        if item_type in TEXT_BLOCK_TYPES and isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif item_type is None and isinstance(item.get("text"), str):
            parts.append(item["text"])

    return normalize_text("\n".join(part for part in parts if part))


def iter_session_files(source: Path) -> list[Path]:
    source = Path(source).expanduser()
    if source.is_file() and source.suffix.lower() == ".jsonl":
        return [source]
    return sorted(source.rglob("*.jsonl"))


def project_name_from_path(path: Path) -> str:
    return path.parent.name or "unknown"


def read_session(path: Path, max_message_chars: int = 12000) -> dict[str, Any] | None:
    messages: list[dict[str, str]] = []
    first_timestamp = ""
    last_timestamp = ""
    cwd = ""
    content_hash = hashlib.sha256()

    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                content_hash.update(line.encode("utf-8", errors="replace"))
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")
                if event_type not in {"user", "assistant"}:
                    continue
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role") or event_type
                if role not in {"user", "assistant"}:
                    continue

                text = extract_message_text(event)
                if not text:
                    continue
                if len(text) > max_message_chars:
                    text = text[:max_message_chars].rstrip() + "..."

                timestamp = event.get("timestamp") or ""
                first_timestamp = first_timestamp or timestamp
                last_timestamp = timestamp or last_timestamp
                cwd = cwd or event.get("cwd") or ""
                messages.append({"role": role, "text": text, "timestamp": timestamp})
    except OSError:
        return None

    if not messages:
        return None

    user_chars = sum(len(m["text"]) for m in messages if m["role"] == "user")
    assistant_chars = sum(len(m["text"]) for m in messages if m["role"] == "assistant")
    path = Path(path)
    return {
        "path": str(path),
        "project": project_name_from_path(path),
        "session_id": path.stem,
        "session_hash": content_hash.hexdigest()[:32],
        "cwd": cwd,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "message_count": len(messages),
        "user_chars": user_chars,
        "assistant_chars": assistant_chars,
        "messages": messages,
    }


def extract_dialogue_text(messages: list[dict[str, str]]) -> str:
    parts = []
    for message in messages:
        role = message.get("role", "")
        text = message.get("text", "").strip()
        if not text:
            continue
        prefix = "User" if role == "user" else "Assistant"
        parts.append(f"{prefix}: {text}")
    return "\n\n".join(parts)


def prepare_dialogue_for_extraction(messages: list[dict[str, str]], max_dialogue_chars: int = 120000) -> str:
    dialogue = extract_dialogue_text(messages)
    if len(dialogue) <= max_dialogue_chars:
        return dialogue

    marker = "\n\n[... middle truncated ...]\n\n"
    tail_chars = min(4000, max(30, max_dialogue_chars // 3))
    head_chars = max_dialogue_chars - tail_chars
    return dialogue[:head_chars].rstrip() + marker + dialogue[-tail_chars:].lstrip()


def session_word_count(session: dict[str, Any]) -> int:
    return sum(len(m.get("text", "").split()) for m in session.get("messages", []))


def _parse_date(timestamp: str):
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except ValueError:
        return None


def should_skip(session: dict[str, Any], sync_log: dict[str, Any], args: Any) -> str | None:
    session_hash = session.get("session_hash", "")
    if session_hash and session_hash in sync_log.get("ingested_ids", {}):
        return "already_imported"

    session_date = _parse_date(session.get("first_timestamp", ""))
    if session_date and getattr(args, "after", None) and session_date < args.after:
        return "before_date_filter"
    if session_date and getattr(args, "before", None) and session_date > args.before:
        return "after_date_filter"

    min_messages = getattr(args, "min_messages", 2) or 2
    min_words = getattr(args, "min_words", 50) or 50
    message_count = session.get("message_count", 0)

    if message_count < min_messages:
        return "single_turn"
    if message_count >= 10:
        return None
    if session_word_count(session) < min_words:
        return "too_little_text"
    return None
