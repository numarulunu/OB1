from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class IngestionMessage:
    role: str
    content: str


@dataclass(frozen=True)
class GateDecision:
    keep: bool
    reason: str
    domains: list[str]


@dataclass(frozen=True)
class IngestionProposal:
    action: str
    content: str
    domains: list[str]
    memory_type: str
    signal_strength: int
    current_status: str
    memory_tier: str
    confidence: float
    reason: str
    existing_id: str = ''
    flag_type: str = ''

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


VALID_ACTIONS = {'save', 'update', 'skip', 'flag'}
VALID_FLAG_TYPES = {'delete_candidate', 'merge_candidate', 'conflict_candidate', 'stale_candidate'}
VALID_MEMORY_TIERS = {'active', 'historical', 'cold'}

DOMAIN_KEYWORDS = {
    'ai': ('ai', 'agent', 'agents', 'chatgpt', 'claude', 'codex', 'mem0', 'open brain', 'ob1', 'kontext', 'mcp', 'qwen'),
    'systems': ('system', 'systems', 'workflow', 'automation', 'pipeline', 'settings', 'hook', 'backup', 'self-hosted'),
    'business': ('business', 'brand', 'branding', 'offer', 'students', 'skool', 'preply', 'sales', 'marketing'),
    'money_execution': ('money', 'finance', 'income', 'pfa', 'investment', 'tax', 'invoice', 'funding', 'cash'),
    'opera': ('opera', 'audition', 'competition', 'role', 'repertoire', 'fach', 'baritone', 'career'),
    'vocality': ('vocality', 'voice', 'vocal', 'singing', 'passaggio', 'larynx', 'vazquez', 'teacher', 'mentor'),
    'relationships': ('relationship', 'relationships', 'mother', 'father', 'family', 'partner', 'attachment', 'trust'),
    'psychology': ('psychology', 'identity', 'nervous system', 'pattern', 'blind spot', 'trauma', 'shadow', 'emotion'),
}

JUNK_TERMS = (
    'stdout',
    'stderr',
    'traceback',
    'stack trace',
    'raw json',
    'tool call',
    'speaker diarization',
    'speaker identification',
    'canonical names',
    'conversation cues',
    'transcript process',
)

GENERIC_VOCAL_TERMS = ('larynx', 'breath support', 'resonance', 'vowel tuning', 'pharyngeal', 'lesson covered')
OWN_VOICE_TERMS = ('my voice', 'my vocal', 'my fach', 'my role', 'my teacher', 'my mentor', 'opera', 'audition', 'career', 'vocality method', 'my method')
KEEP_TERMS = (
    'decided',
    'decision',
    'prefer',
    'preference',
    'want',
    'goal',
    'current',
    'workflow',
    'architecture',
    'should',
    'must',
    'remember',
    'save this',
)

POLICY_TEXT = '''
You extract durable second-brain memories for Ionut.

Core policy:
- User messages are primary truth.
- Assistant messages count only for accepted decisions, completed actions, summaries, project state, or stable interpretations.
- Keep current project state, business, workflows, settings, AI systems, opera career, psychology, relationships, family-origin, and identity-shaping life context.
- Keep Vocality only when it concerns Ionut's own voice/career/method or a reusable business/method pattern; drop generic lesson/anatomy sludge.
- Drop generic assistant advice, raw tool logs, stdout/stderr, stack traces, raw JSON blobs, transcript artifacts, and low-signal chatter.
- No hard-delete in V1. If something looks stale, duplicate, false, conflicting, or delete-worthy, emit a flag action instead.
- JSON only. Return exactly one JSON object with a proposals array.
'''.strip()


def normalize_text(value: Any) -> str:
    return ' '.join(str(value or '').strip().split())


def normalize_messages(messages: list[dict[str, Any]] | list[IngestionMessage] | None) -> list[IngestionMessage]:
    normalized = []
    for message in messages or []:
        if isinstance(message, IngestionMessage):
            role = message.role
            content = message.content
        else:
            role = str(message.get('role') or 'user')
            content = str(message.get('content') or '')
        role = role.strip().lower() or 'user'
        if role not in {'system', 'user', 'assistant', 'tool'}:
            role = 'user'
        content = normalize_text(content)
        if content:
            normalized.append(IngestionMessage(role=role, content=content))
    return normalized


def source_hash(messages: list[IngestionMessage]) -> str:
    payload = [{'role': message.role, 'content': message.content} for message in messages]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def build_sanitized_preview(messages: list[IngestionMessage], max_chars: int = 500) -> str:
    role_counts: dict[str, int] = {}
    total_chars = 0
    for message in messages:
        role_counts[message.role] = role_counts.get(message.role, 0) + 1
        total_chars += len(message.content)
    roles = ','.join(f'{role}:{count}' for role, count in sorted(role_counts.items())) or 'none'
    preview = f'exchange messages={len(messages)} chars={total_chars} roles={roles} hash={source_hash(messages)[:12]}'
    if len(preview) <= max_chars:
        return preview
    return preview[: max_chars - 3].rstrip() + '...'


def contains_term(text: str, term: str) -> bool:
    if re.search(r'[^a-z0-9]', term):
        return term in text
    return re.search(rf'\b{re.escape(term)}\b', text) is not None


def detect_domains(text: str) -> list[str]:
    lower = text.lower()
    domains = []
    for domain, terms in DOMAIN_KEYWORDS.items():
        if any(contains_term(lower, term) for term in terms):
            domains.append(domain)
    return domains


def should_call_llm(messages: list[IngestionMessage]) -> GateDecision:
    text = normalize_text(' '.join(message.content for message in messages))
    lower = text.lower()
    if not text:
        return GateDecision(False, 'empty_exchange', [])
    if any(term in lower for term in JUNK_TERMS):
        return GateDecision(False, 'transcript_or_tool_junk', [])
    if len(text) < 24 and not any(term in lower for term in KEEP_TERMS):
        return GateDecision(False, 'low_signal_chatter', [])
    if any(term in lower for term in GENERIC_VOCAL_TERMS) and not any(term in lower for term in OWN_VOICE_TERMS):
        return GateDecision(False, 'generic_vocal_lesson_sludge', ['vocality'])
    domains = detect_domains(text)
    if domains:
        return GateDecision(True, 'domain_signal', domains)
    if any(term in lower for term in KEEP_TERMS):
        return GateDecision(True, 'durable_language', [])
    return GateDecision(False, 'no_domain_signal', [])


def build_extraction_prompt(
    messages: list[IngestionMessage],
    source: str,
    context_messages: list[IngestionMessage] | None = None,
) -> list[dict[str, str]]:
    payload = {
        'source': source,
        'messages': [asdict(message) for message in messages],
        'context_messages': [asdict(message) for message in (context_messages or [])],
        'schema': {
            'proposals': [
                {
                    'action': 'save|update|skip|flag',
                    'content': 'distilled memory text',
                    'domains': ['ai', 'systems'],
                    'memory_type': 'decision',
                    'signal_strength': 8,
                    'current_status': 'active',
                    'memory_tier': 'active',
                    'confidence': 0.85,
                    'reason': 'why this is durable',
                    'existing_id': 'optional-memory-id',
                    'flag_type': 'delete_candidate|merge_candidate|conflict_candidate|stale_candidate',
                }
            ]
        },
    }
    return [
        {'role': 'system', 'content': POLICY_TEXT},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ]


def normalize_list(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else [value] if value else []
    return [normalize_text(item).lower() for item in raw if normalize_text(item)]


def clamp_int(value: Any, default: int = 5) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(1, min(10, number))


def clamp_float(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def normalize_tier(value: Any) -> str:
    tier = normalize_text(value).lower()
    return tier if tier in VALID_MEMORY_TIERS else 'active'


def normalize_flag_type(value: Any) -> str:
    flag_type = normalize_text(value).lower()
    return flag_type if flag_type in VALID_FLAG_TYPES else 'delete_candidate'


def normalize_action(value: Any) -> tuple[str, str]:
    action = normalize_text(value).lower() or 'save'
    if action == 'delete':
        return 'flag', 'delete_candidate'
    if action in VALID_ACTIONS:
        return action, ''
    return 'skip', ''


def parse_extraction_response(raw: str) -> list[IngestionProposal]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('invalid extraction JSON') from exc
    rows = payload.get('proposals') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('proposals field is required')
    proposals = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        action, implied_flag = normalize_action(row.get('action'))
        content = normalize_text(row.get('content'))
        reason = normalize_text(row.get('reason'))
        if action in {'save', 'update'} and not content:
            continue
        flag_type = implied_flag or normalize_flag_type(row.get('flag_type')) if action == 'flag' else ''
        proposals.append(
            IngestionProposal(
                action=action,
                content=content,
                domains=normalize_list(row.get('domains')),
                memory_type=normalize_text(row.get('memory_type')).lower() or 'pattern',
                signal_strength=clamp_int(row.get('signal_strength')),
                current_status=normalize_text(row.get('current_status')) or 'active',
                memory_tier=normalize_tier(row.get('memory_tier')),
                confidence=clamp_float(row.get('confidence')),
                reason=reason,
                existing_id=normalize_text(row.get('existing_id')),
                flag_type=flag_type,
            )
        )
    return proposals
