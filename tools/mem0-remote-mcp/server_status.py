from __future__ import annotations

from pathlib import Path
from typing import Any


def audit_log_is_writable(path: str | Path) -> bool:
    audit_path = Path(path)
    if audit_path.exists() and audit_path.is_dir():
        return False
    parent = audit_path.parent if audit_path.parent != Path('') else Path('.')
    if not parent.exists() or not parent.is_dir():
        return False
    try:
        with audit_path.open('a', encoding='utf-8'):
            pass
    except OSError:
        return False
    return True


def health_payload(
    profiles: list[str],
    ingestion_enabled: bool,
    audit_log: str | Path,
    profile_permissions: dict[str, dict[str, bool]] | None = None,
    runtime_enabled: bool = True,
) -> dict[str, Any]:
    permissions = profile_permissions or {}
    writes_enabled = any(bool(row.get('can_write')) for row in permissions.values())
    return {
        'ok': True,
        'service': 'ionut-memory-mcp',
        'profiles': profiles,
        'profile_permissions': permissions,
        'runtime': {'enabled': bool(runtime_enabled)},
        'writes': {'enabled': writes_enabled},
        'ingestion': {'enabled': bool(ingestion_enabled)},
        'audit': {'writable': audit_log_is_writable(audit_log)},
    }
