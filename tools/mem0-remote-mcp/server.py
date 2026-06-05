from __future__ import annotations

import json
import os
from dataclasses import dataclass

import anyio
from fastapi import FastAPI, HTTPException, Request
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import StreamableHTTPServerTransport
from starlette.responses import Response

from audit_ledger import AuditLedger
from core import Mem0Client, Mem0Config, ProfileSpec, build_profile_specs
from ingestion import (
    IngestionProposal,
    build_extraction_prompt,
    build_sanitized_preview,
    normalize_messages,
    parse_extraction_response,
    should_call_llm,
    source_hash,
)
from ingestion_llm import LLMConfig, call_llm_with_metadata
from hook_heartbeat import HookHeartbeatLog
from monitoring import build_status_payload
from project_observations import ProjectObservationStore
from project_retrieval import ProjectObservationRetriever
from server_status import health_payload


@dataclass(frozen=True)
class McpProfile:
    spec: ProfileSpec
    mcp: FastMCP


def env(name: str, default: str = '') -> str:
    return os.environ.get(name, default).strip()


MEM0_BASE_URL = env('MEM0_BASE_URL', 'http://127.0.0.1:18888')
MEM0_ITEM_URL_BASE = env('MEM0_ITEM_URL_BASE', 'https://mem0.ionutrosu.xyz/memory')
MEM0_LEXICAL_DATABASE_URL = env('MEM0_LEXICAL_DATABASE_URL')
MCP_HOST = env('MCP_HOST', '127.0.0.1')
MCP_PORT = int(env('MCP_PORT', '18890'))
INGESTION_ENABLED = env('INGESTION_ENABLED', 'true').lower() not in {'0', 'false', 'no', 'off'}
MEM0_MCP_RUNTIME_ENABLED = env('MEM0_MCP_RUNTIME_ENABLED', 'true').lower() not in {'0', 'false', 'no', 'off'}
INGESTION_LLM_BASE_URL = env('INGESTION_LLM_BASE_URL', 'https://openrouter.ai/api/v1')
INGESTION_LLM_MODEL = env('INGESTION_LLM_MODEL', 'qwen/qwen3.6-flash')
INGESTION_LLM_API_KEY = env('INGESTION_LLM_API_KEY') or env('OPENROUTER_API_KEY')
INGESTION_LLM_TIMEOUT = int(env('INGESTION_LLM_TIMEOUT', '20'))
INGESTION_AUDIT_LOG = env('INGESTION_AUDIT_LOG', '/data/mem0-mcp-audit.jsonl')
RETRIEVAL_TELEMETRY_ENABLED = env('RETRIEVAL_TELEMETRY_ENABLED', 'true').lower() not in {'0', 'false', 'no', 'off'}
RETRIEVAL_TELEMETRY_LOG = env('RETRIEVAL_TELEMETRY_LOG', '/data/mem0-mcp-retrieval.jsonl')
HOOK_HEARTBEAT_LOG = env('HOOK_HEARTBEAT_LOG', '/data/mem0-mcp-hooks.jsonl')
PROJECT_OBSERVATIONS_LOG = env('PROJECT_OBSERVATIONS_LOG', '/data/mem0-mcp-project-observations.jsonl')
PROJECT_RETRIEVAL_TELEMETRY_LOG = env('PROJECT_RETRIEVAL_TELEMETRY_LOG', '/data/mem0-mcp-project-retrieval.jsonl')
MAINTENANCE_LOG = env('MAINTENANCE_LOG', '/data/mem0-mcp-maintenance.jsonl')
MAINTENANCE_FLAG_THRESHOLD = int(env('MAINTENANCE_FLAG_THRESHOLD', '10') or '10')
KONTEXT_SHADOW_COMPARE_ENABLED = env('KONTEXT_SHADOW_COMPARE_ENABLED', 'false').lower() not in {'', '0', 'false', 'no', 'off'}
KONTEXT_SHADOW_MCP_URL = env('KONTEXT_SHADOW_MCP_URL', 'http://127.0.0.1:8200/api/v2/mcp/{token}')
KONTEXT_SHADOW_MCP_TOKEN = env('KONTEXT_SHADOW_MCP_TOKEN') or env('MCP_CODEX_TOKEN')
KONTEXT_SHADOW_COMPARE_LOG = env('KONTEXT_SHADOW_COMPARE_LOG', '/data/mem0-kontext-shadow-comparison.jsonl')
KONTEXT_SHADOW_COMPARE_TIMEOUT = float(env('KONTEXT_SHADOW_COMPARE_TIMEOUT', '2.0') or '2.0')
KONTEXT_WRITE_AUDIT_ENABLED = env('KONTEXT_WRITE_AUDIT_ENABLED', 'false').lower() not in {'', '0', 'false', 'no', 'off'}
KONTEXT_WRITE_AUDIT_MCP_URL = env('KONTEXT_WRITE_AUDIT_MCP_URL', KONTEXT_SHADOW_MCP_URL)
KONTEXT_WRITE_AUDIT_MCP_TOKEN = env('KONTEXT_WRITE_AUDIT_MCP_TOKEN') or KONTEXT_SHADOW_MCP_TOKEN
KONTEXT_WRITE_AUDIT_LOG = env('KONTEXT_WRITE_AUDIT_LOG', '/data/mem0-kontext-write-audit.jsonl')
KONTEXT_WRITE_AUDIT_TIMEOUT = float(env('KONTEXT_WRITE_AUDIT_TIMEOUT', '2.0') or '2.0')

app = FastAPI(title='Ionut Memory MCP', version='0.1.0')


def make_mcp_profile(spec: ProfileSpec) -> McpProfile:
    client = Mem0Client(
        Mem0Config(
            base_url=MEM0_BASE_URL,
            api_key=spec.api_key,
            user_id=spec.user_id,
            agent_id=spec.agent_id,
            client_name=spec.client_name,
            item_url_base=MEM0_ITEM_URL_BASE,
            lexical_database_url=MEM0_LEXICAL_DATABASE_URL,
            retrieval_telemetry_log=RETRIEVAL_TELEMETRY_LOG if RETRIEVAL_TELEMETRY_ENABLED else '',
            kontext_shadow_enabled=KONTEXT_SHADOW_COMPARE_ENABLED,
            kontext_shadow_mcp_url=KONTEXT_SHADOW_MCP_URL,
            kontext_shadow_mcp_token=KONTEXT_SHADOW_MCP_TOKEN,
            kontext_shadow_log=KONTEXT_SHADOW_COMPARE_LOG,
            kontext_shadow_timeout=KONTEXT_SHADOW_COMPARE_TIMEOUT,
            kontext_write_audit_enabled=KONTEXT_WRITE_AUDIT_ENABLED,
            kontext_write_audit_mcp_url=KONTEXT_WRITE_AUDIT_MCP_URL,
            kontext_write_audit_mcp_token=KONTEXT_WRITE_AUDIT_MCP_TOKEN,
            kontext_write_audit_log=KONTEXT_WRITE_AUDIT_LOG,
            kontext_write_audit_timeout=KONTEXT_WRITE_AUDIT_TIMEOUT,
        )
    )
    mcp = FastMCP(f'ionut-memory-mcp-{spec.name}')
    audit_ledger = AuditLedger(INGESTION_AUDIT_LOG)
    hook_ledger = HookHeartbeatLog(HOOK_HEARTBEAT_LOG)

    def extract_locally(messages, context_messages=None) -> dict:
        normalized = normalize_messages(messages)
        context = normalize_messages(context_messages)
        exchange_hash = source_hash(normalized)
        preview = build_sanitized_preview(normalized)
        if not INGESTION_ENABLED:
            return {
                'source_hash': exchange_hash,
                'preview': preview,
                'gate': {'keep': False, 'reason': 'ingestion_disabled', 'domains': []},
                'proposals': [],
                'errors': [],
            }
        if audit_ledger.has_source_hash(exchange_hash):
            return {
                'source_hash': exchange_hash,
                'preview': preview,
                'gate': {'keep': False, 'reason': 'already_processed', 'domains': []},
                'proposals': [],
                'errors': [],
            }
        gate = should_call_llm(normalized)
        gate_row = {'keep': gate.keep, 'reason': gate.reason, 'domains': gate.domains}
        if not gate.keep:
            return {'source_hash': exchange_hash, 'preview': preview, 'gate': gate_row, 'proposals': [], 'errors': []}
        if not INGESTION_LLM_API_KEY:
            return {
                'source_hash': exchange_hash,
                'preview': preview,
                'gate': gate_row,
                'proposals': [],
                'errors': ['ingestion LLM API key is missing'],
            }
        prompt = build_extraction_prompt(normalized, source=spec.name, context_messages=context)
        try:
            llm_result = call_llm_with_metadata(
                LLMConfig(
                    base_url=INGESTION_LLM_BASE_URL,
                    api_key=INGESTION_LLM_API_KEY,
                    model=INGESTION_LLM_MODEL,
                    timeout=INGESTION_LLM_TIMEOUT,
                ),
                prompt,
            )
            proposals = [proposal.to_dict() for proposal in parse_extraction_response(llm_result.content)]
        except Exception as exc:
            return {'source_hash': exchange_hash, 'preview': preview, 'gate': gate_row, 'proposals': [], 'errors': [str(exc)]}
        return {
            'source_hash': exchange_hash,
            'preview': preview,
            'gate': gate_row,
            'proposals': proposals,
            'errors': [],
            'llm': {'model': llm_result.model, 'usage': llm_result.usage},
        }

    @mcp.tool(description='Search Ionut\'s private curated second-brain memory. Use for questions about his projects, preferences, psychology, relationships, opera career, business, workflows, AI systems, settings, or long-term goals.')
    async def search(
        query: str,
        top_k: int = 5,
        memory_tiers: list[str] | None = None,
        domains: list[str] | None = None,
        memory_types: list[str] | None = None,
        current_statuses: list[str] | None = None,
    ) -> str:
        result = client.search(
            query,
            top_k=top_k,
            memory_tiers=memory_tiers,
            domains=domains,
            memory_types=memory_types,
            current_statuses=current_statuses,
        )
        if not spec.can_write:
            result = {
                'results': [
                    {'id': row['id'], 'title': row['title'], 'url': row['url']}
                    for row in result['results']
                ]
            }
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool(description='Fetch one full memory by ID returned from search.')
    async def fetch(id: str) -> str:
        return json.dumps(client.fetch(id), ensure_ascii=False)

    @mcp.tool(description='Safe monitoring summary for the hosted Mem0 ingestion layer. Returns aggregate counts only; no raw chat, secrets, profile tokens, or full memory contents.')
    async def ingestion_status(recent_limit: int = 5000) -> str:
        limit = min(max(int(recent_limit or 5000), 1), 20000)
        result = build_status_payload(
            INGESTION_AUDIT_LOG,
            ingestion_enabled=INGESTION_ENABLED,
            ingestion_model=INGESTION_LLM_MODEL,
            profiles=[profile.spec.name for profile in PROFILES_BY_TOKEN.values()] if 'PROFILES_BY_TOKEN' in globals() else [spec.name],
            recent_limit=limit,
            retrieval_log=RETRIEVAL_TELEMETRY_LOG if RETRIEVAL_TELEMETRY_ENABLED else None,
            hook_log=HOOK_HEARTBEAT_LOG,
            maintenance_flag_threshold=MAINTENANCE_FLAG_THRESHOLD,
            maintenance_log=MAINTENANCE_LOG,
        )
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool(description='Record a content-free heartbeat proving this client hook is active. Stores origin, hook type, timestamp, and optional hashes only; never raw prompts or memory text.')
    async def hook_heartbeat(hook_type: str, source: str = '', marker: str = '') -> str:
        row = hook_ledger.append(origin=spec.name, hook_type=hook_type, source=source, marker=marker)
        return json.dumps({'ok': True, 'origin': row['origin'], 'hook_type': row['hook_type'], 'ts': row['ts']}, ensure_ascii=False)

    @mcp.tool(description='Search compact project continuity observations. Returns index rows only: id, date, type, title, project, files count, and token estimate; no raw logs or file contents.')
    async def project_search(query: str, limit: int = 10) -> str:
        retriever = ProjectObservationRetriever(PROJECT_OBSERVATIONS_LOG, telemetry_log=PROJECT_RETRIEVAL_TELEMETRY_LOG)
        return json.dumps(retriever.search(query, limit=limit), ensure_ascii=False)

    @mcp.tool(description='Return compact chronological project context around an explicit observation id or query anchor.')
    async def project_timeline(anchor_id: str = '', query: str = '', before: int = 3, after: int = 3) -> str:
        retriever = ProjectObservationRetriever(PROJECT_OBSERVATIONS_LOG, telemetry_log=PROJECT_RETRIEVAL_TELEMETRY_LOG)
        return json.dumps(retriever.timeline(anchor_id=anchor_id, query=query, before=before, after=after), ensure_ascii=False)

    @mcp.tool(description='Fetch full project observation details by exact observation id. Use only after project_search or project_timeline returns an id.')
    async def project_fetch(id: str) -> str:
        retriever = ProjectObservationRetriever(PROJECT_OBSERVATIONS_LOG, telemetry_log=PROJECT_RETRIEVAL_TELEMETRY_LOG)
        return json.dumps(retriever.fetch(id), ensure_ascii=False)

    @mcp.tool(description='Return prior compact project observation titles for a file and recommend whether a full file read is still needed. This never hard-blocks file reads.')
    async def project_file_context(file_path: str, limit: int = 10) -> str:
        retriever = ProjectObservationRetriever(PROJECT_OBSERVATIONS_LOG, telemetry_log=PROJECT_RETRIEVAL_TELEMETRY_LOG)
        return json.dumps(retriever.file_context(file_path, limit=limit), ensure_ascii=False)

    if spec.can_write:
        @mcp.tool(description='Save one distilled durable memory. Use memory_tier active for current/default context, historical for formative or older context, and cold for rarely needed background. Do not save raw transcripts, secrets, temporary logs, code dumps, low-confidence guesses, or clutter. Prefer compact facts, decisions, preferences, project state, workflows, psychology/relationship patterns, and important life context.')
        async def save(
            content: str,
            domains: list[str] | None = None,
            memory_type: str = 'note',
            signal_strength: float | None = None,
            current_status: str = 'active',
            memory_tier: str = 'active',
        ) -> str:
            result = client.save(
                content=content,
                domains=domains,
                memory_type=memory_type,
                signal_strength=signal_strength,
                current_status=current_status,
                memory_tier=memory_tier,
            )
            return json.dumps(result, ensure_ascii=False)

        @mcp.tool(description='Update one existing memory by exact ID, including memory_tier when its retrieval priority should change. Search and fetch first, then use this when the user says a memory is outdated, wrong, incomplete, or should be tweaked. Requires a reason. Do not use for broad or ambiguous edits.')
        async def update(
            id: str,
            content: str,
            reason: str,
            domains: list[str] | None = None,
            memory_type: str | None = None,
            signal_strength: float | None = None,
            current_status: str | None = None,
            memory_tier: str | None = None,
        ) -> str:
            result = client.update(
                memory_id=id,
                content=content,
                reason=reason,
                domains=domains,
                memory_type=memory_type,
                signal_strength=signal_strength,
                current_status=current_status,
                memory_tier=memory_tier,
            )
            return json.dumps(result, ensure_ascii=False)

        @mcp.tool(description='Hard delete one memory by exact ID. Search and fetch first, then use only when the user asks to forget, remove, delete, or burn a specific outdated, false, duplicate, or low-signal memory. Requires a reason. No bulk deletes.')
        async def delete(id: str, reason: str) -> str:
            result = client.delete(memory_id=id, reason=reason)
            return json.dumps(result, ensure_ascii=False)

        @mcp.tool(description='Dry-run extraction for the hosted Mem0 ingestion layer. Returns proposed durable memories without writing to Mem0. Use for debugging and quality checks.')
        async def extract_memories(messages: list[dict[str, str]], context_messages: list[dict[str, str]] | None = None) -> str:
            result = await anyio.to_thread.run_sync(lambda: extract_locally(messages, context_messages))
            return json.dumps(result, ensure_ascii=False)

        @mcp.tool(description='Extract and apply durable memory updates after a substantive user/assistant exchange. Auto-saves and auto-updates; delete/merge/stale/conflict cases are flag-only for later maintenance.')
        async def ingest_exchange(messages: list[dict[str, str]], context_messages: list[dict[str, str]] | None = None) -> str:
            def run_ingestion() -> dict:
                extraction = extract_locally(messages, context_messages)
                counts = {'saved': 0, 'updated': 0, 'skipped': 0, 'flagged': 0, 'errors': list(extraction.get('errors') or [])}
                if extraction.get('gate', {}).get('reason') == 'already_processed':
                    counts['skipped'] += 1
                results = []
                for row in extraction.get('proposals') or []:
                    proposal = IngestionProposal(**row)
                    try:
                        applied = client.apply_ingestion_proposal(
                            proposal,
                            audit_ledger,
                            extraction['source_hash'],
                            extraction['preview'],
                            spec.name,
                            llm_metadata=extraction.get('llm'),
                        )
                        results.append(applied)
                        action = applied.get('action')
                        if action == 'save':
                            counts['saved'] += 1
                        elif action == 'update':
                            counts['updated'] += 1
                        elif action == 'flag':
                            counts['flagged'] += 1
                        else:
                            counts['skipped'] += 1
                    except Exception as exc:
                        counts['errors'].append(str(exc))
                return {
                    'source_hash': extraction.get('source_hash'),
                    'gate': extraction.get('gate'),
                    'counts': counts,
                    'results': results,
                }

            result = await anyio.to_thread.run_sync(run_ingestion)
            return json.dumps(result, ensure_ascii=False)

        @mcp.tool(description='Trusted-agent safety net for an important memory the automatic extractor may have missed. Goes through the same dedupe, audit, and no-live-delete ingestion path.')
        async def submit_memory_override(
            content: str,
            reason: str,
            domains: list[str] | None = None,
            memory_type: str = 'pattern',
            signal_strength: float = 8,
            current_status: str = 'active',
            memory_tier: str = 'active',
            confidence: float = 0.9,
        ) -> str:
            normalized = normalize_messages([{'role': 'user', 'content': content}])
            exchange_hash = source_hash(normalized)
            preview = build_sanitized_preview(normalized)
            proposal = IngestionProposal(
                action='save',
                content=content,
                domains=domains or [],
                memory_type=memory_type,
                signal_strength=int(round(signal_strength)),
                current_status=current_status,
                memory_tier=memory_tier,
                confidence=confidence,
                reason=reason,
            )
            result = await anyio.to_thread.run_sync(
                lambda: client.apply_ingestion_proposal(proposal, audit_ledger, exchange_hash, preview, spec.name)
            )
            return json.dumps(result, ensure_ascii=False)

        @mcp.tool(description='Flag one exact memory for later dream maintenance. Does not hard-delete. Use for stale, duplicate, false, merge, or conflicting memories.')
        async def flag_memory(id: str, flag_type: str, reason: str, confidence: float = 0.8) -> str:
            def run_flag() -> dict:
                memory = client.fetch(id)
                normalized = normalize_messages([{'role': 'user', 'content': f'{id} {flag_type} {reason}'}])
                exchange_hash = source_hash(normalized)
                return audit_ledger.append(
                    {
                        'source_hash': exchange_hash,
                        'preview': f'flag memory {id}',
                        'origin': spec.name,
                        'action': 'flag',
                        'flag_type': flag_type,
                        'confidence': confidence,
                        'reason': reason,
                        'memory_id': id,
                        'before': memory,
                    }
                )

            row = await anyio.to_thread.run_sync(run_flag)
            result = {'flagged': True, 'id': id, 'flag_type': flag_type, 'audit_ts': row['ts']}
            client.audit_kontext_write(
                'flag_memory',
                {'id': id, 'flag_type': flag_type, 'reason': reason, 'confidence': confidence},
                result,
            )
            return json.dumps(result, ensure_ascii=False)

    return McpProfile(spec=spec, mcp=mcp)


PROFILES_BY_TOKEN = {profile.spec.token: profile for profile in map(make_mcp_profile, build_profile_specs(os.environ))}


@app.get('/health')
async def health():
    return health_payload(
        profiles=[profile.spec.name for profile in PROFILES_BY_TOKEN.values()],
        ingestion_enabled=INGESTION_ENABLED,
        audit_log=INGESTION_AUDIT_LOG,
        profile_permissions={
            profile.spec.name: {'can_write': profile.spec.can_write}
            for profile in PROFILES_BY_TOKEN.values()
        },
        runtime_enabled=MEM0_MCP_RUNTIME_ENABLED,
    )


def require_runtime_enabled() -> None:
    if not MEM0_MCP_RUNTIME_ENABLED:
        raise HTTPException(status_code=410, detail='legacy_mem0_runtime_disabled')


@app.get('/maintenance-status/{token}')
async def handle_maintenance_status(token: str):
    profile = profile_for_token(token)
    require_runtime_enabled()
    result = build_status_payload(
        INGESTION_AUDIT_LOG,
        ingestion_enabled=INGESTION_ENABLED,
        ingestion_model=INGESTION_LLM_MODEL,
        profiles=[profile.spec.name],
        recent_limit=5000,
        maintenance_flag_threshold=MAINTENANCE_FLAG_THRESHOLD,
        maintenance_log=MAINTENANCE_LOG,
    )
    return {'ok': True, 'origin': profile.spec.name, 'maintenance': result.get('maintenance') or {}}


@app.post('/hook-heartbeat/{token}')
async def handle_hook_heartbeat(token: str, request: Request):
    profile = profile_for_token(token)
    require_runtime_enabled()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    row = HookHeartbeatLog(HOOK_HEARTBEAT_LOG).append(
        origin=profile.spec.name,
        hook_type=str(payload.get('hook_type') or 'unknown'),
        source=str(payload.get('source') or ''),
        marker=str(payload.get('marker') or ''),
    )
    return {'ok': True, 'origin': row['origin'], 'hook_type': row['hook_type'], 'ts': row['ts']}


@app.post('/project-observation/{token}')
async def handle_project_observation(token: str, request: Request):
    profile = profile_for_token(token)
    require_runtime_enabled()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload = dict(payload)
    payload['origin'] = profile.spec.name
    result = ProjectObservationStore(PROJECT_OBSERVATIONS_LOG).append(payload)
    return {'ok': True, 'origin': profile.spec.name, 'appended': result.get('appended'), 'id': result.get('id')}


def profile_for_token(token: str) -> McpProfile:
    profile = PROFILES_BY_TOKEN.get(token)
    if profile is None:
        raise HTTPException(status_code=404, detail='Not found')
    return profile


@app.api_route('/mcp/{token}', methods=['POST', 'GET', 'DELETE'])
async def handle_streamable_http(token: str, request: Request):
    profile = profile_for_token(token)
    require_runtime_enabled()

    response_started = False
    response_status = 200
    response_headers: list[tuple[bytes, bytes]] = []
    response_body = bytearray()

    async def capture_send(message):
        nonlocal response_started, response_status
        if message['type'] == 'http.response.start':
            response_started = True
            response_status = message['status']
            response_headers.extend(message.get('headers', []))
        elif message['type'] == 'http.response.body':
            response_body.extend(message.get('body', b''))

    transport = StreamableHTTPServerTransport(mcp_session_id=None, is_json_response_enabled=True)

    async with anyio.create_task_group() as task_group:
        async def run_server(*, task_status=anyio.TASK_STATUS_IGNORED):
            async with transport.connect() as (read_stream, write_stream):
                task_status.started()
                await profile.mcp._mcp_server.run(
                    read_stream,
                    write_stream,
                    profile.mcp._mcp_server.create_initialization_options(),
                    stateless=True,
                )

        await task_group.start(run_server)
        await transport.handle_request(request.scope, request.receive, capture_send)
        await transport.terminate()
        task_group.cancel_scope.cancel()

    if not response_started:
        return Response(status_code=500, content=b'Transport did not produce a response')

    return Response(
        content=bytes(response_body),
        status_code=response_status,
        headers={key.decode(): value.decode() for key, value in response_headers},
    )


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, access_log=False)
