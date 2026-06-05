from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from retrieval_telemetry import build_retrieval_stats
from hook_heartbeat import build_hook_stats
from dream_maintenance import candidate_from_row, group_key_for, load_maintained_group_keys


SUCCESS_ACTIONS = {'save', 'update'}
MAINTENANCE_FLAG_THRESHOLDS = {
    'conflict_candidate': 1,
    'merge_candidate': 3,
    'stale_candidate': 5,
    'delete_candidate': 5,
}



@dataclass
class AuditStats:
    rows_seen: int = 0
    malformed_rows: int = 0
    actions: dict[str, int] = field(default_factory=dict)
    origins: dict[str, int] = field(default_factory=dict)
    flag_types: dict[str, int] = field(default_factory=dict)
    error_types: dict[str, int] = field(default_factory=dict)
    error_count: int = 0
    pending_flags: int = 0
    last_success_by_origin: dict[str, str] = field(default_factory=dict)
    last_error: dict[str, str] | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MaintenanceStatus:
    due: bool = False
    pending_flags: int = 0
    flag_types: dict[str, int] = field(default_factory=dict)
    thresholds: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    requires_approval: bool = True
    dry_run_command: str = ''
    execute_hint: str = ''
    reminder: str = ''

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_recent_jsonl(path: str | Path, recent_limit: int = 5000) -> tuple[list[dict[str, Any]], int]:
    path = Path(path)
    if not path.exists():
        return [], 0
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return [], 0
    parsed: list[dict[str, Any]] = []
    malformed = 0
    for line in lines[-max(int(recent_limit or 0), 0):]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(row, dict):
            parsed.append(row)
        else:
            malformed += 1
    return parsed, malformed


def classify_error(message: str) -> str:
    lowered = str(message or '').lower()
    if 'timeout' in lowered or 'timed out' in lowered:
        return 'timeout'
    if '429' in lowered or 'rate limit' in lowered:
        return 'rate_limit'
    if 'json' in lowered or 'malformed' in lowered:
        return 'malformed_json'
    if 'api key' in lowered or 'unauthorized' in lowered or '401' in lowered or '403' in lowered:
        return 'auth'
    if not lowered.strip():
        return 'unknown'
    return 'other'


def row_errors(row: dict[str, Any]) -> list[str]:
    errors = row.get('errors')
    if isinstance(errors, list):
        return [str(error)[:160] for error in errors if str(error).strip()]
    if isinstance(errors, str) and errors.strip():
        return [errors[:160]]
    error = row.get('error')
    if isinstance(error, str) and error.strip():
        return [error[:160]]
    return []


def extract_usage(row: dict[str, Any]) -> dict[str, int]:
    llm = row.get('llm') if isinstance(row.get('llm'), dict) else {}
    usage = llm.get('usage') if isinstance(llm.get('usage'), dict) else row.get('usage')
    if not isinstance(usage, dict):
        return {}
    result: dict[str, int] = {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        try:
            value = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            result[key] = value
    if result and 'total_tokens' not in result:
        total = result.get('prompt_tokens', 0) + result.get('completion_tokens', 0)
        if total:
            result['total_tokens'] = total
    return result


def build_audit_stats(
    path: str | Path,
    recent_limit: int = 5000,
    maintenance_log: str | Path | None = None,
) -> AuditStats:
    rows, malformed = read_recent_jsonl(path, recent_limit)
    maintained_group_keys = load_maintained_group_keys(maintenance_log, limit=recent_limit) if maintenance_log else set()
    actions: Counter[str] = Counter()
    origins: Counter[str] = Counter()
    flag_types: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    usage_totals: Counter[str] = Counter()
    last_success_by_origin: dict[str, str] = {}
    last_error: dict[str, str] | None = None
    error_count = 0

    for row in rows:
        action = str(row.get('action') or 'unknown').strip().lower() or 'unknown'
        origin = str(row.get('origin') or 'unknown').strip().lower() or 'unknown'
        ts = str(row.get('ts') or '')
        actions[action] += 1
        origins[origin] += 1
        if action == 'flag':
            try:
                candidate = candidate_from_row(row)
            except Exception:
                candidate = None
            if candidate is None or group_key_for(candidate) not in maintained_group_keys:
                flag_type = str(row.get('flag_type') or 'unknown').strip().lower() or 'unknown'
                flag_types[flag_type] += 1
        if action in SUCCESS_ACTIONS and ts:
            last_success_by_origin[origin] = ts
        for message in row_errors(row):
            error_count += 1
            error_types[classify_error(message)] += 1
            last_error = {'ts': ts, 'origin': origin, 'message': message}
        for key, value in extract_usage(row).items():
            usage_totals[key] += value

    return AuditStats(
        rows_seen=len(rows),
        malformed_rows=malformed,
        actions=dict(actions),
        origins=dict(origins),
        flag_types=dict(flag_types),
        error_types=dict(error_types),
        error_count=error_count,
        pending_flags=sum(flag_types.values()),
        last_success_by_origin=last_success_by_origin,
        last_error=last_error,
        usage=dict(usage_totals),
    )


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_maintenance_status(
    stats: AuditStats,
    audit_log: str | Path = '<audit-log>',
    pending_flag_threshold: int = 10,
) -> MaintenanceStatus:
    threshold = max(safe_int(pending_flag_threshold, 10), 1)
    flag_types = {key: int(value) for key, value in sorted(stats.flag_types.items()) if int(value or 0) > 0}
    thresholds = {'pending_flags': threshold, **MAINTENANCE_FLAG_THRESHOLDS}
    reasons: list[str] = []

    if stats.pending_flags >= threshold:
        reasons.append('pending_flags_threshold')
    for flag_type, flag_threshold in MAINTENANCE_FLAG_THRESHOLDS.items():
        if flag_types.get(flag_type, 0) >= flag_threshold:
            reasons.append(f'{flag_type}_threshold')

    due = bool(reasons)
    dry_run_command = (
        'python tools/mem0-remote-mcp/dream_maintenance_cli.py '
        f'--audit-log {audit_log} --output mem0-dream-maintenance-plan.json'
    )
    execute_hint = 'Review the dry-run plan, then apply only with explicit approval and --max-deletes.'
    reminder = ''
    if due:
        summary = ', '.join(f'{key}={value}' for key, value in flag_types.items())
        suffix = f' ({summary})' if summary else ''
        reminder = (
            f'Mem0 maintenance due: {stats.pending_flags} pending flags{suffix}; '
            'ask Ionut before running dream cleanup. Start with the CLI dry-run; destructive apply needs approval.'
        )[:360]

    return MaintenanceStatus(
        due=due,
        pending_flags=stats.pending_flags,
        flag_types=flag_types,
        thresholds=thresholds,
        reasons=reasons,
        requires_approval=True,
        dry_run_command=dry_run_command,
        execute_hint=execute_hint,
        reminder=reminder,
    )


def build_status_payload(
    audit_log: str | Path,
    ingestion_enabled: bool,
    ingestion_model: str,
    profiles: list[str] | None = None,
    recent_limit: int = 5000,
    retrieval_log: str | Path | None = None,
    hook_log: str | Path | None = None,
    maintenance_flag_threshold: int = 10,
    maintenance_log: str | Path | None = None,
) -> dict[str, Any]:
    stats = build_audit_stats(audit_log, recent_limit=recent_limit, maintenance_log=maintenance_log)
    usage = dict(stats.usage)
    usage['cost_status'] = 'usage_tokens_only' if stats.usage else 'usage_unavailable'
    maintenance = build_maintenance_status(
        stats,
        audit_log=audit_log,
        pending_flag_threshold=maintenance_flag_threshold,
    )
    payload = {
        'ok': stats.error_count == 0,
        'service': 'ionut-memory-mcp',
        'profiles': profiles or [],
        'ingestion': {
            'enabled': bool(ingestion_enabled),
            'model': ingestion_model,
        },
        'audit': {
            'recent_limit': recent_limit,
            'rows_seen': stats.rows_seen,
            'malformed_rows': stats.malformed_rows,
            'actions': stats.actions,
            'origins': stats.origins,
            'flag_types': stats.flag_types,
            'pending_flags': stats.pending_flags,
            'error_count': stats.error_count,
            'error_types': stats.error_types,
            'last_success_by_origin': stats.last_success_by_origin,
            'last_error': stats.last_error,
        },
        'usage': usage,
        'maintenance': maintenance.to_dict(),
    }
    if retrieval_log:
        retrieval_stats = build_retrieval_stats(retrieval_log, recent_limit=recent_limit)
        payload['retrieval'] = {
            'recent_limit': recent_limit,
            'rows_seen': retrieval_stats.rows_seen,
            'malformed_rows': retrieval_stats.malformed_rows,
            'origins': retrieval_stats.origins,
            'result_count_total': retrieval_stats.result_count_total,
            'zero_result_count': retrieval_stats.zero_result_count,
            'avg_latency_ms': retrieval_stats.avg_latency_ms,
            'last_success_by_origin': retrieval_stats.last_success_by_origin,
            'top_result_ids': retrieval_stats.top_result_ids,
        }
    if hook_log:
        hook_stats = build_hook_stats(hook_log, recent_limit=recent_limit)
        payload['hooks'] = {
            'recent_limit': recent_limit,
            'rows_seen': hook_stats.rows_seen,
            'malformed_rows': hook_stats.malformed_rows,
            'origins': hook_stats.origins,
            'hook_types': hook_stats.hook_types,
            'last_seen_by_origin': hook_stats.last_seen_by_origin,
        }
    return payload
