from __future__ import annotations

import json
import os
import hashlib
import re
import sys
import time
import urllib.request
from pathlib import Path


DEFAULT_HEARTBEAT_THROTTLE_SECONDS = 600
SIGNIFICANT_COMMAND_CATEGORIES = {'test', 'build', 'deploy', 'git', 'file_edit'}
LOW_SIGNAL_COMMAND_CATEGORIES = {'file_read', 'other'}
KNOWN_HOOK_TYPES = {'session_start', 'user_prompt', 'post_compact', 'post_tool_use', 'session_end'}


def stdin_has_payload(timeout: float = 0.2) -> bool:
    if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
        return False
    if os.name == 'nt':
        return True
    try:
        import select

        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        return True
    return bool(ready)


def read_hook_input() -> dict:
    if not stdin_has_payload():
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=True, separators=(',', ':')))
    return 0


def codex_event_name(mode: str) -> str:
    return {
        'session_start': 'SessionStart',
        'user_prompt': 'UserPromptSubmit',
        'post_compact': 'PostCompact',
    }.get(mode, mode)


def wrap_context_for_client(mode: str, payload: dict, origin: str) -> dict:
    text = str((payload or {}).get('additionalContext') or '').strip()
    if text:
        text = text[:500]
        if origin == 'codex':
            return {'hookSpecificOutput': {'hookEventName': codex_event_name(mode), 'additionalContext': text}}
        return {'additionalContext': text}
    return payload or {'suppressOutput': True}


def heartbeat_url_from_mcp_url(value: str) -> str:
    value = str(value or '').strip().rstrip('/')
    marker = '/mcp/'
    if marker not in value:
        return ''
    base, token = value.rsplit(marker, 1)
    if not base or not token:
        return ''
    return f'{base}/hook-heartbeat/{token}'


def project_observation_url_from_mcp_url(value: str) -> str:
    value = str(value or '').strip().rstrip('/')
    marker = '/mcp/'
    if marker not in value:
        return ''
    base, token = value.rsplit(marker, 1)
    if not base or not token:
        return ''
    return f'{base}/project-observation/{token}'


def maintenance_status_url_from_mcp_url(value: str) -> str:
    value = str(value or '').strip().rstrip('/')
    marker = '/mcp/'
    if marker not in value:
        return ''
    base, token = value.rsplit(marker, 1)
    if not base or not token:
        return ''
    return f'{base}/maintenance-status/{token}'


def infer_origin_from_path(path: str) -> str:
    lowered = str(path or '').replace('\\', '/').lower()
    if '/.codex/' in lowered:
        return 'codex'
    if '/.claude/' in lowered:
        return 'claude'
    return os.environ.get('MEM0_HOOK_ORIGIN', '').strip().lower() or 'unknown'


def read_codex_mcp_url(config_path: Path, preferred_names: tuple[str, ...] = ("kontext", "mem0")) -> str:
    try:
        text = config_path.read_text(encoding='utf-8-sig')
    except OSError:
        return ''
    urls: dict[str, str] = {}
    current = ''
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped.strip('[]').strip().lower()
            current = section.split('.', 1)[1] if section.startswith('mcp_servers.') else ''
            continue
        if current and stripped.lower().startswith('url') and '=' in stripped:
            urls[current] = stripped.split('=', 1)[1].strip().strip('"').strip("'")
    for name in preferred_names:
        if urls.get(name):
            return urls[name]
    return ''


def read_claude_mcp_url(config_path: Path, preferred_names: tuple[str, ...] = ("kontext", "mem0")) -> str:
    try:
        payload = json.loads(config_path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        return ''
    found: dict[str, str] = {}

    def walk(value) -> None:
        if isinstance(value, dict):
            servers = value.get('mcpServers')
            if isinstance(servers, dict):
                for name, server in servers.items():
                    if isinstance(server, dict) and str(server.get('url') or '').strip():
                        found.setdefault(str(name).lower(), str(server.get('url') or '').strip())
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(payload)
    for name in preferred_names:
        if found.get(name):
            return found[name]
    return ''


def find_mcp_url(origin: str) -> str:
    explicit = os.environ.get('KONTEXT_MCP_URL', '').strip() or os.environ.get('MEM0_MCP_URL', '').strip()
    if explicit:
        return explicit
    script_path = Path(__file__).resolve()
    candidates = []
    if origin == 'codex':
        candidates.extend([script_path.with_name('config.toml'), Path.home() / '.codex' / 'config.toml', Path('/config/.codex/config.toml')])
        for path in candidates:
            url = read_codex_mcp_url(path)
            if url:
                return url
    if origin == 'claude':
        candidates.extend([
            Path.home() / '.claude.json',
            script_path.with_name('.mcp.json'),
            Path.home() / '.claude' / '.mcp.json',
            Path('/config/.claude.json'),
            Path('/config/.claude/.mcp.json'),
        ])
        for path in candidates:
            url = read_claude_mcp_url(path)
            if url:
                return url
    return ''


def find_project_observation_url(origin: str) -> str:
    explicit = os.environ.get('KONTEXT_PROJECT_OBSERVATION_URL', '').strip() or os.environ.get('MEM0_PROJECT_OBSERVATION_URL', '').strip()
    if explicit:
        return explicit
    return project_observation_url_from_mcp_url(find_mcp_url(origin))


def find_maintenance_status_url(origin: str) -> str:
    explicit = os.environ.get('KONTEXT_MAINTENANCE_STATUS_URL', '').strip() or os.environ.get('MEM0_MAINTENANCE_STATUS_URL', '').strip()
    if explicit:
        return explicit
    return maintenance_status_url_from_mcp_url(find_mcp_url(origin))


def fetch_maintenance_status(origin: str) -> dict:
    url = find_maintenance_status_url(origin)
    if url:
        request = urllib.request.Request(url, method='GET', headers={'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=1.5) as response:
                raw = response.read(4096)
            payload = json.loads(raw.decode('utf-8'))
            if isinstance(payload, dict) and (payload.get('ok') or isinstance(payload.get('maintenance'), dict)):
                return payload
        except Exception:
            pass
    mcp_payload = call_mcp_tool(find_mcp_url(origin), 'ingestion_status', {'recent_limit': 5})
    if mcp_payload:
        return {'ok': True, 'origin': origin, 'maintenance': mcp_payload.get('maintenance') or {}}
    return {}


def maintenance_reminder_from_status(status: dict) -> str:
    maintenance = status.get('maintenance') if isinstance(status, dict) else None
    if not isinstance(maintenance, dict) or not maintenance.get('due'):
        return ''
    try:
        pending_flags = int(maintenance.get('pending_flags') or 0)
    except (TypeError, ValueError):
        pending_flags = 0
    flag_types = maintenance.get('flag_types') if isinstance(maintenance.get('flag_types'), dict) else {}
    summary = ', '.join(f'{key}={value}' for key, value in sorted(flag_types.items()) if value)
    suffix = f' ({summary})' if summary else ''
    return (
        f'Memory maintenance due: {pending_flags} pending flags{suffix}; '
        'ask Ionut before running dream cleanup. Start with the CLI dry-run; destructive apply needs approval.'
    )[:360]


def maintenance_reminder(origin: str) -> str:
    return maintenance_reminder_from_status(fetch_maintenance_status(origin))


def send_heartbeat(hook_type: str, payload: dict | None = None) -> None:
    origin = infer_origin_from_path(__file__)
    if not should_send_heartbeat(hook_type, payload or {}, origin):
        return
    mcp_url = find_mcp_url(origin)
    if call_mcp_tool(
        mcp_url,
        'hook_heartbeat',
        {
            'hook_type': hook_type,
            'source': str((payload or {}).get('source') or ''),
            'marker': str((payload or {}).get('session_id') or (payload or {}).get('cwd') or ''),
        },
    ):
        return
    heartbeat_url = (
        os.environ.get('KONTEXT_HEARTBEAT_URL', '').strip()
        or os.environ.get('MEM0_HEARTBEAT_URL', '').strip()
        or heartbeat_url_from_mcp_url(mcp_url)
    )
    if not heartbeat_url:
        return
    payload = payload or {}
    body = json.dumps(
        {
            'origin': origin,
            'hook_type': hook_type,
            'source': str(payload.get('source') or ''),
            'marker': str(payload.get('session_id') or payload.get('cwd') or ''),
        }
    ).encode('utf-8')
    request = urllib.request.Request(
        heartbeat_url,
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            response.read(64)
    except Exception:
        return


def stable_hash(value: str) -> str:
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()


def compact_text(value, max_len: int = 160) -> str:
    collapsed = ' '.join(str(value or '').split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + '...'


def nested_dict(payload: dict, *keys: str) -> dict:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def command_category(command: str, tool_name: str = '') -> str:
    text = f'{tool_name} {command}'.lower()
    if any(term in text for term in ('pytest', ' test', 'npm test', 'pnpm test', 'yarn test')):
        return 'test'
    if any(term in text for term in (' build', 'npm run build', 'pnpm build', 'yarn build', ' lint', 'typecheck', 'py_compile', 'compile')):
        return 'build'
    if any(term in text for term in ('docker compose', 'kubectl', 'ssh ', 'scp ', 'rsync', 'deploy')):
        return 'deploy'
    if 'git ' in text:
        return 'git'
    if any(term in text for term in ('apply_patch', ' edit', 'write', 'remove-item', 'copy-item', 'move-item')):
        return 'file_edit'
    if any(term in text for term in ('get-content', ' rg ', 'select-string', 'read')):
        return 'file_read'
    if any(term in text for term in ('python ', 'py ')):
        return 'python'
    if any(term in text for term in ('npm ', 'pnpm ', 'yarn ', 'node ')):
        return 'node'
    return 'other'


def infer_status(response: dict) -> str:
    if isinstance(response.get('success'), bool):
        return 'passed' if response['success'] else 'failed'
    for key in ('exit_code', 'exitCode', 'returncode'):
        if key in response:
            try:
                return 'passed' if int(response.get(key) or 0) == 0 else 'failed'
            except (TypeError, ValueError):
                return 'unknown'
    if response.get('error'):
        return 'failed'
    return 'unknown'


def normalize_path(value: str) -> str:
    path = compact_text(value, 240).replace('\\', '/')
    while '//' in path and '://' not in path:
        path = path.replace('//', '/')
    return path.strip()


def touched_paths(tool_input: dict, command: str) -> list[str]:
    paths = []
    for key in ('path', 'file_path', 'filepath', 'workdir', 'cwd'):
        value = tool_input.get(key)
        if value:
            paths.append(normalize_path(str(value)))
    for key in ('paths', 'files'):
        values = tool_input.get(key)
        if isinstance(values, list):
            paths.extend(normalize_path(str(value)) for value in values if value)
    for match in re.findall(r'[A-Za-z0-9_.:-]*[\\/][A-Za-z0-9_.\\/:-]+', command or ''):
        if not match.startswith(('http:/', 'https:/')):
            paths.append(normalize_path(match))
    clean = []
    for path in paths:
        if path and path not in clean:
            clean.append(path)
    return clean[:20]


def build_project_observation_payload(mode: str, payload: dict, origin: str) -> dict:
    tool_input = nested_dict(payload, 'tool_input', 'toolInput', 'input')
    response = nested_dict(payload, 'tool_response', 'toolResponse', 'result', 'response')
    tool_name = compact_text(payload.get('tool_name') or payload.get('toolName') or tool_input.get('tool_name'), 120)
    command = compact_text(tool_input.get('command') or tool_input.get('cmd'), 240)
    category = command_category(command, tool_name)
    status = infer_status(response)
    paths = touched_paths(tool_input, command)
    cwd = normalize_path(payload.get('cwd') or tool_input.get('cwd') or tool_input.get('workdir') or '')
    source_parts = [mode, origin, tool_name, cwd, category, status, ','.join(paths), str(payload.get('session_id') or '')]
    title_tool = tool_name or 'hook'
    summary_tool = tool_name or 'unknown'
    return {
        'origin': origin,
        'event_type': compact_text(mode, 80).lower() or 'hook',
        'title': compact_text(f'{mode} {title_tool} {status}', 160),
        'summary': compact_text(f'tool={summary_tool} category={category} status={status}', 240),
        'cwd': cwd,
        'tool_name': tool_name,
        'command_category': category,
        'status': status,
        'outcome': compact_text(f'tool={summary_tool} category={category} status={status}', 240),
        'commands': [f'category:{category}'] if category else [],
        'touched_paths': paths,
        'source_hash': stable_hash('|'.join(source_parts)),
    }


def heartbeat_throttle_seconds() -> int:
    raw = os.environ.get('KONTEXT_HOOK_HEARTBEAT_THROTTLE_SECONDS') or os.environ.get('MEM0_HOOK_HEARTBEAT_THROTTLE_SECONDS') or ''
    try:
        return max(0, int(raw or DEFAULT_HEARTBEAT_THROTTLE_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_THROTTLE_SECONDS


def hook_state_dir() -> Path:
    configured = os.environ.get('KONTEXT_HOOK_STATE_DIR') or os.environ.get('MEM0_HOOK_STATE_DIR') or ''
    if configured.strip():
        return Path(configured.strip())
    local_app_data = os.environ.get('LOCALAPPDATA', '').strip()
    if os.name == 'nt' and local_app_data:
        return Path(local_app_data) / 'Kontext' / 'hooks'
    return Path.home() / '.cache' / 'kontext-hooks'


def heartbeat_state_path(hook_type: str, payload: dict, origin: str) -> Path:
    parts = [origin, hook_type]
    if hook_type == 'post_tool_use':
        observation = build_project_observation_payload(hook_type, payload or {}, origin)
        parts.append(observation.get('command_category') or 'unknown')
    digest = stable_hash('|'.join(parts))[:24]
    return hook_state_dir() / f'heartbeat-{digest}.json'


def should_send_project_observation(mode: str, payload: dict | None, origin: str) -> bool:
    if mode != 'post_tool_use':
        return False
    observation = build_project_observation_payload(mode, payload or {}, origin)
    category = observation.get('command_category') or 'other'
    if category in SIGNIFICANT_COMMAND_CATEGORIES:
        return True
    if observation.get('status') == 'failed' and category not in LOW_SIGNAL_COMMAND_CATEGORIES:
        return True
    return False


def should_send_heartbeat(hook_type: str, payload: dict | None, origin: str, now: float | None = None) -> bool:
    if hook_type not in KNOWN_HOOK_TYPES:
        return False
    payload = payload or {}
    if hook_type == 'post_tool_use' and not should_send_project_observation(hook_type, payload, origin):
        return False
    throttle = heartbeat_throttle_seconds()
    if throttle <= 0:
        return True
    current = time.time() if now is None else float(now)
    path = heartbeat_state_path(hook_type, payload, origin)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            previous = json.loads(path.read_text(encoding='utf-8')).get('last_sent')
            if previous is not None and current - float(previous) < throttle:
                return False
        path.write_text(json.dumps({'last_sent': current}, separators=(',', ':')), encoding='utf-8')
        return True
    except Exception:
        return True


def send_project_observation(mode: str, payload: dict | None = None) -> None:
    origin = infer_origin_from_path(__file__)
    if not should_send_project_observation(mode, payload or {}, origin):
        return
    observation = build_project_observation_payload(mode, payload or {}, origin)
    if call_mcp_tool(find_mcp_url(origin), 'record_project_observation', observation):
        return
    url = find_project_observation_url(origin)
    if not url:
        return
    body = json.dumps(observation, ensure_ascii=True).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            response.read(64)
    except Exception:
        return


def call_mcp_tool(mcp_url: str, name: str, arguments: dict) -> dict:
    url = str(mcp_url or '').strip()
    if not url:
        return {}
    body = json.dumps(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        },
        ensure_ascii=True,
        separators=(',', ':'),
    ).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            raw = response.read(4096)
        payload = json.loads(raw.decode('utf-8'))
        result = payload.get('result') if isinstance(payload, dict) else {}
        content = result.get('content') if isinstance(result, dict) else []
        text = content[0].get('text') if isinstance(content, list) and content and isinstance(content[0], dict) else '{}'
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def session_start(payload: dict) -> dict:
    reminder = maintenance_reminder(infer_origin_from_path(__file__))
    if reminder:
        return {'additionalContext': reminder}
    return {'suppressOutput': True}


def user_prompt() -> dict:
    return {'suppressOutput': True}


def post_compact() -> dict:
    reminder = maintenance_reminder(infer_origin_from_path(__file__))
    if reminder:
        return {'additionalContext': reminder}
    return {'suppressOutput': True}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    payload = read_hook_input()
    origin = infer_origin_from_path(__file__)
    send_heartbeat(mode, payload)
    if should_send_project_observation(mode, payload, origin):
        send_project_observation(mode, payload)
    if mode == 'session_start':
        return emit(wrap_context_for_client(mode, session_start(payload), origin))
    if mode == 'user_prompt':
        return emit(wrap_context_for_client(mode, user_prompt(), origin))
    if mode == 'post_compact':
        return emit(wrap_context_for_client(mode, post_compact(), origin))
    if mode in {'post_tool_use', 'session_end'}:
        return emit({'suppressOutput': True})
    return emit({'suppressOutput': True})


if __name__ == '__main__':
    raise SystemExit(main())
