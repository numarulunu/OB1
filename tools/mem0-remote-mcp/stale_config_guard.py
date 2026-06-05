from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


STALE_MCP_SECTION = re.compile(r'^\s*\[mcp_servers\.(kontext|tokenomy)\]\s*$', re.IGNORECASE | re.MULTILINE)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError:
        return path.read_text(encoding='utf-8', errors='replace')


def is_codex_config(path: Path) -> bool:
    return path.name.lower() == 'config.toml' and '.codex' in str(path).lower() or path.name.lower() == 'config.toml'


def is_mcp_config(path: Path) -> bool:
    return path.name.lower() in {'.mcp.json', 'mcp.json'}


def is_hook_config(path: Path) -> bool:
    return path.name.lower() in {'hooks.json', 'settings.json'}


def check_one(path: str | Path) -> list[dict[str, str]]:
    path = Path(path)
    errors: list[dict[str, str]] = []
    if not path.exists():
        errors.append({'path': str(path), 'issue': 'missing file'})
        return errors
    text = read_text(path)
    lowered = text.lower()
    if is_codex_config(path):
        if re.search(r'\bcodex_hooks\b', text, re.IGNORECASE):
            errors.append({'path': str(path), 'issue': 'deprecated codex_hooks feature'})
        for match in STALE_MCP_SECTION.finditer(text):
            errors.append({'path': str(path), 'issue': f'active {match.group(1).lower()} MCP server'})
        if '[features]' in lowered and not re.search(r'(?m)^\s*hooks\s*=\s*true\s*$', text, re.IGNORECASE):
            errors.append({'path': str(path), 'issue': 'missing [features].hooks = true'})
        if 'mcp_servers.mem0' not in lowered:
            errors.append({'path': str(path), 'issue': 'missing mem0 MCP server'})
    if is_mcp_config(path):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            errors.append({'path': str(path), 'issue': 'invalid JSON'})
        else:
            servers = payload.get('mcpServers') if isinstance(payload, dict) else None
            if not isinstance(servers, dict) or 'mem0' not in servers:
                errors.append({'path': str(path), 'issue': 'missing mem0 MCP server'})
            extra_servers = sorted([name for name in (servers or {}) if str(name).lower() != 'mem0'])
            if extra_servers:
                errors.append({'path': str(path), 'issue': f'extra MCP servers: {", ".join(extra_servers)}'})
    if is_hook_config(path):
        if re.search(r'\bkontext\b', text, re.IGNORECASE):
            errors.append({'path': str(path), 'issue': 'active kontext hook reference'})
        if re.search(r'\btokenomy\b', text, re.IGNORECASE):
            errors.append({'path': str(path), 'issue': 'active tokenomy hook reference'})
        if 'mem0_context_hook' not in lowered:
            errors.append({'path': str(path), 'issue': 'missing mem0_context_hook'})
    return errors


def check_config_files(paths: list[str | Path]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    checked = []
    for path in paths:
        checked.append(str(path))
        errors.extend(check_one(path))
    return {'ok': not errors, 'checked': checked, 'errors': errors}


def format_report(report: dict[str, Any]) -> str:
    lines = ['Mem0 stale config guard', f"ok: {str(report.get('ok')).lower()}", f"checked: {len(report.get('checked') or [])}"]
    for error in report.get('errors') or []:
        lines.append(f"{error.get('path')}: {error.get('issue')}")
    return '\n'.join(lines) + '\n'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Fail if active client configs regress to stale memory hooks or MCP servers.')
    parser.add_argument('--path', action='append', default=[], help='Config file to scan, repeatable')
    parser.add_argument('--json', action='store_true', help='Print JSON report')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = check_config_files(args.path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(format_report(report), end='')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
