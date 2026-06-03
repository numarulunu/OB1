from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from kontext_v2.models import MemoryRecord
from kontext_v2.repository import KontextRepository
from kontext_v2.retention import is_protected_autobiographical_history


@dataclass(frozen=True)
class ImportReport:
    created: int
    updated: int
    unchanged: int
    skipped: int


def mem0_source_hash(row: dict[str, Any]) -> str:
    raw = json.dumps(row, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _as_memory_record(row: dict[str, Any]) -> MemoryRecord | None:
    external_id = str(row.get("id") or "").strip()
    text = str(row.get("memory") or row.get("text") or "").strip()
    if not external_id or not text:
        return None

    metadata_value = row.get("metadata") or {}
    if not isinstance(metadata_value, dict):
        return None
    metadata = dict(metadata_value)

    signal_strength = None
    if metadata.get("signal_strength") is not None:
        try:
            signal_strength = float(metadata["signal_strength"])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(signal_strength):
            return None

    return MemoryRecord(
        external_mem0_id=external_id,
        title=str(metadata.get("title") or row.get("title") or ""),
        text=text,
        metadata=metadata,
        memory_type=str(metadata.get("memory_type") or ""),
        current_status=str(metadata.get("current_status") or "active"),
        memory_tier=str(metadata.get("memory_tier") or "active"),
        signal_strength=signal_strength,
        source_hash=mem0_source_hash(row),
    )


def _allows_protected_history_import_override(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return bool(row.get("protected_history_import_override") or metadata.get("protected_history_import_override"))


def _blocks_protected_history_update(existing: MemoryRecord | None, row: dict[str, Any]) -> bool:
    return bool(
        existing is not None
        and is_protected_autobiographical_history(existing)
        and not _allows_protected_history_import_override(row)
    )


def import_mem0_export(repo: KontextRepository, payload: dict[str, Any]) -> ImportReport:
    created = 0
    updated = 0
    unchanged = 0
    skipped = 0

    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            skipped += 1
            continue

        memory = _as_memory_record(row)
        if memory is None:
            skipped += 1
            continue

        existing = repo.fetch_by_external_id(memory.external_mem0_id)

        if existing is not None and existing.source_hash == memory.source_hash:
            unchanged += 1
            continue
        if _blocks_protected_history_update(existing, row):
            skipped += 1
            continue

        repo.upsert_memory(memory)
        if existing is None:
            created += 1
        else:
            updated += 1

    return ImportReport(created=created, updated=updated, unchanged=unchanged, skipped=skipped)

def preview_mem0_export(repo: KontextRepository, payload: dict[str, Any]) -> ImportReport:
    created = 0
    updated = 0
    unchanged = 0
    skipped = 0

    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            skipped += 1
            continue

        memory = _as_memory_record(row)
        if memory is None:
            skipped += 1
            continue

        existing = repo.fetch_by_external_id(memory.external_mem0_id)
        if existing is None:
            created += 1
        elif existing.source_hash == memory.source_hash:
            unchanged += 1
        elif _blocks_protected_history_update(existing, row):
            skipped += 1
        else:
            updated += 1

    return ImportReport(created=created, updated=updated, unchanged=unchanged, skipped=skipped)
