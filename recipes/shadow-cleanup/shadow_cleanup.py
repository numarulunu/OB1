#!/usr/bin/env python3
"""Read-only shadow cleanup proposal pipeline for OB1 thoughts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

VERSION = "0.1.0"
OUTPUT_ROOT = Path(".local") / "open-brain-cleanup"
LOG_PATH = Path(__file__).resolve().parent / "_shadow-cleanup.log"
SNAPSHOT_SELECT = "id,content,metadata,created_at,updated_at"


class ShadowCleanupError(RuntimeError):
    """Plain-language failure for CLI output."""


class RateLimitError(ShadowCleanupError):
    """Claude usage or rate limit stopped this worker safely."""


def is_rate_limit_error(message: str) -> bool:
    lower = str(message or "").lower()
    patterns = (
        "429",
        "rate limit",
        "rate_limit",
        "too many requests",
        "usage limit",
        "quota",
        "credit balance",
        "billing hard limit",
    )
    return any(pattern in lower for pattern in patterns)


def reached_consecutive_error_limit(consecutive_errors: int, max_consecutive_errors: int) -> bool:
    return max_consecutive_errors > 0 and consecutive_errors >= max_consecutive_errors


def claude_cli_error_text(stdout: str, stderr: str, returncode: int) -> str:
    parts: list[str] = []
    if stderr.strip():
        parts.append(stderr.strip())
    try:
        parsed = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict) and (returncode != 0 or parsed.get("is_error")):
        for key in ("api_error_status", "terminal_reason", "stop_reason", "subtype", "result"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    elif returncode != 0 and stdout.strip():
        parts.append(stdout.strip())
    return "\n".join(parts)


def canonical_text(text: str) -> str:
    """Return text normalized for exact duplicate grouping."""
    return " ".join(str(text or "").strip().lower().split())


def pack_id_for(kind: str, cluster_key: str, items: list[dict[str, Any]]) -> str:
    ids = [str(item.get("id", "")) for item in items]
    raw = json.dumps([kind, cluster_key, ids], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def pack_item(record: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": record.get("id"),
        "content": record.get("content", ""),
        "metadata": record.get("metadata") or {},
        "created_at": record.get("created_at"),
    }
    if record.get("updated_at"):
        item["updated_at"] = record.get("updated_at")
    return item


def build_exact_duplicate_packs(records: Iterable[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = canonical_text(record.get("content", ""))
        if not key:
            continue
        groups.setdefault(key, []).append(record)

    packs: list[dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        if len(group) < 2:
            continue
        items = [pack_item(record) for record in group[:max_items]]
        pack = {
            "pack_id": pack_id_for("exact_duplicate", key, items),
            "kind": "exact_duplicate",
            "cluster_key": f"sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}",
            "record_count": len(group),
            "truncated": len(group) > max_items,
            "items": items,
        }
        packs.append(pack)
    return packs


def first_topic(metadata: dict[str, Any]) -> str | None:
    topics = metadata.get("topics")
    if isinstance(topics, list):
        for topic in topics:
            cleaned = canonical_text(str(topic))
            if cleaned:
                return cleaned
    if isinstance(topics, str) and canonical_text(topics):
        return canonical_text(topics)
    topic = metadata.get("topic")
    if isinstance(topic, str) and canonical_text(topic):
        return canonical_text(topic)
    return None


def project_name(metadata: dict[str, Any]) -> str | None:
    for key in ("claude_project", "project", "source_project", "repo", "workspace"):
        value = metadata.get(key)
        if isinstance(value, str) and canonical_text(value):
            return canonical_text(value)
    return None


def build_topic_packs(records: Iterable[dict[str, Any]], min_items: int, max_items: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        metadata = record.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        project = project_name(metadata)
        topic = first_topic(metadata)
        if not project and not topic:
            continue
        cluster_key = f"project:{project or 'unknown'}|topic:{topic or 'general'}"
        groups.setdefault(cluster_key, []).append(record)

    packs: list[dict[str, Any]] = []
    for cluster_key in sorted(groups):
        group = groups[cluster_key]
        if len(group) < min_items:
            continue
        items = [pack_item(record) for record in group[:max_items]]
        packs.append(
            {
                "pack_id": pack_id_for("topic_cluster", cluster_key, items),
                "kind": "topic_cluster",
                "cluster_key": cluster_key,
                "record_count": len(group),
                "truncated": len(group) > max_items,
                "items": items,
            }
        )
    return packs


def strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_claude_json(text: str) -> dict[str, Any]:
    cleaned = strip_code_fence(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])

    if isinstance(parsed, dict) and isinstance(parsed.get("structured_output"), dict):
        return parsed["structured_output"]
    if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
        return parse_claude_json(parsed["result"])
    if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict):
        return parsed["result"]
    if not isinstance(parsed, dict):
        raise ShadowCleanupError("Claude returned JSON, but it was not an object.")
    return parsed


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def ensure_output_dirs(root: Path) -> None:
    for child in ("snapshots", "packs", "proposals"):
        (root / child).mkdir(parents=True, exist_ok=True)


def load_env_values(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def normalize_supabase_url(url: str) -> str:
    cleaned = url.strip().strip('"').strip("'").rstrip("/")
    if cleaned.endswith("/rest/v1"):
        cleaned = cleaned[: -len("/rest/v1")]
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "https://" + cleaned
    return cleaned.rstrip("/")


def supabase_credentials(env_file: Path) -> tuple[str, str]:
    file_values = load_env_values(env_file)
    url = os.environ.get("SUPABASE_URL") or file_values.get("SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or file_values.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
        or file_values.get("SUPABASE_KEY")
    )
    if not url or not key:
        raise ShadowCleanupError(
            "Supabase credentials are not available. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
        )
    return normalize_supabase_url(url), key


def fetch_thoughts(url: str, key: str, page_size: int, limit: int | None) -> Iterable[dict[str, Any]]:
    offset = 0
    yielded = 0
    base = f"{url}/rest/v1/thoughts?select={SNAPSHOT_SELECT}&order=created_at.asc"
    while True:
        if limit is not None and yielded >= limit:
            break
        remaining = page_size if limit is None else min(page_size, limit - yielded)
        end = offset + remaining - 1
        request = urllib.request.Request(
            base,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Range": f"{offset}-{end}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                rows = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ShadowCleanupError(f"Supabase snapshot request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ShadowCleanupError(f"Supabase snapshot request failed: {exc.reason}") from exc

        if not isinstance(rows, list):
            raise ShadowCleanupError("Supabase returned an unexpected response shape.")
        if not rows:
            break
        for row in rows:
            yielded += 1
            yield row
        if len(rows) < remaining:
            break
        offset += len(rows)


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if limit is not None and len(records) >= limit:
                break
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def latest_file(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def build_all_packs(records: list[dict[str, Any]], max_items: int, topic_min_items: int) -> list[dict[str, Any]]:
    packs = build_exact_duplicate_packs(records, max_items=max_items)
    packs.extend(build_topic_packs(records, min_items=topic_min_items, max_items=max_items))
    return packs


def shard_records(records: list[dict[str, Any]], shard_count: int) -> list[list[dict[str, Any]]]:
    if shard_count < 1:
        raise ShadowCleanupError("Shard count must be at least 1.")
    shards: list[list[dict[str, Any]]] = [[] for _ in range(shard_count)]
    for index, record in enumerate(records):
        shards[index % shard_count].append(record)
    return shards


def proposal_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["canonical_memories", "archive_candidates", "risk_notes"],
        "properties": {
            "canonical_memories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["content", "source_ids", "confidence", "reason"],
                    "properties": {
                        "content": {"type": "string"},
                        "source_ids": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                        "metadata_patch": {"type": "object"},
                    },
                },
            },
            "archive_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "reason"],
                    "properties": {
                        "id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "risk_notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def build_claude_prompt(pack: dict[str, Any]) -> str:
    schema = json.dumps(proposal_schema(), ensure_ascii=False, indent=2)
    pack_json = json.dumps(pack, ensure_ascii=False, indent=2)
    return (
        "You are doing read-only cleanup planning for a personal OB1 memory database.\n"
        "Return JSON only. Do not use tools. Do not suggest deleting anything from the live database.\n"
        "Goal: consolidate duplicate or overlapping memory rows into high-signal reusable memories.\n"
        "Rules:\n"
        "- Preserve specific user preferences, project decisions, dates, and constraints.\n"
        "- Do not invent facts not present in the pack.\n"
        "- Mark low-signal, duplicate, or superseded rows as archive candidates only.\n"
        "- Keep canonical memory content concise, atomic, and searchable.\n"
        "- If the pack is unsafe or too mixed, return an empty canonical_memories list and explain in risk_notes.\n\n"
        f"JSON schema to follow:\n{schema}\n\n"
        f"Pack:\n{pack_json}\n"
    )


def find_claude(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude.exe"
    if local.exists():
        return str(local)
    raise ShadowCleanupError("Claude CLI was not found on PATH or ~/.local/bin/claude.exe.")


def read_processed_pack_ids(path: Path) -> set[str]:
    processed: set[str] = set()
    if not path.exists():
        return processed
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            pack_id = record.get("pack_id")
            if isinstance(pack_id, str) and "error" not in record:
                processed.add(pack_id)
    return processed


def build_claude_command(claude: str, model: str, max_budget_usd: float | None) -> list[str]:
    command = [
        claude,
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(proposal_schema(), separators=(",", ":")),
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
    ]
    if max_budget_usd is not None:
        command.extend(["--max-budget-usd", str(max_budget_usd)])
    return command


def run_claude_for_pack(
    claude: str,
    model: str,
    pack: dict[str, Any],
    timeout: int,
    max_budget_usd: float | None,
) -> dict[str, Any]:
    command = build_claude_command(claude, model, max_budget_usd)
    result = subprocess.run(
        command,
        input=build_claude_prompt(pack),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    error_text = claude_cli_error_text(result.stdout, result.stderr, result.returncode)
    if result.returncode != 0:
        if is_rate_limit_error(error_text):
            raise RateLimitError("Claude rate or usage limit reached; stop this worker and resume later.")
        raise ShadowCleanupError(f"Claude CLI failed for pack {pack.get('pack_id')} with exit code {result.returncode}.")
    if is_rate_limit_error(error_text):
        raise RateLimitError("Claude rate or usage limit reached; stop this worker and resume later.")
    proposal = parse_claude_json(result.stdout)
    return {
        "pack_id": pack.get("pack_id"),
        "pack_kind": pack.get("kind"),
        "cluster_key": pack.get("cluster_key"),
        "model": model,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "proposal": proposal,
    }


def cmd_status(args: argparse.Namespace) -> int:
    root = args.output_root
    ensure_output_dirs(root)
    snapshot = latest_file(root / "snapshots", "*.jsonl")
    pack_file = latest_file(root / "packs", "*.jsonl")
    proposal = latest_file(root / "proposals", "*.jsonl")
    print(f"shadow_cleanup_version={VERSION}")
    print(f"output_root={root}")
    print(f"latest_snapshot={snapshot if snapshot else 'none'}")
    print(f"latest_snapshot_rows={count_jsonl(snapshot) if snapshot else 0}")
    print(f"latest_pack_file={pack_file if pack_file else 'none'}")
    print(f"latest_pack_count={count_jsonl(pack_file) if pack_file else 0}")
    print(f"latest_proposal_file={proposal if proposal else 'none'}")
    print(f"latest_proposal_count={count_jsonl(proposal) if proposal else 0}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    url, key = supabase_credentials(args.env_file)
    output = args.output or args.output_root / "snapshots" / f"thoughts-{utc_stamp()}.jsonl"
    count = write_jsonl(output, fetch_thoughts(url, key, args.page_size, args.limit))
    logging.info("snapshot path=%s rows=%s", output, count)
    print(f"snapshot_rows={count}")
    print(f"snapshot_file={output}")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    snapshot = args.snapshot or latest_file(args.output_root / "snapshots", "*.jsonl")
    if not snapshot:
        raise ShadowCleanupError("No snapshot file found. Run the snapshot command first.")
    records = read_jsonl(snapshot, limit=args.limit_records)
    packs = build_all_packs(records, max_items=args.max_items, topic_min_items=args.topic_min_items)
    if args.limit_packs is not None:
        packs = packs[: args.limit_packs]
    output = args.output or args.output_root / "packs" / f"packs-{utc_stamp()}.jsonl"
    count = write_jsonl(output, packs)
    logging.info("pack snapshot=%s output=%s packs=%s", snapshot, output, count)
    print(f"pack_count={count}")
    print(f"pack_file={output}")
    return 0


def cmd_shard(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    pack_file = args.pack_file or latest_file(args.output_root / "packs", "*.jsonl")
    if not pack_file:
        raise ShadowCleanupError("No pack file found. Run the pack command first.")
    records = read_jsonl(pack_file)
    shards = shard_records(records, args.shards)
    output_dir = args.output_dir or args.output_root / "packs" / f"shards-{utc_stamp()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, shard in enumerate(shards, start=1):
        output = output_dir / f"packs-shard-{index:02d}-of-{args.shards:02d}.jsonl"
        write_jsonl(output, shard)
        print(f"shard_{index:02d}_packs={len(shard)}")
        print(f"shard_{index:02d}_file={output}")
    logging.info("shard pack_file=%s output_dir=%s shards=%s", pack_file, output_dir, args.shards)
    return 0


def cmd_run_claude(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    pack_file = args.pack_file or latest_file(args.output_root / "packs", "*.jsonl")
    if not pack_file:
        raise ShadowCleanupError("No pack file found. Run the pack command first.")
    output = args.output or args.output_root / "proposals" / f"proposals-{utc_stamp()}.jsonl"
    claude = find_claude(args.claude_path)
    processed = read_processed_pack_ids(output)
    packs = read_jsonl(pack_file)
    written = 0
    attempted = 0
    consecutive_errors = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for pack in packs:
            pack_id = pack.get("pack_id")
            if isinstance(pack_id, str) and pack_id in processed:
                continue
            if args.limit_packs is not None and attempted >= args.limit_packs:
                break
            attempted += 1
            try:
                proposal = run_claude_for_pack(claude, args.model, pack, args.timeout, args.max_budget_usd)
                consecutive_errors = 0
            except RateLimitError as exc:
                logging.warning("rate limit stop pack_id=%s", pack_id)
                print(f"rate_limited=true", file=sys.stderr)
                print(f"stopped_before_pack_id={pack_id}", file=sys.stderr)
                print(f"reason={exc}", file=sys.stderr)
                break
            except Exception as exc:  # noqa: BLE001 - keep batch moving and log the pack id only.
                logging.exception("claude failed pack_id=%s", pack_id)
                consecutive_errors += 1
                proposal = {
                    "pack_id": pack_id,
                    "pack_kind": pack.get("kind"),
                    "cluster_key": pack.get("cluster_key"),
                    "model": args.model,
                    "created_at": dt.datetime.now(dt.UTC).isoformat(),
                    "error": str(exc),
                }
            handle.write(json.dumps(proposal, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            written += 1
            if reached_consecutive_error_limit(consecutive_errors, args.max_consecutive_errors):
                logging.warning("stopping after consecutive errors count=%s", consecutive_errors)
                print(f"stopped_after_consecutive_errors={consecutive_errors}", file=sys.stderr)
                break
    logging.info("run-claude pack_file=%s output=%s written=%s", pack_file, output, written)
    print(f"proposal_records_written={written}")
    print(f"proposal_file={output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only OB1 shadow cleanup proposal pipeline.")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--version", action="version", version=f"shadow-cleanup {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show local shadow cleanup files.")
    status.set_defaults(func=cmd_status)

    snapshot = subparsers.add_parser("snapshot", help="Export thoughts from Supabase without changing them.")
    snapshot.add_argument("--env-file", type=Path, default=Path(".env.local"))
    snapshot.add_argument("--output", type=Path)
    snapshot.add_argument("--page-size", type=int, default=1000)
    snapshot.add_argument("--limit", type=int)
    snapshot.set_defaults(func=cmd_snapshot)

    pack = subparsers.add_parser("pack", help="Build cleanup packs from a snapshot.")
    pack.add_argument("--snapshot", type=Path)
    pack.add_argument("--output", type=Path)
    pack.add_argument("--max-items", type=int, default=40)
    pack.add_argument("--topic-min-items", type=int, default=3)
    pack.add_argument("--limit-records", type=int)
    pack.add_argument("--limit-packs", type=int)
    pack.set_defaults(func=cmd_pack)

    shard = subparsers.add_parser("shard", help="Split a pack file into disjoint worker inputs.")
    shard.add_argument("--pack-file", type=Path)
    shard.add_argument("--shards", type=int, default=4)
    shard.add_argument("--output-dir", type=Path)
    shard.set_defaults(func=cmd_shard)

    run_claude = subparsers.add_parser("run-claude", help="Ask Claude CLI for JSON cleanup proposals per pack.")
    run_claude.add_argument("--pack-file", type=Path)
    run_claude.add_argument("--output", type=Path)
    run_claude.add_argument("--model", default="opus")
    run_claude.add_argument("--claude-path")
    run_claude.add_argument("--timeout", type=int, default=900)
    run_claude.add_argument("--limit-packs", type=int)
    run_claude.add_argument("--max-budget-usd", type=float)
    run_claude.add_argument("--max-consecutive-errors", type=int, default=3)
    run_claude.set_defaults(func=cmd_run_claude)
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ShadowCleanupError as exc:
        logging.exception("shadow cleanup failed")
        print(f"error={exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - top-level guard keeps failures logged.
        logging.exception("unexpected shadow cleanup failure")
        print(f"error=Unexpected failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
