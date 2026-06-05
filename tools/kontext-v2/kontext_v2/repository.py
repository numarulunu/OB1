from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

import psycopg

from psycopg.rows import dict_row

from kontext_v2.categories import default_category_rows, infer_category_slugs, normalize_category_slug
from kontext_v2.models import CurrentStateFact, MemoryRecord, StateEvent, StateEventCandidate, StateEventEdge, StateSubject
from kontext_v2.project_observations import (
    build_observation,
    compact_row,
    normalize_path,
    row_from_record,
    score_row,
)
from kontext_v2.retention import cleanup_flag_blocked_for_protected_history
from kontext_v2.state_model import (
    DEFAULT_PROJECTION_VERSION,
    build_current_state_facts,
    event_hash as make_event_hash,
    idempotency_key as make_idempotency_key,
    normalize_namespace,
    normalize_state_key,
    normalize_subject_key,
    normalize_subject_type,
    validate_edge_type,
    validate_event_type,
    value_hash as make_value_hash,
)
from kontext_v2.typed_state import render_current_state_typed

def _count_value(row) -> int:
    if isinstance(row, dict):
        return int(row.get("count") or row.get("count(*)") or 0)
    return int(row[0])


class KontextRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self._transaction_depth = 0

    @contextmanager
    def transaction(self):
        with self.conn.transaction():
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1

    def _commit(self) -> None:
        if self._transaction_depth == 0:
            self.conn.commit()

    @staticmethod
    def _coerce_metadata(value: object) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _coerce_json_dict(value: object) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _coerce_json_list(value: object) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return []
            return parsed if isinstance(parsed, list) else []
        return []

    @staticmethod
    def _state_subject_from_row(row: dict) -> StateSubject:
        return StateSubject(
            id=str(row.get("id")) if row.get("id") is not None else None,
            namespace=str(row.get("namespace") or "live"),
            subject_type=str(row.get("subject_type") or ""),
            subject_key=str(row.get("subject_key") or ""),
            display_name=str(row.get("display_name") or ""),
            aliases=[str(item) for item in KontextRepository._coerce_json_list(row.get("aliases"))],
            scope=KontextRepository._coerce_json_dict(row.get("scope")),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _state_candidate_from_row(row: dict) -> StateEventCandidate:
        return StateEventCandidate(
            id=str(row.get("id")) if row.get("id") is not None else None,
            namespace=str(row.get("namespace") or "live"),
            source_kind=str(row.get("source_kind") or ""),
            source_id=str(row.get("source_id") or ""),
            source_hash=str(row.get("source_hash") or ""),
            source_span_hash=str(row.get("source_span_hash") or ""),
            subject_type=str(row.get("subject_type") or ""),
            subject_key=str(row.get("subject_key") or ""),
            state_key=str(row.get("state_key") or ""),
            event_type=str(row.get("event_type") or ""),
            value=KontextRepository._coerce_json_dict(row.get("value")),
            value_text=str(row.get("value_text") or ""),
            value_hash=str(row.get("value_hash") or ""),
            idempotency_key=str(row.get("idempotency_key") or ""),
            prior_value_text=str(row.get("prior_value_text") or ""),
            effective_at=row.get("effective_at"),
            observed_at=row.get("observed_at"),
            actor_role=str(row.get("actor_role") or ""),
            confidence=float(row.get("confidence") or 0.0),
            trust_tier=str(row.get("trust_tier") or ""),
            status=str(row.get("status") or ""),
            extractor_version=str(row.get("extractor_version") or ""),
            metadata=KontextRepository._coerce_json_dict(row.get("metadata")),
            created_at=row.get("created_at"),
        )

    @staticmethod
    def _state_event_from_row(row: dict) -> StateEvent:
        return StateEvent(
            id=str(row.get("id")),
            candidate_id=str(row.get("candidate_id")) if row.get("candidate_id") is not None else None,
            namespace=str(row.get("namespace") or "live"),
            subject_id=str(row.get("subject_id") or ""),
            source_kind=str(row.get("source_kind") or ""),
            source_id=str(row.get("source_id") or ""),
            source_hash=str(row.get("source_hash") or ""),
            source_span_hash=str(row.get("source_span_hash") or ""),
            event_hash=str(row.get("event_hash") or ""),
            state_key=str(row.get("state_key") or ""),
            event_type=str(row.get("event_type") or ""),
            value=KontextRepository._coerce_json_dict(row.get("value")),
            value_text=str(row.get("value_text") or ""),
            value_hash=str(row.get("value_hash") or ""),
            prior_value_text=str(row.get("prior_value_text") or ""),
            effective_at=row.get("effective_at"),
            observed_at=row.get("observed_at"),
            actor_role=str(row.get("actor_role") or ""),
            confidence=float(row.get("confidence") or 0.0),
            trust_tier=str(row.get("trust_tier") or ""),
            status=str(row.get("status") or ""),
            extractor_version=str(row.get("extractor_version") or ""),
            metadata=KontextRepository._coerce_json_dict(row.get("metadata")),
            created_at=row.get("created_at"),
        )

    @staticmethod
    def _state_edge_from_row(row: dict) -> StateEventEdge:
        return StateEventEdge(
            id=int(row.get("id")) if row.get("id") is not None else None,
            namespace=str(row.get("namespace") or "live"),
            source_event_id=str(row.get("source_event_id") or ""),
            target_event_id=str(row.get("target_event_id") or ""),
            edge_type=str(row.get("edge_type") or ""),
            state_key=str(row.get("state_key") or ""),
            confidence=float(row.get("confidence") or 0.0),
            reason_hash=str(row.get("reason_hash") or ""),
            metadata=KontextRepository._coerce_json_dict(row.get("metadata")),
            created_at=row.get("created_at"),
        )

    @staticmethod
    def _current_state_fact_from_row(row: dict) -> CurrentStateFact:
        return CurrentStateFact(
            id=str(row.get("id")) if row.get("id") is not None else None,
            namespace=str(row.get("namespace") or "live"),
            subject_id=str(row.get("subject_id") or ""),
            state_key=str(row.get("state_key") or ""),
            fact_value=KontextRepository._coerce_json_dict(row.get("fact_value")),
            fact_text=str(row.get("fact_text") or ""),
            active_event_id=str(row.get("active_event_id")) if row.get("active_event_id") is not None else None,
            support_event_ids=[str(item) for item in KontextRepository._coerce_json_list(row.get("support_event_ids"))],
            superseded_event_ids=[str(item) for item in KontextRepository._coerce_json_list(row.get("superseded_event_ids"))],
            cancelled_event_ids=[str(item) for item in KontextRepository._coerce_json_list(row.get("cancelled_event_ids"))],
            confidence=float(row.get("confidence") or 0.0),
            trust_tier=str(row.get("trust_tier") or ""),
            status=str(row.get("status") or ""),
            effective_at=row.get("effective_at"),
            projection_version=str(row.get("projection_version") or ""),
            metadata=KontextRepository._coerce_json_dict(row.get("metadata")),
            rebuilt_at=row.get("rebuilt_at"),
        )

    @staticmethod
    def _category_payload(row: dict) -> dict:
        return {
            "id": str(row.get("id") or ""),
            "slug": row.get("slug") or "",
            "name": row.get("name") or "",
            "description": row.get("description") or "",
        }

    def _ensure_default_categories(self, cur) -> None:
        for category in default_category_rows():
            cur.execute(
                """
                INSERT INTO categories (slug, name, description)
                VALUES (%s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description
                """,
                (category["slug"], category["name"], category["description"]),
            )

    def ensure_default_categories(self) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            self._ensure_default_categories(cur)
            rows = cur.execute(
                """
                SELECT id, slug, name, description
                FROM categories
                ORDER BY name ASC
                """
            ).fetchall()
        self.conn.commit()
        return [self._category_payload(dict(row)) for row in rows]

    def _replace_auto_category_assignments(self, cur, memory_id: object, slugs: list[str]) -> None:
        cur.execute(
            "DELETE FROM memory_categories WHERE memory_id = %s AND source = %s",
            (memory_id, "auto_inferred"),
        )
        for slug in slugs:
            cur.execute(
                """
                INSERT INTO memory_categories (memory_id, category_id, source)
                SELECT %s, id, %s FROM categories WHERE slug = %s
                ON CONFLICT (memory_id, category_id) DO NOTHING
                """,
                (memory_id, "auto_inferred", slug),
            )

    def upsert_category(self, *, slug: str, name: str, description: str = "") -> dict:
        normalized_slug = normalize_category_slug(slug or name)
        category_name = str(name or normalized_slug.replace("-", " ").title()).strip()
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO categories (slug, name, description)
                VALUES (%s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description
                RETURNING id, slug, name, description
                """,
                (normalized_slug, category_name, str(description or "")),
            ).fetchone()
        self.conn.commit()
        return self._category_payload(dict(row))

    def delete_category(self, slug: str) -> bool:
        normalized_slug = normalize_category_slug(slug)
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                "DELETE FROM categories WHERE slug = %s RETURNING id",
                (normalized_slug,),
            ).fetchone()
        self.conn.commit()
        return row is not None

    def assign_memory_category(self, *, external_mem0_id: str, category_slug: str, source: str = "manual") -> bool:
        normalized_slug = normalize_category_slug(category_slug)
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                SELECT m.id AS memory_id, c.id AS category_id
                FROM memories m
                CROSS JOIN categories c
                WHERE m.external_mem0_id = %s AND c.slug = %s
                """,
                (str(external_mem0_id or ""), normalized_slug),
            ).fetchone()
            if row is None:
                self.conn.commit()
                return False
            cur.execute(
                """
                INSERT INTO memory_categories (memory_id, category_id, source)
                VALUES (%s, %s, %s)
                ON CONFLICT (memory_id, category_id) DO UPDATE SET
                    source = EXCLUDED.source
                """,
                (row["memory_id"], row["category_id"], str(source or "manual")),
            )
        self.conn.commit()
        return True

    def unassign_memory_category(self, *, external_mem0_id: str, category_slug: str) -> bool:
        normalized_slug = normalize_category_slug(category_slug)
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                DELETE FROM memory_categories mc
                USING memories m, categories c
                WHERE mc.memory_id = m.id
                  AND mc.category_id = c.id
                  AND m.external_mem0_id = %s
                  AND c.slug = %s
                RETURNING mc.memory_id
                """,
                (str(external_mem0_id or ""), normalized_slug),
            ).fetchone()
        self.conn.commit()
        return row is not None

    def list_memory_category_slugs(self, external_mem0_id: str) -> list[str]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT c.slug
                FROM memory_categories mc
                JOIN memories m ON m.id = mc.memory_id
                JOIN categories c ON c.id = mc.category_id
                WHERE m.external_mem0_id = %s
                ORDER BY c.slug ASC
                """,
                (str(external_mem0_id or ""),),
            ).fetchall()
        return [str(row["slug"]) for row in rows]

    def list_categories(self) -> list[dict]:
        self.ensure_default_categories()
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT c.id, c.slug, c.name, c.description,
                       m.external_mem0_id, m.memory_tier, m.current_status, mc.source
                FROM categories c
                LEFT JOIN memory_categories mc ON mc.category_id = c.id
                LEFT JOIN memories m ON m.id = mc.memory_id
                ORDER BY c.name ASC, m.updated_at DESC NULLS LAST, m.external_mem0_id ASC
                """
            ).fetchall()

        categories: dict[str, dict] = {}
        for raw in rows:
            row = dict(raw)
            slug = str(row.get("slug") or "")
            if slug not in categories:
                categories[slug] = {
                    "id": str(row.get("id") or ""),
                    "slug": slug,
                    "name": row.get("name") or "",
                    "description": row.get("description") or "",
                    "count": 0,
                    "entries": [],
                    "stale": 0,
                    "uses": 0,
                    "sources": {},
                }
            memory_id = row.get("external_mem0_id")
            if not memory_id:
                continue
            item = categories[slug]
            item["count"] += 1
            item["entries"].append(str(memory_id))
            tier = str(row.get("memory_tier") or "").lower()
            status = str(row.get("current_status") or "").lower()
            if tier in {"historical", "cold", "archive"} or status in {"resolved", "dormant", "inactive"}:
                item["stale"] += 1
            source = str(row.get("source") or "unknown")
            item["sources"][source] = item["sources"].get(source, 0) + 1
        return list(categories.values())

    def list_category_memories(self, slug: str, limit: int = 100) -> list[dict]:
        normalized_slug = normalize_category_slug(slug)
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT m.external_mem0_id, m.title, m.metadata, m.memory_type,
                       m.current_status, m.memory_tier, m.signal_strength, mc.source
                FROM categories c
                JOIN memory_categories mc ON mc.category_id = c.id
                JOIN memories m ON m.id = mc.memory_id
                WHERE c.slug = %s
                ORDER BY m.signal_strength DESC NULLS LAST, m.updated_at DESC
                LIMIT %s
                """,
                (normalized_slug, min(max(int(limit or 100), 1), 500)),
            ).fetchall()
        return [
            {
                "external_mem0_id": row["external_mem0_id"],
                "id": row["external_mem0_id"],
                "title": row["title"],
                "metadata": self._coerce_metadata(row["metadata"]),
                "memory_type": row["memory_type"],
                "current_status": row["current_status"],
                "memory_tier": row["memory_tier"],
                "signal_strength": row["signal_strength"],
                "source": row["source"],
            }
            for row in rows
        ]


    def refresh_auto_category_assignments(self, limit: int = 10000) -> dict:
        self.ensure_default_categories()
        capped_limit = min(max(int(limit or 10000), 1), 100000)
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT id, external_mem0_id, title, text, metadata, memory_type,
                       current_status, memory_tier, signal_strength, source_hash
                FROM memories
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (capped_limit,),
            ).fetchall()
            refreshed = 0
            assignment_count = 0
            for row in rows:
                metadata = self._coerce_metadata(row["metadata"])
                memory = MemoryRecord(
                    external_mem0_id=row["external_mem0_id"],
                    title=row["title"],
                    text=row["text"],
                    metadata=metadata,
                    memory_type=row["memory_type"],
                    current_status=row["current_status"],
                    memory_tier=row["memory_tier"],
                    signal_strength=row["signal_strength"],
                    source_hash=row["source_hash"],
                )
                slugs = infer_category_slugs(memory)
                self._replace_auto_category_assignments(cur, row["id"], slugs)
                refreshed += 1
                assignment_count += len(slugs)
        self.conn.commit()
        return {"memories": refreshed, "assignments": assignment_count}

    def upsert_memory(self, memory: MemoryRecord, version_source: str = "mem0_import") -> None:
        metadata_json = json.dumps(memory.metadata, sort_keys=True)
        snapshot_json = json.dumps(
            {
                "external_mem0_id": memory.external_mem0_id,
                "title": memory.title,
                "text": memory.text,
                "metadata": memory.metadata,
                "memory_type": memory.memory_type,
                "current_status": memory.current_status,
                "memory_tier": memory.memory_tier,
                "signal_strength": memory.signal_strength,
                "source_hash": memory.source_hash,
            },
            sort_keys=True,
        )

        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO memories (
                    external_mem0_id,
                    title,
                    text,
                    metadata,
                    memory_type,
                    current_status,
                    memory_tier,
                    signal_strength,
                    source_hash
                )
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                ON CONFLICT (external_mem0_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    text = EXCLUDED.text,
                    metadata = EXCLUDED.metadata,
                    memory_type = EXCLUDED.memory_type,
                    current_status = EXCLUDED.current_status,
                    memory_tier = EXCLUDED.memory_tier,
                    signal_strength = EXCLUDED.signal_strength,
                    source_hash = EXCLUDED.source_hash,
                    updated_at = now()
                RETURNING id
                """,
                (
                    memory.external_mem0_id,
                    memory.title,
                    memory.text,
                    metadata_json,
                    memory.memory_type,
                    memory.current_status,
                    memory.memory_tier,
                    memory.signal_strength,
                    memory.source_hash,
                ),
            )
            memory_id = cur.fetchone()["id"]

            self._ensure_default_categories(cur)

            cur.execute(
                """
                INSERT INTO memory_versions (
                    memory_id,
                    external_mem0_id,
                    version_source,
                    snapshot,
                    source_hash
                )
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                (
                    memory_id,
                    memory.external_mem0_id,
                    version_source,
                    snapshot_json,
                    memory.source_hash,
                ),
            )
            self._replace_auto_category_assignments(cur, memory_id, infer_category_slugs(memory))

        self.conn.commit()

    def fetch_by_external_id(self, external_id: str) -> MemoryRecord | None:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    external_mem0_id,
                    title,
                    text,
                    metadata,
                    memory_type,
                    current_status,
                    memory_tier,
                    signal_strength,
                    source_hash
                FROM memories
                WHERE external_mem0_id = %s
                """,
                (external_id,),
            )
            row = cur.fetchone()

        if row is None:
            return None

        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        return MemoryRecord(
            external_mem0_id=row["external_mem0_id"],
            title=row["title"],
            text=row["text"],
            metadata=metadata,
            memory_type=row["memory_type"],
            current_status=row["current_status"],
            memory_tier=row["memory_tier"],
            signal_strength=row["signal_strength"],
            source_hash=row["source_hash"],
        )

    def find_exact_text_id(self, text: str) -> str:
        normalized = " ".join(str(text or "").strip().split())
        if not normalized:
            return ""
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                SELECT external_mem0_id
                FROM memories
                WHERE regexp_replace(btrim(text), '\\s+', ' ', 'g') = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        return str(row["external_mem0_id"]) if row else ""

    def count_memory_versions(self, external_mem0_id: str) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM memory_versions WHERE external_mem0_id = %s",
            (external_mem0_id,),
        ).fetchone()
        return _count_value(row)

    def search_rows(
        self,
        query: str,
        top_k: int,
        domains: list[str],
        memory_types: list[str],
        memory_tiers: list[str],
        current_statuses: list[str],
    ) -> list[dict]:
        search_query = " OR ".join(
            token for token in query.split() if token.strip()
        ) or query
        where = ["search_document @@ websearch_to_tsquery('simple', %s)"]
        params: list[object] = [search_query]

        if domains:
            where.append(
                "EXISTS ("
                "SELECT 1 FROM jsonb_array_elements_text(metadata->'domains') "
                "AS domain(value) WHERE domain.value = ANY(%s)"
                ")"
            )
            params.append(domains)
        if memory_types:
            where.append("memory_type = ANY(%s)")
            params.append(memory_types)
        if memory_tiers:
            where.append("memory_tier = ANY(%s)")
            params.append(memory_tiers)
        if current_statuses:
            where.append("current_status = ANY(%s)")
            params.append(current_statuses)

        sql = f"""
            SELECT external_mem0_id, title, text, metadata, memory_type,
                   current_status, memory_tier, signal_strength,
                   created_at, updated_at, imported_at,
                   ts_rank(search_document, websearch_to_tsquery('simple', %s)) AS rank
            FROM memories
            WHERE {' AND '.join(where)}
            ORDER BY rank DESC, signal_strength DESC NULLS LAST, updated_at DESC
            LIMIT %s
        """
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(sql, [search_query, *params, top_k]).fetchall()
        return [dict(row) for row in rows]


    def record_intake_audit(
        self,
        *,
        source_hash: str,
        origin: str,
        action: str,
        status: str,
        metadata: dict | None = None,
    ) -> dict:
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO memory_intake_audit (source_hash, origin, action, status, metadata)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                RETURNING id, source_hash, origin, action, status, metadata, created_at
                """,
                (source_hash, origin, action, status, metadata_json),
            ).fetchone()
        self.conn.commit()
        return dict(row)

    def count_intake_audit(self) -> int:
        row = self.conn.execute("SELECT count(*) FROM memory_intake_audit").fetchone()
        return _count_value(row)


    def list_dry_run_write_audit(self, limit: int = 30) -> dict:
        allowed_actions = (
            "save",
            "update",
            "delete",
            "extract_memories",
            "ingest_exchange",
            "submit_memory_override",
            "flag_memory",
        )
        safe_limit = min(max(int(limit or 30), 1), 100)
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT id, source_hash, origin, action, status, metadata, created_at
                FROM memory_intake_audit
                WHERE status = 'dry_run' AND action = ANY(%s)
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (list(allowed_actions), safe_limit),
            ).fetchall()
            action_rows = cur.execute(
                """
                SELECT action, count(*) AS count
                FROM memory_intake_audit
                WHERE status = 'dry_run' AND action = ANY(%s)
                GROUP BY action
                ORDER BY action
                """,
                (list(allowed_actions),),
            ).fetchall()
            origin_rows = cur.execute(
                """
                SELECT origin, count(*) AS count
                FROM memory_intake_audit
                WHERE status = 'dry_run' AND action = ANY(%s)
                GROUP BY origin
                ORDER BY count DESC, origin
                LIMIT 10
                """,
                (list(allowed_actions),),
            ).fetchall()
        items = [self._dry_run_audit_row(dict(row)) for row in rows]
        return {
            "items": items,
            "count": len(items),
            "summary": {
                "by_action": {str(row["action"]): int(row["count"] or 0) for row in action_rows},
                "by_origin": {str(row["origin"] or "unknown"): int(row["count"] or 0) for row in origin_rows},
            },
        }

    def _dry_run_audit_row(self, row: dict) -> dict:
        metadata = self._coerce_metadata(row.get("metadata"))
        safe_metadata = {
            key: metadata[key]
            for key in ("tool", "proposal_count")
            if key in metadata
        }
        created_at = row.get("created_at")
        return {
            "id": int(row.get("id") or 0),
            "source_hash": str(row.get("source_hash") or ""),
            "source_hash_short": str(row.get("source_hash") or "")[:12],
            "origin": str(row.get("origin") or "unknown"),
            "action": str(row.get("action") or ""),
            "status": str(row.get("status") or ""),
            "metadata": safe_metadata,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
        }

    def record_memory_flag(
        self,
        *,
        external_mem0_id: str,
        flag_type: str,
        reason_hash: str,
        confidence: float,
        origin: str,
        status: str,
        metadata: dict | None = None,
        protection_check: bool = True,
    ) -> dict:
        if protection_check:
            target = self.fetch_by_external_id(external_mem0_id)
            if cleanup_flag_blocked_for_protected_history(flag_type, target):
                return {
                    "external_mem0_id": external_mem0_id,
                    "flag_type": flag_type,
                    "status": "blocked_protected",
                    "protected": True,
                }
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO memory_flags (
                    external_mem0_id, flag_type, reason_hash, confidence, origin, status, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id, external_mem0_id, flag_type, reason_hash, confidence, origin, status, metadata, created_at
                """,
                (external_mem0_id, flag_type, reason_hash, float(confidence), origin, status, metadata_json),
            ).fetchone()
        self.conn.commit()
        return dict(row)

    def count_memory_flags(self) -> int:
        row = self.conn.execute("SELECT count(*) FROM memory_flags").fetchone()
        return _count_value(row)

    def record_project_observation(self, payload: dict) -> dict:
        row = build_observation(payload)
        files = {
            "touched_paths": row["touched_paths"],
            "files_read": row["files_read"],
            "files_modified": row["files_modified"],
        }
        metadata = {
            "git_branch": row["git_branch"],
            "worktree_root": row["worktree_root"],
            "tool_name": row["tool_name"],
            "command_category": row["command_category"],
            "status": row["status"],
            "outcome": row["outcome"],
            "commands": row["commands"],
            "tests": row["tests"],
            "decisions": row["decisions"],
            "next_steps": row["next_steps"],
            "memory_ids": row["memory_ids"],
        }
        with self.conn.cursor(row_factory=dict_row) as cur:
            saved = cur.execute(
                """
                INSERT INTO project_observations (
                    observation_id, origin, project, project_root, cwd, event_type,
                    title, summary, files, metadata, source_hash, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::timestamptz)
                ON CONFLICT (source_hash) DO NOTHING
                RETURNING observation_id AS id, origin, project, project_root, cwd, event_type, title, summary, files, metadata, source_hash, created_at
                """,
                (
                    row["id"],
                    row["origin"],
                    row["project"],
                    row["project_root"],
                    row["cwd"],
                    row["event_type"],
                    row["title"],
                    row["summary"],
                    json.dumps(files, sort_keys=True),
                    json.dumps(metadata, sort_keys=True),
                    row["source_hash"],
                    row["timestamp"],
                ),
            ).fetchone()
            appended = saved is not None
            if saved is None:
                saved = cur.execute(
                    """
                    SELECT observation_id AS id, origin, project, project_root, cwd, event_type,
                           title, summary, files, metadata, source_hash, created_at
                    FROM project_observations
                    WHERE source_hash = %s
                    """,
                    (row["source_hash"],),
                ).fetchone()
        self.conn.commit()
        result = row_from_record(dict(saved))
        result["appended"] = appended
        return result

    def record_hook_heartbeat(self, payload: dict) -> dict:
        origin = str(payload.get("origin") or "").strip()[:80]
        hook_type = str(payload.get("hook_type") or "").strip()[:120]
        source = str(payload.get("source") or "").strip()
        marker = str(payload.get("marker") or "").strip()
        source_hash = hashlib.sha256("|".join([origin, hook_type, source, marker]).encode("utf-8")).hexdigest()
        marker_hash = hashlib.sha256(marker.encode("utf-8")).hexdigest() if marker else ""
        metadata = {
            "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest() if source else "",
            "marker_present": bool(marker),
        }
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO hook_heartbeats (origin, hook_type, source_hash, marker_hash, metadata)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (origin, hook_type, source_hash, marker_hash, json.dumps(metadata, sort_keys=True)),
            ).fetchone()
        self.conn.commit()
        return {"ok": True, "id": int(row["id"]), "origin": origin, "hook_type": hook_type}

    def maintenance_status(self, recent_limit: int = 500, pending_flag_threshold: int = 10) -> dict:
        limit = min(max(int(recent_limit or 500), 1), 5000)
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT flag_type, COUNT(*) AS count
                FROM (
                    SELECT flag_type
                    FROM memory_flags
                    WHERE status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT %s
                ) recent
                GROUP BY flag_type
                ORDER BY flag_type ASC
                """,
                (limit,),
            ).fetchall()
        flag_types = {str(row["flag_type"]): int(row["count"]) for row in rows}
        pending_flags = sum(flag_types.values())
        due = pending_flags >= int(pending_flag_threshold or 10)
        return {
            "due": due,
            "pending_flags": pending_flags,
            "flag_types": flag_types,
            "requires_approval": True,
            "reasons": ["pending_flags_threshold"] if due else [],
            "reminder": (
                f"Memory maintenance due: {pending_flags} pending flags; ask Ionut before running dream cleanup."
                if due
                else ""
            ),
        }

    def _project_observation_scope_clause(self, project: str | None = None, project_root: str | None = None) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        project_value = str(project or "").strip()
        project_root_value = normalize_path(project_root) if str(project_root or "").strip() else ""
        if project_value and project_value.lower() != "all":
            clauses.append("LOWER(project) = LOWER(%s)")
            params.append(project_value)
        if project_root_value and project_root_value.lower() != "all":
            clauses.append("LOWER(project_root) = LOWER(%s)")
            params.append(project_root_value)
        return (" WHERE " + " AND ".join(clauses), params) if clauses else ("", params)

    def _project_observation_rows(self, project: str | None = None, project_root: str | None = None) -> list[dict]:
        where_sql, params = self._project_observation_scope_clause(project=project, project_root=project_root)
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"""
                SELECT observation_id, origin, project, project_root, cwd, event_type,
                       title, summary, files, metadata, source_hash, created_at
                FROM project_observations
                {where_sql}
                ORDER BY created_at ASC, id ASC
                """,
                tuple(params),
            ).fetchall()
        return [row_from_record(dict(row)) for row in rows]

    def _project_observation_search_rows(
        self,
        query: str,
        limit: int,
        project: str | None = None,
        project_root: str | None = None,
    ) -> list[dict]:
        safe_limit = min(max(int(limit or 10), 1), 50)
        scope_sql, scope_params = self._project_observation_scope_clause(project=project, project_root=project_root)
        scope_sql = scope_sql.replace(" WHERE ", " AND ", 1)
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"""
                SELECT observation_id, origin, project, project_root, cwd, event_type,
                       title, summary, files, metadata, source_hash, created_at,
                       ts_rank_cd(search_document, websearch_to_tsquery('simple', %s)) AS rank
                FROM project_observations
                WHERE search_document @@ websearch_to_tsquery('simple', %s)
                {scope_sql}
                ORDER BY rank DESC, created_at DESC, id ASC
                LIMIT %s
                """,
                (query, query, *scope_params, safe_limit),
            ).fetchall()
        return [row_from_record(dict(row)) for row in rows]

    def project_search(
        self,
        query: str,
        limit: int = 10,
        project: str | None = None,
        project_root: str | None = None,
    ) -> dict:
        safe_limit = max(int(limit or 0), 0)
        selected: list[dict] = []
        if str(query or "").strip() and safe_limit:
            try:
                selected = self._project_observation_search_rows(query, safe_limit, project=project, project_root=project_root)
            except Exception:
                selected = []
        if not selected:
            scored = [(score_row(row, query), row) for row in self._project_observation_rows(project=project, project_root=project_root)]
            matches = [row for score, row in sorted(scored, key=lambda item: (-item[0], item[1].get("timestamp") or "")) if score > 0]
            selected = matches[:safe_limit]
        return {"rows": [compact_row(row) for row in selected], "count": len(selected)}

    def project_fetch(self, observation_id: str) -> dict:
        observation_id = str(observation_id or "").strip()
        for row in self._project_observation_rows():
            if row.get("id") == observation_id:
                return {"found": True, "row": row}
        return {"found": False, "id": observation_id}

    def project_timeline(
        self,
        anchor_id: str = "",
        query: str = "",
        before: int = 3,
        after: int = 3,
        project: str | None = None,
        project_root: str | None = None,
    ) -> dict:
        rows = self._project_observation_rows(project=project, project_root=project_root)
        anchor_id = str(anchor_id or "").strip()
        if not anchor_id and query:
            search_rows = self.project_search(query, limit=1, project=project, project_root=project_root)["rows"]
            anchor_id = search_rows[0]["id"] if search_rows else ""
        index = next((idx for idx, row in enumerate(rows) if row.get("id") == anchor_id), -1)
        if index < 0:
            return {"anchor_id": anchor_id, "rows": []}
        start = max(index - max(int(before or 0), 0), 0)
        stop = min(index + max(int(after or 0), 0) + 1, len(rows))
        return {"anchor_id": anchor_id, "rows": [compact_row(row) for row in rows[start:stop]]}

    def project_file_context(
        self,
        file_path: str,
        limit: int = 10,
        project: str | None = None,
        project_root: str | None = None,
    ) -> dict:
        target = normalize_path(file_path)
        matches = []
        for row in self._project_observation_rows(project=project, project_root=project_root):
            paths = set(row.get("files_read") or []) | set(row.get("files_modified") or []) | set(row.get("touched_paths") or [])
            if target in paths:
                matches.append(row)
        selected = matches[-max(int(limit or 0), 0):]
        return {
            "file_path": target,
            "titles": [str(row.get("title") or "") for row in selected],
            "rows": [compact_row(row) for row in selected],
            "recommend_full_file_read": not bool(selected),
        }
    def record_retrieval_query(
        self,
        *,
        query: str,
        origin: str,
        service: str,
        profile: str,
        filters: dict,
        result_external_ids: list[str],
        latency_ms: float,
    ) -> dict:
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        ids = [str(value) for value in result_external_ids if str(value or "").strip()][:20]
        filters_json = json.dumps(filters or {}, sort_keys=True)
        ids_json = json.dumps(ids)
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO retrieval_queries (
                    query_hash, origin, service, profile, filters, result_external_ids, latency_ms
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (query_hash, origin, service, profile, filters_json, ids_json, float(latency_ms)),
            )
        self.conn.commit()
        return {"query_hash": query_hash, "result_count": len(ids)}

    def record_mirror_sync_run(
        self,
        *,
        source: str,
        mode: str,
        status: str,
        dry_run: bool,
        rows_seen: int,
        created: int,
        updated: int,
        unchanged: int,
        skipped: int,
        error_count: int = 0,
        metadata: dict | None = None,
    ) -> dict:
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO mirror_sync_runs (
                    source, mode, status, dry_run, rows_seen, created_count,
                    updated_count, unchanged_count, skipped_count, error_count, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id, source, mode, status, dry_run, rows_seen, created_count,
                    updated_count, unchanged_count, skipped_count, error_count, metadata, finished_at
                """,
                (
                    source,
                    mode,
                    status,
                    bool(dry_run),
                    int(rows_seen),
                    int(created),
                    int(updated),
                    int(unchanged),
                    int(skipped),
                    int(error_count),
                    metadata_json,
                ),
            ).fetchone()
        self.conn.commit()
        return self._sync_row(row)

    def latest_mirror_sync_run(self, source: str | None = None) -> dict | None:
        sql = """
            SELECT id, source, mode, status, dry_run, rows_seen, created_count,
                   updated_count, unchanged_count, skipped_count, error_count, metadata, finished_at
            FROM mirror_sync_runs
        """
        params: list[object] = []
        if source:
            sql += " WHERE source = %s"
            params.append(source)
        sql += " ORDER BY finished_at DESC, id DESC LIMIT 1"
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(sql, params).fetchone()
        return self._sync_row(row) if row is not None else None

    @staticmethod
    def _sync_row(row: dict) -> dict:
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return {
            "id": row.get("id"),
            "source": row.get("source"),
            "mode": row.get("mode"),
            "status": row.get("status"),
            "dry_run": bool(row.get("dry_run")),
            "rows_seen": int(row.get("rows_seen") or 0),
            "created": int(row.get("created_count") or 0),
            "updated": int(row.get("updated_count") or 0),
            "unchanged": int(row.get("unchanged_count") or 0),
            "skipped": int(row.get("skipped_count") or 0),
            "error_count": int(row.get("error_count") or 0),
            "metadata": metadata,
            "finished_at": row.get("finished_at").isoformat() if hasattr(row.get("finished_at"), "isoformat") else row.get("finished_at"),
        }

    def get_or_create_state_subject(
        self,
        *,
        namespace: str = "live",
        subject_type: str,
        subject_key: str,
        display_name: str = "",
        aliases: list[str] | None = None,
        scope: dict | None = None,
    ) -> StateSubject:
        normalized_namespace = normalize_namespace(namespace)
        normalized_type = normalize_subject_type(subject_type)
        normalized_key = normalize_subject_key(subject_key)
        aliases_json = json.dumps(list(aliases or []), sort_keys=True)
        scope_json = json.dumps(scope or {}, sort_keys=True)
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO state_subjects (namespace, subject_type, subject_key, display_name, aliases, scope)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (namespace, subject_type, subject_key) DO UPDATE SET
                    display_name = CASE
                        WHEN EXCLUDED.display_name <> '' THEN EXCLUDED.display_name
                        ELSE state_subjects.display_name
                    END,
                    aliases = EXCLUDED.aliases,
                    scope = EXCLUDED.scope,
                    updated_at = now()
                RETURNING id, namespace, subject_type, subject_key, display_name, aliases, scope, created_at, updated_at
                """,
                (normalized_namespace, normalized_type, normalized_key, str(display_name or ""), aliases_json, scope_json),
            ).fetchone()
        self._commit()
        return self._state_subject_from_row(dict(row))

    def stage_state_event_candidate(
        self,
        *,
        namespace: str = "live",
        source_kind: str,
        source_id: str = "",
        source_hash: str,
        source_span_hash: str = "",
        subject_type: str,
        subject_key: str,
        state_key: str,
        event_type: str,
        value: dict | None = None,
        value_text: str = "",
        prior_value_text: str = "",
        effective_at: object | None = None,
        observed_at: object | None = None,
        actor_role: str = "",
        confidence: float = 0.0,
        trust_tier: str = "extracted_low",
        status: str = "staged",
        extractor_version: str = "",
        metadata: dict | None = None,
    ) -> StateEventCandidate:
        normalized_namespace = normalize_namespace(namespace)
        normalized_subject_type = normalize_subject_type(subject_type)
        normalized_subject_key = normalize_subject_key(subject_key)
        normalized_state_key = normalize_state_key(state_key)
        normalized_event_type = validate_event_type(event_type)
        payload = dict(value or {})
        payload_hash = make_value_hash(payload)
        idem_key = make_idempotency_key(
            namespace=normalized_namespace,
            source_hash=source_hash,
            source_span_hash=source_span_hash,
            extractor_version=extractor_version,
            subject_type=normalized_subject_type,
            subject_key=normalized_subject_key,
            state_key=normalized_state_key,
            event_type=normalized_event_type,
            value_hash=payload_hash,
        )
        with self.conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO state_event_candidates (
                    namespace, source_kind, source_id, source_hash, source_span_hash,
                    subject_type, subject_key, state_key, event_type,
                    value, value_text, value_hash, idempotency_key, prior_value_text,
                    effective_at, observed_at, actor_role, confidence, trust_tier,
                    status, extractor_version, metadata
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s::jsonb, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb
                )
                ON CONFLICT (namespace, idempotency_key) DO UPDATE SET
                    idempotency_key = EXCLUDED.idempotency_key
                RETURNING *
                """,
                (
                    normalized_namespace,
                    str(source_kind or ""),
                    str(source_id or ""),
                    str(source_hash or ""),
                    str(source_span_hash or ""),
                    normalized_subject_type,
                    normalized_subject_key,
                    normalized_state_key,
                    normalized_event_type,
                    json.dumps(payload, sort_keys=True),
                    str(value_text or ""),
                    payload_hash,
                    idem_key,
                    str(prior_value_text or ""),
                    effective_at,
                    observed_at,
                    str(actor_role or ""),
                    float(confidence or 0.0),
                    str(trust_tier or "extracted_low"),
                    str(status or "staged"),
                    str(extractor_version or ""),
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            ).fetchone()
        self._commit()
        return self._state_candidate_from_row(dict(row))

    def accept_state_event_candidate(self, candidate_id: str, *, namespace: str = "live") -> StateEvent:
        with self.transaction():
            normalized_namespace = normalize_namespace(namespace)
            with self.conn.cursor(row_factory=dict_row) as cur:
                candidate_row = cur.execute(
                    "SELECT * FROM state_event_candidates WHERE id = %s AND namespace = %s",
                    (candidate_id, normalized_namespace),
                ).fetchone()
            if candidate_row is None:
                raise ValueError(f"State event candidate not found: {candidate_id}")
            candidate = self._state_candidate_from_row(dict(candidate_row))
            subject = self.get_or_create_state_subject(
                namespace=candidate.namespace,
                subject_type=candidate.subject_type,
                subject_key=candidate.subject_key,
                display_name=candidate.subject_key,
            )
            event = self.insert_state_event(
                namespace=candidate.namespace,
                candidate_id=candidate.id,
                subject_id=subject.id,
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                source_hash=candidate.source_hash,
                source_span_hash=candidate.source_span_hash,
                subject_type=candidate.subject_type,
                subject_key=candidate.subject_key,
                state_key=candidate.state_key,
                event_type=candidate.event_type,
                value=candidate.value,
                value_text=candidate.value_text,
                prior_value_text=candidate.prior_value_text,
                effective_at=candidate.effective_at,
                observed_at=candidate.observed_at,
                actor_role=candidate.actor_role,
                confidence=candidate.confidence,
                trust_tier=candidate.trust_tier,
                extractor_version=candidate.extractor_version,
                metadata=candidate.metadata,
            )
            with self.conn.cursor() as cur:
                cur.execute("UPDATE state_event_candidates SET status = 'accepted' WHERE id = %s", (candidate_id,))
            return event

    def insert_state_event(
        self,
        *,
        namespace: str = "live",
        candidate_id: str | None = None,
        subject_id: str | None = None,
        source_kind: str,
        source_id: str = "",
        source_hash: str,
        source_span_hash: str = "",
        subject_type: str,
        subject_key: str,
        state_key: str,
        event_type: str,
        value: dict | None = None,
        value_text: str = "",
        prior_value_text: str = "",
        effective_at: object | None = None,
        observed_at: object | None = None,
        actor_role: str = "",
        confidence: float = 0.0,
        trust_tier: str = "extracted_low",
        extractor_version: str = "",
        metadata: dict | None = None,
    ) -> StateEvent:
        with self.transaction():
            normalized_namespace = normalize_namespace(namespace)
            normalized_state_key = normalize_state_key(state_key)
            normalized_event_type = validate_event_type(event_type)
            payload = dict(value or {})
            payload_hash = make_value_hash(payload)
            if subject_id is None:
                subject = self.get_or_create_state_subject(
                    namespace=normalized_namespace,
                    subject_type=subject_type,
                    subject_key=subject_key,
                    display_name=normalize_subject_key(subject_key),
                )
                subject_id = subject.id
            if subject_id is None:
                raise ValueError("state event requires subject_id")
            stable_event_hash = make_event_hash(
                namespace=normalized_namespace,
                subject_id=subject_id,
                source_hash=source_hash,
                source_span_hash=source_span_hash,
                extractor_version=extractor_version,
                state_key=normalized_state_key,
                event_type=normalized_event_type,
                value_hash=payload_hash,
                effective_at=effective_at,
                observed_at=observed_at,
            )
            with self.conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute(
                    """
                    INSERT INTO state_events (
                        candidate_id, namespace, subject_id, source_kind, source_id,
                        source_hash, source_span_hash, event_hash, state_key, event_type,
                        value, value_text, value_hash, prior_value_text, effective_at,
                        observed_at, actor_role, confidence, trust_tier, status,
                        extractor_version, metadata
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s, %s, %s,
                        %s, %s, %s, %s, 'accepted',
                        %s, %s::jsonb
                    )
                    ON CONFLICT (namespace, event_hash) DO NOTHING
                    RETURNING *
                    """,
                    (
                        candidate_id,
                        normalized_namespace,
                        subject_id,
                        str(source_kind or ""),
                        str(source_id or ""),
                        str(source_hash or ""),
                        str(source_span_hash or ""),
                        stable_event_hash,
                        normalized_state_key,
                        normalized_event_type,
                        json.dumps(payload, sort_keys=True),
                        str(value_text or ""),
                        payload_hash,
                        str(prior_value_text or ""),
                        effective_at,
                        observed_at,
                        str(actor_role or ""),
                        float(confidence or 0.0),
                        str(trust_tier or "extracted_low"),
                        str(extractor_version or ""),
                        json.dumps(metadata or {}, sort_keys=True),
                    ),
                ).fetchone()
                if row is None:
                    row = cur.execute(
                        "SELECT * FROM state_events WHERE namespace = %s AND event_hash = %s",
                        (normalized_namespace, stable_event_hash),
                    ).fetchone()
            return self._state_event_from_row(dict(row))

    def insert_state_event_edge(
        self,
        *,
        namespace: str = "live",
        source_event_id: str,
        target_event_id: str,
        edge_type: str,
        state_key: str,
        confidence: float = 1.0,
        reason_hash: str = "",
        metadata: dict | None = None,
    ) -> StateEventEdge:
        normalized_namespace = normalize_namespace(namespace)
        normalized_edge_type = validate_edge_type(edge_type)
        normalized_state_key = normalize_state_key(state_key)
        with self.conn.cursor(row_factory=dict_row) as cur:
            event_rows = cur.execute(
                """
                SELECT id, namespace, subject_id, state_key
                FROM state_events
                WHERE id = ANY(%s::uuid[])
                """,
                ([source_event_id, target_event_id],),
            ).fetchall()
            events_by_id = {str(row["id"]): dict(row) for row in event_rows}
            source_event = events_by_id.get(str(source_event_id))
            target_event = events_by_id.get(str(target_event_id))
            if source_event is None or target_event is None:
                raise ValueError("state edge requires existing source and target events")
            if source_event["namespace"] != normalized_namespace or target_event["namespace"] != normalized_namespace:
                raise ValueError("state edge namespace must match source and target events")
            if source_event["subject_id"] != target_event["subject_id"]:
                raise ValueError("state edge source and target must share subject_id")
            if (
                normalize_state_key(source_event["state_key"]) != normalized_state_key
                or normalize_state_key(target_event["state_key"]) != normalized_state_key
            ):
                raise ValueError("state edge state_key must match source and target events")
            row = cur.execute(
                """
                INSERT INTO state_event_edges (
                    namespace, source_event_id, target_event_id, edge_type,
                    state_key, confidence, reason_hash, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (source_event_id, target_event_id, edge_type, state_key) DO UPDATE SET
                    confidence = state_event_edges.confidence
                RETURNING *
                """,
                (
                    normalized_namespace,
                    source_event_id,
                    target_event_id,
                    normalized_edge_type,
                    normalized_state_key,
                    float(confidence or 0.0),
                    str(reason_hash or ""),
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            ).fetchone()
        self._commit()
        return self._state_edge_from_row(dict(row))

    def rebuild_current_state_projection(
        self,
        *,
        namespace: str = "live",
        projection_version: str = DEFAULT_PROJECTION_VERSION,
        typed_answer_emitter=None,
    ) -> list[CurrentStateFact]:
        normalized_namespace = normalize_namespace(namespace)
        with self.conn.cursor(row_factory=dict_row) as cur:
            event_rows = cur.execute(
                """
                SELECT *
                FROM state_events
                WHERE namespace = %s AND status = 'accepted'
                ORDER BY created_at ASC, id ASC
                """,
                (normalized_namespace,),
            ).fetchall()
            edge_rows = cur.execute(
                """
                SELECT *
                FROM state_event_edges
                WHERE namespace = %s
                ORDER BY id ASC
                """,
                (normalized_namespace,),
            ).fetchall()
        events = [self._state_event_from_row(dict(row)) for row in event_rows]
        edges = [self._state_edge_from_row(dict(row)) for row in edge_rows]
        facts = build_current_state_facts(
            events,
            edges,
            projection_version=projection_version,
            typed_answer_emitter=typed_answer_emitter,
        )
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("DELETE FROM current_state_facts WHERE namespace = %s", (normalized_namespace,))
            saved: list[CurrentStateFact] = []
            for fact in facts:
                row = cur.execute(
                    """
                    INSERT INTO current_state_facts (
                        namespace, subject_id, state_key, fact_value, fact_text,
                        active_event_id, support_event_ids, superseded_event_ids,
                        cancelled_event_ids, confidence, trust_tier, status,
                        effective_at, projection_version, metadata
                    )
                    VALUES (
                        %s, %s, %s, %s::jsonb, %s,
                        %s, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s, %s, %s,
                        %s, %s, %s::jsonb
                    )
                    RETURNING *
                    """,
                    (
                        fact.namespace,
                        fact.subject_id,
                        fact.state_key,
                        json.dumps(fact.fact_value, sort_keys=True),
                        fact.fact_text,
                        fact.active_event_id,
                        json.dumps(fact.support_event_ids, sort_keys=True),
                        json.dumps(fact.superseded_event_ids, sort_keys=True),
                        json.dumps(fact.cancelled_event_ids, sort_keys=True),
                        fact.confidence,
                        fact.trust_tier,
                        fact.status,
                        fact.effective_at,
                        fact.projection_version,
                        json.dumps(fact.metadata, sort_keys=True),
                    ),
                ).fetchone()
                saved.append(self._current_state_fact_from_row(dict(row)))
        self._commit()
        return saved

    def project_typed_state(
        self,
        *,
        namespace: str = "live",
        projection_version: str = DEFAULT_PROJECTION_VERSION,
    ) -> list[CurrentStateFact]:
        return self.rebuild_current_state_projection(
            namespace=namespace,
            projection_version=projection_version,
            typed_answer_emitter=render_current_state_typed,
        )

    def search_current_state_facts(self, query: str, *, namespace: str = "live", top_k: int = 5) -> list[dict]:
        normalized_namespace = normalize_namespace(namespace)
        safe_top_k = min(max(int(top_k or 5), 1), 50)
        search_query = " OR ".join(token for token in str(query or "").split() if token.strip()) or str(query or "")
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT f.id, f.namespace, f.subject_id, f.state_key, f.fact_value,
                       f.fact_text, f.active_event_id, f.support_event_ids,
                       f.superseded_event_ids, f.cancelled_event_ids, f.confidence,
                       f.trust_tier, f.status, f.effective_at, f.rebuilt_at,
                       f.projection_version, f.metadata,
                       active_event.event_type AS state_event_type,
                       s.subject_type, s.subject_key,
                       ts_rank(f.search_document, websearch_to_tsquery('simple', %s)) AS rank
                FROM current_state_facts f
                JOIN state_subjects s ON s.id = f.subject_id
                LEFT JOIN state_events active_event ON active_event.id = f.active_event_id
                WHERE f.namespace = %s
                  AND f.status IN ('active', 'cancelled', 'ambiguous')
                  AND f.search_document @@ websearch_to_tsquery('simple', %s)
                ORDER BY rank DESC, f.confidence DESC, f.effective_at DESC NULLS LAST
                LIMIT %s
                """,
                (search_query, normalized_namespace, search_query, safe_top_k),
            ).fetchall()
            if not rows:
                rows = cur.execute(
                    """
                    SELECT f.id, f.namespace, f.subject_id, f.state_key, f.fact_value,
                           f.fact_text, f.active_event_id, f.support_event_ids,
                           f.superseded_event_ids, f.cancelled_event_ids, f.confidence,
                           f.trust_tier, f.status, f.effective_at, f.rebuilt_at,
                           f.projection_version, f.metadata,
                           active_event.event_type AS state_event_type,
                           s.subject_type, s.subject_key,
                           0.0::double precision AS rank
                    FROM current_state_facts f
                    JOIN state_subjects s ON s.id = f.subject_id
                    LEFT JOIN state_events active_event ON active_event.id = f.active_event_id
                    WHERE f.namespace = %s
                      AND f.status IN ('active', 'cancelled', 'ambiguous')
                    ORDER BY f.confidence DESC, f.effective_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (normalized_namespace, safe_top_k),
                ).fetchall()
        results: list[dict] = []
        for row in rows:
            row_dict = dict(row)
            metadata = self._coerce_json_dict(row_dict.get("metadata"))
            metadata.update(
                {
                    "retrieval_path": "state_projection",
                    "namespace": row_dict.get("namespace"),
                    "subject_id": str(row_dict.get("subject_id")),
                    "subject_type": row_dict.get("subject_type"),
                    "subject_key": row_dict.get("subject_key"),
                    "state_key": row_dict.get("state_key"),
                    "state_status": row_dict.get("status"),
                    "state_event_type": row_dict.get("state_event_type") or "",
                    "current_value": self._coerce_json_dict(row_dict.get("fact_value")),
                    "active_event_id": str(row_dict.get("active_event_id")) if row_dict.get("active_event_id") else "",
                    "support_event_ids": [str(item) for item in self._coerce_json_list(row_dict.get("support_event_ids"))],
                    "superseded_event_ids": [str(item) for item in self._coerce_json_list(row_dict.get("superseded_event_ids"))],
                    "cancelled_event_ids": [str(item) for item in self._coerce_json_list(row_dict.get("cancelled_event_ids"))],
                    "projection_version": row_dict.get("projection_version"),
                }
            )
            results.append(
                {
                    "external_mem0_id": f"state:{row_dict.get('id')}",
                    "title": f"Current state: {row_dict.get('state_key')}",
                    "text": row_dict.get("fact_text") or "",
                    "fact_value": self._coerce_json_dict(row_dict.get("fact_value")),
                    "state_event_type": row_dict.get("state_event_type") or "",
                    "metadata": metadata,
                    "memory_type": "current_state",
                    "current_status": row_dict.get("status") or "active",
                    "memory_tier": "active",
                    "signal_strength": float(row_dict.get("confidence") or 0.0) * 10.0,
                    "created_at": row_dict.get("rebuilt_at"),
                    "updated_at": row_dict.get("rebuilt_at"),
                    "imported_at": row_dict.get("rebuilt_at"),
                    "rank": float(row_dict.get("rank") or 0.0),
                    "_retrieval_path": "state_projection",
                }
            )
        return results

    def list_state_history(
        self,
        *,
        namespace: str = "live",
        subject_type: str,
        subject_key: str,
        state_key: str | None = None,
    ) -> dict:
        normalized_namespace = normalize_namespace(namespace)
        normalized_type = normalize_subject_type(subject_type)
        normalized_key = normalize_subject_key(subject_key)
        normalized_state_key = normalize_state_key(state_key) if state_key else None
        with self.conn.cursor(row_factory=dict_row) as cur:
            subject_row = cur.execute(
                """
                SELECT *
                FROM state_subjects
                WHERE namespace = %s AND subject_type = %s AND subject_key = %s
                """,
                (normalized_namespace, normalized_type, normalized_key),
            ).fetchone()
            if subject_row is None:
                return {"subject": None, "events": [], "edges": []}
            subject = self._state_subject_from_row(dict(subject_row))
            params: list[object] = [subject.id]
            where = ["subject_id = %s"]
            if normalized_state_key:
                where.append("state_key = %s")
                params.append(normalized_state_key)
            events = [
                self._state_event_from_row(dict(row))
                for row in cur.execute(
                    f"""
                    SELECT *
                    FROM state_events
                    WHERE {' AND '.join(where)}
                    ORDER BY effective_at ASC NULLS LAST, created_at ASC, id ASC
                    """,
                    tuple(params),
                ).fetchall()
            ]
            event_ids = [event.id for event in events]
            if not event_ids:
                return {"subject": subject, "events": [], "edges": []}
            edges = [
                self._state_edge_from_row(dict(row))
                for row in cur.execute(
                    """
                    SELECT *
                    FROM state_event_edges
                    WHERE source_event_id = ANY(%s::uuid[]) OR target_event_id = ANY(%s::uuid[])
                    ORDER BY id ASC
                    """,
                    (event_ids, event_ids),
                ).fetchall()
            ]
        return {"subject": subject, "events": events, "edges": edges}

    def list_state_event_candidates(
        self,
        *,
        namespace: str = "live",
        status: str | None = None,
        limit: int = 50,
    ) -> list[StateEventCandidate]:
        normalized_namespace = normalize_namespace(namespace)
        safe_limit = min(max(int(limit or 50), 1), 500)
        where = ["namespace = %s"]
        params: list[object] = [normalized_namespace]
        if status:
            where.append("status = %s")
            params.append(str(status))
        params.append(safe_limit)
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"""
                SELECT *
                FROM state_event_candidates
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC, id ASC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        return [self._state_candidate_from_row(dict(row)) for row in rows]

    def state_model_counts(self, *, namespace: str = "live") -> dict:
        normalized_namespace = normalize_namespace(namespace)
        with self.conn.cursor(row_factory=dict_row) as cur:
            candidate_rows = cur.execute(
                """
                SELECT status, count(*) AS count
                FROM state_event_candidates
                WHERE namespace = %s
                GROUP BY status
                ORDER BY status
                """,
                (normalized_namespace,),
            ).fetchall()
            event_row = cur.execute(
                "SELECT count(*) AS count FROM state_events WHERE namespace = %s",
                (normalized_namespace,),
            ).fetchone()
            fact_rows = cur.execute(
                """
                SELECT status, count(*) AS count
                FROM current_state_facts
                WHERE namespace = %s
                GROUP BY status
                ORDER BY status
                """,
                (normalized_namespace,),
            ).fetchall()
        return {
            "namespace": normalized_namespace,
            "candidates_by_status": {str(row["status"]): int(row["count"]) for row in candidate_rows},
            "events": int(event_row["count"] if event_row else 0),
            "facts_by_status": {str(row["status"]): int(row["count"]) for row in fact_rows},
        }

    def count_memories(self) -> int:
        row = self.conn.execute("SELECT count(*) FROM memories").fetchone()
        return _count_value(row)

    def list_memory_rows(
        self,
        limit: int = 1000,
        offset: int = 0,
        *,
        current_status: str | None = None,
        memory_tier: str | None = None,
        after: dict | None = None,
    ) -> list[dict]:
        after_external_id = str((after or {}).get("external_mem0_id") or "").strip()
        max_limit = 2500 if after_external_id else 10000
        safe_limit = min(max(int(limit or 1000), 1), max_limit)
        safe_offset = min(max(int(offset or 0), 0), 1_000_000)
        where: list[str] = []
        params: list = []
        if current_status:
            where.append("current_status = %s")
            params.append(str(current_status))
        if memory_tier:
            where.append("memory_tier = %s")
            params.append(str(memory_tier))
        if after_external_id:
            after_signal = (after or {}).get("signal_strength")
            after_updated = (after or {}).get("updated_at") or "-infinity"
            if after_signal is None:
                after_signal = float("-inf")
            where.append(
                """
                (
                    COALESCE(signal_strength, '-Infinity'::float8) < %s
                    OR (
                        COALESCE(signal_strength, '-Infinity'::float8) = %s
                        AND COALESCE(updated_at, '-infinity'::timestamptz) < %s
                    )
                    OR (
                        COALESCE(signal_strength, '-Infinity'::float8) = %s
                        AND COALESCE(updated_at, '-infinity'::timestamptz) = %s
                        AND external_mem0_id > %s
                    )
                )
                """
            )
            params.extend([after_signal, after_signal, after_updated, after_signal, after_updated, after_external_id])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        pagination_sql = "LIMIT %s" if after_external_id else "LIMIT %s OFFSET %s"
        params.append(safe_limit)
        if not after_external_id:
            params.append(safe_offset)
        with self.conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"""
                SELECT external_mem0_id, title, text, metadata, memory_type,
                       current_status, memory_tier, signal_strength,
                       created_at, updated_at, imported_at,
                       0.0::double precision AS rank
                FROM memories
                {where_sql}
                ORDER BY signal_strength DESC NULLS LAST, updated_at DESC NULLS LAST, external_mem0_id ASC
                {pagination_sql}
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]
