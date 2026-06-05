from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit_ledger import AuditLedger


ALLOWED_ACTIONS = {'keep', 'update', 'delete', 'merge', 'downgrade_tier', 'ask_user'}
MUTATING_ACTIONS = {'update', 'delete', 'downgrade_tier'}


@dataclass(frozen=True)
class FlagCandidate:
    flag_type: str
    confidence: float
    origin: str
    reason: str
    ts: str
    memory_id: str = ''
    memory_ids: list[str] = field(default_factory=list)
    content: str = ''

    def all_memory_ids(self) -> list[str]:
        ids = [self.memory_id, *self.memory_ids]
        return stable_unique([memory_id for memory_id in ids if memory_id])


@dataclass(frozen=True)
class MaintenanceGroup:
    key: str
    candidates: list[FlagCandidate]
    memory_ids: list[str]

    @property
    def flag_types(self) -> set[str]:
        return {candidate.flag_type for candidate in self.candidates}

    @property
    def max_confidence(self) -> float:
        return max((candidate.confidence for candidate in self.candidates), default=0.0)

    @property
    def origins(self) -> set[str]:
        return {candidate.origin for candidate in self.candidates if candidate.origin}


@dataclass(frozen=True)
class MaintenanceAction:
    action: str
    memory_ids: list[str]
    reason: str
    content: str = ''
    memory_tier: str = ''
    current_status: str = ''
    group_key: str = ''

    def __post_init__(self):
        action = normalize_action(self.action)
        object.__setattr__(self, 'action', action)
        object.__setattr__(self, 'memory_ids', stable_unique([str(memory_id).strip() for memory_id in self.memory_ids if str(memory_id).strip()]))
        if action in MUTATING_ACTIONS and not self.memory_ids:
            raise ValueError(f'{action} requires an exact memory id')
        if action == 'merge' and len(self.memory_ids) < 2:
            raise ValueError('merge requires at least two exact memory ids')

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MaintenancePlan:
    actions: list[MaintenanceAction]
    skipped_rows: int = 0
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {'dry_run': self.dry_run, 'skipped_rows': self.skipped_rows, 'actions': [action.to_dict() for action in self.actions]}


def normalize_action(value: Any) -> str:
    action = str(value or 'keep').strip().lower()
    return action if action in ALLOWED_ACTIONS else 'ask_user'


def normalize_text(value: Any) -> str:
    return ' '.join(str(value or '').strip().split())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def memory_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    metadata = memory.get('metadata') if isinstance(memory.get('metadata'), dict) else {}
    return dict(metadata)


def memory_tier(memory: dict[str, Any]) -> str:
    metadata = memory_metadata(memory)
    return normalize_text(metadata.get('memory_tier') or memory.get('memory_tier') or 'active') or 'active'


def current_status(memory: dict[str, Any]) -> str:
    metadata = memory_metadata(memory)
    return normalize_text(metadata.get('current_status') or memory.get('current_status') or 'unknown') or 'unknown'


def normalize_content_key(value: Any) -> str:
    return normalize_text(value).lower()


def stable_unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


def candidate_from_row(row: dict[str, Any]) -> FlagCandidate:
    proposal = row.get('proposal') if isinstance(row.get('proposal'), dict) else {}
    memory_ids = row.get('memory_ids')
    if not isinstance(memory_ids, list):
        memory_ids = proposal.get('memory_ids') if isinstance(proposal.get('memory_ids'), list) else []
    memory_id = normalize_text(row.get('memory_id') or proposal.get('existing_id'))
    content = normalize_text(proposal.get('content') or row.get('content'))
    return FlagCandidate(
        flag_type=normalize_text(row.get('flag_type') or proposal.get('flag_type') or 'delete_candidate').lower(),
        confidence=clamp_confidence(row.get('confidence') if row.get('confidence') is not None else proposal.get('confidence')),
        origin=normalize_text(row.get('origin')).lower(),
        reason=normalize_text(row.get('reason') or proposal.get('reason')),
        ts=normalize_text(row.get('ts')),
        memory_id=memory_id,
        memory_ids=[normalize_text(memory_id) for memory_id in memory_ids if normalize_text(memory_id)],
        content=content,
    )


def group_key_for(candidate: FlagCandidate) -> str:
    ids = candidate.all_memory_ids()
    if ids:
        return 'id:' + '|'.join(ids)
    content = normalize_content_key(candidate.content)
    return f'content:{content}' if content else f'flag:{candidate.flag_type}:{candidate.reason}'


def nested_maintenance_group_key(row: dict[str, Any]) -> str:
    direct = normalize_text(row.get('group_key'))
    if direct:
        return direct
    result = row.get('result') if isinstance(row.get('result'), dict) else {}
    after = result.get('after') if isinstance(result.get('after'), dict) else {}
    metadata = after.get('metadata') if isinstance(after.get('metadata'), dict) else {}
    return normalize_text(metadata.get('maintenance_group_key'))


def load_maintained_group_keys(path: str | Path, limit: int = 5000) -> set[str]:
    rows, _ = read_recent_rows_with_skipped(path, limit=limit)
    return {key for row in rows if (key := nested_maintenance_group_key(row))}


def parse_audit_flags(
    path: str | Path,
    limit: int = 5000,
    maintained_group_keys: set[str] | None = None,
) -> tuple[list[MaintenanceGroup], int]:
    rows, skipped = read_recent_rows_with_skipped(path, limit=limit)
    maintained = maintained_group_keys or set()
    grouped: dict[str, list[FlagCandidate]] = {}
    for row in rows:
        if str(row.get('action') or '').strip().lower() != 'flag':
            continue
        try:
            candidate = candidate_from_row(row)
        except Exception:
            skipped += 1
            continue
        key = group_key_for(candidate)
        if key in maintained:
            continue
        grouped.setdefault(key, []).append(candidate)
    groups = []
    for key, candidates in grouped.items():
        ids: list[str] = []
        for candidate in candidates:
            ids.extend(candidate.all_memory_ids())
        groups.append(MaintenanceGroup(key=key, candidates=candidates, memory_ids=stable_unique(ids)))
    groups.sort(key=lambda group: group.key)
    return groups, skipped


def read_recent_rows_with_skipped(path: str | Path, limit: int = 5000) -> tuple[list[dict[str, Any]], int]:
    path = Path(path)
    if not path.exists():
        return [], 0
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return [], 0
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in lines[-max(int(limit or 0), 0):]:
        try:
            row = json.loads(line.lstrip('\ufeff'))
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(row, dict):
            skipped += 1
            continue
        rows.append(row)
    return rows, skipped


def plan_group(group: MaintenanceGroup) -> MaintenanceAction:
    flag_types = group.flag_types
    max_confidence = group.max_confidence
    repeated = len(group.candidates) > 1 or len(group.origins) > 1
    reason = '; '.join(stable_unique([candidate.reason for candidate in group.candidates if candidate.reason])) or 'maintenance flag'
    ids = group.memory_ids
    if 'conflict_candidate' in flag_types:
        return MaintenanceAction('ask_user', ids, reason=f'conflict requires review: {reason}', group_key=group.key)
    if 'merge_candidate' in flag_types:
        if len(ids) >= 2:
            return MaintenanceAction('merge', ids, reason=f'merge candidate: {reason}', group_key=group.key)
        return MaintenanceAction('ask_user', ids, reason=f'merge candidate lacks exact ids: {reason}', group_key=group.key)
    if 'stale_candidate' in flag_types or 'stale' in flag_types:
        if ids:
            return MaintenanceAction('downgrade_tier', ids[:1], reason=f'stale candidate: {reason}', memory_tier='historical', current_status='unknown', group_key=group.key)
        return MaintenanceAction('ask_user', [], reason=f'stale candidate lacks exact id: {reason}', group_key=group.key)
    if 'possible_correction' in flag_types:
        contents = stable_unique([candidate.content for candidate in group.candidates if candidate.content])
        if len(ids) == 1 and len(contents) == 1:
            return MaintenanceAction('update', ids, reason=f'possible correction: {reason}', content=contents[0], group_key=group.key)
        return MaintenanceAction('ask_user', ids[:1], reason=f'possible correction needs one exact id and corrected content: {reason}', group_key=group.key)
    if 'delete_candidate' in flag_types:
        if ids and (max_confidence >= 0.85 or repeated):
            return MaintenanceAction('delete', ids[:1], reason=f'delete candidate: {reason}', group_key=group.key)
        return MaintenanceAction('keep', ids, reason=f'weak delete candidate: {reason}', group_key=group.key)
    return MaintenanceAction('keep', ids, reason=f'unsupported flag type: {reason}', group_key=group.key)


def build_maintenance_plan(groups: list[MaintenanceGroup], skipped_rows: int = 0) -> MaintenancePlan:
    return MaintenancePlan(actions=[plan_group(group) for group in groups], skipped_rows=skipped_rows, dry_run=True)


def build_llm_pack(groups: list[MaintenanceGroup], memories: dict[str, dict[str, Any]]) -> dict[str, Any]:
    packed_groups = []
    for group in groups:
        packed_groups.append(
            {
                'group_key': group.key,
                'memory_ids': group.memory_ids,
                'memories': [safe_memory_for_pack(memories[memory_id]) for memory_id in group.memory_ids if memory_id in memories],
                'flags': [
                    {
                        'flag_type': candidate.flag_type,
                        'confidence': candidate.confidence,
                        'origin': candidate.origin,
                        'reason': candidate.reason,
                        'ts': candidate.ts,
                    }
                    for candidate in group.candidates
                ],
            }
        )
    return {'groups': packed_groups, 'allowed_actions': sorted(ALLOWED_ACTIONS)}


def safe_memory_for_pack(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': memory.get('id'),
        'text': memory.get('text') or memory.get('memory') or '',
        'metadata': memory.get('metadata') or {},
    }


def parse_llm_actions(raw: str, groups: list[MaintenanceGroup]) -> list[MaintenanceAction]:
    valid_ids = {memory_id for group in groups for memory_id in group.memory_ids}
    try:
        payload = json.loads(raw)
        rows = payload.get('actions') if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        rows = None
    if not isinstance(rows, list):
        return [MaintenanceAction('ask_user', [], reason='invalid LLM actions JSON')]
    actions = []
    for row in rows:
        if not isinstance(row, dict):
            actions.append(MaintenanceAction('ask_user', [], reason='invalid LLM action row'))
            continue
        ids = [normalize_text(memory_id) for memory_id in row.get('memory_ids') or [] if normalize_text(memory_id)]
        if any(memory_id not in valid_ids for memory_id in ids):
            actions.append(MaintenanceAction('ask_user', [], reason='invalid LLM action: unknown exact memory id'))
            continue
        try:
            actions.append(
                MaintenanceAction(
                    row.get('action'),
                    ids,
                    reason=normalize_text(row.get('reason')) or 'LLM maintenance action',
                    content=normalize_text(row.get('content')),
                    memory_tier=normalize_text(row.get('memory_tier')),
                    current_status=normalize_text(row.get('current_status')),
                )
            )
        except ValueError as exc:
            actions.append(MaintenanceAction('ask_user', ids, reason=f'invalid LLM action: {exc}'))
    return actions


def execute_maintenance_plan(
    plan: MaintenancePlan,
    client: Any,
    rollback_log: str | Path,
    maintenance_log: str | Path,
    max_deletes: int | None = None,
) -> dict[str, Any]:
    delete_count = sum(1 for action in plan.actions if action.action == 'delete')
    if delete_count and max_deletes is None:
        raise ValueError('max_deletes is required when delete actions exist')
    if max_deletes is not None and delete_count > max_deletes:
        raise ValueError('delete action count exceeds max_deletes')
    rollback = AuditLedger(rollback_log)
    maintenance = AuditLedger(maintenance_log)
    counts = {'kept': 0, 'updated': 0, 'deleted': 0, 'merged': 0, 'downgraded': 0, 'ask_user': 0, 'failed': 0}
    results = []
    for action in plan.actions:
        try:
            result = execute_action(action, client, rollback, maintenance)
            results.append(result)
            increment_count(counts, action.action)
        except Exception as exc:
            counts['failed'] += 1
            results.append({'action': action.action, 'memory_ids': action.memory_ids, 'error': str(exc)})
    return {'counts': counts, 'results': results}


def increment_count(counts: dict[str, int], action: str) -> None:
    key = {
        'keep': 'kept',
        'update': 'updated',
        'delete': 'deleted',
        'merge': 'merged',
        'downgrade_tier': 'downgraded',
        'ask_user': 'ask_user',
    }.get(action, 'failed')
    counts[key] += 1


def execute_action(action: MaintenanceAction, client: Any, rollback: AuditLedger, maintenance: AuditLedger) -> dict[str, Any]:
    if action.action in {'keep', 'ask_user'}:
        row = maintenance.append({'action': action.action, 'memory_ids': action.memory_ids, 'reason': action.reason, 'group_key': action.group_key})
        return {'action': action.action, 'audit_ts': row['ts']}
    before_records = [client.fetch(memory_id) if hasattr(client, 'fetch') else client.request('GET', f'/memories/{memory_id}') for memory_id in action.memory_ids]
    for before in before_records:
        rollback.append({'action': action.action, 'memory_id': before.get('id'), 'before': before, 'reason': action.reason})
    if action.action == 'delete':
        result = client.delete(action.memory_ids[0], action.reason)
    elif action.action == 'downgrade_tier':
        before = before_records[0]
        result = client.update(
            action.memory_ids[0],
            before.get('text') or before.get('memory') or '',
            reason=action.reason,
            memory_tier=action.memory_tier or 'historical',
            current_status=action.current_status or 'unknown',
            metadata_extra={
                'downgraded_at': utc_now_iso(),
                'downgraded_by': 'dream_maintenance',
                'previous_memory_tier': memory_tier(before),
                'previous_current_status': current_status(before),
                'stale_reason': action.reason,
                'maintenance_group_key': action.group_key or None,
            },
        )
    elif action.action == 'update':
        result = client.update(action.memory_ids[0], action.content, reason=action.reason)
    elif action.action == 'merge':
        content = action.content or '\n'.join(before.get('text') or before.get('memory') or '' for before in before_records)
        result = client.update(action.memory_ids[0], content, reason=action.reason)
    else:
        result = {'skipped': True}
    row = maintenance.append({'action': action.action, 'memory_ids': action.memory_ids, 'reason': action.reason, 'group_key': action.group_key, 'result': result})
    return {'action': action.action, 'audit_ts': row['ts'], 'result': result}
