"""Kontext V2 shadow MCP stdio proxy.

Local clients use this as a stdio MCP server. The proxy forwards JSON-RPC
requests to the live Kontext V2 HTTP MCP endpoint through localhost on the VPS.
It never logs request/response bodies because tool calls may contain memory text.
"""
from __future__ import annotations

import atexit
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_SSH_TARGET = "root@178.104.203.128"
DEFAULT_LOCAL_HOST = "127.0.0.1"
DEFAULT_LOCAL_PORT = 18200
DEFAULT_REMOTE_PORT = 8200
LOG_PATH = Path(__file__).with_name("_kontext_shadow_mcp.log")


class ProxyState:
    def __init__(self) -> None:
        self.base_url: str | None = None
        self.token: str | None = None
        self.tunnel: subprocess.Popen | None = None


STATE = ProxyState()


def cleanup() -> None:
    proc = STATE.tunnel
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


atexit.register(cleanup)


def log(message: str) -> None:
    try:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except Exception:
        pass


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _token_from_url(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if "/mcp/" not in value:
        return ""
    token = value.rsplit("/", 1)[-1].strip()
    return token if token and not any(ch.isspace() for ch in token) else ""


def _find_codex_mem0_token() -> str:
    text = _read_text(Path.home() / ".codex" / "config.toml")
    match = re.search(r'(?ms)^\[mcp_servers\.mem0\]\s*.*?^\s*url\s*=\s*"([^"]+)"', text)
    return _token_from_url(match.group(1)) if match else ""


def _find_claude_mem0_token() -> str:
    for path in (Path.home() / ".claude.json", Path.home() / ".claude" / ".mcp.json", Path.home() / ".mcp.json"):
        text = _read_text(path)
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        mem0 = servers.get("mem0") if isinstance(servers, dict) else None
        if isinstance(mem0, dict):
            token = _token_from_url(str(mem0.get("url") or ""))
            if token:
                return token
    return ""


def find_token() -> str:
    explicit_url = os.environ.get("KONTEXT_MCP_URL", "").strip()
    token = _token_from_url(explicit_url)
    if token:
        return token
    for name in ("KONTEXT_MCP_TOKEN", "KONTEXT_MCP_CODEX_TOKEN", "MCP_CODEX_TOKEN", "MCP_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return _find_codex_mem0_token() or _find_claude_mem0_token()


def can_connect(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def health_ok(base_url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/v2/health", timeout=timeout) as response:
            if response.status != 200:
                return False
            data = json.loads(response.read().decode("utf-8", "replace"))
            return bool(data.get("ok"))
    except Exception:
        return False


def start_tunnel() -> str:
    direct = os.environ.get("KONTEXT_SHADOW_DIRECT_BASE", "").strip() or "http://127.0.0.1:8200"
    if health_ok(direct):
        return direct.rstrip("/")

    host = os.environ.get("KONTEXT_SHADOW_LOCAL_HOST", DEFAULT_LOCAL_HOST)
    start_port = int(os.environ.get("KONTEXT_SHADOW_LOCAL_PORT", str(DEFAULT_LOCAL_PORT)))
    remote_port = int(os.environ.get("KONTEXT_SHADOW_REMOTE_PORT", str(DEFAULT_REMOTE_PORT)))
    ssh_target = os.environ.get("KONTEXT_SHADOW_SSH_TARGET", DEFAULT_SSH_TARGET).strip()

    for port in range(start_port, start_port + 20):
        base = f"http://{host}:{port}"
        if can_connect(host, port):
            if health_ok(base):
                return base
            continue
        cmd = ["ssh", "-N", "-L", f"{host}:{port}:127.0.0.1:{remote_port}", ssh_target]
        kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if platform.system().lower().startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except Exception as exc:
            log(f"tunnel_start_failed type={type(exc).__name__}")
            continue
        for _ in range(30):
            if proc.poll() is not None:
                break
            if health_ok(base):
                STATE.tunnel = proc
                log(f"tunnel_started local_port={port}")
                return base
            time.sleep(0.2)
        if proc.poll() is None:
            proc.terminate()
        log(f"tunnel_unhealthy local_port={port}")
    raise RuntimeError("Kontext tunnel unavailable")


def ensure_ready() -> tuple[str, str]:
    if STATE.base_url and STATE.token:
        return STATE.base_url, STATE.token
    token = find_token()
    if not token:
        raise RuntimeError("Mem0/Kontext MCP token not found in local config or env")
    base_url = start_tunnel()
    STATE.base_url = base_url
    STATE.token = token
    return base_url, token


def forward(request: dict[str, Any]) -> dict[str, Any] | None:
    method = str(request.get("method") or "")
    req_id = request.get("id")
    if method.startswith("notifications/"):
        return None
    if method in {"resources/list", "prompts/list"}:
        key = "resources" if method == "resources/list" else "prompts"
        return {"jsonrpc": "2.0", "id": req_id, "result": {key: []}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    base_url, token = ensure_ready()
    url = f"{base_url}/api/v2/mcp/{token}"
    body = json.dumps(request, ensure_ascii=True).encode("utf-8")
    http_request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(http_request, timeout=30) as response:
            payload = response.read().decode("utf-8", "replace")
        if not payload.strip():
            return None
        return json.loads(payload)
    except urllib.error.HTTPError as exc:
        log(f"http_error status={exc.code} method={method}")
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": f"Kontext HTTP error {exc.code}"}}
    except Exception as exc:
        log(f"forward_failed type={type(exc).__name__} method={method}")
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": f"Kontext proxy error: {type(exc).__name__}"}}


def emit(response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main() -> int:
    log("proxy_start")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        response = forward(request)
        if response is not None:
            emit(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
