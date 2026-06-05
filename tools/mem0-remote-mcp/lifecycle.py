
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit_ledger import AuditLedger
from retrieval import normalize_memory_tier
from retrieval_telemetry import read_recent_jsonl


MUTATING_ACTIONS = {'downgrade_tier', 'promote_tier', 'update'}
PROTECTED_DOMAINS = {
    'family',
    'family_origin',
    'identity',
    'opera',
    'personal_life',
    'projects',
    'psychology',
    'relationships',
    'systems',
}
PROTECTED_MEMORY_TYPES = {
    'architecture_decision',
    'career_context',
    'decision',
    'dossier',
    'formative_event',
    'identity_pattern',
    'project_state',
    'relationship_pattern',
}


@dataclass(frozen=True)
class LifecyclePolicy:
    active_to_historical_epochs: int = 200
    historical_to_cold_epochs: int = 1000
    promote_after_access_count: int = 20
    protect_signal_strength_at_or_above: float = 8.0
    protected_domains: set[str] = field(default_factory=lambda: set(PROTECTED_DOMAINS))
    protected_memory_types: set[str] = field(default_factory=lambda: set(PROTECTED_MEMORY_TYPES))


@dataclass(frozen=True)
class RetrievalAccessIndex:
    current_epoch: int
    last_seen_by_id: dict[str, int]
    access_counts: dict[str, int]

    def access_count(self, memory_id: str) -> int:
        return int(self.access_counts.get(str(memory_id), 0))

    def last_seen_epoch(self, memory_id: str) -> int | None:
        return self.last_seen_by_id.get(str(memory_id))

    def epochs_since_access(self, memory_id: str) -> int:
        last_seen = self.last_seen_epoch(memory_id)
        if last_seen is None:
            return self.current_epoch
        return max(self.current_epoch - last_seen, 0)


@dataclass(frozen=True)
class LifecycleAction:
    action: str
    memory_ids: list[str]
    reason: str
    memory_tier: str = ''
    current_status: str = ''
    access_count: int = 0
    epochs_since_access: int = 0

    def __post_init__(self):
        action = str(self.action or 'keep').strip().lower()
        object.__setattr__(self, 'action', action)
        object.__setattr__(self, 'memory_ids', stable_unique([str(memory_id).strip() for memory_id in self.memory_ids if str(memory_id).strip()]))
        if action in MUTATING_ACTIONS and not self.memory_ids:
            raise ValueError(f'{action} requires an exact memory id')

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LifecyclePlan:
    job: str
    actions: list[LifecycleAction]
    dry_run: bool = True
    skipped_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'job': self.job,
            'dry_run': self.dry_run,
            'skipped_rows': self.skipped_rows,
            'actions': [action.to_dict() for action in self.actions],
            'summary': summarize_actions(self.actions),
        }


def stable_unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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


def memory_text(memory: dict[str, Any]) -> str:
    return str(memory.get('text') or memory.get('memory') or memory.get('content') or '')


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def memory_id(memory: dict[str, Any]) -> str:
    return str(memory.get('id') or '').strip()


def is_protected_memory(memory: dict[str, Any], policy: LifecyclePolicy) -> bool:
    if signal_strength(memory) >= policy.protect_signal_strength_at_or_above:
        return True
    if memory_domains(memory) & {domain.lower() for domain in policy.protected_domains}:
        return True
    if memory_type(memory) in {value.lower() for value in policy.protected_memory_types}:
        return True
    return False


def build_access_index(telemetry_log: str | Path, recent_limit: int = 20000) -> RetrievalAccessIndex:
    rows, _ = read_recent_jsonl(telemetry_log, recent_limit=recent_limit)
    current_epoch = 0
    last_seen_by_id: dict[str, int] = {}
    access_counts: dict[str, int] = {}
    for row in rows:
        if str(row.get('event') or 'search').strip().lower() != 'search':
            continue
        current_epoch += 1
        for result in row.get('results') or []:
            if not isinstance(result, dict):
                continue
            result_id = str(result.get('id') or '').strip()
            if not result_id:
                continue
            access_counts[result_id] = access_counts.get(result_id, 0) + 1
            last_seen_by_id[result_id] = current_epoch
    return RetrievalAccessIndex(current_epoch=current_epoch, last_seen_by_id=last_seen_by_id, access_counts=access_counts)


def build_decay_plan(
    memories: list[dict[str, Any]],
    access_index: RetrievalAccessIndex,
    policy: LifecyclePolicy | None = None,
) -> LifecyclePlan:
    policy = policy or LifecyclePolicy()
    actions = [plan_memory_decay(memory, access_index, policy) for memory in memories if memory_id(memory)]
    return LifecyclePlan(job='decay_low_use', actions=actions, dry_run=True)


def plan_memory_decay(memory: dict[str, Any], access_index: RetrievalAccessIndex, policy: LifecyclePolicy) -> LifecycleAction:
    mid = memory_id(memory)
    tier = memory_tier(memory)
    status = current_status(memory)
    access_count = access_index.access_count(mid)
    epochs_since = access_index.epochs_since_access(mid)

    if tier in {'historical', 'cold'} and access_count >= policy.promote_after_access_count:
        return LifecycleAction(
            'promote_tier',
            [mid],
            reason=f'frequently retrieved across {access_count} retrieval events',
            memory_tier='active' if tier == 'historical' else 'historical',
            current_status='active' if tier == 'historical' else (status or 'unknown'),
            access_count=access_count,
            epochs_since_access=epochs_since,
        )

    if is_protected_memory(memory, policy):
        return LifecycleAction('keep', [mid], reason='protected memory domain/type/signal', access_count=access_count, epochs_since_access=epochs_since)

    if tier == 'active':
        if epochs_since < policy.active_to_historical_epochs:
            return LifecycleAction('keep', [mid], reason='below epoch threshold for active decay', access_count=access_count, epochs_since_access=epochs_since)
        return LifecycleAction(
            'downgrade_tier',
            [mid],
            reason=f'low-use active memory unseen for {epochs_since} retrieval epochs',
            memory_tier='historical',
            current_status=status or 'unknown',
            access_count=access_count,
            epochs_since_access=epochs_since,
        )

    if tier == 'historical':
        if epochs_since < policy.historical_to_cold_epochs:
            return LifecycleAction('keep', [mid], reason='below epoch threshold for historical decay', access_count=access_count, epochs_since_access=epochs_since)
        return LifecycleAction(
            'downgrade_tier',
            [mid],
            reason=f'low-use historical memory unseen for {epochs_since} retrieval epochs',
            memory_tier='cold',
            current_status=status or 'unknown',
            access_count=access_count,
            epochs_since_access=epochs_since,
        )

    return LifecycleAction('keep', [mid], reason='no lifecycle change needed', access_count=access_count, epochs_since_access=epochs_since)


def summarize_actions(actions: list[LifecycleAction]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.action] = counts.get(action.action, 0) + 1
    return counts


def execute_lifecycle_plan(
    plan: LifecyclePlan,
    client: Any,
    rollback_log: str | Path,
    maintenance_log: str | Path,
    max_updates: int | None = None,
) -> dict[str, Any]:
    update_count = sum(1 for action in plan.actions if action.action in MUTATING_ACTIONS)
    if update_count and max_updates is None:
        raise ValueError('max_updates is required when lifecycle update actions exist')
    if max_updates is not None and update_count > max_updates:
        raise ValueError('lifecycle update action count exceeds max_updates')

    rollback = AuditLedger(rollback_log)
    maintenance = AuditLedger(maintenance_log)
    counts = {'kept': 0, 'downgraded': 0, 'promoted': 0, 'updated': 0, 'ask_user': 0, 'failed': 0}
    results = []
    for action in plan.actions:
        try:
            result = execute_lifecycle_action(action, client, rollback, maintenance)
            results.append(result)
            increment_count(counts, action.action)
        except Exception as exc:
            counts['failed'] += 1
            results.append({'action': action.action, 'memory_ids': action.memory_ids, 'error': str(exc)})
    return {'job': plan.job, 'counts': counts, 'results': results}


def increment_count(counts: dict[str, int], action: str) -> None:
    key = {
        'keep': 'kept',
        'downgrade_tier': 'downgraded',
        'promote_tier': 'promoted',
        'update': 'updated',
        'ask_user': 'ask_user',
    }.get(action, 'failed')
    counts[key] += 1


def execute_lifecycle_action(action: LifecycleAction, client: Any, rollback: AuditLedger, maintenance: AuditLedger) -> dict[str, Any]:
    if action.action in {'keep', 'ask_user'}:
        row = maintenance.append({'job': 'lifecycle', 'action': action.action, 'memory_ids': action.memory_ids, 'reason': action.reason})
        return {'action': action.action, 'audit_ts': row['ts']}

    before = client.fetch(action.memory_ids[0]) if hasattr(client, 'fetch') else client.request('GET', f'/memories/{action.memory_ids[0]}')
    rollback.append({'job': 'lifecycle', 'action': action.action, 'memory_id': before.get('id'), 'before': before, 'reason': action.reason})

    if action.action in {'downgrade_tier', 'promote_tier'}:
        previous_tier = memory_tier(before)
        previous_status = current_status(before) or 'unknown'
        result = client.update(
            action.memory_ids[0],
            memory_text(before),
            reason=action.reason,
            memory_tier=action.memory_tier,
            current_status=action.current_status or previous_status,
            metadata_extra={
                'lifecycle_action': action.action,
                'lifecycle_updated_at': utc_now_iso(),
                'previous_memory_tier': previous_tier,
                'previous_current_status': previous_status,
                'retrieval_access_count': action.access_count,
                'retrieval_epochs_since_access': action.epochs_since_access,
            },
        )
    else:
        result = {'skipped': True}
    row = maintenance.append({'job': 'lifecycle', 'action': action.action, 'memory_ids': action.memory_ids, 'reason': action.reason, 'result': compact_result(result)})
    return {'action': action.action, 'audit_ts': row['ts'], 'result': result}


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
