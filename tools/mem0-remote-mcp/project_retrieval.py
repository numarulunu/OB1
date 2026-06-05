from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_observations import normalize_path, read_jsonl, stable_hash, utc_now_iso


def tokens(value: Any) -> set[str]:
    text = str(value or '').lower().replace('-', ' ').replace('_', ' ')
    return {part for part in ''.join(ch if ch.isalnum() else ' ' for ch in text).split() if len(part) > 2}


def project_name(row: dict[str, Any]) -> str:
    root = normalize_path(row.get('project_root') or row.get('cwd') or '')
    if not root:
        return ''
    return root.rstrip('/').split('/')[-1]


def files_count(row: dict[str, Any]) -> int:
    paths = set(row.get('files_read') or []) | set(row.get('files_modified') or []) | set(row.get('touched_paths') or [])
    return len(paths)


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': str(row.get('id') or ''),
        'date': str(row.get('timestamp') or '')[:10],
        'type': str(row.get('event_type') or ''),
        'title': str(row.get('title') or ''),
        'project': project_name(row),
        'files_count': files_count(row),
        'token_estimate': max(1, len(json.dumps(row, ensure_ascii=False)) // 4),
    }


def searchable_text(row: dict[str, Any]) -> str:
    parts = [row.get('title'), row.get('event_type'), row.get('summary'), row.get('project_root'), row.get('cwd')]
    parts.extend(row.get('files_read') or [])
    parts.extend(row.get('files_modified') or [])
    parts.extend(row.get('touched_paths') or [])
    return ' '.join(str(part or '') for part in parts)


def score_row(row: dict[str, Any], query: str) -> int:
    query_tokens = tokens(query)
    if not query_tokens:
        return 0
    row_tokens = tokens(searchable_text(row))
    title_tokens = tokens(row.get('title'))
    return len(query_tokens & row_tokens) + (2 * len(query_tokens & title_tokens))


def log_retrieval(path: str | Path | None, event: str, query: str, rows: list[dict[str, Any]]) -> None:
    if not path:
        return
    telemetry_path = Path(path)
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'ts': utc_now_iso(),
        'event': event,
        'query_hash': stable_hash(query),
        'result_count': len(rows),
        'result_ids': [str(row.get('id') or '') for row in rows[:20] if row.get('id')],
    }
    with telemetry_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')


class ProjectObservationRetriever:
    def __init__(self, observations_log: str | Path, telemetry_log: str | Path | None = None):
        self.observations_log = Path(observations_log)
        self.telemetry_log = Path(telemetry_log) if telemetry_log else None

    def rows(self, limit: int = 5000) -> list[dict[str, Any]]:
        rows, _ = read_jsonl(self.observations_log, limit=limit)
        return sorted(rows, key=lambda row: str(row.get('timestamp') or ''))

    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        scored = [(score_row(row, query), row) for row in self.rows()]
        matches = [row for score, row in sorted(scored, key=lambda item: (-item[0], str(item[1].get('timestamp') or ''))) if score > 0]
        selected = matches[: max(int(limit or 0), 0)]
        log_retrieval(self.telemetry_log, 'project_search', query, selected)
        return {'rows': [compact_row(row) for row in selected], 'count': len(selected)}

    def fetch(self, observation_id: str) -> dict[str, Any]:
        observation_id = str(observation_id or '').strip()
        for row in self.rows():
            if row.get('id') == observation_id:
                return {'found': True, 'row': row}
        return {'found': False, 'id': observation_id}

    def timeline(self, anchor_id: str = '', query: str = '', before: int = 3, after: int = 3) -> dict[str, Any]:
        rows = self.rows()
        anchor_id = str(anchor_id or '').strip()
        if not anchor_id and query:
            search_rows = self.search(query, limit=1)['rows']
            anchor_id = search_rows[0]['id'] if search_rows else ''
        index = next((idx for idx, row in enumerate(rows) if row.get('id') == anchor_id), -1)
        if index < 0:
            return {'anchor_id': anchor_id, 'rows': []}
        start = max(index - max(int(before or 0), 0), 0)
        stop = min(index + max(int(after or 0), 0) + 1, len(rows))
        selected = rows[start:stop]
        log_retrieval(self.telemetry_log, 'project_timeline', anchor_id or query, selected)
        return {'anchor_id': anchor_id, 'rows': [compact_row(row) for row in selected]}

    def file_context(self, file_path: str, limit: int = 10) -> dict[str, Any]:
        target = normalize_path(file_path)
        matches = []
        for row in self.rows():
            paths = set(row.get('files_read') or []) | set(row.get('files_modified') or []) | set(row.get('touched_paths') or [])
            if target in paths:
                matches.append(row)
        selected = matches[-max(int(limit or 0), 0):]
        log_retrieval(self.telemetry_log, 'project_file_context', target, selected)
        return {
            'file_path': target,
            'titles': [str(row.get('title') or '') for row in selected],
            'rows': [compact_row(row) for row in selected],
            'recommend_full_file_read': not bool(selected),
        }
