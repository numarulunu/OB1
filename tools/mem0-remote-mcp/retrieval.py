from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


VALID_MEMORY_TIERS = {'active', 'historical', 'cold'}

STOPWORDS = {
    'about', 'after', 'again', 'also', 'and', 'are', 'ask', 'but', 'can', 'connector',
    'context', 'for', 'from', 'have', 'how', 'ionut', 'memory', 'mem0', 'my', 'of',
    'or', 'search', 'the', 'then', 'this', 'to', 'use', 'what', 'with', 'you', 'your',
}

KNOWN_ENTITY_TERMS = {
    'chatgpt', 'claude', 'codex', 'docker', 'gemini', 'kontext', 'luiza', 'mcp', 'mem0',
    'mother', 'ob1', 'open brain', 'openrouter', 'perplexity', 'pfa', 'pinecone',
    'preply', 'qwen', 'skool', 'supabase', 'vazquez', 'vocality', 'vps', 'whatsapp',
}

DOMAIN_HINTS = {
    'ai': {'ai', 'agent', 'agents', 'chatgpt', 'claude', 'codex', 'gemini', 'llm', 'mcp', 'mem0', 'model', 'openrouter', 'qwen'},
    'business': {'business', 'client', 'clients', 'income', 'money', 'preply', 'revenue', 'skool', 'student', 'students'},
    'family': {'family', 'father', 'mother', 'parent', 'parents'},
    'finance': {'finance', 'financial', 'income', 'investment', 'money', 'tax'},
    'infrastructure': {'backup', 'container', 'deploy', 'docker', 'mcp', 'server', 'supabase', 'vps'},
    'opera': {'audition', 'auditions', 'career', 'competition', 'opera', 'repertoire', 'role', 'roles'},
    'psychology': {'attachment', 'behavior', 'blindspot', 'identity', 'mother', 'pattern', 'psychology', 'shadow', 'trauma'},
    'relationships': {'attachment', 'family', 'luiza', 'mother', 'relationship', 'relationships', 'trust'},
    'systems': {'architecture', 'automation', 'mcp', 'mem0', 'system', 'systems', 'workflow', 'workflows'},
    'vocality': {'larynx', 'opera', 'preply', 'singing', 'skool', 'student', 'teacher', 'vocal', 'vocality', 'voice'},
    'workflow': {'automation', 'pipeline', 'process', 'system', 'workflow', 'workflows'},
}

SENSITIVE_QUERY_TERMS = {
    'attachment', 'family', 'formative', 'identity', 'mother', 'parent', 'psychology',
    'relationship', 'relationships', 'shadow', 'trauma', 'trust',
}

SENSITIVE_DOMAINS = {'family', 'identity', 'personal_life', 'psychology', 'relationships'}


TRIVIAL_QUERY_TEXT = {
    '', 'ok', 'okay', 'yes', 'no', 'thanks', 'thank you', 'cool', 'go', 'continue',
    'continue on', 'do it', 'proceed', 'run it', 'status', 'update', 'now', 'nice',
}

PROJECT_DOMAINS = {'ai', 'business', 'finance', 'infrastructure', 'opera', 'systems', 'vocality', 'workflow'}
DOMAIN_PRIORITY = (
    'relationships', 'psychology', 'family', 'identity', 'ai', 'systems', 'infrastructure',
    'workflow', 'business', 'finance', 'opera', 'vocality', 'personal_life',
)
HIGH_SIGNAL_MEMORY_TYPES = {
    'architecture_decision', 'career_context', 'decision', 'formative_event', 'identity_pattern',
    'project_state', 'relationship_pattern', 'workflow', 'workflow_state',
}
LOW_SIGNAL_MEMORY_TYPES = {'lesson_note', 'note', 'transcript_note'}
JUNK_MEMORY_TERMS = {
    'speaker diarization', 'speaker identification', 'canonical names', 'conversation cues',
    'raw json', 'stdout', 'stderr', 'traceback', 'stack trace',
}


@dataclass(frozen=True)
class QueryRoute:
    mode: str
    should_search: bool
    domains: list[str]
    candidate_multiplier: int
    reason: str



QUERY_EXPANSION_RULES = (
    (('stuck', 'blocked', 'cannot start', "can't start", 'overwhelmed', 'procrastinat'), 'blocked execution friction procrastination avoidance workflow next step decision clarity psychology'),
    (('partner', 'girlfriend', 'relationship conflict', 'couple', 'luiza'), 'attachment anxious escalation avoidant withdrawal relationship trust psychology formative pattern'),
    (('mother', 'father', 'parent', 'family'), 'family origin attachment identity trust formative psychology relationship pattern'),
    (('money', 'income', 'finance', 'funding', 'retirement'), 'business finance income funding opera career travel engine systems'),
    (('voice', 'singing', 'opera', 'role', 'audition', 'repertoire'), 'vocality voice fach role repertoire audition opera career'),
    (('server', 'vps', 'docker', 'mcp', 'container'), 'infrastructure deployment systems mem0 agent docker mcp'),
)


def expand_query(query: str) -> str:
    original = str(query or '').strip()
    if not original:
        return ''
    lower = original.lower()
    additions: list[str] = []
    seen: set[str] = set()
    for triggers, terms in QUERY_EXPANSION_RULES:
        if any(trigger in lower for trigger in triggers):
            for term in terms.split():
                normalized = term.strip().lower()
                if normalized and normalized not in seen and normalized not in lower:
                    seen.add(normalized)
                    additions.append(normalized)
    if not additions:
        return original
    return original + ' ' + ' '.join(additions)


def normalize_memory_tier(value: Any) -> str:
    tier = str(value or 'active').strip().lower()
    if tier in VALID_MEMORY_TIERS:
        return tier
    return 'active'


def normalized_memory_text(text: Any) -> str:
    return ' '.join(str(text or '').strip().lower().split())


def memory_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    metadata = memory.get('metadata') if isinstance(memory.get('metadata'), dict) else {}
    return dict(metadata)


def parse_memory_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or '').strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def memory_effective_timestamp(memory: dict[str, Any]) -> datetime | None:
    metadata = memory_metadata(memory)
    for key in ('effective_at', 'observed_at', 'updated_at', 'last_modified_at', 'created_at', 'imported_at'):
        parsed = parse_memory_timestamp(metadata.get(key)) or parse_memory_timestamp(memory.get(key))
        if parsed is not None:
            return parsed
    return None


def memory_recency_score(memory: dict[str, Any]) -> float:
    timestamp = memory_effective_timestamp(memory)
    if timestamp is None:
        return 0.0
    now = datetime.now(timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86400.0, 0.0)
    return 3.0 / (1.0 + (age_days / 90.0))


def is_superseded(memory: dict[str, Any]) -> bool:
    metadata = memory_metadata(memory)
    status = str(metadata.get('current_status') or memory.get('current_status') or '').strip().lower()
    return bool(
        metadata.get('superseded_by')
        or metadata.get('superseded_at')
        or memory.get('superseded_by')
        or memory.get('superseded_at')
        or status in {'superseded', 'outdated', 'false', 'deleted'}
    )


def lexical_tokens(text: str) -> list[str]:
    return [token for token in re.findall(r'[a-z0-9]+', str(text or '').lower()) if len(token) > 2 and token not in STOPWORDS]


def lexical_score(query: str, tokens: list[str], text: str) -> int:
    haystack = normalized_memory_text(text)
    if not haystack:
        return 0
    hits = sum(1 for token in tokens if token in haystack)
    if hits < min(2, len(tokens)):
        return 0
    query_norm = normalized_memory_text(query)
    phrase_bonus = 20 if query_norm and query_norm in haystack else 0
    coverage_bonus = 10 if hits == len(tokens) else 0
    return hits + phrase_bonus + coverage_bonus



def adjacent_token_score(tokens: list[str], text: str) -> int:
    haystack_tokens = lexical_tokens(text)
    if len(tokens) < 2 or len(haystack_tokens) < 2:
        return 0
    haystack = ' '.join(haystack_tokens)
    score = 0
    for size, weight in ((3, 5), (2, 2)):
        if len(tokens) < size:
            continue
        for index in range(0, len(tokens) - size + 1):
            phrase = ' '.join(tokens[index : index + size])
            if phrase in haystack:
                score += weight
    return score


def merge_ranked_memories(primary: list[dict[str, Any]], secondary: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*primary, *secondary]:
        key = str(row.get('id') or row.get('text') or '')
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(row)
        if len(merged) >= limit:
            break
    return merged


def normalize_values(values: Iterable[Any] | Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def memory_domains(memory: dict[str, Any]) -> set[str]:
    metadata = memory_metadata(memory)
    return normalize_values(metadata.get('domains'))


def memory_type(memory: dict[str, Any]) -> str:
    metadata = memory_metadata(memory)
    return str(metadata.get('memory_type') or '').strip().lower()


def current_status(memory: dict[str, Any]) -> str:
    metadata = memory_metadata(memory)
    return str(metadata.get('current_status') or memory.get('current_status') or '').strip().lower()


def memory_tier(memory: dict[str, Any]) -> str:
    metadata = memory_metadata(memory)
    return normalize_memory_tier(metadata.get('memory_tier') or memory.get('memory_tier'))


def signal_strength(memory: dict[str, Any]) -> float:
    metadata = memory_metadata(memory)
    try:
        value = float(metadata.get('signal_strength') if metadata.get('signal_strength') is not None else memory.get('signal_strength') or 0)
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(value, 10.0))


def vector_score(memory: dict[str, Any]) -> float:
    try:
        value = float(memory.get('score') or 0)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    return min(value, 1.0)


def query_domain_hints(query: str) -> set[str]:
    tokens = set(lexical_tokens(query))
    lower = str(query or '').lower()
    domains = set()
    for domain, hints in DOMAIN_HINTS.items():
        if tokens & hints or any(' ' in hint and hint in lower for hint in hints):
            domains.add(domain)
    return domains


def extract_entities(query: str) -> list[str]:
    query_text = str(query or '')
    lower = query_text.lower()
    entities = {
        match.lower()
        for match in re.findall(r'\b[A-Z][A-Za-z0-9._-]{2,}\b', query_text)
        if match.lower() not in STOPWORDS
    }
    for term in KNOWN_ENTITY_TERMS:
        if term in lower:
            entities.add(term)
    return sorted(entities, key=lambda value: (-len(value), value))



def ordered_domains(domains: Iterable[Any]) -> list[str]:
    normalized = normalize_values(domains)
    ordered = [domain for domain in DOMAIN_PRIORITY if domain in normalized]
    ordered.extend(sorted(normalized - set(ordered)))
    return ordered


def route_query(query: str, requested_domains: Iterable[Any] | None = None) -> QueryRoute:
    clean = normalized_memory_text(query)
    tokens = lexical_tokens(clean)
    requested_domain_set = normalize_values(requested_domains)
    inferred_domains = query_domain_hints(query) | requested_domain_set
    entities = extract_entities(query)

    if clean in TRIVIAL_QUERY_TEXT or (len(tokens) < 2 and not inferred_domains and not entities):
        return QueryRoute(mode='skip', should_search=False, domains=[], candidate_multiplier=0, reason='trivial_query')

    sensitive = bool((set(tokens) & SENSITIVE_QUERY_TERMS) or (inferred_domains & SENSITIVE_DOMAINS))
    if sensitive:
        protected_domains = inferred_domains | {'relationships', 'psychology', 'family'}
        return QueryRoute(
            mode='sensitive_deep',
            should_search=True,
            domains=ordered_domains(protected_domains),
            candidate_multiplier=10,
            reason='sensitive_or_identity_query',
        )

    if inferred_domains & PROJECT_DOMAINS:
        return QueryRoute(
            mode='project',
            should_search=True,
            domains=ordered_domains(inferred_domains),
            candidate_multiplier=7,
            reason='project_or_workflow_query',
        )

    if entities:
        return QueryRoute(
            mode='entity_exact',
            should_search=True,
            domains=ordered_domains(inferred_domains),
            candidate_multiplier=8,
            reason='named_entity_query',
        )

    return QueryRoute(
        mode='normal',
        should_search=True,
        domains=ordered_domains(inferred_domains),
        candidate_multiplier=5,
        reason='general_query',
    )


def filter_memories(
    memories: list[dict[str, Any]],
    domains: Iterable[Any] | None = None,
    memory_tiers: Iterable[Any] | None = None,
    memory_types: Iterable[Any] | None = None,
    current_statuses: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    requested_domains = normalize_values(domains)
    requested_tiers = {normalize_memory_tier(value) for value in (memory_tiers or []) if str(value).strip()}
    requested_types = normalize_values(memory_types)
    requested_statuses = normalize_values(current_statuses)
    results = []
    for memory in memories:
        if requested_domains and not (requested_domains & memory_domains(memory)):
            continue
        if requested_tiers and memory_tier(memory) not in requested_tiers:
            continue
        if requested_types and memory_type(memory) not in requested_types:
            continue
        if requested_statuses and current_status(memory) not in requested_statuses:
            continue
        results.append(memory)
    return results


def score_memory(
    query: str,
    memory: dict[str, Any],
    requested_domains: Iterable[Any] | None = None,
    requested_tiers: Iterable[Any] | None = None,
) -> float:
    text = str(memory.get('text') or memory.get('memory') or '')
    metadata_domains = memory_domains(memory)
    requested_domain_set = normalize_values(requested_domains)
    requested_tier_set = {normalize_memory_tier(value) for value in (requested_tiers or []) if str(value).strip()}
    expanded_query = expand_query(query)
    query_domains = query_domain_hints(expanded_query)
    tokens = lexical_tokens(expanded_query)
    score = 0.0

    score += vector_score(memory) * 8.0
    score += lexical_score(expanded_query, tokens, text) * 1.2
    score += adjacent_token_score(tokens, text) * 1.6

    type_name = memory_type(memory)
    haystack = normalized_memory_text(' '.join([text, ' '.join(metadata_domains), type_name]))
    for entity in extract_entities(expanded_query):
        if entity in haystack:
            score += 7.0

    domain_hits = query_domains & metadata_domains
    if requested_domain_set and requested_domain_set & metadata_domains:
        score += 8.0
    elif domain_hits:
        score += 5.0
    score += min(len(domain_hits), 3) * 2.5

    strength = signal_strength(memory)
    score += strength * 0.8
    if type_name in HIGH_SIGNAL_MEMORY_TYPES:
        score += 3.5
    elif type_name in LOW_SIGNAL_MEMORY_TYPES and strength < 4:
        score -= 2.5
    if any(term in haystack for term in JUNK_MEMORY_TERMS):
        score -= 8.0

    tier = memory_tier(memory)
    sensitive_query = bool(set(tokens) & SENSITIVE_QUERY_TERMS or metadata_domains & SENSITIVE_DOMAINS)
    if requested_tier_set and tier in requested_tier_set:
        pass
    elif tier == 'active':
        score += 2.5
    elif tier == 'historical':
        score += 3.0 if sensitive_query else -1.0
    elif tier == 'cold':
        score -= 8.0

    status = current_status(memory)
    superseded = is_superseded(memory)
    if status == 'active':
        score += 2.0
    elif status in {'dormant', 'paused', 'unknown'}:
        score -= 0.5
    elif status in {'resolved', 'inactive'}:
        score -= 2.0
    elif status in {'superseded', 'false', 'outdated', 'deleted'}:
        score -= 8.0

    if superseded:
        score -= 5.0 if requested_tier_set else 16.0
    elif tier == 'active' and status in {'', 'active'}:
        score += memory_recency_score(memory)
    elif tier == 'historical' and sensitive_query:
        score += memory_recency_score(memory) * 0.35

    return score


def rank_memories(
    query: str,
    memories: list[dict[str, Any]],
    requested_domains: Iterable[Any] | None = None,
    requested_tiers: Iterable[Any] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    limit = min(max(int(top_k or 5), 1), 20)
    scored = [
        (index, score_memory(query, memory, requested_domains=requested_domains, requested_tiers=requested_tiers), memory)
        for index, memory in enumerate(memories)
    ]
    scored.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    return [memory for _, _, memory in scored[:limit]]
