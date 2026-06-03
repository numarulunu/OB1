from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

SECRET_ASSIGN_RE = re.compile(r"(?i)\b(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+")
CLI_SECRET_RE = re.compile(r"(?i)(--(?:api[-_]?key|token|password|secret|authorization)\s+)[^\s,;]+")
BEARER_RE = re.compile(r"(?i)\bbearer\s+([a-z0-9._~+/=-]{4,})")
PEM_MARKER_RE = re.compile(r"(?i)-----BEGIN [a-z0-9 _-]+-----|-----END [a-z0-9 _-]+-----")
LONG_HEX_RE = re.compile(r"(?i)\b[a-f0-9]{32,}\b")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def compact_text(value: Any, max_len: int = 500) -> str:
    collapsed = " ".join(str(value or "").split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + "..."


def redact_text(value: Any, max_len: int = 500) -> str:
    text = compact_text(value, max_len=max_len)
    text = BEARER_RE.sub("Bearer <redacted>", text)
    text = CLI_SECRET_RE.sub(r"\1<redacted>", text)
    text = SECRET_ASSIGN_RE.sub("<redacted>", text)
    text = PEM_MARKER_RE.sub("<redacted:pem-marker>", text)
    return LONG_HEX_RE.sub("<redacted:long-hex>", text)


def normalize_path(value: Any) -> str:
    path = compact_text(value, 300).replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    return path.strip()


def normalize_list(values: Any, *, paths: bool = False) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    clean = []
    for value in values:
        item = normalize_path(value) if paths else redact_text(value)
        if item:
            clean.append(item)
    return clean


def observation_hash(payload: dict[str, Any]) -> str:
    clean = {key: value for key, value in payload.items() if key not in {"id", "timestamp", "source_hash"}}
    return stable_hash(json.dumps(clean, sort_keys=True, ensure_ascii=False))


def project_name(row: dict[str, Any]) -> str:
    root = normalize_path(row.get("project_root") or row.get("cwd") or row.get("project") or "")
    if not root:
        return ""
    return root.rstrip("/").split("/")[-1]


def files_count(row: dict[str, Any]) -> int:
    paths = set(row.get("files_read") or []) | set(row.get("files_modified") or []) | set(row.get("touched_paths") or [])
    return len(paths)


def token_estimate(row: dict[str, Any]) -> int:
    return max(1, len(json.dumps(row, ensure_ascii=False)) // 4)


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "date": str(row.get("timestamp") or "")[:10],
        "type": str(row.get("event_type") or ""),
        "title": str(row.get("title") or ""),
        "project": project_name(row),
        "files_count": files_count(row),
        "token_estimate": token_estimate(row),
    }


def build_observation(payload: dict[str, Any]) -> dict[str, Any]:
    row = {
        "origin": compact_text(payload.get("origin"), 80).lower() or "unknown",
        "project_root": normalize_path(payload.get("project_root")),
        "cwd": normalize_path(payload.get("cwd")),
        "git_branch": compact_text(payload.get("git_branch"), 120),
        "worktree_root": normalize_path(payload.get("worktree_root")),
        "event_type": compact_text(payload.get("event_type"), 80).lower() or "note",
        "title": redact_text(payload.get("title")),
        "summary": redact_text(payload.get("summary")),
        "tool_name": compact_text(payload.get("tool_name"), 120),
        "command_category": compact_text(payload.get("command_category"), 80).lower(),
        "status": compact_text(payload.get("status"), 40).lower(),
        "outcome": redact_text(payload.get("outcome")),
        "touched_paths": normalize_list(payload.get("touched_paths"), paths=True),
        "files_read": normalize_list(payload.get("files_read"), paths=True),
        "files_modified": normalize_list(payload.get("files_modified"), paths=True),
        "commands": normalize_list(payload.get("commands")),
        "tests": normalize_list(payload.get("tests")),
        "decisions": normalize_list(payload.get("decisions")),
        "next_steps": normalize_list(payload.get("next_steps")),
        "memory_ids": normalize_list(payload.get("memory_ids")),
    }
    source_hash = compact_text(payload.get("source_hash"), 128) or observation_hash(row)
    row["id"] = "obs_" + stable_hash(source_hash)[:16]
    row["timestamp"] = compact_text(payload.get("timestamp"), 80) or utc_now_iso()
    row["source_hash"] = source_hash
    row["project"] = project_name(row)
    return row


def searchable_text(row: dict[str, Any]) -> str:
    parts = [row.get("title"), row.get("event_type"), row.get("summary"), row.get("project_root"), row.get("cwd")]
    parts.extend(row.get("files_read") or [])
    parts.extend(row.get("files_modified") or [])
    parts.extend(row.get("touched_paths") or [])
    return " ".join(str(part or "") for part in parts)


def tokens(value: Any) -> set[str]:
    text = str(value or "").lower().replace("-", " ").replace("_", " ")
    return {part for part in "".join(ch if ch.isalnum() else " " for ch in text).split() if len(part) > 2}


def score_row(row: dict[str, Any], query: str) -> int:
    query_tokens = tokens(query)
    if not query_tokens:
        return 0
    row_tokens = tokens(searchable_text(row))
    title_tokens = tokens(row.get("title"))
    return len(query_tokens & row_tokens) + (2 * len(query_tokens & title_tokens))


def row_from_record(record: dict[str, Any]) -> dict[str, Any]:
    files = record.get("files") or {}
    metadata = record.get("metadata") or {}
    if isinstance(files, str):
        files = json.loads(files)
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    created_at = record.get("created_at")
    timestamp = created_at.isoformat().replace("+00:00", "Z") if hasattr(created_at, "isoformat") else str(created_at or "")
    return {
        "id": str(record.get("observation_id") or record.get("id") or ""),
        "timestamp": timestamp,
        "origin": str(record.get("origin") or ""),
        "project": str(record.get("project") or ""),
        "project_root": str(record.get("project_root") or ""),
        "cwd": str(record.get("cwd") or ""),
        "event_type": str(record.get("event_type") or ""),
        "title": str(record.get("title") or ""),
        "summary": str(record.get("summary") or ""),
        "source_hash": str(record.get("source_hash") or ""),
        "touched_paths": list(files.get("touched_paths") or []),
        "files_read": list(files.get("files_read") or []),
        "files_modified": list(files.get("files_modified") or []),
        "commands": list(metadata.get("commands") or []),
        "tests": list(metadata.get("tests") or []),
        "decisions": list(metadata.get("decisions") or []),
        "next_steps": list(metadata.get("next_steps") or []),
        "memory_ids": list(metadata.get("memory_ids") or []),
        "metadata": metadata,
    }
