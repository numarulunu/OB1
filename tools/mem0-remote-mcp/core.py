from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from importlib import import_module
from time import perf_counter
from typing import Any, Mapping

from retrieval import (
    filter_memories,
    lexical_score,
    lexical_tokens,
    merge_ranked_memories,
    normalize_memory_tier,
    normalized_memory_text,
    rank_memories,
    route_query,
)
from kontext_shadow_compare import KontextShadowConfig, log_kontext_shadow_search_async
from kontext_write_audit import KontextWriteAuditConfig, log_kontext_write_audit_async
from retrieval_telemetry import RetrievalTelemetry


def _text_hash(value: Any, length: int = 12) -> str:
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()[:length]


def sanitize_audit_preview(value: Any) -> str:
    text = str(value or '')
    return f'preview chars={len(text)} hash={_text_hash(text)}'


def sanitize_proposal_for_audit(proposal: Any) -> dict[str, Any]:
    content = str(getattr(proposal, 'content', '') or '')
    reason = str(getattr(proposal, 'reason', '') or '')
    existing_id = str(getattr(proposal, 'existing_id', '') or '')
    result: dict[str, Any] = {
        'action': str(getattr(proposal, 'action', '') or ''),
        'confidence': getattr(proposal, 'confidence', None),
        'content_len': len(content),
        'content_hash': _text_hash(content),
        'reason_len': len(reason),
        'reason_hash': _text_hash(reason),
        'domains': getattr(proposal, 'domains', None),
        'memory_type': getattr(proposal, 'memory_type', None),
        'current_status': getattr(proposal, 'current_status', None),
        'memory_tier': getattr(proposal, 'memory_tier', None),
        'flag_type': getattr(proposal, 'flag_type', None),
    }
    if existing_id:
        result['existing_id_hash'] = _text_hash(existing_id)
    return result


@dataclass
class Mem0Config:
    base_url: str
    api_key: str
    user_id: str = 'ionut'
    agent_id: str = 'hosted-memory-mcp'
    client_name: str = 'hosted-mcp'
    item_url_base: str = 'https://mem0.ionutrosu.xyz/memory'
    lexical_database_url: str = ''
    retrieval_telemetry_log: str = ''
    kontext_shadow_enabled: bool = False
    kontext_shadow_mcp_url: str = ''
    kontext_shadow_mcp_token: str = ''
    kontext_shadow_log: str = ''
    kontext_shadow_timeout: float = 2.0
    kontext_write_audit_enabled: bool = False
    kontext_write_audit_mcp_url: str = ''
    kontext_write_audit_mcp_token: str = ''
    kontext_write_audit_log: str = ''
    kontext_write_audit_timeout: float = 2.0


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    token: str
    api_key: str
    can_write: bool
    agent_id: str
    client_name: str
    user_id: str

    @property
    def can_ingest(self) -> bool:
        return self.can_write


def build_profile_specs(values: Mapping[str, str]) -> list[ProfileSpec]:
    def get(name: str, default: str = '') -> str:
        return str(values.get(name, default) or '').strip()

    def first(*names: str, default: str = '') -> str:
        for name in names:
            value = get(name)
            if value:
                return value
        return default

    def flag(*names: str, default: bool = False) -> bool:
        value = first(*names, default='true' if default else 'false').lower()
        return value not in {'', '0', 'false', 'no', 'off'}

    mem0_api_key = get('MEM0_API_KEY')
    user_id = get('MEM0_USER_ID', 'ionut')
    writes_enabled = flag('MEM0_MCP_WRITES_ENABLED', default=True)
    specs: list[ProfileSpec] = []
    readonly_token = first('MCP_READONLY_TOKEN', 'MCP_CHATGPT_TOKEN', 'MCP_PATH_TOKEN')
    if readonly_token:
        specs.append(
            ProfileSpec(
                name='chatgpt',
                token=readonly_token,
                api_key=first('MEM0_API_KEY_CHATGPT', 'MEM0_API_KEY_READONLY', default=mem0_api_key),
                can_write=writes_enabled and flag('MCP_CHATGPT_CAN_WRITE', 'MCP_READONLY_CAN_WRITE', default=True),
                agent_id='chatgpt-live-memory',
                client_name='chatgpt',
                user_id=user_id,
            )
        )
    codex_token = get('MCP_CODEX_TOKEN')
    if codex_token:
        specs.append(
            ProfileSpec(
                name='codex',
                token=codex_token,
                api_key=first('MEM0_API_KEY_CODEX', default=mem0_api_key),
                can_write=writes_enabled,
                agent_id='codex-live-memory',
                client_name='codex',
                user_id=user_id,
            )
        )
    claude_token = get('MCP_CLAUDE_TOKEN')
    if claude_token:
        specs.append(
            ProfileSpec(
                name='claude',
                token=claude_token,
                api_key=first('MEM0_API_KEY_CLAUDE', default=mem0_api_key),
                can_write=writes_enabled,
                agent_id='claude-live-memory',
                client_name='claude',
                user_id=user_id,
            )
        )
    perplexity_token = get('MCP_PERPLEXITY_TOKEN')
    if perplexity_token:
        specs.append(
            ProfileSpec(
                name='perplexity',
                token=perplexity_token,
                api_key=first('MEM0_API_KEY_PERPLEXITY', default=mem0_api_key),
                can_write=writes_enabled and flag('MCP_PERPLEXITY_CAN_WRITE', default=False),
                agent_id='perplexity-live-memory',
                client_name='perplexity',
                user_id=user_id,
            )
        )
    if not specs:
        raise RuntimeError('At least one MCP token is required')
    tokens = [spec.token for spec in specs]
    if len(tokens) != len(set(tokens)):
        raise RuntimeError('MCP profile tokens must be unique')
    missing_api_key = [spec.name for spec in specs if not spec.api_key]
    if missing_api_key:
        raise RuntimeError(f'Mem0 API key is required for profiles: {", ".join(missing_api_key)}')
    return specs


class Mem0Client:
    def __init__(self, config: Mem0Config):
        self.config = config

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            self.config.base_url.rstrip('/') + path,
            data=data,
            method=method,
            headers={'X-API-Key': self.config.api_key, 'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = response.read().decode('utf-8')
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:500]
            raise RuntimeError(f'Mem0 HTTP {exc.code}: {detail}') from exc

    def search(
        self,
        query: str,
        top_k: int = 5,
        domains: list[str] | None = None,
        memory_tiers: list[str] | None = None,
        memory_types: list[str] | None = None,
        current_statuses: list[str] | None = None,
        record_telemetry: bool = True,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError('query is required')
        top_k = min(max(int(top_k or 5), 1), 20)
        route = route_query(query, requested_domains=domains)
        candidate_limit = min(max(top_k * max(route.candidate_multiplier, 1), 20), 100)
        filters_for_telemetry = {
            'domains': domains or [],
            'memory_tiers': memory_tiers or [],
            'memory_types': memory_types or [],
            'current_statuses': current_statuses or [],
            'route_mode': route.mode,
            'route_domains': route.domains,
        }
        started = perf_counter()
        ranked_results: list[dict[str, Any]] = []
        try:
            if not route.should_search:
                return {'query': query, 'results': ranked_results}
            data = self.request('POST', '/search', {'query': query, 'filters': {'user_id': self.config.user_id}, 'top_k': candidate_limit})
            rows = data.get('results') if isinstance(data, dict) else data
            results = [sanitize_memory(row) for row in (rows or []) if isinstance(row, dict)]
            results = merge_ranked_memories(self.lexical_search(query, limit=candidate_limit), results, candidate_limit)
            requested_domains = {str(domain).strip().lower() for domain in (domains or []) if str(domain).strip()}
            routed_domains = set(route.domains)
            ranking_domains = requested_domains or routed_domains
            requested_tiers = {normalize_memory_tier(tier) for tier in (memory_tiers or []) if str(tier).strip()}
            results = filter_memories(
                results,
                domains=requested_domains,
                memory_tiers=requested_tiers,
                memory_types=memory_types,
                current_statuses=current_statuses,
            )
            ranked_results = rank_memories(
                query,
                results,
                requested_domains=ranking_domains,
                requested_tiers=requested_tiers,
                top_k=top_k,
            )
            return {'query': query, 'results': ranked_results}
        finally:
            if record_telemetry:
                self.log_retrieval_search(
                    query=query,
                    filters=filters_for_telemetry,
                    results=ranked_results,
                    latency_ms=(perf_counter() - started) * 1000,
                    top_k=top_k,
                )

    def log_retrieval_search(
        self,
        *,
        query: str,
        filters: dict[str, Any],
        results: list[dict[str, Any]],
        latency_ms: float,
        top_k: int = 5,
    ) -> bool:
        if self.config.kontext_shadow_enabled:
            log_kontext_shadow_search_async(
                KontextShadowConfig(
                    enabled=self.config.kontext_shadow_enabled,
                    mcp_url=self.config.kontext_shadow_mcp_url,
                    token=self.config.kontext_shadow_mcp_token,
                    log_path=self.config.kontext_shadow_log,
                    timeout=self.config.kontext_shadow_timeout,
                ),
                origin=self.config.client_name,
                query=query,
                top_k=top_k,
                filters=filters,
                mem0_results=results,
                mem0_latency_ms=latency_ms,
            )
        if not self.config.retrieval_telemetry_log:
            return False
        return RetrievalTelemetry(self.config.retrieval_telemetry_log).log_search(
            origin=self.config.client_name,
            query=query,
            filters=filters,
            results=results,
            latency_ms=latency_ms,
        )

    def lexical_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        tokens = lexical_tokens(query)
        if len(tokens) < 2:
            return []
        if self.config.lexical_database_url:
            try:
                return self.postgres_lexical_search(query, tokens, limit)
            except Exception:
                pass
        data = self.request('POST', '/search', {'query': query, 'filters': {'user_id': self.config.user_id}, 'top_k': 1000})
        rows = data.get('results') or data.get('memories') if isinstance(data, dict) else data
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            memory = sanitize_memory(row)
            score = lexical_score(query, tokens, memory['text'])
            if score > 0:
                memory['score'] = memory.get('score') or score
                scored.append((score, memory))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [memory for _, memory in scored[:limit]]

    def postgres_lexical_search(self, query: str, tokens: list[str], limit: int = 10) -> list[dict[str, Any]]:
        psycopg = import_module('psycopg')
        rare_tokens = tokens[:8]
        where = ' or '.join(['payload::text ilike %s' for _ in rare_tokens])
        sql = f'''
            select id::text, payload
            from mem0_memories
            where payload->>'user_id' = %s and {where}
            order by payload->>'updated_at' desc nulls last
            limit %s
        '''
        params = [self.config.user_id, *[f'%{token}%' for token in rare_tokens], max(limit * 20, 100)]
        rows = []
        with psycopg.connect(self.config.lexical_database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
        scored: list[tuple[int, dict[str, Any]]] = []
        for memory_id, payload in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            row = dict(payload or {})
            row['id'] = memory_id
            memory = sanitize_memory(row)
            score = lexical_score(query, tokens, memory['text'])
            if score > 0:
                memory['score'] = memory.get('score') or score
                scored.append((score, memory))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [memory for _, memory in scored[:limit]]

    def postgres_get_all(self, limit: int = 10000) -> list[dict[str, Any]]:
        psycopg = import_module('psycopg')
        sql = '''
            select id::text, payload
            from mem0_memories
            where payload->>'user_id' = %s
            order by payload->>'updated_at' desc nulls last
            limit %s
        '''
        rows = []
        with psycopg.connect(self.config.lexical_database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, [self.config.user_id, max(int(limit or 10000), 1)])
                rows = cursor.fetchall()
        memories: list[dict[str, Any]] = []
        for memory_id, payload in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            row = dict(payload or {})
            row['id'] = memory_id
            memories.append(row)
        return memories

    def get_all(self, page_size: int = 100, max_pages: int = 100) -> dict[str, Any]:
        page_size = min(max(int(page_size or 100), 1), 1000)
        max_pages = min(max(int(max_pages or 100), 1), 1000)
        limit = page_size * max_pages
        if self.config.lexical_database_url:
            rows = self.postgres_get_all(limit=limit)
        else:
            query = urllib.parse.urlencode({'user_id': self.config.user_id})
            data = self.request('GET', f'/memories?{query}')
            rows = data.get('results') if isinstance(data, dict) else data
        results = [sanitize_memory(row, self.config.item_url_base) for row in (rows or []) if isinstance(row, dict)]
        return {'results': results, 'count': len(results)}

    def fetch(self, memory_id: str) -> dict[str, Any]:
        memory_id = memory_id.strip()
        if not memory_id:
            raise ValueError('id is required')
        return sanitize_memory(self.request('GET', f'/memories/{memory_id}'), self.config.item_url_base)

    def save(
        self,
        content: str,
        domains: list[str] | None = None,
        memory_type: str = 'note',
        signal_strength: int | float | None = None,
        current_status: str = 'active',
        memory_tier: str = 'active',
        metadata_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError('content is required')
        metadata = {
            'source': 'hosted-mcp',
            'client': self.config.client_name,
            'domains': [str(domain).strip() for domain in (domains or []) if str(domain).strip()],
            'memory_type': (memory_type or 'note').strip() or 'note',
            'signal_strength': signal_strength,
            'current_status': (current_status or 'active').strip() or 'active',
            'memory_tier': normalize_memory_tier(memory_tier),
        }
        if metadata_extra:
            metadata.update({key: value for key, value in metadata_extra.items() if value is not None})
        data = self.request(
            'POST',
            '/memories',
            {
                'messages': [{'role': 'user', 'content': content}],
                'user_id': self.config.user_id,
                'agent_id': self.config.agent_id,
                'metadata': metadata,
                'infer': False,
            },
        )
        result = {'saved': True, 'response': data}
        self.audit_kontext_write(
            'save',
            {
                'content': content,
                'domains': metadata['domains'],
                'memory_type': metadata['memory_type'],
                'signal_strength': signal_strength,
                'current_status': metadata['current_status'],
                'memory_tier': metadata['memory_tier'],
            },
            result,
        )
        return result


    def update(
        self,
        memory_id: str,
        content: str,
        reason: str,
        domains: list[str] | None = None,
        memory_type: str | None = None,
        signal_strength: int | float | None = None,
        current_status: str | None = None,
        memory_tier: str | None = None,
        metadata_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        memory_id = memory_id.strip()
        content = content.strip()
        reason = reason.strip()
        if not memory_id:
            raise ValueError('id is required')
        if not content:
            raise ValueError('content is required')
        if not reason:
            raise ValueError('reason is required')

        raw_before = self.request('GET', f'/memories/{memory_id}')
        metadata = dict((raw_before or {}).get('metadata') or {})
        if domains is not None:
            metadata['domains'] = [str(domain).strip() for domain in domains if str(domain).strip()]
        elif 'domains' not in metadata and (raw_before or {}).get('domains'):
            metadata['domains'] = (raw_before or {}).get('domains')
        if memory_type is not None:
            metadata['memory_type'] = (memory_type or 'note').strip() or 'note'
        elif 'memory_type' not in metadata and (raw_before or {}).get('memory_type'):
            metadata['memory_type'] = (raw_before or {}).get('memory_type')
        if signal_strength is not None:
            metadata['signal_strength'] = signal_strength
        elif 'signal_strength' not in metadata and (raw_before or {}).get('signal_strength') is not None:
            metadata['signal_strength'] = (raw_before or {}).get('signal_strength')
        if current_status is not None:
            metadata['current_status'] = (current_status or 'active').strip() or 'active'
        elif 'current_status' not in metadata and (raw_before or {}).get('current_status'):
            metadata['current_status'] = (raw_before or {}).get('current_status')
        if memory_tier is not None:
            metadata['memory_tier'] = normalize_memory_tier(memory_tier)
        elif 'memory_tier' not in metadata and (raw_before or {}).get('memory_tier'):
            metadata['memory_tier'] = normalize_memory_tier((raw_before or {}).get('memory_tier'))
        elif 'memory_tier' in metadata:
            metadata['memory_tier'] = normalize_memory_tier(metadata.get('memory_tier'))

        metadata['last_modified_by'] = self.config.client_name
        metadata['last_modified_via'] = 'hosted-mcp'
        metadata['last_modified_reason'] = reason
        if metadata_extra:
            metadata.update({key: value for key, value in metadata_extra.items() if value is not None})

        response = self.request('PUT', f'/memories/{memory_id}', {'text': content, 'metadata': metadata})
        raw_after = self.request('GET', f'/memories/{memory_id}')
        result = {
            'updated': True,
            'id': memory_id,
            'reason': reason,
            'before': sanitize_memory(raw_before, self.config.item_url_base),
            'after': sanitize_memory(raw_after, self.config.item_url_base),
            'response': response,
        }
        self.audit_kontext_write(
            'update',
            {
                'id': memory_id,
                'content': content,
                'reason': reason,
                'domains': metadata.get('domains') or [],
                'memory_type': metadata.get('memory_type'),
                'signal_strength': metadata.get('signal_strength'),
                'current_status': metadata.get('current_status'),
                'memory_tier': metadata.get('memory_tier'),
            },
            result,
        )
        return result

    def audit_kontext_write(self, tool: str, arguments: dict[str, Any], mem0_result: dict[str, Any]) -> bool:
        if not self.config.kontext_write_audit_enabled:
            return False
        return log_kontext_write_audit_async(
            KontextWriteAuditConfig(
                enabled=self.config.kontext_write_audit_enabled,
                mcp_url=self.config.kontext_write_audit_mcp_url,
                token=self.config.kontext_write_audit_mcp_token,
                log_path=self.config.kontext_write_audit_log,
                timeout=self.config.kontext_write_audit_timeout,
            ),
            origin=self.config.client_name,
            tool=tool,
            arguments=arguments,
            mem0_result=mem0_result,
        )

    def ingestion_metadata(self, proposal: Any, source_hash: str, origin: str) -> dict[str, Any]:
        return {
            'source': 'hosted-mcp-ingestion',
            'source_hash': source_hash,
            'ingestion_origin': origin,
            'ingestion_action': getattr(proposal, 'action', ''),
            'ingestion_confidence': getattr(proposal, 'confidence', None),
            'ingestion_reason': getattr(proposal, 'reason', ''),
        }

    def exact_duplicate_id(self, proposal: Any) -> str:
        content = normalized_memory_text(getattr(proposal, 'content', ''))
        if not content:
            return ''
        for row in self.search(getattr(proposal, 'content', ''), top_k=5, record_telemetry=False).get('results', []):
            if normalized_memory_text(row.get('text')) == content:
                return str(row.get('id') or '')
        return ''

    def best_existing_memory_id(self, proposal: Any) -> str:
        content = getattr(proposal, 'content', '')
        tokens = lexical_tokens(content)
        if len(tokens) < 2:
            return ''
        best_id = ''
        best_score = 0
        for row in self.search(content, top_k=5, record_telemetry=False).get('results', []):
            score = lexical_score(content, tokens, row.get('text') or '')
            if score > best_score:
                best_score = score
                best_id = str(row.get('id') or '')
        return best_id if best_score >= 4 else ''

    def apply_ingestion_proposal(
        self,
        proposal: Any,
        audit_ledger: Any,
        source_hash: str,
        preview: str,
        origin: str,
        llm_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = str(getattr(proposal, 'action', 'skip') or 'skip').strip().lower()
        reason = str(getattr(proposal, 'reason', '') or '')
        audit_base = {
            'source_hash': source_hash,
            'preview': sanitize_audit_preview(preview),
            'origin': origin,
            'action': action,
            'confidence': getattr(proposal, 'confidence', None),
            'reason_len': len(reason),
            'reason_hash': _text_hash(reason),
            'proposal': sanitize_proposal_for_audit(proposal),
        }
        safe_llm_metadata = sanitize_llm_metadata(llm_metadata)
        if safe_llm_metadata:
            audit_base['llm'] = safe_llm_metadata
        if action == 'skip':
            audit_ledger.append(audit_base)
            return {'action': 'skip', 'reason': getattr(proposal, 'reason', '')}
        if action == 'flag':
            flag_type = str(getattr(proposal, 'flag_type', '') or 'delete_candidate')
            audit_ledger.append({**audit_base, 'action': 'flag', 'flag_type': flag_type})
            return {'action': 'flag', 'flag_type': flag_type, 'reason': getattr(proposal, 'reason', '')}
        if action == 'update':
            existing_id = str(getattr(proposal, 'existing_id', '') or '').strip() or self.best_existing_memory_id(proposal)
            if existing_id:
                result = self.update(
                    existing_id,
                    getattr(proposal, 'content', ''),
                    reason=getattr(proposal, 'reason', '') or 'ingestion update',
                    domains=getattr(proposal, 'domains', None),
                    memory_type=getattr(proposal, 'memory_type', None),
                    signal_strength=getattr(proposal, 'signal_strength', None),
                    current_status=getattr(proposal, 'current_status', None),
                    memory_tier=getattr(proposal, 'memory_tier', None),
                    metadata_extra=self.ingestion_metadata(proposal, source_hash, origin),
                )
                audit_ledger.append({**audit_base, 'action': 'update', 'memory_id': existing_id})
                return {'action': 'update', 'id': existing_id, 'result': result}
            if getattr(proposal, 'confidence', 0) < 0.75:
                audit_ledger.append({**audit_base, 'action': 'flag', 'flag_type': 'conflict_candidate'})
                return {'action': 'flag', 'flag_type': 'conflict_candidate'}
            action = 'save'
        if action == 'save':
            existing_id = self.exact_duplicate_id(proposal)
            if existing_id:
                audit_ledger.append({**audit_base, 'action': 'skip', 'existing_id': existing_id, 'reason': 'exact duplicate'})
                return {'action': 'skip', 'existing_id': existing_id, 'reason': 'exact duplicate'}
            result = self.save(
                getattr(proposal, 'content', ''),
                domains=getattr(proposal, 'domains', None),
                memory_type=getattr(proposal, 'memory_type', 'note'),
                signal_strength=getattr(proposal, 'signal_strength', None),
                current_status=getattr(proposal, 'current_status', 'active'),
                memory_tier=getattr(proposal, 'memory_tier', 'active'),
                metadata_extra=self.ingestion_metadata(proposal, source_hash, origin),
            )
            response = result.get('response') if isinstance(result, dict) else {}
            memory_id = response.get('id') if isinstance(response, dict) else None
            audit_ledger.append({**audit_base, 'action': 'save', 'memory_id': memory_id})
            return {'action': 'save', 'id': memory_id, 'result': result}
        audit_ledger.append({**audit_base, 'action': 'skip', 'reason': 'unknown action'})
        return {'action': 'skip', 'reason': 'unknown action'}

    def delete(self, memory_id: str, reason: str) -> dict[str, Any]:
        memory_id = memory_id.strip()
        reason = reason.strip()
        if not memory_id:
            raise ValueError('id is required')
        if not reason:
            raise ValueError('reason is required')

        raw_before = self.request('GET', f'/memories/{memory_id}')
        response = self.request('DELETE', f'/memories/{memory_id}')
        return {
            'deleted': True,
            'id': memory_id,
            'reason': reason,
            'before': sanitize_memory(raw_before, self.config.item_url_base),
            'response': response,
        }


def sanitize_memory(row: dict[str, Any], item_url_base: str = 'https://mem0.ionutrosu.xyz/memory') -> dict[str, Any]:
    metadata = row.get('metadata') or {}
    metadata = {
        'domains': metadata.get('domains') or row.get('domains') or [],
        'memory_type': metadata.get('memory_type') or row.get('memory_type') or '',
        'signal_strength': metadata.get('signal_strength') if metadata.get('signal_strength') is not None else row.get('signal_strength'),
        'current_status': metadata.get('current_status') or row.get('current_status') or '',
        'memory_tier': normalize_memory_tier(metadata.get('memory_tier') or row.get('memory_tier')),
    }
    memory = row.get('memory') or row.get('text') or row.get('content') or row.get('data') or ''
    memory_id = row.get('id')
    return {
        'id': memory_id,
        'title': title_for(memory),
        'text': memory,
        'url': f"{item_url_base.rstrip('/')}/{memory_id}" if memory_id else item_url_base.rstrip('/'),
        'score': row.get('score') or row.get('similarity'),
        'metadata': {
            'domains': metadata['domains'],
            'memory_type': metadata['memory_type'],
            'signal_strength': metadata['signal_strength'],
            'current_status': metadata['current_status'],
            'memory_tier': metadata['memory_tier'],
        },
    }


def sanitize_llm_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    model = str(value.get('model') or '').strip()
    if model:
        result['model'] = model
    usage = value.get('usage')
    if isinstance(usage, dict):
        clean_usage: dict[str, int] = {}
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            try:
                number = int(usage.get(key) or 0)
            except (TypeError, ValueError):
                number = 0
            if number:
                clean_usage[key] = number
        if clean_usage:
            result['usage'] = clean_usage
    return result


def title_for(text: str, max_len: int = 90) -> str:
    collapsed = ' '.join(str(text or '').split())
    if len(collapsed) <= max_len:
        return collapsed
    cut = max_len - 3
    prefix = collapsed[:cut].rstrip()
    if cut < len(collapsed) and not collapsed[cut].isspace() and ' ' in prefix:
        prefix = prefix.rsplit(' ', 1)[0]
    return prefix + '...'
