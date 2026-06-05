#!/usr/bin/env python3
"""
Open Brain - WhatsApp Chat Importer (v1.0).

Imports WhatsApp text exports into OB1 as distilled, duplicate-checked thoughts.
It handles raw WhatsApp _chat.txt logs and already-processed summary text files.
Operational output is limited to counts, paths, chunk IDs, and status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

try:
    import requests
except ImportError:
    print("Missing dependency: requests")
    print("Install with: pip install requests")
    sys.exit(1)

VERSION = "1.0"
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_SYNC_LOG = REPO_ROOT / ".local" / "open-brain-imports" / "whatsapp-sync-log.json"
DEFAULT_RUN_LOG = REPO_ROOT / ".local" / "open-brain-imports" / "_import-whatsapp.log"
DEFAULT_MODEL = "qwen/qwen3-235b-a22b-2507"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MAX_THOUGHTS_PER_CHUNK = 5
DEFAULT_MAX_CHARS = 60000
DEFAULT_MAX_DAYS = 14

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

TIMESTAMP_RE = re.compile(
    r"^\u200e?\[(\d{1,2})\.(\d{1,2})\.(\d{2,4}),\s+(\d{1,2}):(\d{2}):(\d{2})\]\s+(.*)$"
)

EXTRACTION_PROMPT = """\
You are distilling WhatsApp chat history into durable personal memory for OB1.
Be highly selective. Extract only information that will still be useful months
from now in a personal external brain.

Capture:
- Durable patterns in the user's communication style, boundaries, needs, habits, and behavior
- Relationship dynamics that help future assistants understand context without storing gossip
- Important live events, decisions, commitments, dates, logistics, people, and constraints
- Recurring friction, support patterns, responsibilities, preferences, or risks
- Useful project/life context that appears in the chat and is clearly tied to the user

Privacy filter:
- Do not import gossip, intimate details, insults, or third-party private facts unless they create durable user context
- Do not summarize raw chat blow-by-blow
- Do not preserve sensitive wording when a neutral pattern memory is enough
- Prefer inferred pattern memories over transcript-like summaries

Each thought must be standalone, concise, and searchable.
Return 0-5 thoughts. Zero is correct if there is nothing worth remembering.

Each thought object must have:
- content: 1-3 sentence memory
- type: one of decision, preference, learning, context, brainstorm, reference
- topics: short list
- people: short list
- confidence: tentative, inferred, or firm

Return only JSON:
{{"thoughts": [...], "conversation_type": "...", "skip_reason": "..."}}

Chat: {chat_name}
Source: WhatsApp
Source kind: {source_kind}
Chunk: {chunk_index}/{chunk_count}
Date range: {start_date} to {end_date}
Messages: {message_count}
Approx words: {word_count}

Text:
{conversation_text}"""


class WhatsAppMessage(NamedTuple):
    timestamp: datetime
    sender: str
    text: str
    is_system: bool


class WhatsAppRecord(NamedTuple):
    record_id: str
    chat_name: str
    text: str
    source_name: str
    source_kind: str
    source_path: str
    byte_count: int


class WhatsAppChunk(NamedTuple):
    chunk_id: str
    record: WhatsAppRecord
    chat_name: str
    source_kind: str
    chunk_index: int
    chunk_count: int
    text: str
    message_count: int
    start_date: str
    end_date: str


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and not key.startswith("#"):
            os.environ[key] = value.strip().strip('"').strip("'")


def refresh_env() -> None:
    global SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENROUTER_API_KEY
    SUPABASE_URL = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


def normalize_supabase_url(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return url


def require_env(names: list[str]) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing))


def normalize_text(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\u200e", "").replace("\u200f", "")
    text = re.sub(r"\r+\n", "\n", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def text_hash(text: str) -> str:
    normalized = " ".join(normalize_text(text).lower().split())
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def parse_whatsapp_datetime(day: str, month: str, year: str, hour: str, minute: str, second: str) -> datetime:
    parsed_year = int(year)
    if parsed_year < 100:
        parsed_year += 2000
    return datetime(parsed_year, int(month), int(day), int(hour), int(minute), int(second), tzinfo=timezone.utc)


def split_sender_text(rest: str) -> tuple[str, str, bool]:
    if ": " not in rest:
        return "", rest.strip(), True
    sender, text = rest.split(": ", 1)
    sender = sender.strip()
    if not sender:
        return "", text.strip(), True
    return sender, text.strip(), False


def parse_whatsapp_messages(text: str) -> list[WhatsAppMessage]:
    messages: list[WhatsAppMessage] = []
    current_timestamp: datetime | None = None
    current_sender = ""
    current_lines: list[str] = []
    current_is_system = False

    def emit_current() -> None:
        if current_timestamp is None:
            return
        body = "\n".join(line for line in current_lines).strip()
        messages.append(WhatsAppMessage(current_timestamp, current_sender, body, current_is_system))

    for raw_line in normalize_text(text).splitlines():
        line = raw_line.rstrip("\n")
        match = TIMESTAMP_RE.match(line)
        if match:
            emit_current()
            day, month, year, hour, minute, second, rest = match.groups()
            current_timestamp = parse_whatsapp_datetime(day, month, year, hour, minute, second)
            current_sender, body, current_is_system = split_sender_text(rest)
            current_lines = [body] if body else []
            continue
        if current_timestamp is not None:
            current_lines.append(line.strip())
    emit_current()
    return messages


def is_low_signal_message(text: str) -> bool:
    cleaned = " ".join(str(text or "").strip().lower().split())
    if not cleaned:
        return True
    skipped_fragments = (
        "messages and calls are end-to-end encrypted",
        "this message was deleted",
        "you deleted this message",
        "<media omitted>",
        "image omitted",
        "video omitted",
        "audio omitted",
        "sticker omitted",
        "gif omitted",
        "contact card omitted",
        "missed voice call",
        "missed video call",
    )
    return any(fragment in cleaned for fragment in skipped_fragments)


def filter_importable_messages(messages: list[WhatsAppMessage]) -> list[WhatsAppMessage]:
    return [message for message in messages if not message.is_system and not is_low_signal_message(message.text)]


def format_message(message: WhatsAppMessage) -> str:
    stamp = message.timestamp.strftime("%Y-%m-%d %H:%M")
    sender = message.sender or "System"
    return f"[{stamp}] {sender}: {message.text}"


def format_messages(messages: list[WhatsAppMessage]) -> str:
    return "\n".join(format_message(message) for message in messages).strip()


def chunk_messages(chat_name: str, messages: list[WhatsAppMessage], max_chars: int, max_days: int) -> list[WhatsAppChunk]:
    importable = filter_importable_messages(messages)
    if not importable:
        return []

    groups: list[list[WhatsAppMessage]] = []
    current: list[WhatsAppMessage] = []
    current_start = importable[0].timestamp
    current_chars = 0

    for message in importable:
        line = format_message(message)
        candidate_chars = len(line) if not current else current_chars + 1 + len(line)
        over_chars = candidate_chars > max_chars and bool(current)
        over_days = (message.timestamp.date() - current_start.date()).days > max_days and bool(current)
        if over_chars or over_days:
            groups.append(current)
            current = [message]
            current_start = message.timestamp
            current_chars = len(line)
        else:
            current.append(message)
            current_chars = candidate_chars
    if current:
        groups.append(current)

    chunks: list[WhatsAppChunk] = []
    total = len(groups)
    for index, group in enumerate(groups, start=1):
        text = format_messages(group)
        start_date = group[0].timestamp.date().isoformat()
        end_date = group[-1].timestamp.date().isoformat()
        chunk_id = hashlib.sha256(f"{chat_name}|raw|{index}|{text_hash(text)}".encode("utf-8")).hexdigest()[:24]
        record = WhatsAppRecord(
            record_id=hashlib.sha256(f"{chat_name}|raw".encode("utf-8")).hexdigest()[:24],
            chat_name=chat_name,
            text="",
            source_name="",
            source_kind="raw_export",
            source_path="",
            byte_count=0,
        )
        chunks.append(WhatsAppChunk(chunk_id, record, chat_name, "raw_export", index, total, text, len(group), start_date, end_date))
    return chunks


def split_text_chunks(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    text = normalize_text(text)
    if len(text) <= max_chars:
        return [text] if text else []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    def emit(value: str) -> None:
        value = value.strip()
        if value:
            chunks.append(value)

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            emit(current)
            current = ""
            for start in range(0, len(paragraph), max_chars):
                emit(paragraph[start : start + max_chars])
            continue
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) > max_chars:
            emit(current)
            current = paragraph
        else:
            current = candidate
    emit(current)
    return chunks


def chat_name_from_processed(path: Path) -> str:
    stem = path.stem.strip()
    cleaned = re.sub(r"-\d+$", "", stem).strip()
    return cleaned or stem or "WhatsApp"


def chat_name_from_raw(path: Path) -> str:
    parent = path.parent.name.strip()
    if parent and parent.lower() not in {"raw exports", "whatsapp"}:
        return parent
    return path.stem.strip("_") or "WhatsApp"


def make_record(source_kind: str, source_path: str, source_name: str, chat_name: str, raw_bytes: bytes) -> WhatsAppRecord | None:
    text = normalize_text(raw_bytes.decode("utf-8-sig", errors="replace"))
    if not text:
        return None
    digest = text_hash(f"{source_kind}|{chat_name}|{text}")
    return WhatsAppRecord(
        record_id=digest[:24],
        chat_name=chat_name,
        text=text,
        source_name=Path(source_name).name,
        source_kind=source_kind,
        source_path=source_path,
        byte_count=len(raw_bytes),
    )


def scan_directory(source: Path, include_processed: bool, include_raw: bool) -> list[WhatsAppRecord]:
    records: list[WhatsAppRecord] = []
    if include_processed:
        for path in sorted(source.rglob("*.txt"), key=lambda p: str(p).lower()):
            parts = {part.lower() for part in path.parts}
            if "processed" not in parts:
                continue
            record = make_record("processed_summary", str(path), path.name, chat_name_from_processed(path), path.read_bytes())
            if record:
                records.append(record)
    if include_raw:
        for path in sorted(source.rglob("_chat.txt"), key=lambda p: str(p).lower()):
            parts = {part.lower() for part in path.parts}
            if "processed" in parts:
                continue
            record = make_record("raw_export", str(path), path.name, chat_name_from_raw(path), path.read_bytes())
            if record:
                records.append(record)
    return records


def scan_zip(source: Path, include_processed: bool, include_raw: bool) -> list[WhatsAppRecord]:
    records: list[WhatsAppRecord] = []
    with zipfile.ZipFile(source) as zf:
        for info in sorted((i for i in zf.infolist() if not i.is_dir()), key=lambda i: i.filename.lower()):
            name_path = Path(info.filename)
            lower_parts = {part.lower() for part in name_path.parts}
            is_processed = "processed" in lower_parts and name_path.suffix.lower() == ".txt"
            is_raw = name_path.name.lower() == "_chat.txt"
            if is_processed and include_processed:
                record = make_record(
                    "processed_summary", str(source), info.filename, chat_name_from_processed(name_path), zf.read(info.filename)
                )
            elif is_raw and include_raw:
                record = make_record("raw_export", str(source), info.filename, chat_name_from_raw(name_path), zf.read(info.filename))
            else:
                record = None
            if record:
                records.append(record)
    return records


def scan_whatsapp_sources(
    source_paths: list[str | Path], include_processed: bool = True, include_raw: bool = True
) -> list[WhatsAppRecord]:
    records: list[WhatsAppRecord] = []
    seen_hashes: set[str] = set()

    def add(record: WhatsAppRecord | None) -> None:
        if not record:
            return
        digest = text_hash(f"{record.source_kind}|{record.chat_name}|{record.text}")
        if digest in seen_hashes:
            return
        seen_hashes.add(digest)
        records.append(record)

    for raw_source in source_paths:
        source = Path(raw_source)
        if not source.exists():
            raise FileNotFoundError(f"Source path not found: {source}")
        if source.is_dir():
            for record in scan_directory(source, include_processed, include_raw):
                add(record)
        elif source.suffix.lower() == ".zip":
            for record in scan_zip(source, include_processed, include_raw):
                add(record)
        elif source.suffix.lower() == ".txt":
            if source.name.lower() == "_chat.txt" and include_raw:
                add(make_record("raw_export", str(source), source.name, chat_name_from_raw(source), source.read_bytes()))
            elif include_processed:
                add(make_record("processed_summary", str(source), source.name, chat_name_from_processed(source), source.read_bytes()))
        else:
            raise ValueError(f"Unsupported source path: {source}")
    return records


def build_chunks(records: list[WhatsAppRecord], max_chars: int, max_days: int) -> list[WhatsAppChunk]:
    chunks: list[WhatsAppChunk] = []
    for record in records:
        if record.source_kind == "processed_summary":
            texts = split_text_chunks(record.text, max_chars=max_chars)
            for index, text in enumerate(texts, start=1):
                chunk_id = hashlib.sha256(f"{record.record_id}|processed|{index}|{text_hash(text)}".encode("utf-8")).hexdigest()[:24]
                chunks.append(
                    WhatsAppChunk(
                        chunk_id,
                        record,
                        record.chat_name,
                        record.source_kind,
                        index,
                        len(texts),
                        text,
                        0,
                        "",
                        "",
                    )
                )
            continue
        messages = parse_whatsapp_messages(record.text)
        raw_chunks = chunk_messages(record.chat_name, messages, max_chars=max_chars, max_days=max_days)
        total = len(raw_chunks)
        for index, chunk in enumerate(raw_chunks, start=1):
            chunk_id = hashlib.sha256(
                f"{record.record_id}|raw|{index}|{text_hash(chunk.text)}".encode("utf-8")
            ).hexdigest()[:24]
            chunks.append(
                WhatsAppChunk(
                    chunk_id,
                    record,
                    record.chat_name,
                    record.source_kind,
                    index,
                    total,
                    chunk.text,
                    chunk.message_count,
                    chunk.start_date,
                    chunk.end_date,
                )
            )
    return chunks


def parse_extraction_response(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {"thoughts": [], "conversation_type": "", "skip_reason": "parse_error"}
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {"thoughts": [], "conversation_type": "", "skip_reason": "parse_error"}

    thoughts = parsed.get("thoughts", []) if isinstance(parsed, dict) else []
    valid = []
    for thought in thoughts:
        if not isinstance(thought, dict):
            continue
        content = str(thought.get("content", "")).strip()
        if not content:
            continue
        topics = thought.get("topics", [])
        people = thought.get("people", [])
        if isinstance(topics, str):
            topics = [topics]
        if not isinstance(topics, list):
            topics = []
        if isinstance(people, str):
            people = [people]
        if not isinstance(people, list):
            people = []
        valid.append(
            {
                "content": content,
                "type": thought.get("type", "context") or "context",
                "topics": [str(topic) for topic in topics if str(topic).strip()],
                "people": [str(person) for person in people if str(person).strip()],
                "confidence": thought.get("confidence", "firm") or "firm",
            }
        )
    return {
        "thoughts": valid[:MAX_THOUGHTS_PER_CHUNK],
        "conversation_type": parsed.get("conversation_type", "") if isinstance(parsed, dict) else "",
        "skip_reason": parsed.get("skip_reason", "") if isinstance(parsed, dict) else "",
    }


def http_post_with_retry(url: str, headers: dict, body: dict, retries: int = 3):
    for attempt in range(retries + 1):
        try:
            response = requests.post(url, headers=headers, json=body, timeout=120)
        except requests.RequestException as exc:
            if attempt >= retries:
                print(f"warning=http_request_failed attempts={attempt + 1} error={type(exc).__name__}")
                return None
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(3 * (attempt + 1))
            continue
        return response
    return None


def summarize_openrouter(chunk: WhatsAppChunk, model: str, fallback_model: str = "") -> dict:
    prompt = EXTRACTION_PROMPT.format(
        chat_name=chunk.chat_name,
        source_kind=chunk.source_kind,
        chunk_index=chunk.chunk_index,
        chunk_count=chunk.chunk_count,
        start_date=chunk.start_date or "processed-summary",
        end_date=chunk.end_date or "processed-summary",
        message_count=chunk.message_count,
        word_count=len(chunk.text.split()),
        conversation_text=chunk.text,
    )
    models = [model]
    if fallback_model and fallback_model not in models:
        models.append(fallback_model)

    for model_name in models:
        response = http_post_with_retry(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            body={
                "model": model_name,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
        )
        if not response or response.status_code != 200:
            status = response.status_code if response else "no_response"
            print(f"warning=extraction_failed model={model_name} status={status}")
            continue
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            print(f"warning=extraction_parse_failed model={model_name}")
            continue
        return parse_extraction_response(content)
    return {"thoughts": [], "conversation_type": "", "skip_reason": "api_error"}


def generate_embedding(text: str, retries: int = 2):
    truncated = text[:8000]
    for attempt in range(retries + 1):
        response = http_post_with_retry(
            f"{OPENROUTER_BASE}/embeddings",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            body={"model": "openai/text-embedding-3-small", "input": truncated},
            retries=1,
        )
        if not response or response.status_code != 200:
            if attempt < retries:
                time.sleep(1 * (attempt + 1))
                continue
            return None
        try:
            return response.json()["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1 * (attempt + 1))
                continue
            return None
    return None


def check_semantic_duplicate(thought_text: str, threshold: float = 0.92, source_filter: str = "") -> bool:
    embedding = generate_embedding(thought_text)
    if not embedding:
        return False
    body = {"query_embedding": embedding, "match_threshold": threshold, "match_count": 1, "filter": {}}
    if source_filter:
        body["filter"] = {"source": source_filter}
    response = http_post_with_retry(
        f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/rpc/match_thoughts",
        headers={
            "Content-Type": "application/json",
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        },
        body=body,
        retries=1,
    )
    if not response or response.status_code != 200:
        return False
    try:
        return len(response.json()) > 0
    except (json.JSONDecodeError, TypeError):
        return False


def ingest_thought(content: str, metadata: dict, embed_text: str) -> dict:
    embedding = generate_embedding(embed_text)
    if not embedding:
        return {"ok": False, "error": "embedding_failed"}
    response = http_post_with_retry(
        f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts",
        headers={
            "Content-Type": "application/json",
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Prefer": "return=minimal",
        },
        body={"content": content, "embedding": embedding, "metadata": metadata},
        retries=2,
    )
    if not response:
        return {"ok": False, "error": "no_response"}
    if response.status_code not in (200, 201):
        return {"ok": False, "error": f"http_{response.status_code}"}
    return {"ok": True}


def read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(path)


def log_line(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


def run_plan(source_paths: list[str], args: argparse.Namespace) -> tuple[list[WhatsAppRecord], list[WhatsAppChunk]]:
    records = scan_whatsapp_sources(
        source_paths,
        include_processed=not args.skip_processed,
        include_raw=not args.skip_raw,
    )
    chunks = build_chunks(records, max_chars=args.max_chars, max_days=args.max_days)
    raw_records = sum(1 for record in records if record.source_kind == "raw_export")
    processed_records = sum(1 for record in records if record.source_kind == "processed_summary")
    raw_messages = sum(chunk.message_count for chunk in chunks if chunk.source_kind == "raw_export")
    total_chars = sum(len(record.text) for record in records)
    total_words = sum(len(record.text.split()) for record in records)
    print(
        f"plan records={len(records)} processed_records={processed_records} raw_records={raw_records} "
        f"chunks={len(chunks)} raw_messages={raw_messages} chars={total_chars} words={total_words} "
        f"max_chars={args.max_chars} max_days={args.max_days}"
    )
    return records, chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import WhatsApp text exports into Open Brain.")
    parser.add_argument("sources", nargs="+", help="WhatsApp source directory, txt file, or zip archive.")
    parser.add_argument("--plan-only", action="store_true", help="Scan and count only. No API calls or DB writes.")
    parser.add_argument("--dry-run", action="store_true", help="Extract with LLM but do not insert into Supabase or mark chunks synced.")
    parser.add_argument("--skip-processed", action="store_true", help="Skip processed summary text files.")
    parser.add_argument("--skip-raw", action="store_true", help="Skip raw WhatsApp _chat.txt exports.")
    parser.add_argument("--limit-chunks", type=int, default=0, help="Max unsynced chunks to process.")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Max chars per LLM chunk.")
    parser.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS, help="Max days per raw chat chunk.")
    parser.add_argument("--openrouter-model", default=DEFAULT_MODEL, help="OpenRouter extraction model.")
    parser.add_argument("--fallback-openrouter-model", default="", help="Optional fallback model after primary failure.")
    parser.add_argument("--sync-log", type=Path, default=DEFAULT_SYNC_LOG, help="Chunk sync log path.")
    parser.add_argument("--run-log", type=Path, default=DEFAULT_RUN_LOG, help="Operational log path.")
    parser.add_argument("--duplicate-source-filter", default="", help="Restrict semantic duplicate checks to one source. Empty checks all thoughts.")
    parser.add_argument("--skip-duplicate-check", action="store_true", help="Skip semantic duplicate checks before insert.")
    parser.add_argument("--insert-sleep", type=float, default=0.2, help="Delay between inserts.")
    parser.add_argument("--max-consecutive-errors", type=int, default=5, help="Stop after this many consecutive chunk errors.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(REPO_ROOT / ".env.local")
    load_env_file(REPO_ROOT / ".local" / "model-bakeoff" / ".env")
    refresh_env()

    records, chunks = run_plan(args.sources, args)
    log_line(args.run_log, f"plan records={len(records)} chunks={len(chunks)} model={args.openrouter_model}")
    if args.plan_only:
        return 0

    require_env(["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENROUTER_API_KEY"])
    sync = read_json(args.sync_log, {"version": VERSION, "chunks": {}, "last_sync": "", "stats": {}})
    sync.setdefault("chunks", {})
    sync.setdefault("stats", {})
    stats = {"chunks_processed": 0, "thoughts": 0, "duplicates": 0, "ingested": 0, "errors": 0}
    consecutive_errors = 0
    processed = 0
    batch = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    log_line(args.run_log, f"start batch={batch} limit={args.limit_chunks or 'all'} dry_run={args.dry_run}")
    for chunk in chunks:
        if chunk.chunk_id in sync["chunks"]:
            continue
        if args.limit_chunks and processed >= args.limit_chunks:
            break
        processed += 1
        extraction = summarize_openrouter(chunk, args.openrouter_model, args.fallback_openrouter_model)
        thoughts = extraction.get("thoughts", [])
        chunk_errors = 0
        duplicates = 0
        ingested = 0

        for thought in thoughts:
            content_text = thought["content"]
            if not args.dry_run and not args.skip_duplicate_check:
                if check_semantic_duplicate(content_text, source_filter=args.duplicate_source_filter):
                    duplicates += 1
                    continue
            metadata = {
                "source": "whatsapp",
                "whatsapp_chat_name": chunk.chat_name,
                "whatsapp_source_name": chunk.record.source_name,
                "whatsapp_source_kind": chunk.record.source_kind,
                "whatsapp_record_id": chunk.record.record_id,
                "whatsapp_chunk_id": chunk.chunk_id,
                "whatsapp_chunk_index": chunk.chunk_index,
                "whatsapp_chunk_count": chunk.chunk_count,
                "whatsapp_start_date": chunk.start_date,
                "whatsapp_end_date": chunk.end_date,
                "whatsapp_message_count": chunk.message_count,
                "whatsapp_conversation_type": extraction.get("conversation_type", ""),
                "type": thought.get("type", "context"),
                "topics": thought.get("topics", []),
                "people": thought.get("people", []),
                "confidence": thought.get("confidence", "firm"),
                "import_batch": batch,
                "import_mode": "whatsapp_chat_import_v1",
            }
            content = f"[WhatsApp: {chunk.chat_name}] {content_text}"
            if args.dry_run:
                ingested += 1
                continue
            result = ingest_thought(content, metadata, embed_text=content_text)
            if result.get("ok"):
                ingested += 1
            else:
                chunk_errors += 1
            time.sleep(args.insert_sleep)

        stats["chunks_processed"] += 1
        stats["thoughts"] += len(thoughts)
        stats["duplicates"] += duplicates
        stats["ingested"] += ingested
        stats["errors"] += chunk_errors
        if chunk_errors:
            consecutive_errors += 1
        else:
            consecutive_errors = 0

        if not args.dry_run:
            sync["chunks"][chunk.chunk_id] = {
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "record_id": chunk.record.record_id,
                "source_kind": chunk.record.source_kind,
                "chat_name": chunk.chat_name,
                "chunk_index": chunk.chunk_index,
                "chunk_count": chunk.chunk_count,
                "message_count": chunk.message_count,
                "thoughts": len(thoughts),
                "duplicates": duplicates,
                "ingested": ingested,
                "errors": chunk_errors,
            }
            sync["last_sync"] = datetime.now(timezone.utc).isoformat()
            sync["stats"] = stats
            write_json(args.sync_log, sync)

        status = (
            f"chunk processed={processed} ok={chunk_errors == 0} chunk_id={chunk.chunk_id} "
            f"source_kind={chunk.source_kind} thoughts={len(thoughts)} duplicates={duplicates} "
            f"ingested={ingested} errors={chunk_errors} dry_run={args.dry_run}"
        )
        print(status)
        log_line(args.run_log, status)
        if consecutive_errors >= args.max_consecutive_errors:
            print(f"stopped_after_consecutive_errors={consecutive_errors}")
            log_line(args.run_log, f"stopped_after_consecutive_errors={consecutive_errors}")
            break

    stop = (
        f"stop processed={stats['chunks_processed']} thoughts={stats['thoughts']} "
        f"duplicates={stats['duplicates']} ingested={stats['ingested']} errors={stats['errors']} dry_run={args.dry_run}"
    )
    print(stop)
    log_line(args.run_log, stop)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
