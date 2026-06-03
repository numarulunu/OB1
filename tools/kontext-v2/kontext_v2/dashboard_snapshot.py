from __future__ import annotations

import math
import os
import re
import time
import logging
from contextvars import ContextVar
from hashlib import sha256
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from kontext_v2.repository import KontextRepository
from kontext_v2.retention import is_protected_autobiographical_history


DAY_MS = 86_400_000
DEFAULT_ENTRY_LIMIT = 300
DECAY_RATE_PER_DAY = 0.03
DECAY_HALF_LIFE_DAYS = round(math.log(2) / DECAY_RATE_PER_DAY)
_QUERY_FAILED = object()
_SNAPSHOT_ERRORS: ContextVar[list[dict[str, str]] | None] = ContextVar("dashboard_snapshot_errors", default=None)
logger = logging.getLogger(__name__)


def _body_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()[:8]


def _iso(value: Any, fallback: datetime | None = None) -> str:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = fallback or datetime.now(timezone.utc)
    else:
        dt = fallback or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _epoch_ms(value: Any, fallback: datetime | None = None) -> int:
    text = _iso(value, fallback)
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def _slug(value: Any, fallback: str = "memory") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or fallback


def _tier(value: Any) -> str:
    tier = str(value or "active").strip().lower()
    if tier in {"s", "core"}:
        return "S"
    if tier in {"a", "active"}:
        return "A"
    if tier in {"b", "historical", "ambient"}:
        return "B"
    if tier in {"c", "d", "cold", "archive"}:
        return "C"
    return "A"


def _dashboard_tier(value: Any) -> str:
    return {"S": "core", "A": "active", "B": "ambient", "C": "archive"}.get(_tier(value), "active")


def _decay(row: dict[str, Any], now: datetime) -> float:
    status = str(row.get("current_status") or "").lower()
    tier = str(row.get("memory_tier") or "").lower()
    updated_ms = _epoch_ms(row.get("updated_at") or row.get("imported_at"), now)
    days = max(0.0, ((now.timestamp() * 1000) - updated_ms) / DAY_MS)
    base = 1 - math.exp(-days * DECAY_RATE_PER_DAY)
    if is_protected_autobiographical_history(row):
        return round(min(base, 0.2), 4)
    if tier in {"cold", "archive"} or status in {"dormant", "resolved", "inactive"}:
        base = max(base, 0.55)
    return round(min(1.0, max(0.0, base)), 4)


def _source_id_from_raw(raw: Any) -> str:
    value = str(raw or "kontext").lower()
    if "claude" in value:
        return "claude"
    if "codex" in value:
        return "codex"
    if "chatgpt" in value:
        return "chatgpt"
    if "mem0" in value:
        return "legacy_mem0"
    if "project" in value:
        return "project"
    return _slug(value, "kontext")


def _source_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    raw = (
        metadata.get("ingestion_origin")
        or metadata.get("source")
        or metadata.get("client")
        or row.get("memory_type")
        or "kontext"
    )
    return _source_id_from_raw(raw)


def _fallback_title(memory_type: str) -> str:
    label = " ".join(part for part in _slug(memory_type, "memory").split("-") if part)
    if not label or label == "memory":
        return "Untitled memory"
    return f"Untitled {label} memory"


def _entry(row: dict[str, Any], now: datetime, uses: int = 0) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    external_id = str(row.get("external_mem0_id") or row.get("id") or "")
    memory_type = str(row.get("memory_type") or metadata.get("memory_type") or "memory")
    title = str(row.get("title") or "").strip()
    text = str(row.get("text") or row.get("memory") or "").strip()
    if not title:
        title = _fallback_title(memory_type)
    updated = row.get("updated_at") or row.get("imported_at") or row.get("created_at")
    created = row.get("created_at") or row.get("imported_at") or updated
    domains = metadata.get("domains") if isinstance(metadata.get("domains"), list) else []
    return {
        "id": external_id,
        "external_mem0_id": external_id,
        "file": external_id,
        "type": memory_type,
        "tier": _tier(row.get("memory_tier")),
        "desc": title,
        "title": title,
        "body_hash": _body_hash(text),
        "body_len": len(text),
        "why": f"{memory_type}; {row.get('current_status') or 'active'}; {row.get('memory_tier') or 'active'}",
        "created": _iso(created, now),
        "lastUsed": _epoch_ms(updated, now),
        "uses": int(uses or 0),
        "decay": _decay(row, now),
        "confidence": min(1.0, max(0.0, float(row.get("signal_strength") or 7.0) / 10.0)),
        "source": {"id": _source_id(row), "ref": external_id},
        "tags": [str(item) for item in domains[:4]] or [memory_type],
        "relations": [],
        "metadata": {
            "memory_type": memory_type,
            "current_status": row.get("current_status") or "",
            "memory_tier": row.get("memory_tier") or "active",
            "domains": domains,
        },
    }


def _sql_head(sql: str) -> str:
    return " ".join(str(sql or "").strip().split())[:120]


def _snapshot_errors() -> list[dict[str, str]] | None:
    return _SNAPSHOT_ERRORS.get()


def _record_query_error(sql: str, exc: Exception) -> None:
    errors = _snapshot_errors()
    item = {"sql_head": _sql_head(sql), "error": type(exc).__name__}
    if errors is not None:
        errors.append(item)
    logger.exception("dashboard snapshot query failed", extra=item)


def _query(repo: KontextRepository, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        with repo.conn.cursor(row_factory=dict_row) as cur:
            return [dict(row) for row in cur.execute(sql, params).fetchall()]
    except Exception as exc:
        _record_query_error(sql, exc)
        return []


def _scalar(repo: KontextRepository, sql: str, params: tuple[Any, ...] = ()) -> Any:
    errors = _snapshot_errors()
    before = len(errors) if errors is not None else 0
    rows = _query(repo, sql, params)
    if errors is not None and len(errors) > before:
        return _QUERY_FAILED
    return next(iter(rows[0].values())) if rows else None


def _int_or_none(value: Any) -> int | None:
    if value is _QUERY_FAILED:
        return None
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _uses_by_memory(repo: KontextRepository) -> dict[str, int]:
    rows = _query(
        repo,
        """
        SELECT result_id, count(*) AS uses
        FROM retrieval_queries, jsonb_array_elements_text(result_external_ids) AS result_id
        WHERE created_at >= now() - interval '30 days'
        GROUP BY result_id
        """,
    )
    return {str(row["result_id"]): int(row["uses"] or 0) for row in rows}


def _activity(repo: KontextRepository, now: datetime) -> list[dict[str, Any]]:
    start = (now.date() - timedelta(days=29))
    days = {
        start + timedelta(days=index): {"ingested": 0, "used": 0, "decayed": 0}
        for index in range(30)
    }
    queries = {
        "ingested": "SELECT date(created_at) AS d, count(*) AS c FROM memories WHERE created_at >= now() - interval '30 days' GROUP BY d",
        "used": "SELECT date(created_at) AS d, count(*) AS c FROM retrieval_queries WHERE created_at >= now() - interval '30 days' GROUP BY d",
        "decayed": """
            SELECT date(created_at) AS d, count(*) AS c
            FROM memory_flags
            WHERE created_at >= now() - interval '30 days'
              AND flag_type IN ('stale_candidate', 'decay')
            GROUP BY d
        """,
    }
    for key, sql in queries.items():
        for row in _query(repo, sql):
            day = row.get("d")
            if isinstance(day, datetime):
                day = day.date()
            if isinstance(day, date) and day in days:
                days[day][key] = int(row.get("c") or 0)
    return [
        {"t": datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat(), **values}
        for day, values in days.items()
    ]


def _event_source(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("kontext-v2-"):
        text = text.removeprefix("kontext-v2-")
    return text or "kontext"


def _memory_count_text(value: Any) -> str:
    count = max(0, int(value or 0))
    noun = "memory" if count == 1 else "memories"
    return f"{count} {noun}"


def _memory_write_label(action: Any) -> str:
    normalized = str(action or "").strip().lower()
    if normalized == "update":
        return "Memory updated"
    return "Memory ingested"


def _events(repo: KontextRepository) -> list[dict[str, Any]]:
    event_rows: list[dict[str, Any]] = []
    event_rows.extend(
        {
            "t": _iso(row.get("created_at")),
            "kind": "write",
            "label": _memory_write_label(row.get("action")),
            "detail": _event_source(row.get("origin")),
            "tone": "ok",
        }
        for row in _query(
            repo,
            """
            SELECT origin, action, status, created_at
            FROM memory_intake_audit
            WHERE status = 'apply'
              AND action IN ('save', 'update', 'submit_memory_override', 'ingest_exchange')
            ORDER BY created_at DESC
            LIMIT 12
            """,
        )
    )
    event_rows.extend(
        {
            "t": _iso(row.get("created_at")),
            "kind": "read",
            "label": "Memory pulled",
            "detail": f"{_event_source(row.get('profile') or row.get('origin') or row.get('service'))}; {_memory_count_text(row.get('result_count'))}",
            "tone": "muted",
        }
        for row in _query(
            repo,
            """
            SELECT origin, service, profile,
                   CASE
                       WHEN jsonb_typeof(result_external_ids) = 'array'
                       THEN jsonb_array_length(result_external_ids)
                       ELSE 0
                   END AS result_count,
                   latency_ms, created_at
            FROM retrieval_queries
            ORDER BY created_at DESC
            LIMIT 24
            """,
        )
    )
    return sorted(event_rows, key=lambda item: item["t"], reverse=True)[:40]


def _sources(repo: KontextRepository, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = _query(
        repo,
        """
        SELECT COALESCE(
            metadata->>'ingestion_origin',
            metadata->>'source',
            metadata->>'client',
            memory_type,
            'kontext'
        ) AS source, count(*) AS count
        FROM memories
        GROUP BY source
        ORDER BY count DESC, source ASC
        """,
    )
    counts = Counter()
    if rows:
        for row in rows:
            counts[_source_id_from_raw(row.get("source"))] += int(row.get("count") or 0)
    else:
        counts = Counter(entry["source"]["id"] for entry in entries)
    labels = {
        "codex": "Codex",
        "claude": "Claude",
        "chatgpt": "ChatGPT",
        "legacy_mem0": "Legacy Mem0",
        "project": "Project observations",
        "kontext": "Kontext",
    }
    return [
        {"id": source_id, "label": labels.get(source_id, source_id.replace("-", " ").replace("_", " ").title()), "count": count}
        for source_id, count in counts.most_common()
    ]


def _tier_counts(repo: KontextRepository, total: int, entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"core": 0, "active": 0, "ambient": 0, "archive": 0}
    rows = _query(
        repo,
        """
        SELECT memory_tier, count(*) AS count
        FROM memories
        GROUP BY memory_tier
        """,
    )
    if rows:
        for row in rows:
            bucket = _dashboard_tier(row.get("memory_tier"))
            counts[bucket] += int(row.get("count") or 0)
        return counts

    loaded = Counter(_dashboard_tier(entry.get("tier")) for entry in entries)
    for bucket in counts:
        counts[bucket] = int(loaded.get(bucket, 0))
    unseen = max(0, total - sum(counts.values()))
    counts["active"] += unseen
    return counts


def _relations(repo: KontextRepository, limit: int = 500) -> list[dict[str, Any]]:
    rows = _query(
        repo,
        """
        SELECT sm.external_mem0_id AS source, tm.external_mem0_id AS target,
               r.relation_type, r.confidence
        FROM relations r
        JOIN memories sm ON sm.id = r.source_memory_id
        JOIN memories tm ON tm.id = r.target_memory_id
        ORDER BY r.confidence DESC, r.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [
        {
            "source": row.get("source"),
            "target": row.get("target"),
            "kind": row.get("relation_type") or "related",
            "strength": float(row.get("confidence") or 0.5),
            "synthetic": False,
        }
        for row in rows
        if row.get("source") and row.get("target")
    ]


def _relation_tokens(entry: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for tag in entry.get("tags") or []:
        slug = _slug(tag, "")
        if slug:
            tokens.add(f"tag:{slug}")
    memory_type = _slug(entry.get("type"), "")
    if memory_type:
        tokens.add(f"type:{memory_type}")
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    source_id = _slug(source.get("id"), "")
    if source_id:
        tokens.add(f"source:{source_id}")
    return tokens


def _derived_relations(
    entries: list[dict[str, Any]],
    categories: list[dict[str, Any]] | None = None,
    *,
    limit: int = 600,
    max_degree: int = 7,
) -> list[dict[str, Any]]:
    token_by_id = {str(entry.get("id")): _relation_tokens(entry) for entry in entries if entry.get("id")}
    for category in categories or []:
        category_slug = _slug(category.get("slug"), "")
        if not category_slug:
            continue
        for entry_id in category.get("entries") or []:
            if isinstance(entry_id, dict):
                entry_key = str(entry_id.get("id") or entry_id.get("external_mem0_id") or entry_id.get("memory_id") or "")
            else:
                entry_key = str(entry_id or "")
            if entry_key in token_by_id:
                token_by_id[entry_key].add(f"category:{category_slug}")
    document_frequency = Counter(token for tokens in token_by_id.values() for token in tokens)
    total = max(len(token_by_id), 1)
    candidates: list[tuple[float, str, str]] = []

    for left_index, left in enumerate(entries):
        left_id = str(left.get("id") or "")
        if not left_id:
            continue
        left_tokens = token_by_id.get(left_id, set())
        for right in entries[left_index + 1 :]:
            right_id = str(right.get("id") or "")
            if not right_id:
                continue
            shared = left_tokens.intersection(token_by_id.get(right_id, set()))
            if not shared:
                continue
            score = sum(math.log1p(total / max(document_frequency[token], 1)) for token in shared)
            if left.get("tier") == right.get("tier"):
                score += 0.15
            score += min(float(left.get("confidence") or 0), float(right.get("confidence") or 0)) * 0.15
            candidates.append((score, left_id, right_id))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    degree: Counter[str] = Counter()
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for score, source, target in candidates:
        pair = tuple(sorted((source, target)))
        if pair in seen or degree[source] >= max_degree or degree[target] >= max_degree:
            continue
        seen.add(pair)
        degree[source] += 1
        degree[target] += 1
        strength = round(min(1.0, max(0.35, 0.42 + score / 4.0)), 3)
        relations.append({"source": source, "target": target, "kind": "shared_context", "strength": strength, "synthetic": True})
        if len(relations) >= limit:
            break
    return relations


def _stale_entries(repo: KontextRepository, now: datetime, entry_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = _query(
        repo,
        """
        SELECT m.external_mem0_id, m.title, m.text, m.metadata, m.memory_type,
               m.current_status, m.memory_tier, m.signal_strength, m.created_at,
               m.updated_at, m.imported_at, f.flag_type
        FROM memory_flags f
        LEFT JOIN memories m ON m.external_mem0_id = f.external_mem0_id
        WHERE f.status = 'pending'
        ORDER BY f.created_at DESC
        LIMIT 50
        """,
    )
    stale: list[dict[str, Any]] = []
    for row in rows:
        if is_protected_autobiographical_history(row):
            continue
        external_id = str(row.get("external_mem0_id") or "")
        if external_id in entry_by_id:
            if not is_protected_autobiographical_history(entry_by_id[external_id]):
                stale.append(entry_by_id[external_id])
        elif external_id:
            item = _entry(row, now)
            item["decay"] = max(float(item.get("decay") or 0), 0.65)
            stale.append(item)
    if stale:
        return stale
    return [
        entry
        for entry in entry_by_id.values()
        if float(entry.get("decay") or 0) > 0.3 and not is_protected_autobiographical_history(entry)
    ][:50]


def _latest_activity(repo: KontextRepository, latest_sync: dict | None, now: datetime) -> str:
    timestamps = [latest_sync.get("finished_at")] if isinstance(latest_sync, dict) else []
    for table, column in (
        ("memories", "updated_at"),
        ("memory_intake_audit", "created_at"),
        ("hook_heartbeats", "created_at"),
        ("project_observations", "created_at"),
        ("retrieval_queries", "created_at"),
        ("memory_flags", "created_at"),
    ):
        value = _scalar(repo, f"SELECT max({column}) FROM {table}")
        if value is not _QUERY_FAILED and value:
            timestamps.append(value)
    parsed = [_iso(value, now) for value in timestamps if value]
    return max(parsed) if parsed else _iso(now)


def _health_components(
    *,
    fresh_pct: float,
    maintenance: dict[str, Any],
    latest_sync: dict[str, Any],
    sync_drift: int | None,
    errors: list[dict[str, str]],
) -> dict[str, float]:
    maintenance_score = 0.75 if maintenance.get("due") else 1.0
    if sync_drift is None:
        sync_score = 0.55
    elif sync_drift <= 0 and str(latest_sync.get("status") or "").lower() in {"ok", "success", "completed"}:
        sync_score = 1.0
    else:
        sync_score = max(0.0, 1.0 - min(sync_drift, 10) / 10.0)
    return {
        "freshness": round(max(0.0, min(1.0, fresh_pct)), 4),
        "maintenance": round(maintenance_score, 4),
        "sync": round(sync_score, 4),
        "errors": 0.0 if errors else 1.0,
    }


def _composite_health(components: dict[str, float]) -> float:
    return round(
        components.get("freshness", 0.0) * 0.35
        + components.get("maintenance", 0.0) * 0.25
        + components.get("sync", 0.0) * 0.2
        + components.get("errors", 0.0) * 0.2,
        4,
    )


def _env_bool(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _context_mode_integration() -> dict[str, Any]:
    override = _env_bool("KONTEXT_CONTEXT_MODE_ENABLED")
    path_override = os.environ.get("KONTEXT_CONTEXT_MODE_PLUGIN_PATHS", "").strip()
    paths = [
        Path.home() / ".claude" / "plugins" / "cache" / "context-mode",
        Path.home() / ".codex" / "plugins" / "cache" / "context-mode",
    ]
    if path_override:
        paths.extend(Path(item.strip()).expanduser() for item in path_override.split(os.pathsep) if item.strip())
    available = override if override is not None else any(path.exists() for path in paths)
    url = os.environ.get("KONTEXT_CONTEXT_MODE_INSIGHT_URL", "http://localhost:4747").strip() or "http://localhost:4747"
    return {
        "available": bool(available),
        "label": "Context Mode",
        "insight_url": url,
    }


def build_dashboard_snapshot(
    repo: KontextRepository,
    *,
    entry_limit: int = DEFAULT_ENTRY_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    errors: list[dict[str, str]] = []
    token = _SNAPSHOT_ERRORS.set(errors)
    try:
        total = int(repo.count_memories())
        use_counts = _uses_by_memory(repo)
        rows = repo.list_memory_rows(limit=entry_limit, offset=0)
        entries = [_entry(row, now, use_counts.get(str(row.get("external_mem0_id") or ""), 0)) for row in rows]
        entry_by_id = {entry["id"]: entry for entry in entries if entry.get("id")}
        categories = repo.list_categories()
        for category in categories:
            try:
                category_entries = repo.list_category_memories(category["slug"], limit=100)
                if category_entries:
                    category["entries"] = category_entries
            except Exception as exc:
                errors.append({"sql_head": f"list_category_memories:{category.get('slug') or ''}", "error": type(exc).__name__})
                category["entries"] = category.get("entries") or []
        explicit_relations = _relations(repo)
        relations = explicit_relations or _derived_relations(entries, categories)
        relations_synthetic = not bool(explicit_relations) and bool(relations)
        relations_source = "database" if explicit_relations else "derived" if relations else "none"
        relation_endpoints: set[str] = set()
        for relation in relations:
            relation.setdefault("synthetic", relations_synthetic)
            source = str(relation.get("source") or "")
            target = str(relation.get("target") or "")
            relation_endpoints.update(value for value in (source, target) if value)
            if source in entry_by_id and target in entry_by_id:
                entry_by_id[source].setdefault("relations", []).append(target)
                entry_by_id[target].setdefault("relations", []).append(source)
        dry_run_writes = repo.list_dry_run_write_audit(limit=30)
        latest_sync = repo.latest_mirror_sync_run() or {}
        maintenance = dict(repo.maintenance_status(recent_limit=500) or {})
        maintenance.setdefault("checked_at", _iso(now))
        stale = _stale_entries(repo, now, entry_by_id)
        pending_raw = _scalar(repo, "SELECT count(*) FROM memory_flags WHERE status = 'pending'")
        pending_flags = _int_or_none(pending_raw)
        by_tier = _tier_counts(repo, total, entries)
        latest_activity = _latest_activity(repo, latest_sync, now)
        relation_count_raw = _scalar(repo, "SELECT count(*) FROM relations")
        relation_count = _int_or_none(relation_count_raw)
        if relation_count is None:
            relation_count = len(relations)
        elif relation_count == 0 and relations_synthetic:
            relation_count = len(relations)
        stale_total_raw = _scalar(
            repo,
            """
            SELECT count(DISTINCT external_mem0_id)
            FROM memory_flags
            WHERE status = 'pending'
              AND flag_type IN ('stale_candidate', 'decay')
            """,
        )
        stale_total = _int_or_none(stale_total_raw)
        stale_for_freshness = stale_total if stale_total is not None else len(stale)
        fresh_pct_scope = "all_memories_pending_flags" if stale_total is not None else "loaded_entries_decay_sample"
        fresh_pct = 1.0 - (stale_for_freshness / max(total, 1))
        sync_drift_known = "error_count" in latest_sync
        sync_drift = int(latest_sync.get("error_count") or 0) if sync_drift_known else None
        health_components = _health_components(
            fresh_pct=fresh_pct,
            maintenance=maintenance,
            latest_sync=latest_sync,
            sync_drift=sync_drift,
            errors=errors,
        )
        health = _composite_health(health_components)
        return {
        "now": int(now.timestamp() * 1000),
        "data_source": "kontext_v2",
        "meta": {
            "data_source": "kontext_v2",
            "sync_source": "kontext_v2",
            "project": "kontext",
            "checked_at": _iso(now),
            "cleanup_checked_at": maintenance.get("checked_at"),
            "last_sync": latest_activity,
            "legacy_mem0_sync": latest_sync.get("finished_at"),
            "next_sync": "",
            "health": health,
            "health_components": health_components,
            "total": total,
            "loaded": len(entries),
            "by_tier": {
                "core": int(by_tier.get("core", 0)),
                "active": int(by_tier.get("active", 0)),
                "ambient": int(by_tier.get("ambient", 0)),
                "archive": int(by_tier.get("archive", 0)),
            },
            "stale": len(stale),
            "pending_flags": pending_flags,
            "flag_types": maintenance.get("flag_types") or {},
            "relations": relation_count,
            "relations_synthetic": relations_synthetic,
            "relations_source": relations_source,
            "relation_coverage": min(1.0, len(relation_endpoints) / max(len(entries), 1)),
            "fresh_pct": fresh_pct,
            "fresh_pct_scope": fresh_pct_scope,
            "sync_drift": sync_drift,
            "sync_drift_known": sync_drift_known,
            "sync_status": latest_sync.get("status") or "unknown",
            "errors": list(errors),
        },
        "totals": {"entries": total},
        "entries": entries,
        "categories": categories,
        "relations": relations,
        "activity": _activity(repo, now),
        "feed": _events(repo),
        "events": _events(repo),
        "stale": stale,
        "sources": _sources(repo, entries),
        "integrations": {
            "context_mode": _context_mode_integration(),
        },
        "dryRunWrites": dry_run_writes,
        "maintenance": maintenance,
        "config": {
            "source": "static_defaults",
            "memory": {
                "system": "Kontext V2 primary",
                "endpoint": "https://kontext-mcp.ionutrosu.xyz",
                "project": "kontext",
            },
            "mem0": {
                "enabled": False,
                "endpoint": "Legacy Mem0 backup only",
                "project": "kontext",
                "key_hint": "backup",
                "rotated": latest_sync.get("finished_at") or latest_activity,
            },
            "sync": {
                "interval_minutes": 360,
                "conflict_strategy": latest_sync.get("mode") or "kontext_primary",
                "push_enabled": True,
                "pull_enabled": True,
            },
            "decay": {"half_life_days": DECAY_HALF_LIFE_DAYS, "archive_after_days": 120, "tick_interval_hours": 24, "never_decay_tiers": ["core"]},
            "ingest": {"auto_tier": True, "min_confidence": 0.7, "dedupe_threshold": 0.88},
            "ui": {"density": "comfortable", "inspector": True, "graph_labels": True},
        },
    }
    finally:
        _SNAPSHOT_ERRORS.reset(token)


def build_dashboard_snapshot_from_env(entry_limit: int = DEFAULT_ENTRY_LIMIT) -> dict[str, Any]:
    database_url = os.environ.get("KONTEXT_V2_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("KONTEXT_V2_DATABASE_URL is not configured")
    with psycopg.connect(database_url) as conn:
        repo = KontextRepository(conn)
        return build_dashboard_snapshot(repo, entry_limit=entry_limit)
