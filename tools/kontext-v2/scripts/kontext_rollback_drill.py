from __future__ import annotations

import argparse
import json
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_FEATURE_FLAG_NAMES = (
    "KONTEXT_STATE_MODEL_ENABLED",
    "KONTEXT_STATE_INGESTION_ENABLED",
    "KONTEXT_STATE_AUTO_ACCEPT_ENABLED",
    "KONTEXT_STATE_ROUTING_ENABLED",
    "KONTEXT_TYPED_STATE_V2",
    "KONTEXT_TYPED_OBJECT_SUMMARY",
    "KONTEXT_BENCHMARK_STATE_MODEL_ENABLED",
)
FALSE_VALUES = {"", "0", "false", "no", "off", "none", "null"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def fetch_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=max(timeout, 0.1)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def env_value(path: str | Path, key: str) -> str:
    target = Path(path)
    if not target.exists():
        return ""
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def mcp_profile_status(url: str, token: str, timeout: float = 10.0) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "ingestion_status", "arguments": {"recent_limit": 1}},
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/" + token,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    with urllib.request.urlopen(request, timeout=max(timeout, 0.1)) as response:
        raw = json.loads(response.read().decode("utf-8"))
    result = raw.get("result") if isinstance(raw, dict) else {}
    content = result.get("content") if isinstance(result, dict) else []
    text = content[0].get("text") if isinstance(content, list) and content and isinstance(content[0], dict) else "{}"
    status = json.loads(text)
    mcp = status.get("mcp") if isinstance(status, dict) else {}
    return mcp if isinstance(mcp, dict) else {}


def add_check(
    checks: dict[str, dict[str, Any]],
    name: str,
    status: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    checks[name] = {"status": status, "summary": summary, "evidence": evidence or {}}


def summarize_checks(checks: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks.values():
        status = str(check.get("status") or "warn")
        counts[status if status in counts else "warn"] += 1
    return counts


def archive_evidence(rollback_archive: Path) -> dict[str, Any]:
    evidence = {
        "archive_exists": rollback_archive.exists(),
        "archive_readable": False,
        "compose_member_present": False,
        "member_count": 0,
    }
    if not rollback_archive.exists():
        return evidence
    try:
        with tarfile.open(rollback_archive, "r:gz") as archive:
            members = archive.getmembers()
            evidence["member_count"] = len(members)
            evidence["compose_member_present"] = any(member.name.endswith("docker-compose.yml") for member in members)
            evidence["archive_readable"] = True
    except (tarfile.TarError, OSError):
        evidence["archive_readable"] = False
    return evidence


def compose_write_enabled(compose_text: str) -> bool:
    return "KONTEXT_MCP_WRITE_PROFILES" in compose_text or "KONTEXT_MCP_WRITE_ENABLED_PROFILES" in compose_text


def enabled_state_feature_flags(compose_text: str) -> list[str]:
    enabled: list[str] = []
    for raw_line in compose_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        for name in STATE_FEATURE_FLAG_NAMES:
            value: str | None = None
            if line == name:
                value = "1"
            elif line.startswith(f"{name}:"):
                value = line.split(":", 1)[1]
            elif line.startswith(f"{name}="):
                value = line.split("=", 1)[1]
            if value is None:
                continue
            clean_value = value.strip().strip('"').strip("'").lower()
            if clean_value not in FALSE_VALUES:
                enabled.append(name)
    return sorted(set(enabled))


def build_rollback_drill_report(
    *,
    rollback_archive: str | Path,
    compose_backup: str | Path,
    current_compose: str | Path,
    mem0_health: dict[str, Any],
    kontext_health: dict[str, Any],
    mcp_status: dict[str, Any],
    label: str = "kontext-rollback-drill",
) -> dict[str, Any]:
    archive_path = Path(rollback_archive)
    backup_path = Path(compose_backup)
    compose_path = Path(current_compose)
    checks: dict[str, dict[str, Any]] = {}
    attention: list[str] = []

    mem0_ok = mem0_health.get("ok") is True
    add_check(
        checks,
        "mem0_primary_health",
        "pass" if mem0_ok else "fail",
        "Mem0 primary health is ok" if mem0_ok else "Mem0 primary health failed",
        {"ok": mem0_ok, "service": str(mem0_health.get("service") or "")[:80]},
    )
    if not mem0_ok:
        attention.append("Mem0 primary health failed")

    kontext_ok = kontext_health.get("ok") is True
    add_check(
        checks,
        "kontext_health",
        "pass" if kontext_ok else "fail",
        "Kontext health is ok" if kontext_ok else "Kontext health failed",
        {"ok": kontext_ok, "mode": str(kontext_health.get("mode") or "")[:80]},
    )
    if not kontext_ok:
        attention.append("Kontext health failed")

    current_text = compose_path.read_text(encoding="utf-8") if compose_path.exists() else ""
    write_enabled = mcp_status.get("write_enabled") is True or compose_write_enabled(current_text)
    dry_run_enabled = mcp_status.get("dry_run_write_enabled") is True
    add_check(
        checks,
        "write_mode_disabled",
        "fail" if write_enabled else "pass",
        "Kontext write mode is disabled" if not write_enabled else "Kontext write mode is still enabled",
        {
            "profile": str(mcp_status.get("profile") or "")[:80],
            "write_enabled": bool(mcp_status.get("write_enabled")),
            "dry_run_write_enabled": dry_run_enabled,
            "compose_write_profile_present": compose_write_enabled(current_text),
        },
    )
    if write_enabled:
        attention.append("write mode is still enabled")

    enabled_flags = enabled_state_feature_flags(current_text)
    add_check(
        checks,
        "state_feature_flags_disabled",
        "pass" if not enabled_flags else "fail",
        "State, typed, and benchmark feature flags are disabled"
        if not enabled_flags
        else "State, typed, or benchmark feature flags are still enabled",
        {"enabled_flags": enabled_flags},
    )
    if enabled_flags:
        attention.append("state, typed, or benchmark feature flags are still enabled")

    archive = archive_evidence(archive_path)
    backup_exists = backup_path.exists()
    artifacts_ok = bool(archive["archive_readable"] and archive["compose_member_present"] and backup_exists)
    add_check(
        checks,
        "rollback_artifacts",
        "pass" if artifacts_ok else "fail",
        "Rollback archive and compose backup are readable" if artifacts_ok else "Rollback artifacts are incomplete",
        {
            **archive,
            "compose_backup_exists": backup_exists,
            "archive_basename": archive_path.name[:160],
            "compose_backup_basename": backup_path.name[:160],
        },
    )
    if not artifacts_ok:
        attention.append("rollback artifacts are incomplete")

    backup_text = backup_path.read_text(encoding="utf-8") if backup_exists else ""
    backup_enabled_flags = enabled_state_feature_flags(backup_text)
    restore_ok = bool(
        backup_text
        and not compose_write_enabled(backup_text)
        and not backup_enabled_flags
        and archive.get("archive_readable")
    )
    add_check(
        checks,
        "restore_sandbox",
        "pass" if restore_ok else "fail",
        "Rollback compose restore target is safe" if restore_ok else "Rollback compose restore target is not proven safe",
        {
            "compose_backup_readable": bool(backup_text),
            "compose_backup_write_profile_present": compose_write_enabled(backup_text),
            "compose_backup_enabled_state_flags": backup_enabled_flags,
            "archive_member_count": safe_int(archive.get("member_count")),
        },
    )
    if not restore_ok:
        attention.append("rollback restore target is not proven safe")

    summary = summarize_checks(checks)
    return {
        "ok": summary["fail"] == 0,
        "mode": "kontext-rollback-drill",
        "runs_model_calls": False,
        "generated_at": utc_now(),
        "label": label,
        "summary": summary,
        "checks": checks,
        "attention_items": list(dict.fromkeys(attention)),
        "notes": [
            "This rollback drill report is sanitized.",
            "It does not include raw memory text, raw chats, secrets, tokens, env contents, or full log paths.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a sanitized Kontext rollback drill report.")
    parser.add_argument("--rollback-archive", required=True)
    parser.add_argument("--compose-backup", required=True)
    parser.add_argument("--current-compose", default="/opt/kontext/docker-compose.yml")
    parser.add_argument("--health-json")
    parser.add_argument("--mcp-status-json")
    parser.add_argument("--mem0-health-url", default="https://memory-mcp.ionutrosu.xyz/health")
    parser.add_argument("--kontext-health-url", default="http://127.0.0.1:8200/api/v2/health")
    parser.add_argument("--mcp-base-url", default="http://127.0.0.1:8200/api/v2/mcp")
    parser.add_argument("--kontext-env", default="/opt/kontext/.env")
    parser.add_argument("--label", default="kontext-rollback-drill")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.health_json:
        health = load_json(args.health_json)
        mem0_health = health.get("mem0") if isinstance(health.get("mem0"), dict) else {}
        kontext_health = health.get("kontext") if isinstance(health.get("kontext"), dict) else {}
    else:
        mem0_health = fetch_json(args.mem0_health_url, timeout=args.timeout)
        kontext_health = fetch_json(args.kontext_health_url, timeout=args.timeout)
    if args.mcp_status_json:
        mcp_status = load_json(args.mcp_status_json)
    else:
        token = env_value(args.kontext_env, "KONTEXT_MCP_MEM0_TOKEN") or env_value(
            args.kontext_env, "KONTEXT_MCP_MEM0_COMPAT_TOKEN"
        )
        mcp_status = mcp_profile_status(args.mcp_base_url, token, timeout=args.timeout) if token else {}
    report = build_rollback_drill_report(
        rollback_archive=args.rollback_archive,
        compose_backup=args.compose_backup,
        current_compose=args.current_compose,
        mem0_health=mem0_health,
        kontext_health=kontext_health,
        mcp_status=mcp_status,
        label=args.label,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
