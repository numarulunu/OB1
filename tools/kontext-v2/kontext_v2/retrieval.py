from __future__ import annotations

import re
from typing import Any, Iterable

from kontext_v2.repository import KontextRepository

VALID_MEMORY_TIERS = {"active", "historical", "cold"}
STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "ask", "but", "can", "connector",
    "context", "for", "from", "have", "how", "ionut", "memory", "mem0", "my", "of",
    "or", "search", "the", "then", "this", "to", "use", "what", "with", "you", "your",
}
DOMAIN_HINTS = {
    "ai": {"ai", "agent", "agents", "chatgpt", "claude", "codex", "llm", "mcp", "mem0", "model", "qwen"},
    "business": {"business", "client", "clients", "income", "money", "preply", "revenue", "skool", "student", "students"},
    "family": {"childhood", "family", "father", "mother", "parent", "parents"},
    "finance": {"finance", "financial", "funding", "income", "investment", "money", "pfa", "retirement", "tax"},
    "infrastructure": {"backup", "container", "deploy", "docker", "mcp", "server", "supabase", "vps"},
    "opera": {"audition", "auditions", "career", "competition", "opera", "repertoire", "role", "roles"},
    "psychology": {"attachment", "behavior", "blindspot", "identity", "mother", "pattern", "psychology", "shadow", "trauma"},
    "relationships": {"attachment", "family", "luiza", "mother", "partner", "relationship", "relationships", "trust"},
    "systems": {"architecture", "automation", "mcp", "mem0", "system", "systems", "workflow", "workflows"},
    "vocality": {"larynx", "opera", "preply", "singing", "skool", "student", "teacher", "vocal", "vocality", "voice"},
    "workflow": {"automation", "execution", "pipeline", "process", "system", "workflow", "workflows"},
}
DOMAIN_ALIASES = {
    "family_origin": {"family", "psychology"},
    "mental_health": {"psychology"},
    "memory_systems": {"ai", "systems"},
    "communication": {"workflow"},
}
HIGH_SIGNAL_MEMORY_TYPES = {
    "architecture_decision", "career_context", "clinical_context", "decision", "execution_pattern",
    "finance_context", "formative_event", "goal", "identity_pattern", "project_state",
    "psychology_pattern", "relationship_pattern", "voice_profile", "workflow", "workflow_state",
}
LOW_SIGNAL_MEMORY_TYPES = {"lesson_note", "note", "transcript_note"}
SENSITIVE_QUERY_TERMS = {
    "attachment", "childhood", "family", "formative", "identity", "mother", "parent",
    "psychology", "relationship", "relationships", "shadow", "trauma", "trust",
}
SENSITIVE_DOMAINS = {"family", "identity", "personal_life", "psychology", "relationships"}
PROJECT_DOMAINS = {"ai", "business", "finance", "infrastructure", "opera", "systems", "vocality", "workflow"}
QUERY_EXPANSION_RULES = (
    (("stuck", "blocked", "cannot start", "can't start", "overwhelmed", "procrastinat"), "blocked execution friction procrastination avoidance workflow next step decision clarity psychology"),
    (("partner", "girlfriend", "relationship conflict", "couple", "luiza"), "attachment anxious escalation avoidant withdrawal relationship trust psychology formative pattern"),
    (("mother", "father", "parent", "family"), "family origin attachment identity trust formative psychology relationship pattern childhood"),
    (("money", "income", "finance", "funding", "retirement", "pfa", "tax"), "business finance income funding opera career travel engine systems decision"),
    (("voice", "singing", "opera", "role", "audition", "repertoire"), "vocality voice fach role repertoire audition opera career context"),
    (("server", "vps", "docker", "mcp", "container"), "infrastructure deployment systems mem0 agent docker mcp"),
)

QUESTION_CAPITALIZED_STOPWORDS = {
    "after", "and", "are", "before", "can", "did", "do", "does", "for", "from",
    "has", "have", "how", "is", "it", "may", "might", "shall", "should", "than",
    "that", "the", "then", "there", "this", "to", "was", "were", "what", "when",
    "where", "which", "who", "why", "will", "with", "would",
}
DATE_HINT_RE = re.compile(
    r"\b(?:\d{4}|\d{1,2}[/-]\d{1,2}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
PROPER_NOUN_QUERY_RE = re.compile(r"\b(?:[A-Z][A-Za-z0-9&'\-]{2,}|[A-Z]{2,})\b")
TEMPORAL_QUERY_RE = re.compile(
    r"\b(?:when|what time|what day|which day|date|month|year|time|today|tomorrow|yesterday|before|after|ago|week|weeks|month|months|year|years|morning|afternoon|evening|night|tonight|next|last)\b",
    re.IGNORECASE,
)


def query_proper_noun_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for match in PROPER_NOUN_QUERY_RE.findall(str(query or "")):
        token = str(match).strip().lower()
        if not token or token in STOPWORDS or token in QUESTION_CAPITALIZED_STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def query_temporal_intent(query: str) -> bool:
    lower = str(query or "").lower()
    return bool(TEMPORAL_QUERY_RE.search(lower) or DATE_HINT_RE.search(lower))


def has_date_hint(text: Any) -> bool:
    return bool(DATE_HINT_RE.search(str(text or "")))


def normalized_text(text: Any) -> str:
    return " ".join(str(text or "").strip().lower().split())


def lexical_tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(token) > 2 and token not in STOPWORDS]


def normalize_values(values: Iterable[Any] | Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def normalize_domain_values(values: Iterable[Any] | Any) -> set[str]:
    domains = normalize_values(values)
    expanded = set(domains)
    for domain in domains:
        expanded.update(DOMAIN_ALIASES.get(domain, set()))
    return expanded


def normalize_memory_tier(value: Any) -> str:
    tier = str(value or "active").strip().lower()
    return tier if tier in VALID_MEMORY_TIERS else "active"


def query_domain_hints(query: str) -> set[str]:
    tokens = set(lexical_tokens(query))
    lower = str(query or "").lower()
    domains: set[str] = set()
    for domain, hints in DOMAIN_HINTS.items():
        if tokens & hints or any(" " in hint and hint in lower for hint in hints):
            domains.add(domain)
    return domains


def expand_query(query: str) -> str:
    original = str(query or "").strip()
    if not original:
        return ""
    lower = original.lower()
    additions: list[str] = []
    seen: set[str] = set()
    for triggers, terms in QUERY_EXPANSION_RULES:
        if any(trigger in lower for trigger in triggers):
            for term in terms.split():
                if term not in seen and term not in lower:
                    seen.add(term)
                    additions.append(term)
    return original + (" " + " ".join(additions) if additions else "")


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return dict(metadata)


def row_domains(row: dict[str, Any]) -> set[str]:
    metadata = row_metadata(row)
    return normalize_domain_values(metadata.get("domains") or row.get("domains"))


def row_status(row: dict[str, Any]) -> str:
    metadata = row_metadata(row)
    return str(metadata.get("current_status") or row.get("current_status") or "active").strip().lower()


def row_tier(row: dict[str, Any]) -> str:
    metadata = row_metadata(row)
    return normalize_memory_tier(metadata.get("memory_tier") or row.get("memory_tier"))


def raw_memory_type(row: dict[str, Any]) -> str:
    metadata = row_metadata(row)
    return str(metadata.get("memory_type") or row.get("memory_type") or "").strip().lower()


def row_signal(row: dict[str, Any]) -> float:
    metadata = row_metadata(row)
    try:
        value = float(metadata.get("signal_strength") if metadata.get("signal_strength") is not None else row.get("signal_strength") or 0)
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(value, 10.0))


def normalized_memory_type(row: dict[str, Any]) -> str:
    current = raw_memory_type(row)
    domains = row_domains(row)
    text = normalized_text(" ".join([str(row.get("title") or ""), str(row.get("text") or row.get("memory") or "")]))
    if current == "decision" and domains & {"vocality"} and domains & {"business", "workflow"}:
        return "business_context"
    if current == "workflow" and domains & {"ai", "systems", "infrastructure"}:
        return "project_state"
    if current == "event" and domains & {"ai", "systems", "infrastructure"}:
        return "project_state"
    if current in HIGH_SIGNAL_MEMORY_TYPES:
        return current
    if current == "person" and domains & {"relationships", "family", "psychology"}:
        return "relationship_pattern"
    if current in {"identity_shaping", "event", "pattern", "shadow_motive", "person", ""}:
        if "luiza" in text or "relationship" in text or "partner" in text or "attachment" in text:
            return "relationship_pattern"
        if domains & {"family", "psychology"} and any(term in text for term in ("mother", "father", "parent", "childhood", "origin", "formative", "hypervigilance")):
            return "formative_event"
        if current == "shadow_motive" or (domains & {"psychology"} and any(term in text for term in ("shadow", "avoidance", "blocked", "stuck", "procrastination", "execution"))):
            return "psychology_pattern"
        if domains & {"workflow"} or any(term in text for term in ("workflow", "pipeline", "execution", "process")):
            return "workflow"
        if domains & {"finance"}:
            return "finance_context"
        if domains & {"opera", "vocality"}:
            return "career_context"
        if domains & {"ai", "systems", "infrastructure"}:
            return "project_state"
    return current


def normalized_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    metadata = row_metadata(row)
    domains = sorted(row_domains(row))
    memory_type = normalized_memory_type(row)
    metadata["domains"] = domains
    metadata["memory_type"] = memory_type
    metadata["signal_strength"] = row_signal(row)
    metadata["current_status"] = row_status(row)
    metadata["memory_tier"] = row_tier(row)
    result["metadata"] = metadata
    result["memory_type"] = memory_type
    result["current_status"] = row_status(row)
    result["memory_tier"] = row_tier(row)
    result["signal_strength"] = row_signal(row)
    return result


def lexical_score(query: str, tokens: list[str], text: str) -> float:
    haystack = normalized_text(text)
    if not haystack:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    if hits < min(2, len(tokens)):
        return 0.0
    phrase_bonus = 10.0 if normalized_text(query) and normalized_text(query) in haystack else 0.0
    coverage_bonus = 5.0 if hits == len(tokens) else 0.0
    return hits + phrase_bonus + coverage_bonus


def adjacent_token_score(tokens: list[str], text: str) -> float:
    haystack_tokens = lexical_tokens(text)
    if len(tokens) < 2 or len(haystack_tokens) < 2:
        return 0.0
    haystack = " ".join(haystack_tokens)
    score = 0.0
    for size, weight in ((3, 4.0), (2, 1.5)):
        if len(tokens) < size:
            continue
        for index in range(0, len(tokens) - size + 1):
            if " ".join(tokens[index : index + size]) in haystack:
                score += weight
    return score


def score_row(query: str, row: dict[str, Any], requested_domains: set[str], requested_tiers: set[str]) -> float:
    expanded = expand_query(query)
    tokens = lexical_tokens(expanded)
    own_text = " ".join([str(row.get("title") or ""), str(row.get("text") or row.get("memory") or "")])
    context_text = normalized_text(row.get("context_text") or "")
    combined_text = normalized_text(" ".join(part for part in [own_text, context_text] if part))
    domains = row_domains(row)
    query_domains = query_domain_hints(expanded)
    type_name = normalized_memory_type(row)
    temporal_query = query_temporal_intent(query)
    lexical_own_weight = 0.85 if temporal_query else 1.0
    lexical_context_weight = 0.25 if temporal_query else 0.35
    adjacent_own_weight = 1.0 if temporal_query else 1.2
    adjacent_context_weight = 0.25 if temporal_query else 0.35
    proper_own_weight = 2.0 if temporal_query else 4.0
    proper_context_weight = 0.75 if temporal_query else 1.5
    date_own_bonus = 10.0 if temporal_query else 4.5
    date_context_bonus = 2.0 if temporal_query else 1.5
    score = 0.0
    try:
        score += float(row.get("rank") or 0.0) * 10.0
    except (TypeError, ValueError):
        pass
    score += lexical_score(expanded, tokens, own_text) * lexical_own_weight
    if context_text:
        score += lexical_score(expanded, tokens, context_text) * lexical_context_weight
    score += adjacent_token_score(tokens, own_text) * adjacent_own_weight
    if context_text:
        score += adjacent_token_score(tokens, context_text) * adjacent_context_weight
    domain_hits = query_domains & domains
    if requested_domains and requested_domains & domains:
        score += 12.0
    elif domain_hits:
        score += 8.0
    score += min(len(domain_hits), 3) * 4.0
    if type_name in HIGH_SIGNAL_MEMORY_TYPES:
        score += 5.0
    if type_name in LOW_SIGNAL_MEMORY_TYPES:
        score -= 5.0
    if type_name in {"relationship_pattern", "formative_event", "clinical_context"} and set(tokens) & SENSITIVE_QUERY_TERMS:
        score += 8.0
    if type_name in {"finance_context", "decision", "goal"} and query_domains & {"finance", "business"}:
        score += 8.0
    if type_name in {"career_context", "voice_profile"} and query_domains & {"opera", "vocality"}:
        score += 8.0
    if type_name in {"execution_pattern", "psychology_pattern", "workflow"} and query_domains & {"psychology", "workflow"}:
        score += 8.0
    if row_metadata(row).get("observation_kind") == "session":
        score += 4.0
    for token in ("luiza", "mother", "pfa", "vocality", "opera"):
        if token in normalized_text(expanded) and token in combined_text:
            score += 6.0
    proper_noun_tokens = query_proper_noun_tokens(query)
    if proper_noun_tokens:
        own_proper_hits = sum(1 for token in proper_noun_tokens if token in normalized_text(own_text))
        context_proper_hits = sum(1 for token in proper_noun_tokens if token in context_text)
        score += own_proper_hits * proper_own_weight
        score += context_proper_hits * proper_context_weight
    if temporal_query:
        own_date_hits = 1 if has_date_hint(own_text) else 0
        context_date_hits = 1 if has_date_hint(context_text) else 0
        if own_date_hits:
            score += date_own_bonus
        else:
            score -= 5.0
        score += context_date_hits * date_context_bonus
    signal = row_signal(row)
    score += signal * 1.2
    if type_name in LOW_SIGNAL_MEMORY_TYPES and signal < 4:
        score -= 8.0
    tier = row_tier(row)
    sensitive = bool((set(tokens) & SENSITIVE_QUERY_TERMS) or (domains & SENSITIVE_DOMAINS))
    if requested_tiers and tier in requested_tiers:
        score += 2.0
    elif tier == "active":
        score += 2.5
    elif tier == "historical":
        score += 3.0 if sensitive else -1.0
    elif tier == "cold":
        score -= 8.0
    status = row_status(row)
    if status == "active":
        score += 2.0
    elif status in {"resolved", "inactive", "false", "outdated", "deleted"}:
        score -= 8.0
    return score


def filter_rows(
    rows: list[dict[str, Any]],
    domains: list[str],
    memory_types: list[str],
    memory_tiers: list[str],
    current_statuses: list[str],
) -> list[dict[str, Any]]:
    requested_domains = normalize_domain_values(domains)
    requested_types = normalize_values(memory_types)
    requested_tiers = {normalize_memory_tier(value) for value in memory_tiers if str(value).strip()}
    requested_statuses = normalize_values(current_statuses)
    filtered = []
    for row in rows:
        normalized = normalized_row(row)
        if requested_domains and not (requested_domains & row_domains(normalized)):
            continue
        if requested_types and normalized_memory_type(normalized) not in requested_types:
            continue
        if requested_tiers and row_tier(normalized) not in requested_tiers:
            continue
        if requested_statuses and row_status(normalized) not in requested_statuses:
            continue
        filtered.append(normalized)
    return filtered


def search_memories(
    repo: KontextRepository,
    query: str,
    top_k: int,
    domains: list[str],
    memory_types: list[str],
    memory_tiers: list[str],
    current_statuses: list[str],
) -> list[dict]:
    limit = min(max(int(top_k or 5), 1), 20)
    requested_domains = normalize_domain_values(domains)
    requested_tiers = {normalize_memory_tier(value) for value in memory_tiers if str(value).strip()}
    query_domains = query_domain_hints(query) | requested_domains
    candidate_limit = max(limit * (10 if query_domains & (SENSITIVE_DOMAINS | PROJECT_DOMAINS) else 6), 100)
    rows = repo.list_memory_rows(limit=candidate_limit)
    rows = filter_rows(rows, domains, memory_types, memory_tiers, current_statuses)
    scored = [
        (index, score_row(query, row, requested_domains=requested_domains or query_domains, requested_tiers=requested_tiers), row)
        for index, row in enumerate(rows)
    ]
    scored.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    return [row for _, _, row in scored[:limit]]
