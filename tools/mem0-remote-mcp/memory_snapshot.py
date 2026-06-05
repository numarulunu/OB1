
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def safe_snapshot_memory(memory: dict[str, Any], include_text: bool = False) -> dict[str, Any]:
    row = {'id': str(memory.get('id') or '').strip()}
    metadata = memory.get('metadata') if isinstance(memory.get('metadata'), dict) else {}
    row['metadata'] = {key: value for key, value in metadata.items() if value not in (None, '', [])}
    if include_text:
        row['text'] = str(memory.get('text') or memory.get('memory') or '')
    return row


def snapshot_memories(client: Any, output_path: str | Path, page_size: int = 100, max_pages: int = 100, include_text: bool = False) -> dict[str, Any]:
    payload = client.get_all(page_size=page_size, max_pages=max_pages)
    rows = payload.get('results') if isinstance(payload, dict) else payload
    memories = [safe_snapshot_memory(row, include_text=include_text) for row in (rows or []) if isinstance(row, dict) and str(row.get('id') or '').strip()]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'results': memories, 'count': len(memories), 'include_text': include_text}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'output': str(output), 'count': len(memories), 'include_text': include_text}
