from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from kontext_v2.repository import KontextRepository
from kontext_v2.retention import is_protected_autobiographical_history
from kontext_v2.state_model import normalize_namespace
from kontext_v2.typed_state import attach_typed_state_metadata, typed_state_v2_enabled

VALID_MEMORY_TIERS = {"active", "historical", "cold"}
STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "ask", "but", "can", "connector",
    "context", "for", "from", "have", "how", "ionut", "memory", "mem0", "my", "of",
    "or", "search", "the", "then", "this", "to", "use", "what", "with", "you", "your",
}
DOMAIN_HINTS = {
    "ai": {"ai", "agent", "agents", "benchmark", "chatgpt", "claude", "codex", "kontext", "llm", "mcp", "mem0", "model", "qwen", "retrieval", "scorer"},
    "business": {"brand", "branding", "business", "client", "clients", "income", "money", "preply", "pricing", "revenue", "skool", "student", "students"},
    "family": {"childhood", "family", "father", "mother", "parent", "parents"},
    "finance": {"accounting", "bookkeeping", "finance", "financial", "funding", "income", "investment", "money", "pfa", "retirement", "tax", "taxes"},
    "infrastructure": {"backup", "container", "deploy", "docker", "gate", "kontext", "mcp", "metrics", "report", "reports", "server", "supabase", "sync", "synced", "telemetry", "vps"},
    "opera": {"audition", "auditions", "career", "competition", "melocchi", "opera", "repertoire", "role", "roles"},
    "psychology": {"attachment", "behavior", "blindspot", "identity", "mother", "pattern", "psychology", "shadow", "trauma"},
    "relationships": {"attachment", "family", "luiza", "mother", "partner", "relationship", "relationships", "trust"},
    "systems": {"architecture", "automation", "benchmark", "gate", "kontext", "mcp", "mem0", "metrics", "retrieval", "scorer", "system", "systems", "sync", "synced", "telemetry", "workflow", "workflows"},
    "vocality": {"larynx", "melocchi", "opera", "preply", "singing", "skool", "student", "teacher", "vocal", "vocality", "voice"},
    "workflow": {"automation", "execution", "gate", "metrics", "pipeline", "process", "report", "reports", "system", "telemetry", "workflow", "workflows"},
}
DOMAIN_HINTS_RO = {
    "business": {"brand", "client", "clienti", "marca", "venit"},
    "family": {"copilarie", "familie", "frate", "frati", "mama", "mami", "parinti", "sora", "surori", "tata", "tati"},
    "finance": {"bani", "cheltuiala", "cheltuieli", "contabilitate", "factura", "facturare", "impozit", "pfa", "tva", "venit"},
    "infrastructure": {"container", "implementare", "rulare", "server"},
    "opera": {"auditie", "cantec", "cariera", "concurs", "repertoriu", "rol", "scena", "voce"},
    "psychology": {"anxietate", "atasament", "comportament", "frica", "identitate", "psihologie", "rusine", "tipar", "trauma", "vina"},
    "relationships": {"cuplu", "iubit", "iubire", "iubita", "incredere", "luiza", "partener", "prietena", "prieten", "relatie"},
    "systems": {"automatizare", "infrastructura", "instrument", "rulare", "sistem"},
    "vocality": {"cursanti", "elev", "elevi", "metoda", "tehnica", "vocal", "vocala", "vocale"},
    "workflow": {"automatizare", "executie", "flux", "proces"},
}


def _merge_hint_maps(*maps: dict[str, set[str]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for hints_by_domain in maps:
        for domain, hints in hints_by_domain.items():
            out.setdefault(domain, set()).update(hints)
    return out


EFFECTIVE_DOMAIN_HINTS = _merge_hint_maps(DOMAIN_HINTS, DOMAIN_HINTS_RO)
DOMAIN_ALIASES = {
    "ai_systems": {"ai", "systems"},
    "family_origin": {"family", "psychology"},
    "mental_health": {"psychology"},
    "memory": {"ai", "systems"},
    "memory_system": {"ai", "systems"},
    "memory_systems": {"ai", "systems"},
    "communication": {"workflow"},
    "workflows": {"workflow"},
    "workflow_state": {"workflow"},
}
HIGH_SIGNAL_MEMORY_TYPES = {
    "architecture_decision", "career_context", "clinical_context", "decision", "execution_pattern",
    "finance_context", "formative_event", "goal", "identity_pattern", "project_state",
    "policy", "psychology_pattern", "relationship_pattern", "voice_profile", "workflow", "workflow_state",
}
LOW_SIGNAL_MEMORY_TYPES = {"lesson_note", "note", "transcript_note"}
SENSITIVE_QUERY_TERMS = {
    "attachment", "childhood", "family", "formative", "identity", "mother", "parent",
    "psychology", "relationship", "relationships", "shadow", "trauma", "trust",
    "abuse", "boundary", "boundaries", "brother", "dad", "divorce", "father", "grief",
    "guilt", "history", "loss", "mom", "origin", "partner", "past", "shame", "sibling",
    "sister", "wound",
    "atasament", "copilarie", "familie", "identitate", "incredere", "mama", "partener",
    "relatie", "rusine", "tata", "vina",
}
SENSITIVE_DOMAINS = {"family", "identity", "personal_life", "psychology", "relationships"}
PROJECT_DOMAINS = {"ai", "business", "finance", "infrastructure", "opera", "systems", "vocality", "workflow"}
PROJECT_QUERY_DOMAINS = {"ai", "systems", "infrastructure"}
PROJECT_RETRIEVAL_DOMAINS = {"ai", "systems", "infrastructure", "workflow"}
PROJECT_MEMORY_TYPES = {"architecture_decision", "decision", "preference", "project_state", "workflow_state"}
PROJECT_QUERY_TERMS = {"agent", "agents", "architecture", "automation", "benchmark", "gate", "kontext", "mcp", "mem0", "memory", "metrics", "retrieval", "scorer", "system", "systems", "telemetry"}
PERSONAL_PATTERN_TYPES = {"clinical_context", "formative_event", "identity_pattern", "psychology_pattern", "relationship_pattern"}
CURRENT_STATE_QUERY_TERMS = {"current", "latest", "logistics", "now", "recent", "today", "tomorrow"}
AUTOBIOGRAPHICAL_HISTORY_QUERY_TERMS = {
    "after",
    "ago",
    "attachment",
    "before",
    "childhood",
    "family",
    "formative",
    "history",
    "mother",
    "parent",
    "parents",
    "past",
    "relationship",
    "relationships",
    "trauma",
    "trust",
    "when",
    "years",
    "abuse",
    "ani",
    "atasament",
    "brother",
    "copilarie",
    "dad",
    "dupa",
    "familie",
    "father",
    "inainte",
    "inapoi",
    "mama",
    "mom",
    "partener",
    "relatie",
    "sibling",
    "sister",
    "tata",
    "viata",
}
MEMORY_POLICY_QUERY_TERMS = {"archive", "cleanup", "compact", "compacting", "delete", "deleting", "memories", "policy"}
FINANCE_FOCUS_QUERY_TERMS = {"accounting", "bookkeeping", "finance", "financial", "investment", "pfa", "retirement", "tax", "taxes"}
STATE_ROUTE_CURRENT_TERMS = CURRENT_STATE_QUERY_TERMS | {
    "currently",
    "instruction",
    "instructions",
    "prefer",
    "prefers",
    "preference",
    "preferences",
    "setting",
    "settings",
    "status",
}
STATE_ROUTE_HISTORY_TERMS = AUTOBIOGRAPHICAL_HISTORY_QUERY_TERMS | {"historical", "history", "timeline"}
STATE_ROUTE_ACTION_TERMS = {
    "blocked",
    "blocker",
    "decision",
    "decide",
    "explain",
    "how",
    "next",
    "plan",
    "should",
    "why",
}
QUERY_EXPANSION_RULES = (
    (("stuck", "blocked", "cannot start", "can't start", "overwhelmed", "procrastinat"), "blocked execution friction procrastination avoidance workflow next step decision clarity psychology"),
    (("partner", "girlfriend", "relationship conflict", "couple", "luiza"), "attachment anxious escalation avoidant withdrawal relationship trust psychology formative pattern"),
    (("mother", "father", "parent", "family"), "family origin attachment identity trust formative psychology relationship pattern childhood"),
    (("money", "income", "finance", "funding", "retirement", "pfa", "tax"), "business finance income funding opera career travel engine systems decision"),
    (("voice", "singing", "opera", "role", "audition", "repertoire"), "vocality voice fach role repertoire audition opera career context"),
    (("server", "vps", "docker", "mcp", "container"), "infrastructure deployment systems mem0 agent docker mcp"),
    (("second-brain", "second brain", "cleanup", "deleting", "delete", "compacting", "compact", "archive memories", "memory policy"), "systems workflow policy decision cleanup delete compact archive memories architecture"),
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
    r"\b(?:when|what time|what day|which day|current|latest|recent|now|date|month|year|time|today|tomorrow|yesterday|before|after|ago|week|weeks|month|months|year|years|morning|afternoon|evening|night|tonight|next|last)\b",
    re.IGNORECASE,
)
SCORE_TEXT_CACHE_KEY = "_score_text_cache"
MAX_PREPARED_ROW_CACHE = 20_000
DB_ASSISTED_CANDIDATE_LIMIT = 1_500
HIGH_SIGNAL_FALLBACK_LIMIT = 700
MIN_DB_ASSISTED_CANDIDATES = 200
MAX_RETRIEVAL_CANDIDATE_LIMIT = 2_500
MAX_SEARCH_RESULTS = 50
MAX_SCORING_TEXT_CHARS = 12_000
SCORING_TEXT_TAIL_CHARS = 4_000
STATE_PROJECTION_RETRIEVAL_SCORE = 1_000_000_000.0
_PREPARED_ROW_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}


@dataclass(frozen=True)
class ScoreQueryContext:
    query: str
    expanded: str
    expanded_norm: str
    tokens: list[str]
    original_token_set: set[str]
    raw_query_domains: set[str]
    project_shadow_mode: bool
    query_domains: set[str]
    sensitive_query_tokens: set[str]
    temporal_query: bool
    proper_noun_tokens: list[str]
    finance_focus_query: bool
    project_query_intent: bool
    memory_policy_query: bool
    project_state_policy_query: bool


def clear_prepared_row_cache() -> None:
    _PREPARED_ROW_CACHE.clear()


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


def _strip_diacritics(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", str(text or ""))
        if unicodedata.category(char) != "Mn"
    )


def lexical_tokens(text: str) -> list[str]:
    normalized = _strip_diacritics(str(text or "")).lower()
    short_domain_tokens = {"ai", "go", "os", "ui", "ux"}
    return [
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if (len(token) > 2 or token in short_domain_tokens) and token not in STOPWORDS
    ]


def bounded_scoring_text(text: Any) -> str:
    value = str(text or "")
    if len(value) <= MAX_SCORING_TEXT_CHARS:
        return value
    tail_size = min(SCORING_TEXT_TAIL_CHARS, MAX_SCORING_TEXT_CHARS // 2)
    head_size = MAX_SCORING_TEXT_CHARS - tail_size
    return value[:head_size] + " " + value[-tail_size:]


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
    lower = _strip_diacritics(str(query or "")).lower()
    domains: set[str] = set()
    for domain, hints in EFFECTIVE_DOMAIN_HINTS.items():
        if tokens & hints or any(" " in hint and hint in lower for hint in hints):
            domains.add(domain)
    return domains


def project_shadow_mode_query(original_tokens: set[str], query_domains: set[str]) -> bool:
    explicit_sensitive = original_tokens & (SENSITIVE_QUERY_TERMS - {"shadow"})
    return bool(
        "shadow" in original_tokens
        and not explicit_sensitive
        and original_tokens & PROJECT_QUERY_TERMS
        and query_domains & PROJECT_QUERY_DOMAINS
    )


def scoring_query_domains(query: str, expanded: str | None = None) -> set[str]:
    expanded_query = expanded if expanded is not None else expand_query(query)
    domains = query_domain_hints(expanded_query)
    if project_shadow_mode_query(set(lexical_tokens(query)), domains):
        return domains - SENSITIVE_DOMAINS
    return domains


def build_score_query_context(query: str) -> ScoreQueryContext:
    expanded = expand_query(query)
    expanded_norm = normalized_text(expanded)
    tokens = lexical_tokens(expanded)
    original_token_set = set(lexical_tokens(query))
    raw_query_domains = query_domain_hints(expanded)
    project_shadow_mode = project_shadow_mode_query(original_token_set, raw_query_domains)
    query_domains = raw_query_domains - SENSITIVE_DOMAINS if project_shadow_mode else raw_query_domains
    sensitive_query_tokens = set(tokens) & SENSITIVE_QUERY_TERMS
    if project_shadow_mode:
        sensitive_query_tokens = sensitive_query_tokens - {"shadow"}
    return ScoreQueryContext(
        query=query,
        expanded=expanded,
        expanded_norm=expanded_norm,
        tokens=tokens,
        original_token_set=original_token_set,
        raw_query_domains=raw_query_domains,
        project_shadow_mode=project_shadow_mode,
        query_domains=query_domains,
        sensitive_query_tokens=sensitive_query_tokens,
        temporal_query=query_temporal_intent(query),
        proper_noun_tokens=query_proper_noun_tokens(query),
        finance_focus_query=bool(original_token_set & FINANCE_FOCUS_QUERY_TERMS),
        project_query_intent=bool(original_token_set & PROJECT_QUERY_TERMS),
        memory_policy_query=bool(set(tokens) & MEMORY_POLICY_QUERY_TERMS),
        project_state_policy_query=bool(original_token_set & {"agent", "agents", "codex", "claude", "dream", "continuity", "compaction"}),
    )


def expand_query(query: str) -> str:
    original = str(query or "").strip()
    if not original:
        return ""
    lower = _strip_diacritics(original).lower()
    query_tokens = set(lexical_tokens(original))
    additions: list[str] = []
    seen: set[str] = set()
    for triggers, terms in QUERY_EXPANSION_RULES:
        if any(_query_expansion_trigger_hit(trigger, lower, query_tokens) for trigger in triggers):
            for term in terms.split():
                if term not in seen and term not in lower:
                    seen.add(term)
                    additions.append(term)
    return original + (" " + " ".join(additions) if additions else "")


def _query_expansion_trigger_hit(trigger: str, lower_query: str, query_tokens: set[str]) -> bool:
    normalized_trigger = _strip_diacritics(str(trigger or "")).lower().strip()
    if not normalized_trigger:
        return False
    if " " in normalized_trigger or "-" in normalized_trigger or "'" in normalized_trigger:
        return re.search(rf"(?<!\w){re.escape(normalized_trigger)}(?!\w)", lower_query) is not None
    if normalized_trigger in query_tokens:
        return True
    if normalized_trigger == "procrastinat":
        return any(token.startswith(normalized_trigger) for token in query_tokens)
    return False


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return dict(metadata)


def parse_row_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def row_effective_timestamp(row: dict[str, Any]) -> datetime | None:
    metadata = row_metadata(row)
    for key in ("effective_at", "observed_at", "updated_at", "last_modified_at", "created_at", "imported_at"):
        parsed = parse_row_timestamp(metadata.get(key)) or parse_row_timestamp(row.get(key))
        if parsed is not None:
            return parsed
    return None


def row_recency_score(row: dict[str, Any]) -> float:
    timestamp = row_effective_timestamp(row)
    return recency_score_for_timestamp(timestamp)


def recency_score_for_timestamp(timestamp: datetime | None) -> float:
    if timestamp is None:
        return 0.0
    now = datetime.now(timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86400.0, 0.0)
    return 8.0 / (1.0 + (age_days / 90.0))


def row_superseded(row: dict[str, Any]) -> bool:
    metadata = row_metadata(row)
    status = str(metadata.get("current_status") or row.get("current_status") or "").strip().lower()
    return bool(
        metadata.get("superseded_by")
        or metadata.get("superseded_at")
        or row.get("superseded_by")
        or row.get("superseded_at")
        or status in {"superseded", "outdated", "false", "deleted"}
    )


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
    query_norm = normalized_text(query)
    return _lexical_score_normalized(query_norm, tokens, haystack)


def _lexical_score_normalized(query_norm: str, tokens: list[str], haystack: str) -> float:
    if not haystack:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    if hits < min(2, len(tokens)):
        return 0.0
    phrase_bonus = 10.0 if query_norm and query_norm in haystack else 0.0
    coverage_bonus = 5.0 if hits == len(tokens) else 0.0
    return hits + phrase_bonus + coverage_bonus


def adjacent_token_score(tokens: list[str], text: str) -> float:
    haystack_tokens = lexical_tokens(text)
    haystack = " ".join(haystack_tokens)
    return _adjacent_token_score_prepared(tokens, haystack, len(haystack_tokens))


def _adjacent_token_score_prepared(tokens: list[str], haystack: str, haystack_token_count: int) -> float:
    if len(tokens) < 2 or haystack_token_count < 2:
        return 0.0
    score = 0.0
    for size, weight in ((3, 4.0), (2, 1.5)):
        if len(tokens) < size:
            continue
        for index in range(0, len(tokens) - size + 1):
            if " ".join(tokens[index : index + size]) in haystack:
                score += weight
    return score


def prepared_row_cache_key(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    memory_id = str(row.get("external_mem0_id") or row.get("id") or "").strip()
    updated = str(row.get("updated_at") or "").strip()
    if not memory_id or not updated:
        return None
    return (
        memory_id,
        updated,
        str(row.get("memory_type") or ""),
        str(row.get("signal_strength") or ""),
    )


def store_prepared_row_cache(key: tuple[str, str, str, str] | None, cache: dict[str, Any]) -> None:
    if key is None:
        return
    if len(_PREPARED_ROW_CACHE) >= MAX_PREPARED_ROW_CACHE:
        _PREPARED_ROW_CACHE.pop(next(iter(_PREPARED_ROW_CACHE)))
    _PREPARED_ROW_CACHE[key] = cache


def prepare_score_row(row: dict[str, Any]) -> dict[str, Any]:
    key = prepared_row_cache_key(row)
    if key is not None and key in _PREPARED_ROW_CACHE:
        row[SCORE_TEXT_CACHE_KEY] = _PREPARED_ROW_CACHE[key]
        return row
    own_text = " ".join([str(row.get("title") or ""), bounded_scoring_text(row.get("text") or row.get("memory") or "")])
    context_text = normalized_text(bounded_scoring_text(row.get("context_text") or ""))
    own_tokens = lexical_tokens(own_text)
    context_tokens = lexical_tokens(context_text)
    metadata = row_metadata(row)
    cache = {
        "own_text": own_text,
        "own_norm": normalized_text(own_text),
        "own_token_text": " ".join(own_tokens),
        "own_token_count": len(own_tokens),
        "own_has_date": has_date_hint(own_text),
        "context_norm": context_text,
        "context_token_text": " ".join(context_tokens),
        "context_token_count": len(context_tokens),
        "context_has_date": has_date_hint(context_text),
        "combined_norm": normalized_text(" ".join(part for part in [own_text, context_text] if part)),
        "domains": row_domains(row),
        "memory_type": normalized_memory_type(row),
        "status": row_status(row),
        "tier": row_tier(row),
        "signal": row_signal(row),
        "superseded": row_superseded(row),
        "effective_timestamp": row_effective_timestamp(row),
        "observation_kind": metadata.get("observation_kind"),
    }
    row[SCORE_TEXT_CACHE_KEY] = cache
    store_prepared_row_cache(key, cache)
    return row


def _score_text_cache(row: dict[str, Any]) -> dict[str, Any]:
    cache = row.get(SCORE_TEXT_CACHE_KEY)
    if isinstance(cache, dict):
        return cache
    return prepare_score_row(row)[SCORE_TEXT_CACHE_KEY]


def _add_score_feature(features: list[dict[str, Any]], name: str, value: float, reason: str, include_zero: bool = False) -> float:
    if value or include_zero:
        features.append({"name": name, "value": value, "reason": reason})
    return value


def _score_row_with_explanation(
    query: str,
    row: dict[str, Any],
    requested_domains: set[str],
    requested_tiers: set[str],
    query_context: ScoreQueryContext | None = None,
) -> tuple[float, dict[str, Any]]:
    context = query_context or build_score_query_context(query)
    expanded_norm = context.expanded_norm
    tokens = context.tokens
    query_token_set = set(tokens)
    original_token_set = context.original_token_set
    text_cache = _score_text_cache(row)
    own_text = str(text_cache.get("own_text") or "")
    own_norm = str(text_cache.get("own_norm") or "")
    context_text = str(text_cache.get("context_norm") or "")
    combined_text = str(text_cache.get("combined_norm") or "")
    domains = set(text_cache.get("domains") or row_domains(row))
    query_domains = context.query_domains
    project_shadow_mode = context.project_shadow_mode
    sensitive_query_tokens = context.sensitive_query_tokens
    type_name = str(text_cache.get("memory_type") or normalized_memory_type(row))
    temporal_query = context.temporal_query
    protected_history = is_protected_autobiographical_history(row)
    lexical_own_weight = 0.85 if temporal_query else 1.0
    lexical_context_weight = 0.25 if temporal_query else 0.35
    adjacent_own_weight = 1.0 if temporal_query else 1.2
    adjacent_context_weight = 0.25 if temporal_query else 0.35
    proper_own_weight = 2.0 if temporal_query else 4.0
    proper_context_weight = 0.75 if temporal_query else 1.5
    date_own_bonus = 10.0 if temporal_query else 4.5
    date_context_bonus = 2.0 if temporal_query else 1.5
    features: list[dict[str, Any]] = []
    score = 0.0
    try:
        score += _add_score_feature(features, "base_rank", float(row.get("rank") or 0.0) * 10.0, "stored vector/fused rank")
    except (TypeError, ValueError):
        pass
    lexical_own = _lexical_score_normalized(expanded_norm, tokens, own_norm) * lexical_own_weight
    score += _add_score_feature(features, "lexical_own", lexical_own, "query tokens matched title or memory body", include_zero=True)
    if context_text:
        lexical_context = _lexical_score_normalized(expanded_norm, tokens, context_text) * lexical_context_weight
        score += _add_score_feature(features, "lexical_context", lexical_context, "query tokens matched compact context")
    adjacent_own = _adjacent_token_score_prepared(
        tokens,
        str(text_cache.get("own_token_text") or ""),
        int(text_cache.get("own_token_count") or 0),
    ) * adjacent_own_weight
    score += _add_score_feature(features, "adjacent_own", adjacent_own, "adjacent query tokens matched title or memory body")
    if context_text:
        adjacent_context = _adjacent_token_score_prepared(
            tokens,
            str(text_cache.get("context_token_text") or ""),
            int(text_cache.get("context_token_count") or 0),
        ) * adjacent_context_weight
        score += _add_score_feature(features, "adjacent_context", adjacent_context, "adjacent query tokens matched compact context")
    domain_hits = query_domains & domains
    if requested_domains and requested_domains & domains:
        score += _add_score_feature(features, "requested_domain", 12.0, "requested domain matched memory domains")
    elif domain_hits:
        score += _add_score_feature(features, "domain_hint", 8.0, "query domain hint matched memory domains")
    score += _add_score_feature(features, "domain_hit_count", min(len(domain_hits), 3) * 4.0, "number of matched query domains")
    if type_name in HIGH_SIGNAL_MEMORY_TYPES:
        score += _add_score_feature(features, "high_signal_memory_type", 5.0, "memory type is high signal")
    if type_name in LOW_SIGNAL_MEMORY_TYPES:
        score += _add_score_feature(features, "low_signal_memory_type", -5.0, "memory type is low signal")
    if type_name in {"relationship_pattern", "formative_event", "clinical_context"} and sensitive_query_tokens:
        score += _add_score_feature(features, "sensitive_personal_type", 8.0, "sensitive query matched personal-pattern memory type")
    sensitive_agent_context_query = bool(
        original_token_set & {"agent", "agents"}
        and query_domains & {"psychology", "relationships", "family"}
    )
    if sensitive_agent_context_query and type_name in {"clinical_context", "decision", "policy", "psychology_pattern", "relationship_pattern"} and domains & {"psychology", "relationships", "family", "workflow"}:
        score += _add_score_feature(features, "sensitive_agent_context_type", 6.0, "agent query about sensitive context matched a careful-handling rule or pattern")
    if type_name in {"finance_context", "decision", "goal"} and query_domains & {"finance", "business"}:
        score += _add_score_feature(features, "finance_business_type", 8.0, "finance or business query matched relevant memory type")
    finance_focus_query = context.finance_focus_query
    if finance_focus_query and "finance" in query_domains and "finance" in domains:
        score += _add_score_feature(features, "finance_domain_focus", 18.0, "finance query matched explicit finance domain")
        if type_name in {"finance_context", "project_state", "workflow_state"}:
            score += _add_score_feature(features, "finance_state_type", 8.0, "finance query matched stateful finance/project memory")
    elif "business" in query_domains and "business" in domains and original_token_set & {"brand", "branding", "business", "goal", "goals", "pricing", "student", "students"}:
        score += _add_score_feature(features, "business_domain_focus", 8.0, "business query matched explicit business domain")
    project_query_intent = context.project_query_intent
    if project_query_intent and query_domains & PROJECT_QUERY_DOMAINS:
        query_token_set = set(tokens)
        project_domain_hit = bool(domains & PROJECT_QUERY_DOMAINS)
        project_retrieval_hit = bool(domains & PROJECT_RETRIEVAL_DOMAINS)
        project_type_hit = type_name in PROJECT_MEMORY_TYPES
        if project_domain_hit:
            score += _add_score_feature(features, "project_domain", 10.0, "project query matched AI/systems/infrastructure domains")
        if project_domain_hit and project_type_hit:
            score += _add_score_feature(features, "project_type", 18.0, "project query matched project memory type")
        if "architecture" in query_token_set and project_domain_hit and type_name in {"architecture_decision", "decision", "project_state"}:
            score += _add_score_feature(features, "project_architecture", 18.0, "architecture query matched architecture/project decision type")
        if query_token_set & {"agent", "agents", "automation", "preferences", "preference"} and project_retrieval_hit and type_name in {"decision", "preference", "project_state", "workflow_state"}:
            score += _add_score_feature(features, "project_agent_preference", 16.0, "agent or automation query matched project preference/workflow type")
        non_sensitive_project_query = not sensitive_query_tokens and not (query_domains & {"psychology", "relationships", "family"})
        if non_sensitive_project_query and type_name in PERSONAL_PATTERN_TYPES:
            score += _add_score_feature(features, "project_personal_type_penalty", -24.0, "non-sensitive project query should not prefer personal-pattern memory")
        if non_sensitive_project_query and domains & {"psychology", "relationships", "family"} and not project_type_hit:
            score += _add_score_feature(features, "project_personal_domain_penalty", -14.0, "non-sensitive project query should not prefer personal domains")
        if non_sensitive_project_query and not project_domain_hit:
            score += _add_score_feature(features, "project_domain_miss_penalty", -24.0, "project query missed project domains")
    memory_policy_query = context.memory_policy_query
    project_state_policy_query = context.project_state_policy_query
    if memory_policy_query and type_name in {"architecture_decision", "decision", "policy"} and domains & {"ai", "systems", "workflow"}:
        score += _add_score_feature(features, "memory_policy", 48.0, "memory cleanup policy query matched AI/systems/workflow policy memory")
    elif memory_policy_query and project_state_policy_query and type_name in {"project_state", "workflow_state"} and domains & {"ai", "systems", "workflow"}:
        score += _add_score_feature(features, "memory_policy_project_state", 48.0, "specific memory policy query matched project/workflow state")
    if type_name in {"career_context", "voice_profile"} and query_domains & {"opera", "vocality"}:
        score += _add_score_feature(features, "voice_career_type", 8.0, "opera or Vocality query matched voice/career memory type")
    if type_name in {"execution_pattern", "psychology_pattern", "workflow"} and query_domains & {"psychology", "workflow"}:
        score += _add_score_feature(features, "psychology_workflow_type", 8.0, "psychology or workflow query matched execution/workflow memory type")
    if text_cache.get("observation_kind") == "session":
        session_boost = 32.0 if type_name == "benchmark_observation" else 4.0
        score += _add_score_feature(features, "session_observation", session_boost, "project observation came from a session summary")
    for token in ("luiza", "mother", "pfa", "vocality", "opera", "melocchi", "branding", "accounting"):
        if token in expanded_norm and token in combined_text:
            score += _add_score_feature(features, f"named_topic_{token}", 6.0, "named topic appeared in query and memory")
    proper_noun_tokens = context.proper_noun_tokens
    if proper_noun_tokens:
        own_proper_hits = sum(1 for token in proper_noun_tokens if token in own_norm)
        context_proper_hits = sum(1 for token in proper_noun_tokens if token in context_text)
        score += _add_score_feature(features, "proper_noun_own", own_proper_hits * proper_own_weight, "proper nouns matched title or memory body")
        score += _add_score_feature(features, "proper_noun_context", context_proper_hits * proper_context_weight, "proper nouns matched compact context")
    if temporal_query:
        own_date_hits = 1 if text_cache.get("own_has_date") else 0
        context_date_hits = 1 if text_cache.get("context_has_date") else 0
        if own_date_hits:
            score += _add_score_feature(features, "date_own", date_own_bonus, "temporal query matched dated memory body")
        else:
            score += _add_score_feature(features, "missing_date_penalty", -5.0, "temporal query preferred dated memories")
        score += _add_score_feature(features, "date_context", context_date_hits * date_context_bonus, "temporal query matched dated compact context")
    signal = float(text_cache.get("signal") if text_cache.get("signal") is not None else row_signal(row))
    score += _add_score_feature(features, "signal_strength", signal * 1.2, "memory signal strength")
    if type_name in LOW_SIGNAL_MEMORY_TYPES and signal < 4:
        score += _add_score_feature(features, "low_signal_weak_memory_penalty", -8.0, "low-signal memory type also has weak signal strength")
    tier = str(text_cache.get("tier") or row_tier(row))
    sensitive = bool(sensitive_query_tokens or (domains & SENSITIVE_DOMAINS))
    current_state_query = bool(query_token_set & CURRENT_STATE_QUERY_TERMS)
    autobiographical_history_query = bool(query_token_set & AUTOBIOGRAPHICAL_HISTORY_QUERY_TERMS)
    protected_history_query = bool(
        protected_history
        and sensitive_query_tokens
        and not current_state_query
        and (not temporal_query or autobiographical_history_query)
    )
    if requested_tiers and tier in requested_tiers:
        score += _add_score_feature(features, "requested_tier", 2.0, "requested tier matched memory tier")
    elif tier == "active":
        score += _add_score_feature(features, "tier_active", 2.5, "active memories rank above archived tiers")
    elif tier == "historical":
        value = 4.5 if protected_history_query else (-1.0 if temporal_query else (3.0 if sensitive else -1.0))
        score += _add_score_feature(features, "tier_historical", value, "historical tier is contextual unless sensitive/history query needs it")
    elif tier == "cold":
        if protected_history_query:
            score += _add_score_feature(features, "protected_history_cold_tier", 2.0, "protected autobiographical history remains retrievable even if stored as cold")
        else:
            score += _add_score_feature(features, "tier_cold", -8.0, "cold memories are archived low-priority context")
    status = str(text_cache.get("status") or row_status(row))
    superseded = bool(text_cache.get("superseded")) if "superseded" in text_cache else row_superseded(row)
    if status == "active":
        score += _add_score_feature(features, "status_active", 2.0, "active status ranks above stale statuses")
    elif protected_history_query and status in {"dormant", "resolved", "inactive"}:
        score += _add_score_feature(features, "protected_history_status", 0.0, "protected historical context is not stale solely because it is old or dormant")
    elif status in {"resolved", "inactive", "superseded", "false", "outdated", "deleted"}:
        score += _add_score_feature(features, "stale_status_penalty", -8.0, "stale status demotes retrieval")
    if superseded:
        score += _add_score_feature(features, "superseded_penalty", -10.0 if requested_tiers else -32.0, "superseded memories should lose to current successors")
    elif tier == "active" and status in {"", "active"}:
        score += _add_score_feature(features, "recency", recency_score_for_timestamp(text_cache.get("effective_timestamp") if isinstance(text_cache.get("effective_timestamp"), datetime) else row_effective_timestamp(row)), "recent active memories receive a bounded boost")
    elif tier == "historical" and sensitive:
        recency = recency_score_for_timestamp(text_cache.get("effective_timestamp") if isinstance(text_cache.get("effective_timestamp"), datetime) else row_effective_timestamp(row))
        score += _add_score_feature(features, "historical_sensitive_recency", recency * 0.35, "sensitive historical context gets a smaller recency boost")
    explanation = {
        "total": score,
        "features": features,
        "domains": sorted(domains),
        "query_domains": sorted(query_domains),
        "matched_domains": sorted(domain_hits),
        "memory_type": type_name,
        "tier": tier,
        "status": status,
        "superseded": superseded,
        "temporal_query": temporal_query,
        "project_shadow_mode": project_shadow_mode,
        "requested_domains": sorted(requested_domains),
        "requested_tiers": sorted(requested_tiers),
    }
    return score, explanation


def score_row(query: str, row: dict[str, Any], requested_domains: set[str], requested_tiers: set[str]) -> float:
    score, _ = _score_row_with_explanation(query, row, requested_domains, requested_tiers)
    return score


def score_row_with_context(
    query: str,
    row: dict[str, Any],
    requested_domains: set[str],
    requested_tiers: set[str],
    query_context: ScoreQueryContext,
) -> float:
    score, _ = _score_row_with_explanation(query, row, requested_domains, requested_tiers, query_context=query_context)
    return score


def explain_score_row(query: str, row: dict[str, Any], requested_domains: set[str], requested_tiers: set[str]) -> dict[str, Any]:
    _, explanation = _score_row_with_explanation(query, row, requested_domains, requested_tiers)
    return explanation


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


def merge_candidate_rows(*row_groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for rows in row_groups:
        for row in rows:
            key = str(row.get("external_mem0_id") or row.get("id") or "").strip()
            if key:
                if key in seen:
                    continue
                seen.add(key)
            merged.append(row)
    return merged


def db_assisted_candidate_rows(
    repo: KontextRepository,
    *,
    query_context: ScoreQueryContext,
    candidate_limit: int,
    top_k: int,
    requested_domains: set[str],
    memory_types: list[str],
    memory_tiers: list[str],
    current_statuses: list[str],
) -> list[dict] | None:
    if candidate_limit <= HIGH_SIGNAL_FALLBACK_LIMIT or not hasattr(repo, "search_rows"):
        return None

    search_limit = min(candidate_limit, max(DB_ASSISTED_CANDIDATE_LIMIT, top_k * 120))
    fallback_limit = min(candidate_limit, max(HIGH_SIGNAL_FALLBACK_LIMIT, top_k * 80))
    try:
        search_rows = repo.search_rows(
            query_context.expanded,
            search_limit,
            sorted(requested_domains),
            memory_types,
            memory_tiers,
            current_statuses,
        )
    except Exception:
        return None

    fallback_rows = repo.list_memory_rows(limit=fallback_limit)
    candidates = merge_candidate_rows(search_rows, fallback_rows)
    if len(candidates) < min(MIN_DB_ASSISTED_CANDIDATES, candidate_limit):
        return None
    return candidates


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def state_projection_routing_enabled() -> bool:
    return _env_bool("KONTEXT_STATE_MODEL_ENABLED") and _env_bool("KONTEXT_STATE_ROUTING_ENABLED")


def state_projection_namespace() -> str:
    return normalize_namespace(os.environ.get("KONTEXT_STATE_NAMESPACE") or "live")


def state_query_route(query: str) -> str:
    tokens = set(lexical_tokens(query))
    if not tokens & STATE_ROUTE_CURRENT_TERMS:
        return "memory_search"
    if tokens & STATE_ROUTE_ACTION_TERMS:
        return "state_plus_history"
    if tokens & STATE_ROUTE_HISTORY_TERMS:
        return "state_plus_history"
    return "current_state"


def state_projection_rows(repo: KontextRepository, query: str, limit: int, *, namespace: str | None = None) -> list[dict]:
    if not state_projection_routing_enabled() or state_query_route(query) != "current_state":
        return []
    if not hasattr(repo, "search_current_state_facts"):
        return []
    projection_namespace = normalize_namespace(namespace) if namespace is not None else state_projection_namespace()
    try:
        rows = repo.search_current_state_facts(query, namespace=projection_namespace, top_k=limit)
    except Exception:
        return []
    projected: list[dict] = []
    for index, row in enumerate(rows, start=1):
        clean_row = dict(row)
        metadata = row_metadata(clean_row)
        metadata["retrieval_path"] = "state_projection"
        current_value = (
            clean_row.get("current_value")
            or clean_row.get("fact_value")
            or metadata.get("current_value")
            or metadata.get("fact_value")
            or {}
        )
        state_event_type = (
            clean_row.get("state_event_type")
            or clean_row.get("event_type")
            or metadata.get("state_event_type")
            or metadata.get("event_type")
            or ""
        )
        state_key = clean_row.get("state_key") or metadata.get("state_key") or ""
        state_status = (
            clean_row.get("state_status")
            or metadata.get("state_status")
            or clean_row.get("current_status")
            or metadata.get("current_status")
            or "active"
        )
        active_event_id = str(clean_row.get("active_event_id") or metadata.get("active_event_id") or "")
        support_event_ids = clean_row.get("support_event_ids") or metadata.get("support_event_ids") or []
        superseded_event_ids = clean_row.get("superseded_event_ids") or metadata.get("superseded_event_ids") or []
        cancelled_event_ids = clean_row.get("cancelled_event_ids") or metadata.get("cancelled_event_ids") or []
        metadata.update(
            {
                "state_event_type": str(state_event_type),
                "state_key": str(state_key),
                "state_status": str(state_status),
                "active_event_id": active_event_id,
                "support_event_ids": [str(item) for item in support_event_ids],
                "superseded_event_ids": [str(item) for item in superseded_event_ids],
                "cancelled_event_ids": [str(item) for item in cancelled_event_ids],
            }
        )
        clean_row["metadata"] = metadata
        clean_row["current_value"] = current_value if isinstance(current_value, dict) else {}
        clean_row["state_event_type"] = str(state_event_type)
        clean_row["state_key"] = str(state_key)
        clean_row["state_status"] = str(state_status)
        clean_row["active_event_id"] = active_event_id
        clean_row["support_event_ids"] = [str(item) for item in support_event_ids]
        clean_row["superseded_event_ids"] = [str(item) for item in superseded_event_ids]
        clean_row["cancelled_event_ids"] = [str(item) for item in cancelled_event_ids]
        clean_row["_retrieval_score"] = STATE_PROJECTION_RETRIEVAL_SCORE
        clean_row["_retrieval_rank"] = index
        clean_row["_retrieval_path"] = "state_projection"
        if typed_state_v2_enabled():
            clean_row = attach_typed_state_metadata(clean_row)
        projected.append(clean_row)
    return projected


def search_memories(
    repo: KontextRepository,
    query: str,
    top_k: int,
    domains: list[str],
    memory_types: list[str],
    memory_tiers: list[str],
    current_statuses: list[str],
    include_explanations: bool = False,
    namespace: str | None = None,
) -> list[dict]:
    limit = min(max(int(top_k or 5), 1), MAX_SEARCH_RESULTS)
    projected_rows = state_projection_rows(repo, query, limit, namespace=namespace)
    requested_domains = normalize_domain_values(domains)
    requested_tiers = {normalize_memory_tier(value) for value in memory_tiers if str(value).strip()}
    query_context = build_score_query_context(query)
    query_domains = query_context.query_domains | requested_domains
    candidate_limit = 10000 if query_domains & (SENSITIVE_DOMAINS | PROJECT_DOMAINS) else max(limit * 6, 100)
    if hasattr(repo, "search_rows"):
        candidate_limit = min(candidate_limit, MAX_RETRIEVAL_CANDIDATE_LIMIT)
    rows = db_assisted_candidate_rows(
        repo,
        query_context=query_context,
        candidate_limit=candidate_limit,
        top_k=limit,
        requested_domains=requested_domains,
        memory_types=memory_types,
        memory_tiers=memory_tiers,
        current_statuses=current_statuses,
    )
    if rows is None:
        rows = repo.list_memory_rows(limit=candidate_limit)
    rows = filter_rows(rows, domains, memory_types, memory_tiers, current_statuses)
    effective_requested_domains = requested_domains or query_domains
    scored = [
        (
            index,
            score_row_with_context(
                query,
                row,
                requested_domains=effective_requested_domains,
                requested_tiers=requested_tiers,
                query_context=query_context,
            ),
            row,
        )
        for index, row in enumerate(rows)
    ]
    scored.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    results = []
    for _, score, row in scored[:limit]:
        row["_retrieval_score"] = score
        if include_explanations:
            row = dict(row)
            row["score_explanation"] = explain_score_row(
                query,
                row,
                requested_domains=effective_requested_domains,
                requested_tiers=requested_tiers,
            )
        results.append(row)
    if projected_rows:
        projected_statuses = {
            str(row.get("state_status") or row_metadata(row).get("state_status") or "").strip().lower()
            for row in projected_rows
        }
        if projected_statuses & {"cancelled", "ambiguous"}:
            return projected_rows[:limit]
        return merge_candidate_rows(projected_rows, results)[:limit]
    return results
