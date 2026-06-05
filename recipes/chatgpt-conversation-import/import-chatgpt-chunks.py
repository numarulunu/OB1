"""
Open Brain - ChatGPT chunk-level audit importer (v1.0).

Reprocesses large ChatGPT exports at the LLM chunk level while reusing the
existing OB1 ChatGPT parser, OpenRouter extraction, duplicate detection, and
Supabase insert functions. Logs only operational counters, not chat content.
"""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_SOURCE = REPO_ROOT / ".local" / "open-brain-imports" / "chatgpt-browser-export"
DEFAULT_SYNC_LOG = REPO_ROOT / ".local" / "open-brain-imports" / "chatgpt-chunk-sync-log.json"
DEFAULT_RUN_LOG = REPO_ROOT / ".local" / "open-brain-imports" / "_import-chatgpt-chunks.log"
DEFAULT_MODEL = "qwen/qwen3-235b-a22b-2507"

sys.path.insert(0, str(SCRIPT_DIR))

from chatgpt_parser import (  # noqa: E402
    conversation_hash,
    count_messages,
    extract_conversation_metadata,
    extract_conversations,
    prepare_dialogue_for_extraction,
    resolve_canonical_path,
    should_skip,
)


def load_env_file(path):
    """Load KEY=VALUE lines into process env without printing values."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def load_recipe_module():
    """Load import-chatgpt.py after environment variables are available."""
    module_path = SCRIPT_DIR / "import-chatgpt.py"
    spec = importlib.util.spec_from_file_location("ob1_import_chatgpt", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(path)


def log_line(path, message):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"{now_iso()} {message}\n")


def normalize_supabase_env():
    value = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if value.endswith("/rest/v1"):
        value = value[:-8]
    if value:
        os.environ["SUPABASE_URL"] = value


def require_env(names):
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing))


def date_str_from_timestamp(value):
    if not value:
        return "unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")


def chunk_id_for(conv_hash, update_time, chunk_index, chunk_text):
    raw = "|".join(
        [
            conv_hash,
            str(update_time or ""),
            str(chunk_index),
            hashlib.sha256(chunk_text.encode("utf-8", errors="ignore")).hexdigest(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def iter_chunks(source_path, args):
    conversations = extract_conversations(str(source_path))
    conversations.sort(key=lambda c: c.get("create_time", 0))

    empty_sync = {"ingested_ids": {}, "last_sync": ""}
    filter_counts = {}
    yielded = 0

    for conv_index, conv in enumerate(conversations, 1):
        messages = resolve_canonical_path(conv.get("mapping", {}), conv.get("current_node"))
        message_count = count_messages(messages)
        dialogue_texts = prepare_dialogue_for_extraction(messages, len(conversations))

        skip_reason = should_skip(conv, "\n".join(dialogue_texts), message_count, empty_sync, args)
        if skip_reason:
            filter_counts[skip_reason] = filter_counts.get(skip_reason, 0) + 1
            continue

        conv_hash = conversation_hash(conv)
        conv_meta = extract_conversation_metadata(conv)
        date_str = date_str_from_timestamp(conv.get("create_time"))
        chunk_count = len(dialogue_texts)

        for chunk_index, chunk_text in enumerate(dialogue_texts, 1):
            yielded += 1
            yield {
                "chunk_id": chunk_id_for(conv_hash, conv.get("update_time"), chunk_index, chunk_text),
                "conv_index": conv_index,
                "conv_hash": conv_hash,
                "conv": conv,
                "conv_meta": conv_meta,
                "date_str": date_str,
                "message_count": message_count,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "chunk_text": chunk_text,
                "chunk_words": len(chunk_text.split()),
                "chunk_chars": len(chunk_text),
            }

    return filter_counts, yielded


class StdoutCapture:
    def __init__(self):
        self.buffer = io.StringIO()

    def __enter__(self):
        self.redirect = contextlib.redirect_stdout(self.buffer)
        self.redirect.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.redirect.__exit__(exc_type, exc, tb)

    def warning_count(self):
        text = self.buffer.getvalue()
        return text.count("Warning:") + text.count("Error:")


def process_chunk(recipe, chunk, args, import_batch):
    conv = chunk["conv"]
    title = conv.get("title", "(untitled)")
    model_slug = chunk["conv_meta"].get("model_slug") or ""
    fallback = args.fallback_openrouter_model or None

    with StdoutCapture() as captured:
        extraction = recipe.summarize_openrouter(
            title,
            chunk["date_str"],
            chunk["chunk_text"],
            chunk["message_count"],
            model_slug,
            False,
            args.openrouter_model,
            "",
            fallback,
        )
    thoughts = extraction.get("thoughts", [])

    stats = {
        "thoughts": len(thoughts),
        "duplicates": 0,
        "ingested": 0,
        "errors": 0,
        "warnings": captured.warning_count(),
    }

    all_ok = True
    for thought in thoughts:
        content_text = thought.get("content", "")
        if not content_text:
            continue

        if not args.skip_duplicate_check:
            with StdoutCapture() as duplicate_capture:
                duplicate = recipe.check_semantic_duplicate(content_text)
            stats["warnings"] += duplicate_capture.warning_count()
            if duplicate:
                stats["duplicates"] += 1
                continue

        metadata = {
            "source": "chatgpt",
            "chatgpt_title": title,
            "chatgpt_create_time": chunk["date_str"],
            "chatgpt_conversation_hash": chunk["conv_hash"],
            "chatgpt_conversation_id": conv.get("id", ""),
            "chatgpt_chunk_id": chunk["chunk_id"],
            "chatgpt_chunk_index": chunk["chunk_index"],
            "chatgpt_chunk_count": chunk["chunk_count"],
            "chatgpt_model": model_slug,
            "chatgpt_message_count": chunk["message_count"],
            "chatgpt_conversation_type": extraction.get("conversation_type", ""),
            "confidence": thought.get("confidence", ""),
            "type": thought.get("type", "context"),
            "topics": thought.get("topics", []),
            "people": thought.get("people", []),
            "voice": chunk["conv_meta"].get("voice"),
            "gizmo_id": chunk["conv_meta"].get("gizmo_id"),
            "language": extraction.get("language", "en"),
            "import_batch": import_batch,
            "import_mode": "chatgpt_chunk_audit_v1",
        }
        content = f"[ChatGPT: {title} | {chunk['date_str']}] {content_text}"

        with StdoutCapture() as ingest_capture:
            result = recipe.ingest_thought_supabase(content, metadata, embed_text=content_text)
        stats["warnings"] += ingest_capture.warning_count()
        if result.get("ok"):
            stats["ingested"] += 1
        else:
            stats["errors"] += 1
            all_ok = False

        time.sleep(args.insert_sleep)

    return all_ok, stats


def parse_args():
    parser = argparse.ArgumentParser(description="Chunk-level ChatGPT audit importer for OB1.")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE), help="Export directory containing conversations.json")
    parser.add_argument("--sync-log", default=str(DEFAULT_SYNC_LOG), help="Chunk sync log path")
    parser.add_argument("--run-log", default=str(DEFAULT_RUN_LOG), help="Operational log path")
    parser.add_argument("--openrouter-model", default=DEFAULT_MODEL, help="OpenRouter extraction model")
    parser.add_argument("--fallback-openrouter-model", default="", help="Optional fallback model")
    parser.add_argument("--limit", type=int, default=0, help="Max unsynced chunks to process")
    parser.add_argument("--plan-only", action="store_true", help="Count chunks without API calls or DB writes")
    parser.add_argument("--skip-duplicate-check", action="store_true", help="Do not run semantic duplicate checks before insert")
    parser.add_argument("--insert-sleep", type=float, default=0.2, help="Delay between thought inserts")
    parser.add_argument("--min-messages", type=int, default=0, help="Override minimum message count for filtering")
    parser.add_argument("--min-words", type=int, default=0, help="Override minimum word count for filtering")
    parser.add_argument("--after", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--before", default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def main():
    args = parse_args()
    source_path = Path(args.source).resolve()
    sync_path = Path(args.sync_log).resolve()
    run_log = Path(args.run_log).resolve()

    if not source_path.exists():
        raise SystemExit(f"Source path not found: {source_path}")

    load_env_file(REPO_ROOT / ".env.local")
    load_env_file(REPO_ROOT / ".local" / "model-bakeoff" / ".env")
    normalize_supabase_env()

    sync = read_json(sync_path, {"version": 1, "chunks": {}, "last_sync": "", "stats": {}})
    sync.setdefault("chunks", {})
    sync.setdefault("stats", {})

    # Capture parser status output so console/log stay content-free.
    with StdoutCapture():
        chunks = list(iter_chunks(source_path, args))

    total_chunks = len(chunks)
    synced_chunks = sum(1 for c in chunks if c["chunk_id"] in sync["chunks"])
    remaining_chunks = total_chunks - synced_chunks
    total_words = sum(c["chunk_words"] for c in chunks)
    total_chars = sum(c["chunk_chars"] for c in chunks)

    summary = (
        f"plan source={source_path} total_chunks={total_chunks} synced={synced_chunks} "
        f"remaining={remaining_chunks} chunk_words={total_words} chunk_chars={total_chars} model={args.openrouter_model}"
    )
    print(summary)
    log_line(run_log, summary)

    if args.plan_only:
        return 0

    require_env(["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENROUTER_API_KEY"])
    recipe = load_recipe_module()
    import_batch = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    processed = 0
    run_stats = {
        "chunks_processed": 0,
        "thoughts": 0,
        "duplicates": 0,
        "ingested": 0,
        "errors": 0,
        "warnings": 0,
    }
    log_line(run_log, f"start batch={import_batch} limit={args.limit or 'all'}")

    for chunk in chunks:
        if chunk["chunk_id"] in sync["chunks"]:
            continue
        if args.limit and processed >= args.limit:
            break

        processed += 1
        ok, stats = process_chunk(recipe, chunk, args, import_batch)
        for key in run_stats:
            if key == "chunks_processed":
                continue
            run_stats[key] += stats.get(key, 0)
        run_stats["chunks_processed"] += 1

        if ok:
            sync["chunks"][chunk["chunk_id"]] = {
                "imported_at": now_iso(),
                "conv_hash": chunk["conv_hash"],
                "chunk_index": chunk["chunk_index"],
                "chunk_count": chunk["chunk_count"],
                "thoughts": stats["thoughts"],
                "duplicates": stats["duplicates"],
                "ingested": stats["ingested"],
            }
            sync["last_sync"] = now_iso()
            sync["stats"] = run_stats
            write_json(sync_path, sync)

        status = (
            f"chunk processed={processed} ok={ok} chunk_id={chunk['chunk_id']} "
            f"thoughts={stats['thoughts']} duplicates={stats['duplicates']} "
            f"ingested={stats['ingested']} errors={stats['errors']} warnings={stats['warnings']}"
        )
        print(status)
        log_line(run_log, status)

        if stats["errors"]:
            time.sleep(2)

    done = (
        f"stop processed={run_stats['chunks_processed']} thoughts={run_stats['thoughts']} "
        f"duplicates={run_stats['duplicates']} ingested={run_stats['ingested']} "
        f"errors={run_stats['errors']} warnings={run_stats['warnings']}"
    )
    print(done)
    log_line(run_log, done)
    return 0 if run_stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
