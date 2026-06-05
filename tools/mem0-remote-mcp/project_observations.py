from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_OBSERVATIONS_LOG = '/data/mem0-mcp-project-observations.jsonl'

SECRET_ASSIGN_RE = re.compile(r'(?i)\b(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+')
BEARER_RE = re.compile(r'(?i)\bbearer\s+([a-z0-9._~+/=-]{4,})')


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def stable_hash(value: Any) -> str:
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()


def compact_text(value: Any, max_len: int = 500) -> str:
    collapsed = ' '.join(str(value or '').split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + '...'


def redact_text(value: Any) -> str:
    text = compact_text(value)
    text = BEARER_RE.sub('Bearer <redacted>', text)
    return SECRET_ASSIGN_RE.sub('<redacted>', text)


def normalize_path(value: Any) -> str:
    path = compact_text(value, 300).replace('\\', '/')
    while '//' in path:
        path = path.replace('//', '/')
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


def read_jsonl(path: str | Path, limit: int = 5000) -> tuple[list[dict[str, Any]], int]:
    path = Path(path)
    if not path.exists():
        return [], 0
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        return [], 0
    rows = []
    malformed = 0
    for line in lines[-max(int(limit or 0), 0):]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            malformed += 1
    return rows, malformed


def observation_hash(payload: dict[str, Any]) -> str:
    clean = {key: value for key, value in payload.items() if key not in {'id', 'timestamp', 'source_hash'}}
    return stable_hash(json.dumps(clean, sort_keys=True, ensure_ascii=False))


def build_observation(payload: dict[str, Any]) -> dict[str, Any]:
    row = {
        'origin': compact_text(payload.get('origin'), 80).lower() or 'unknown',
        'project_root': normalize_path(payload.get('project_root')),
        'cwd': normalize_path(payload.get('cwd')),
        'git_branch': compact_text(payload.get('git_branch'), 120),
        'worktree_root': normalize_path(payload.get('worktree_root')),
        'event_type': compact_text(payload.get('event_type'), 80).lower() or 'note',
        'title': redact_text(payload.get('title')),
        'summary': redact_text(payload.get('summary')),
        'tool_name': compact_text(payload.get('tool_name'), 120),
        'command_category': compact_text(payload.get('command_category'), 80).lower(),
        'status': compact_text(payload.get('status'), 40).lower(),
        'outcome': redact_text(payload.get('outcome')),
        'touched_paths': normalize_list(payload.get('touched_paths'), paths=True),
        'files_read': normalize_list(payload.get('files_read'), paths=True),
        'files_modified': normalize_list(payload.get('files_modified'), paths=True),
        'commands': normalize_list(payload.get('commands')),
        'tests': normalize_list(payload.get('tests')),
        'decisions': normalize_list(payload.get('decisions')),
        'next_steps': normalize_list(payload.get('next_steps')),
        'memory_ids': normalize_list(payload.get('memory_ids')),
    }
    source_hash = compact_text(payload.get('source_hash'), 128) or observation_hash(row)
    row['id'] = 'obs_' + stable_hash(source_hash)[:16]
    row['timestamp'] = compact_text(payload.get('timestamp'), 80) or utc_now_iso()
    row['source_hash'] = source_hash
    return row


class ProjectObservationStore:
    def __init__(self, path: str | Path = DEFAULT_PROJECT_OBSERVATIONS_LOG):
        self.path = Path(path)

    def has_source_hash(self, source_hash: str, limit: int = 5000) -> bool:
        source_hash = str(source_hash or '').strip()
        if not source_hash:
            return False
        rows, _ = read_jsonl(self.path, limit=limit)
        return any(row.get('source_hash') == source_hash for row in rows)

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = build_observation(payload)
        if self.has_source_hash(row['source_hash']):
            return {'appended': False, 'id': row['id'], 'source_hash': row['source_hash']}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
        return {'appended': True, 'id': row['id'], 'source_hash': row['source_hash'], 'row': row}

    def recent(self, limit: int = 20, *, origin: str = '', event_type: str = '') -> list[dict[str, Any]]:
        rows, _ = read_jsonl(self.path, limit=5000)
        if origin:
            rows = [row for row in rows if str(row.get('origin') or '').lower() == origin.lower()]
        if event_type:
            rows = [row for row in rows if str(row.get('event_type') or '').lower() == event_type.lower()]
        return rows[-max(int(limit or 0), 0):]

    def by_file(self, file_path: str, limit: int = 20) -> list[dict[str, Any]]:
        target = normalize_path(file_path)
        rows, _ = read_jsonl(self.path, limit=5000)
        matches = []
        for row in rows:
            paths = set(row.get('files_read') or []) | set(row.get('files_modified') or []) | set(row.get('touched_paths') or [])
            if target in paths:
                matches.append(row)
        return matches[-max(int(limit or 0), 0):]

    def stats(self, limit: int = 5000) -> dict[str, Any]:
        rows, malformed = read_jsonl(self.path, limit=limit)
        origins = Counter(str(row.get('origin') or 'unknown') for row in rows)
        event_types = Counter(str(row.get('event_type') or 'unknown') for row in rows)
        return {
            'rows_seen': len(rows),
            'malformed_rows': malformed,
            'origins': dict(origins),
            'event_types': dict(event_types),
        }
