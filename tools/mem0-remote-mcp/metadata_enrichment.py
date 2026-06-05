from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from audit_ledger import AuditLedger

TARGET_DOMAINS = {'relationships', 'psychology', 'family', 'family_origin', 'identity', 'personal_life'}
SPECIFIC_TYPES = {
    'attachment_pattern',
    'clinical_context',
    'formative_event',
    'identity_pattern',
    'relationship_pattern',
    'person_context',
}
WEAK_TYPES = {'', 'note', 'person', 'summary', 'memory', 'context', 'identity_shaping'}
CLINICAL_TERMS = {
    'anosognosia', 'antipsychotic', 'delusional', 'delusion', 'hospitalized',
    'internare', 'medication', 'psychiatric', 'psychiatry', 'psychotic', 'ward',
}
ATTACHMENT_TERMS = {'anxious attachment', 'avoidant', 'attachment', 'withdrawal', 'escalation'}
FORMATIVE_TERMS = {'childhood', 'family origin', 'family-origin', 'formative', 'parentification', 'hypervigilance'}
IDENTITY_TERMS = {'identity', 'ambition', 'trust calibration', 'nervous system', 'shadow'}
RELATIONSHIP_TERMS = {'luiza', 'partner', 'relationship pattern', 'relationship', 'mother', 'father'}


@dataclass(frozen=True)
class MetadataEnrichmentAction:
    action: str
    memory_ids: list[str]
    reason: str
    memory_type: str = ''
    current_memory_type: str = ''

    def __post_init__(self):
        action = str(self.action or 'keep').strip().lower()
        object.__setattr__(self, 'action', action)
        object.__setattr__(self, 'memory_ids', [str(value).strip() for value in self.memory_ids if str(value).strip()])
        if action == 'update_type' and (not self.memory_ids or not self.memory_type):
            raise ValueError('update_type requires exact memory id and memory_type')

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetadataEnrichmentPlan:
    actions: list[MetadataEnrichmentAction]
    dry_run: bool = True
    skipped_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'job': 'metadata_enrichment',
            'dry_run': self.dry_run,
            'skipped_rows': self.skipped_rows,
            'summary': summarize_actions(self.actions),
            'actions': [action.to_dict() for action in self.actions],
        }


def normalize_values(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def memory_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    return memory.get('metadata') if isinstance(memory.get('metadata'), dict) else {}


def memory_domains(memory: dict[str, Any]) -> set[str]:
    metadata = memory_metadata(memory)
    return normalize_values(metadata.get('domains') or memory.get('domains'))


def memory_type(memory: dict[str, Any]) -> str:
    metadata = memory_metadata(memory)
    return str(metadata.get('memory_type') or memory.get('memory_type') or '').strip().lower()


def memory_text(memory: dict[str, Any]) -> str:
    return str(memory.get('text') or memory.get('memory') or memory.get('content') or '')


def memory_id(memory: dict[str, Any]) -> str:
    return str(memory.get('id') or '').strip()


def contains_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def proposed_memory_type(memory: dict[str, Any]) -> tuple[str, str]:
    text = memory_text(memory).lower()
    domains = memory_domains(memory)
    current_type = memory_type(memory)
    if not (domains & TARGET_DOMAINS):
        return current_type, 'outside target domains'
    if current_type in SPECIFIC_TYPES:
        return current_type, 'memory_type already specific'
    if current_type not in WEAK_TYPES:
        return current_type, 'memory_type not weak enough for deterministic update'
    if contains_any(text, CLINICAL_TERMS):
        return 'clinical_context', 'clinical/psychiatric terms detected'
    if contains_any(text, ATTACHMENT_TERMS):
        return 'relationship_pattern', 'attachment/escalation relationship terms detected'
    if contains_any(text, FORMATIVE_TERMS):
        return 'formative_event', 'family-origin formative terms detected'
    if contains_any(text, IDENTITY_TERMS):
        return 'identity_pattern', 'identity/nervous-system terms detected'
    if contains_any(text, RELATIONSHIP_TERMS):
        return 'relationship_pattern', 'relationship/person terms detected'
    if current_type == 'person':
        return 'person_context', 'fallback person context'
    return current_type, 'no deterministic type signal'


def plan_memory_metadata(memory: dict[str, Any]) -> MetadataEnrichmentAction:
    mid = memory_id(memory)
    current_type = memory_type(memory)
    if not mid:
        return MetadataEnrichmentAction('keep', [], reason='missing memory id', current_memory_type=current_type)
    proposal, reason = proposed_memory_type(memory)
    if proposal and proposal != current_type:
        return MetadataEnrichmentAction('update_type', [mid], reason=reason, memory_type=proposal, current_memory_type=current_type)
    return MetadataEnrichmentAction('keep', [mid], reason=reason, current_memory_type=current_type)


def build_metadata_enrichment_plan(memories: list[dict[str, Any]]) -> MetadataEnrichmentPlan:
    return MetadataEnrichmentPlan(actions=[plan_memory_metadata(memory) for memory in memories if isinstance(memory, dict)])


def summarize_actions(actions: list[MetadataEnrichmentAction]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.action] = counts.get(action.action, 0) + 1
    return counts


def compact_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {'ok': bool(result)}
    return {key: value for key, value in result.items() if key not in {'before', 'after', 'response'}}


def load_memories_json(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    rows = payload.get('results') if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError('memories JSON must be a list or object with results list')
    return [row for row in rows if isinstance(row, dict)]


def execute_metadata_enrichment_plan(
    plan: MetadataEnrichmentPlan,
    client: Any,
    rollback_log: str | Path,
    maintenance_log: str | Path,
    max_updates: int | None = None,
) -> dict[str, Any]:
    update_count = sum(1 for action in plan.actions if action.action == 'update_type')
    if update_count and max_updates is None:
        raise ValueError('max_updates is required when metadata updates exist')
    if max_updates is not None and update_count > max_updates:
        raise ValueError('metadata update action count exceeds max_updates')
    rollback = AuditLedger(rollback_log)
    maintenance = AuditLedger(maintenance_log)
    counts = {'kept': 0, 'updated': 0, 'failed': 0}
    results = []
    for action in plan.actions:
        try:
            if action.action != 'update_type':
                row = maintenance.append({'job': 'metadata_enrichment', 'action': 'keep', 'memory_ids': action.memory_ids, 'reason': action.reason})
                counts['kept'] += 1
                results.append({'action': 'keep', 'audit_ts': row['ts']})
                continue
            before = client.fetch(action.memory_ids[0]) if hasattr(client, 'fetch') else client.request('GET', f'/memories/{action.memory_ids[0]}')
            rollback.append({'job': 'metadata_enrichment', 'action': action.action, 'memory_id': before.get('id'), 'before': before, 'reason': action.reason})
            result = client.update(
                action.memory_ids[0],
                memory_text(before),
                reason=action.reason,
                domains=list(memory_domains(before)) or None,
                memory_type=action.memory_type,
                signal_strength=memory_metadata(before).get('signal_strength'),
                current_status=memory_metadata(before).get('current_status'),
                memory_tier=memory_metadata(before).get('memory_tier'),
            )
            row = maintenance.append({'job': 'metadata_enrichment', 'action': action.action, 'memory_ids': action.memory_ids, 'memory_type': action.memory_type, 'reason': action.reason, 'result': compact_result(result)})
            counts['updated'] += 1
            results.append({'action': action.action, 'audit_ts': row['ts'], 'result': compact_result(result)})
        except Exception as exc:
            counts['failed'] += 1
            results.append({'action': action.action, 'memory_ids': action.memory_ids, 'error': str(exc)})
    return {'job': 'metadata_enrichment', 'counts': counts, 'results': results}
