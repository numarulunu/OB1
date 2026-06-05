# Mem0 Codex MCP Bridge

Local stdio MCP server that lets Codex search and save distilled memories in the self-hosted Mem0 instance.

## Tools

- `memory_search`: searches Mem0 using `filters.user_id` and returns sanitized memory, score, domains, memory type, and signal strength.
- `memory_save`: saves a compact durable memory with `infer=false`; do not use it for raw transcripts or secrets.

## Runtime

Codex global MCP entry:

```powershell
codex mcp list
```

The `mem0` entry runs:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Tools\OB1\tools\mem0-codex-mcp\run_mem0_mcp.ps1
```

Secrets/config live outside the repo at:

```text
%USERPROFILE%\.codex\.sandbox-secrets\mem0-codex-mcp.env
```

Do not commit or print that file. It contains the Mem0 client API key.

## Verification

```powershell
python -m pytest tools\mem0-codex-mcp\test_mem0_mcp_server.py -q
codex mcp list
```
