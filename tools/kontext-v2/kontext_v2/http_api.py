from __future__ import annotations

import hmac
import os
import threading
from hashlib import sha256
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kontext_v2.dashboard_snapshot import build_dashboard_snapshot
from kontext_v2.mcp_server import (
    fetch_tool,
    ingest_exchange_dry_run,
    ingestion_status_tool,
    list_tools,
    normalize_search_args,
)
from kontext_v2.repository import KontextRepository
from kontext_v2.retrieval import search_memories
from kontext_v2.schema import apply_schema
from kontext_v2.mcp_bridge import build_mcp_profiles, profile_names, register_mcp_routes


_SCHEMA_LOCK = threading.Lock()


def _request_token(request: Request) -> str:
    authorization = str(request.headers.get("authorization") or "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return str(
        request.headers.get("x-kontext-token")
        or request.headers.get("x-mcp-token")
        or request.query_params.get("token")
        or ""
    ).strip()


def _require_configured_token(request: Request, profiles: dict[str, Any]) -> None:
    token = _request_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="token required")
    valid_tokens = [str(profile.token or "") for profile in profiles.values() if str(profile.token or "")]
    if not any(hmac.compare_digest(token, valid) for valid in valid_tokens):
        raise HTTPException(status_code=401, detail="invalid token")


def _result_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("external_mem0_id") or row.get("id"),
        "memory": row.get("text") or row.get("memory") or "",
        "title": row.get("title") or "",
        "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
    }


def _explained_result_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("external_mem0_id") or row.get("id"),
        "title": row.get("title") or "",
        "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
        "explanation": row.get("score_explanation") if isinstance(row.get("score_explanation"), dict) else {},
    }


def _preview_payload(payload: dict[str, Any]) -> dict[str, Any]:
    body = str(payload.get("memory") or payload.get("text") or payload.get("body") or "")
    return {
        "ok": True,
        "id": payload.get("id"),
        "title": payload.get("title") or "",
        "body": body,
        "body_hash": sha256(body.encode("utf-8")).hexdigest()[:8],
        "body_len": len(body),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }


class KontextV2Api:
    def __init__(
        self,
        database_url: str,
        write_enabled: bool = False,
        category_write_enabled: bool = False,
        dry_run_write_enabled: bool = False,
    ) -> None:
        self.database_url = database_url
        self.write_enabled = write_enabled
        self.category_write_enabled = category_write_enabled
        self.dry_run_write_enabled = dry_run_write_enabled
        self._schema_ready = False

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with _SCHEMA_LOCK:
            if self._schema_ready:
                return
            with psycopg.connect(self.database_url) as conn:
                apply_schema(conn)
            self._schema_ready = True

    @contextmanager
    def repo(self) -> Iterator[KontextRepository]:
        self.ensure_schema()
        with psycopg.connect(self.database_url) as conn:
            yield KontextRepository(conn)


def build_app(
    database_url: str,
    write_enabled: bool = False,
    mcp_env: dict[str, str] | None = None,
    category_write_enabled: bool = False,
    dry_run_write_enabled: bool = False,
) -> FastAPI:
    service = KontextV2Api(
        database_url=database_url,
        write_enabled=write_enabled,
        category_write_enabled=category_write_enabled,
        dry_run_write_enabled=dry_run_write_enabled,
    )
    mcp_profiles = build_mcp_profiles(
        os.environ if mcp_env is None else mcp_env,
        write_enabled=write_enabled,
        dry_run_write_enabled=dry_run_write_enabled,
    )
    app = FastAPI(title="Kontext V2 Mirror API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://claude.ai", "https://claude.com"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        try:
            with service.repo() as repo:
                status = ingestion_status_tool(repo, recent_limit=5)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=503,
                content={"ok": False, "service": "kontext-v2", "error": type(exc).__name__},
            )
        status["mcp"] = {"profiles": profile_names(mcp_profiles)}
        status.setdefault("writes", {})["dry_run_tools_enabled"] = service.dry_run_write_enabled
        return JSONResponse(content=status)

    @app.get("/tools")
    def tools() -> dict[str, Any]:
        return {
            "tools": list_tools(
                write_enabled=service.write_enabled,
                dry_run_write_enabled=service.dry_run_write_enabled,
                category_write_enabled=service.category_write_enabled,
            )
        }

    @app.post("/search")
    def search(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            args = normalize_search_args(payload)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with service.repo() as repo:
            rows = search_memories(
                repo,
                query=args.query,
                top_k=args.top_k,
                domains=args.domains,
                memory_types=args.memory_types,
                memory_tiers=args.memory_tiers,
                current_statuses=args.current_statuses,
                namespace=args.namespace or None,
            )
        return {"results": [_result_row(row) for row in rows], "count": len(rows)}

    @app.post("/search/explain")
    def search_explain(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        _require_configured_token(request, mcp_profiles)
        try:
            args = normalize_search_args(payload)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with service.repo() as repo:
            rows = search_memories(
                repo,
                query=args.query,
                top_k=args.top_k,
                domains=args.domains,
                memory_types=args.memory_types,
                memory_tiers=args.memory_tiers,
                current_statuses=args.current_statuses,
                include_explanations=True,
                namespace=args.namespace or None,
            )
        return {"results": [_explained_result_row(row) for row in rows], "count": len(rows)}


    def _require_write_enabled() -> None:
        if not service.write_enabled:
            raise HTTPException(status_code=403, detail="writes disabled")

    def _require_category_write_enabled() -> None:
        if not (service.write_enabled or service.category_write_enabled):
            raise HTTPException(status_code=403, detail="category writes disabled")

    @app.get("/categories")
    def list_categories() -> dict[str, Any]:
        with service.repo() as repo:
            categories = repo.list_categories()
        return {
            "ok": True,
            "categories": categories,
            "count": len(categories),
            "category_write_enabled": bool(service.write_enabled or service.category_write_enabled),
        }

    @app.post("/categories")
    def upsert_category(payload: dict[str, Any]) -> dict[str, Any]:
        _require_category_write_enabled()
        slug = str(payload.get("slug") or payload.get("name") or "").strip()
        name = str(payload.get("name") or slug).strip()
        if not slug or not name:
            raise HTTPException(status_code=400, detail="slug or name is required")
        with service.repo() as repo:
            category = repo.upsert_category(
                slug=slug,
                name=name,
                description=str(payload.get("description") or ""),
            )
        return {"ok": True, "category": category}

    @app.delete("/categories/{slug}")
    def delete_category(slug: str) -> dict[str, Any]:
        _require_category_write_enabled()
        with service.repo() as repo:
            deleted = repo.delete_category(slug)
        if not deleted:
            raise HTTPException(status_code=404, detail="category not found")
        return {"ok": True, "deleted": True, "slug": slug}

    @app.get("/categories/{slug}/memories")
    def list_category_memories(slug: str, limit: int = 100) -> dict[str, Any]:
        with service.repo() as repo:
            memories = repo.list_category_memories(slug, limit=limit)
        return {"ok": True, "slug": slug, "memories": memories, "count": len(memories)}

    @app.post("/categories/{slug}/assign")
    def assign_category(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        _require_category_write_enabled()
        memory_id = str(payload.get("memory_id") or payload.get("external_mem0_id") or payload.get("id") or "").strip()
        if not memory_id:
            raise HTTPException(status_code=400, detail="memory_id is required")
        with service.repo() as repo:
            assigned = repo.assign_memory_category(
                external_mem0_id=memory_id,
                category_slug=slug,
                source=str(payload.get("source") or "manual_http"),
            )
        if not assigned:
            raise HTTPException(status_code=404, detail="memory or category not found")
        return {"ok": True, "assigned": True, "slug": slug, "memory_id": memory_id}

    @app.post("/categories/{slug}/unassign")
    def unassign_category(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        _require_category_write_enabled()
        memory_id = str(payload.get("memory_id") or payload.get("external_mem0_id") or payload.get("id") or "").strip()
        if not memory_id:
            raise HTTPException(status_code=400, detail="memory_id is required")
        with service.repo() as repo:
            removed = repo.unassign_memory_category(external_mem0_id=memory_id, category_slug=slug)
        return {"ok": True, "removed": removed, "slug": slug, "memory_id": memory_id}


    @app.get("/dry-run-writes")
    def dry_run_writes(request: Request, limit: int = 30) -> dict[str, Any]:
        _require_configured_token(request, mcp_profiles)
        with service.repo() as repo:
            payload = repo.list_dry_run_write_audit(limit=limit)
        return {
            "ok": True,
            "write_enabled": service.write_enabled,
            "dry_run_tools_enabled": service.dry_run_write_enabled,
            **payload,
        }

    @app.get("/dashboard/snapshot")
    def dashboard_snapshot(request: Request) -> dict[str, Any]:
        _require_configured_token(request, mcp_profiles)
        with service.repo() as repo:
            payload = build_dashboard_snapshot(repo)
        return {"ok": True, **payload}

    @app.get("/fetch/{memory_id}")
    def fetch(memory_id: str, request: Request) -> dict[str, Any]:
        _require_configured_token(request, mcp_profiles)
        with service.repo() as repo:
            payload = fetch_tool(repo, memory_id)
        if not payload.get("ok", True):
            raise HTTPException(status_code=404, detail="not_found")
        return payload

    @app.get("/memory/{memory_id}/preview")
    def memory_preview(memory_id: str, request: Request) -> dict[str, Any]:
        _require_configured_token(request, mcp_profiles)
        with service.repo() as repo:
            payload = fetch_tool(repo, memory_id)
        if not payload.get("ok", True):
            raise HTTPException(status_code=404, detail="not_found")
        return _preview_payload(payload)


    @app.get("/sync/status")
    def sync_status() -> dict[str, Any]:
        with service.repo() as repo:
            latest = repo.latest_mirror_sync_run()
        checked_at = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "checked_at": checked_at, "sync": {"latest": latest or {"status": "unknown", "checked_at": checked_at}}}
    @app.post("/ingest_exchange")
    def ingest_exchange(payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="messages must be a list")
        origin = str(payload.get("origin") or "kontext-v2-http")
        return ingest_exchange_dry_run(messages=messages, origin=origin)

    @app.post("/project-observation/{token}")
    def project_observation(token: str, payload: dict[str, Any]) -> dict[str, Any]:
        profile = mcp_profiles.get(token)
        if profile is None:
            raise HTTPException(status_code=404, detail="Not found")
        if not isinstance(payload, dict):
            payload = {}
        body = dict(payload)
        body["origin"] = profile.name
        with service.repo() as repo:
            result = repo.record_project_observation(body)
        return {"ok": True, "origin": profile.name, "appended": bool(result.get("appended")), "id": result.get("id")}

    @app.post("/hook-heartbeat/{token}")
    def hook_heartbeat(token: str, payload: dict[str, Any]) -> dict[str, Any]:
        profile = mcp_profiles.get(token)
        if profile is None:
            raise HTTPException(status_code=404, detail="Not found")
        if not isinstance(payload, dict):
            payload = {}
        body = dict(payload)
        body["origin"] = profile.name
        with service.repo() as repo:
            result = repo.record_hook_heartbeat(body)
        return {"ok": True, "origin": profile.name, "hook_type": result.get("hook_type")}

    @app.get("/maintenance-status/{token}")
    def maintenance_status(token: str) -> dict[str, Any]:
        profile = mcp_profiles.get(token)
        if profile is None:
            raise HTTPException(status_code=404, detail="Not found")
        with service.repo() as repo:
            maintenance = repo.maintenance_status()
        return {"ok": True, "origin": profile.name, "maintenance": maintenance}

    register_mcp_routes(app, service, mcp_profiles)

    return app
