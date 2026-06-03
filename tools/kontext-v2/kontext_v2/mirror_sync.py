from __future__ import annotations

from typing import Any

from kontext_v2.importer import ImportReport, import_mem0_export, preview_mem0_export
from kontext_v2.parity import compare_exact_id_freshness
from kontext_v2.repository import KontextRepository


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("results") or payload.get("memories") or []
    return [row for row in (payload or []) if isinstance(row, dict)]


def _row_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("external_mem0_id") or "").strip()


def _merge_rows_by_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for row in rows:
        row_id = _row_id(row)
        if not row_id:
            anonymous.append(row)
            continue
        merged[row_id] = row
    return list(merged.values()) + anonymous


def _fetch_existing_rows(repo: KontextRepository, client: Any, limit: int | None, offset: int | None = 0) -> list[dict[str, Any]]:
    safe_limit = max(int(limit or 0), 0)
    safe_offset = max(int(offset or 0), 0)
    if safe_limit <= 0:
        return []
    rows: list[dict[str, Any]] = []
    try:
        source_rows = repo.list_memory_rows(limit=safe_limit, offset=safe_offset)
    except TypeError:
        source_rows = repo.list_memory_rows(limit=safe_limit)
    for row in source_rows:
        external_id = str(row.get("external_mem0_id") or "").strip()
        if not external_id:
            continue
        fetched = client.fetch(external_id)
        if isinstance(fetched, dict):
            rows.append(fetched)
    return rows


def _fetch_id_rows(client: Any, ids: list[str] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in ids or []:
        external_id = str(value or "").strip()
        if not external_id or external_id in seen:
            continue
        seen.add(external_id)
        fetched = client.fetch(external_id)
        if isinstance(fetched, dict):
            rows.append(fetched)
    return rows


def _stale_exact_rows(repo: KontextRepository, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {_row_id(row): row for row in rows if _row_id(row)}
    if not by_id:
        return []
    report = compare_exact_id_freshness(repo, by_id.get, list(by_id))
    stale_ids = {
        str(row.get("id") or "")
        for row in report.get("results", [])
        if row.get("mem0_found") and row.get("kontext_found") and not row.get("fresh")
    }
    return [row for row in rows if _row_id(row) in stale_ids]


def _report_dict(report: ImportReport) -> dict[str, int]:
    return {
        "created": report.created,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "skipped": report.skipped,
    }


def run_live_snapshot_sync(
    repo: KontextRepository,
    client: Any,
    *,
    cap: int | None = None,
    dry_run: bool = True,
    source: str = "mem0",
    mode: str = "live_snapshot",
    refresh_existing_limit: int | None = None,
    refresh_existing_offset: int | None = None,
    refresh_ids: list[str] | None = None,
) -> dict[str, Any]:
    source_rows = _rows(client.get_all())
    safe_cap = None if cap is None else max(int(cap), 0)
    snapshot_rows = source_rows if safe_cap is None else source_rows[:safe_cap]
    safe_refresh_offset = max(int(refresh_existing_offset or 0), 0)
    safe_refresh_ids = []
    seen_refresh_ids: set[str] = set()
    for value in refresh_ids or []:
        external_id = str(value or "").strip()
        if external_id and external_id not in seen_refresh_ids:
            seen_refresh_ids.add(external_id)
            safe_refresh_ids.append(external_id)
    refreshed_rows = _fetch_existing_rows(repo, client, refresh_existing_limit, safe_refresh_offset)
    stale_refreshed_rows = _stale_exact_rows(repo, refreshed_rows)
    refreshed_id_rows = _fetch_id_rows(client, safe_refresh_ids)
    rows = _merge_rows_by_id([*snapshot_rows, *stale_refreshed_rows, *refreshed_id_rows])
    payload = {"results": rows}
    report = preview_mem0_export(repo, payload) if dry_run else import_mem0_export(repo, payload)
    report_payload = _report_dict(report)
    sync_row = repo.record_mirror_sync_run(
        source=source,
        mode=mode,
        status="ok",
        dry_run=dry_run,
        rows_seen=len(rows),
        created=report.created,
        updated=report.updated,
        unchanged=report.unchanged,
        skipped=report.skipped,
        metadata={
            "cap": safe_cap,
            "source_rows_seen": len(source_rows),
            "snapshot_rows_seen": len(snapshot_rows),
            "refresh_existing_limit": max(int(refresh_existing_limit or 0), 0),
            "refresh_existing_offset": safe_refresh_offset,
            "refreshed_existing_rows": len(refreshed_rows),
            "refreshed_stale_rows": len(stale_refreshed_rows),
            "refresh_id_count": len(safe_refresh_ids),
            "refreshed_id_rows": len(refreshed_id_rows),
        },
    )
    return {
        "ok": True,
        "source": source,
        "mode": mode,
        "dry_run": dry_run,
        "processed_rows": len(rows),
        "source_rows_seen": len(source_rows),
        "report": report_payload,
        "sync": sync_row,
    }
