#!/usr/bin/env python3
"""
Open Brain - Gemini Conversation Importer (v1.0).

Imports Gemini text exports into OB1 as distilled, duplicate-checked thoughts.
It scans directories, zip archives, and text files, dedupes normalized chat
content, splits oversized chats into chunks, extracts memories with OpenRouter,
and inserts into Supabase. Operational logs never include raw chat text.
"""

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
DEFAULT_SYNC_LOG = REPO_ROOT / ".local" / "open-brain-imports" / "gemini-sync-log.json"
DEFAULT_RUN_LOG = REPO_ROOT / ".local" / "open-brain-imports" / "_import-gemini.log"
DEFAULT_MODEL = "qwen/qwen3-235b-a22b-2507"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MAX_THOUGHTS_PER_CHUNK = 5
DEFAULT_MAX_CHARS = 70000

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

EXTRACTION_PROMPT = """\
You are distilling a Gemini conversation into durable personal/project memory.
Be highly selective. Extract only information that would still be useful months
from now in a personal external brain.

Capture:
- Decisions, commitments, plans, and why they mattered
- Project context, architecture choices, strategies, constraints, and status
- Personal preferences, communication style, values, recurring behavior, and goals
- Useful research findings tied to the user's projects or life
- Durable references: people, tools, businesses, places, workflows, health/voice/tax/finance context

Skip:
- One-off lookups with no personal context
- Generic advice not connected to the user
- Repeated instructions or boilerplate
- Raw transcript summaries

Each thought must be standalone, specific, and written as a concise memory.
Return 0-5 thoughts. Zero is correct if there is nothing worth remembering.

Each thought object must have:
- content: 1-3 sentence memory
- type: one of decision, preference, learning, context, brainstorm, reference
- topics: short list
- people: short list
- confidence: tentative, inferred, or firm

Return only JSON:
{{"thoughts": [...], "conversation_type": "...", "skip_reason": "..."}}

Title: {title}
Source: Gemini
Chunk: {chunk_index}/{chunk_count}
Approx words: {word_count}

Conversation text:
{conversation_text}"""


class GeminiRecord(NamedTuple):
    record_id: str
    title: str
    text: str
    source_name: str
    source_kind: str
    source_path: str
    byte_count: int


class GeminiChunk(NamedTuple):
    chunk_id: str
    record: GeminiRecord
    chunk_index: int
    chunk_count: int
    text: str


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
    text = re.sub(r"\r+\n", "\n", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8", errors="ignore")).hexdigest()


def title_from_name(name: str) -> str:
    return Path(name).stem.strip() or "untitled"


def should_skip_text_file(path_or_name: str, include_merged: bool) -> bool:
    name = Path(path_or_name).name.lower()
    if not name.endswith(".txt"):
        return True
    if not include_merged and "merged" in name:
        return True
    return False


def make_record(source_kind: str, source_path: str, source_name: str, raw_bytes: bytes) -> GeminiRecord | None:
    text = normalize_text(raw_bytes.decode("utf-8-sig", errors="replace"))
    if not text:
        return None
    digest = text_hash(text)
    return GeminiRecord(
        record_id=digest[:24],
        title=title_from_name(source_name),
        text=text,
        source_name=Path(source_name).name,
        source_kind=source_kind,
        source_path=source_path,
        byte_count=len(raw_bytes),
    )


def scan_gemini_sources(source_paths: list[str | Path], include_merged: bool = False) -> list[GeminiRecord]:
    records: list[GeminiRecord] = []
    seen_hashes: set[str] = set()

    def add_record(record: GeminiRecord | None) -> None:
        if not record:
            return
        digest = text_hash(record.text)
        if digest in seen_hashes:
            return
        seen_hashes.add(digest)
        records.append(record)

    for raw_source in source_paths:
        source = Path(raw_source)
        if not source.exists():
            raise FileNotFoundError(f"Source path not found: {source}")
        if source.is_dir():
            for path in sorted(source.rglob("*.txt"), key=lambda p: str(p).lower()):
                if should_skip_text_file(path.name, include_merged):
                    continue
                add_record(make_record("directory", str(path), path.name, path.read_bytes()))
        elif source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as zf:
                for info in sorted((i for i in zf.infolist() if not i.is_dir()), key=lambda i: i.filename.lower()):
                    if should_skip_text_file(info.filename, include_merged):
                        continue
                    add_record(make_record("zip", str(source), info.filename, zf.read(info.filename)))
        elif source.suffix.lower() == ".txt":
            if should_skip_text_file(source.name, include_merged):
                continue
            add_record(make_record("file", str(source), source.name, source.read_bytes()))
        else:
            raise ValueError(f"Unsupported source path: {source}")

    return records


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


def build_chunks(records: list[GeminiRecord], max_chars: int) -> list[GeminiChunk]:
    chunks: list[GeminiChunk] = []
    for record in records:
        texts = split_text_chunks(record.text, max_chars=max_chars)
        for index, text in enumerate(texts, start=1):
            raw = f"{record.record_id}|{index}|{text_hash(text)}"
            chunk_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
            chunks.append(GeminiChunk(chunk_id, record, index, len(texts), text))
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
                "topics": [str(t) for t in topics if str(t).strip()],
                "people": [str(p) for p in people if str(p).strip()],
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


def summarize_openrouter(chunk: GeminiChunk, model: str, fallback_model: str = "") -> dict:
    prompt = EXTRACTION_PROMPT.format(
        title=chunk.record.title,
        chunk_index=chunk.chunk_index,
        chunk_count=chunk.chunk_count,
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
        headers={"Content-Type": "application/json", "apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
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


def run_plan(source_paths: list[str], args: argparse.Namespace) -> tuple[list[GeminiRecord], list[GeminiChunk]]:
    records = scan_gemini_sources(source_paths, include_merged=args.include_merged)
    chunks = build_chunks(records, max_chars=args.max_chars)
    total_chars = sum(len(record.text) for record in records)
    total_words = sum(len(record.text.split()) for record in records)
    print(
        f"plan records={len(records)} chunks={len(chunks)} chars={total_chars} "
        f"words={total_words} max_chars={args.max_chars} include_merged={args.include_merged}"
    )
    return records, chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Gemini text exports into Open Brain.")
    parser.add_argument("sources", nargs="+", help="Gemini txt file, zip archive, or directory. Pass all account sources.")
    parser.add_argument("--plan-only", action="store_true", help="Scan and count only. No API calls or DB writes.")
    parser.add_argument("--dry-run", action="store_true", help="Extract with LLM but do not insert into Supabase.")
    parser.add_argument("--include-merged", action="store_true", help="Include files with 'merged' in the filename.")
    parser.add_argument("--limit-chunks", type=int, default=0, help="Max unsynced chunks to process.")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Max chars per LLM chunk.")
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
                "source": "gemini",
                "gemini_title": chunk.record.title,
                "gemini_source_name": chunk.record.source_name,
                "gemini_source_kind": chunk.record.source_kind,
                "gemini_record_id": chunk.record.record_id,
                "gemini_chunk_id": chunk.chunk_id,
                "gemini_chunk_index": chunk.chunk_index,
                "gemini_chunk_count": chunk.chunk_count,
                "gemini_conversation_type": extraction.get("conversation_type", ""),
                "type": thought.get("type", "context"),
                "topics": thought.get("topics", []),
                "people": thought.get("people", []),
                "confidence": thought.get("confidence", "firm"),
                "import_batch": batch,
                "import_mode": "gemini_conversation_import_v1",
            }
            content = f"[Gemini: {chunk.record.title}] {content_text}"
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

        sync["chunks"][chunk.chunk_id] = {
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "record_id": chunk.record.record_id,
            "chunk_index": chunk.chunk_index,
            "chunk_count": chunk.chunk_count,
            "thoughts": len(thoughts),
            "duplicates": duplicates,
            "ingested": ingested,
            "errors": chunk_errors,
            "dry_run": args.dry_run,
        }
        sync["last_sync"] = datetime.now(timezone.utc).isoformat()
        sync["stats"] = stats
        write_json(args.sync_log, sync)

        status = (
            f"chunk processed={processed} ok={chunk_errors == 0} chunk_id={chunk.chunk_id} "
            f"thoughts={len(thoughts)} duplicates={duplicates} ingested={ingested} errors={chunk_errors}"
        )
        print(status)
        log_line(args.run_log, status)
        if consecutive_errors >= args.max_consecutive_errors:
            print(f"stopped_after_consecutive_errors={consecutive_errors}")
            log_line(args.run_log, f"stopped_after_consecutive_errors={consecutive_errors}")
            break

    stop = (
        f"stop processed={stats['chunks_processed']} thoughts={stats['thoughts']} "
        f"duplicates={stats['duplicates']} ingested={stats['ingested']} errors={stats['errors']}"
    )
    print(stop)
    log_line(args.run_log, stop)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
