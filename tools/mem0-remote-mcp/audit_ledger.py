from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, row: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(row)
        payload['ts'] = datetime.now(UTC).isoformat().replace('+00:00', 'Z')
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')
        return payload

    def has_source_hash(self, source_hash: str, scan_limit: int = 5000) -> bool:
        source_hash = str(source_hash or '').strip()
        if not source_hash or not self.path.exists():
            return False
        try:
            lines = self.path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return False
        for line in reversed(lines[-scan_limit:]):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get('source_hash') == source_hash:
                return True
        return False

    def iter_recent(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines[-max(int(limit or 0), 0):]:
            line = line.lstrip('\ufeff')
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def latest_by_origin(self, limit: int = 5000) -> dict[str, dict[str, Any]]:
        by_origin: dict[str, dict[str, Any]] = {}
        for row in self.iter_recent(limit=limit):
            origin = str(row.get('origin') or 'unknown').strip().lower() or 'unknown'
            action = str(row.get('action') or 'unknown').strip().lower() or 'unknown'
            ts = str(row.get('ts') or '')
            summary = by_origin.setdefault(origin, {'latest_ts': '', 'actions': {}, 'rows': 0})
            summary['rows'] += 1
            summary['actions'][action] = summary['actions'].get(action, 0) + 1
            if ts and ts > summary['latest_ts']:
                summary['latest_ts'] = ts
        return by_origin
