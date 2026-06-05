from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class HookStats:
    rows_seen: int = 0
    malformed_rows: int = 0
    origins: dict[str, int] = field(default_factory=dict)
    hook_types: dict[str, int] = field(default_factory=dict)
    last_seen_by_origin: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def compact_text(value: Any, max_len: int = 80) -> str:
    collapsed = ' '.join(str(value or '').split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + '...'


def stable_hash(value: Any) -> str:
    value = str(value or '')
    if not value:
        return ''
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class HookHeartbeatLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(
        self,
        *,
        origin: str,
        hook_type: str,
        source: str = '',
        marker: str = '',
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            'ts': utc_now_iso(),
            'event': 'hook_heartbeat',
            'origin': compact_text(origin, 80).lower() or 'unknown',
            'hook_type': compact_text(hook_type, 80).lower() or 'unknown',
        }
        clean_source = compact_text(source, 80).lower()
        if clean_source:
            row['source'] = clean_source
        marker_hash = stable_hash(marker)
        if marker_hash:
            row['marker_hash'] = marker_hash
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
        return row


def read_recent_jsonl(path: str | Path, recent_limit: int = 5000) -> tuple[list[dict[str, Any]], int]:
    path = Path(path)
    if not path.exists():
        return [], 0
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        return [], 0
    rows: list[dict[str, Any]] = []
    malformed = 0
    for line in lines[-max(int(recent_limit or 0), 0):]:
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


def build_hook_stats(path: str | Path, recent_limit: int = 5000) -> HookStats:
    rows, malformed = read_recent_jsonl(path, recent_limit=recent_limit)
    origins: Counter[str] = Counter()
    hook_types: Counter[str] = Counter()
    last_seen_by_origin: dict[str, str] = {}
    for row in rows:
        origin = compact_text(row.get('origin'), 80).lower() or 'unknown'
        hook_type = compact_text(row.get('hook_type'), 80).lower() or 'unknown'
        ts = str(row.get('ts') or '')
        origins[origin] += 1
        hook_types[hook_type] += 1
        if ts:
            last_seen_by_origin[origin] = ts
    return HookStats(
        rows_seen=len(rows),
        malformed_rows=malformed,
        origins=dict(origins),
        hook_types=dict(hook_types),
        last_seen_by_origin=last_seen_by_origin,
    )
