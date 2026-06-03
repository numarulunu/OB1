#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/opt/kontext")
REPORT_DIR = ROOT / "reports"
RAW_DIR = REPORT_DIR / "raw"
ENV_FILE = Path("/opt/mem0-remote-mcp/.env")
CASES_FILE = ROOT / "src" / "retrieval_eval_cases.v1.13.json"
PYTHONPATH = str(ROOT / "src")
SYNC_CAP = int(os.environ.get("KONTEXT_SHADOW_SYNC_CAP", "25"))
TOP_K = int(os.environ.get("KONTEXT_SHADOW_TOP_K", "5"))
PROFILE = os.environ.get("KONTEXT_SHADOW_PROFILE", "codex")
EXACT_ID_FRESHNESS_LIMIT = int(os.environ.get("KONTEXT_EXACT_ID_FRESHNESS_LIMIT", "250"))
EXACT_ID_FETCH_TIMEOUT = int(os.environ.get("KONTEXT_EXACT_ID_FETCH_TIMEOUT", "15"))
EXACT_ID_PROCESS_TIMEOUT = int(os.environ.get("KONTEXT_EXACT_ID_PROCESS_TIMEOUT", "240"))
SYNC_REFRESH_EXISTING_LIMIT = int(
    os.environ.get("KONTEXT_SHADOW_SYNC_REFRESH_EXISTING_LIMIT", str(EXACT_ID_FRESHNESS_LIMIT))
)
EXACT_ID_FRESHNESS_STATE_FILE = Path(
    os.environ.get("KONTEXT_EXACT_ID_FRESHNESS_STATE_FILE", str(REPORT_DIR / "exact-id-freshness-state.json"))
)

ENDPOINTS = {
    "kontext_docs": "http://127.0.0.1:8200/docs",
    "kontext_v2_health": "http://127.0.0.1:8200/api/v2/health",
    "sync_status": "http://127.0.0.1:8200/api/v2/sync/status",
    "tools": "http://127.0.0.1:8200/api/v2/tools",
    "mem0_health": "https://memory-mcp.ionutrosu.xyz/health",
}

SECRET_KEY_MARKERS = ("token", "api_key", "apikey", "password", "secret", "authorization")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in SECRET_KEY_MARKERS):
                output[str(key)] = "<redacted>"
            else:
                output[str(key)] = redact(item)
        return output
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def safe_int(value: Any, default: int = 0, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return min(max(parsed, minimum), maximum)


def read_exact_id_freshness_offset() -> int:
    if "KONTEXT_EXACT_ID_FRESHNESS_OFFSET" in os.environ:
        return safe_int(os.environ.get("KONTEXT_EXACT_ID_FRESHNESS_OFFSET"))
    try:
        payload = json.loads(EXACT_ID_FRESHNESS_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    return safe_int(payload.get("next_scan_offset"))


def write_exact_id_freshness_state(payload: dict[str, Any]) -> None:
    next_scan_offset = payload.get("next_scan_offset")
    if next_scan_offset is None:
        return
    state = {
        "updated_at": utc_now().isoformat().replace("+00:00", "Z"),
        "last_scan_offset": safe_int(payload.get("scan_offset")),
        "next_scan_offset": safe_int(next_scan_offset),
        "limit": safe_int(payload.get("scan_limit"), EXACT_ID_FRESHNESS_LIMIT),
        "checked": safe_int(payload.get("checked")),
        "total_rows": safe_int(payload.get("total_rows")),
    }
    EXACT_ID_FRESHNESS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXACT_ID_FRESHNESS_STATE_FILE.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def http_json(url: str, timeout: int = 15) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = getattr(response, "status", None)
            body = response.read(50000).decode("utf-8", "replace")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"body_length": len(body)}
        return {"ok": 200 <= int(status or 0) < 300, "status": status, "latency_ms": elapsed_ms, "payload": payload}
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {"ok": False, "latency_ms": elapsed_ms, "error_type": type(exc).__name__}


def summarize_endpoint(name: str, result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "latency_ms": result.get("latency_ms"),
    }
    if result.get("error_type"):
        summary["error_type"] = result["error_type"]
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    if name == "tools":
        tools = payload.get("tools") or payload.get("result", {}).get("tools") or []
        if isinstance(tools, list):
            names = sorted(str(tool.get("name") or tool) for tool in tools if tool)
            summary["tool_count"] = len(names)
            summary["tools"] = names
    elif name == "sync_status":
        keep = {"ok", "mode", "status", "source", "write_enabled", "writes_enabled", "last_sync_at", "row_count", "memory_count"}
        summary["status_fields"] = {key: payload.get(key) for key in sorted(payload) if key in keep}
        summary["payload_keys"] = sorted(str(key) for key in payload.keys())[:20]
    elif name.endswith("health"):
        for key in ("ok", "service", "status", "version"):
            if key in payload:
                summary[key] = payload.get(key)
    return redact(summary)


def summarize_reliability(payload: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {
        "ok": bool(payload.get("ok")),
        "case_count": payload.get("case_count"),
        "top_k": payload.get("top_k"),
        "profiles": {},
    }
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        profile_summary: dict[str, Any] = {"ok": bool(profile.get("ok"))}
        if profile.get("error"):
            profile_summary["error"] = profile.get("error")
        services = profile.get("services") if isinstance(profile.get("services"), dict) else {}
        profile_summary["services"] = {}
        for service_name, service in services.items():
            if not isinstance(service, dict):
                continue
            eval_report = service.get("eval") if isinstance(service.get("eval"), dict) else {}
            service_summary = {
                "init_ok": bool(service.get("init_ok")),
                "tool_count": len(service.get("tools") or []),
                "tools": service.get("tools") or [],
                "latency": service.get("latency") or {},
                "errors": service.get("errors") or [],
                "eval_summary": eval_report.get("summary") or {},
                "failed_cases": eval_report.get("failed_cases") or [],
            }
            profile_summary["services"][service_name] = service_summary
        comparison = profile.get("comparison") if isinstance(profile.get("comparison"), dict) else {}
        profile_summary["comparison"] = {
            key: comparison.get(key)
            for key in (
                "pair",
                "cases",
                "shared_any_cases",
                "shared_first_cases",
                "shared_any_rate",
                "shared_first_rate",
                "left_first_overlap_mrr",
                "right_first_overlap_mrr",
                "mean_first_overlap_mrr",
                "left_first_satisfying_mrr",
                "right_first_satisfying_mrr",
                "mean_first_satisfying_mrr",
                "left_better_satisfying_cases",
                "right_better_satisfying_cases",
                "equal_satisfying_cases",
                "no_overlap_cases",
            )
            if key in comparison
        }
        output["profiles"][profile_name] = profile_summary
    return redact(output)


def run_reliability(env_values: dict[str, str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(env_values)
    env["PYTHONPATH"] = PYTHONPATH
    cmd = [
        sys.executable,
        "-m",
        "kontext_v2.mcp_reliability_eval",
        "--cases",
        str(CASES_FILE),
        "--profiles",
        PROFILE,
        "--top-k",
        str(TOP_K),
    ]
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=180)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if proc.returncode != 0:
        return {"ok": False, "returncode": proc.returncode, "latency_ms": elapsed_ms, "error": "reliability_eval_failed"}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "returncode": proc.returncode, "latency_ms": elapsed_ms, "error": "reliability_eval_invalid_json"}
    summary = summarize_reliability(payload)
    summary["latency_ms"] = elapsed_ms
    return summary


def run_sync_dry_run(env_values: dict[str, str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(env_values)
    public_base_url = os.environ.get("KONTEXT_SHADOW_MEM0_API_BASE_URL", "https://mem0-api.ionutrosu.xyz")
    base_url = env.get("MEM0_API_BASE_URL") or env.get("MEM0_BASE_URL") or ""
    if base_url.startswith("http://mem0") or base_url.startswith("https://mem0"):
        base_url = public_base_url
    if not base_url:
        base_url = public_base_url
    env["MEM0_API_BASE_URL"] = base_url
    env["MEM0_BASE_URL"] = base_url
    env["MEM0_LEXICAL_DATABASE_URL"] = ""
    required = ["MEM0_API_KEY", "MEM0_USER_ID", "MEM0_BASE_URL"]
    missing = [name for name in required if not env.get(name)]
    if missing:
        return {"ok": False, "error": "missing_env", "missing": missing}
    refresh_existing_offset = read_exact_id_freshness_offset()
    cmd = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "docker-compose.yml"),
        "exec",
        "-T",
        "-e",
        "MEM0_API_KEY",
        "-e",
        "MEM0_USER_ID",
        "-e",
        "MEM0_BASE_URL",
        "-e",
        "MEM0_API_BASE_URL",
        "-e",
        "MEM0_LEXICAL_DATABASE_URL",
        "kontext",
        "python",
        "-m",
        "kontext_v2.sync_cli",
        "--cap",
        str(SYNC_CAP),
        "--refresh-existing-limit",
        str(SYNC_REFRESH_EXISTING_LIMIT),
        "--refresh-existing-offset",
        str(refresh_existing_offset),
    ]
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=180)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if proc.returncode != 0:
        return {"ok": False, "returncode": proc.returncode, "latency_ms": elapsed_ms, "error": "sync_dry_run_failed"}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "returncode": proc.returncode, "latency_ms": elapsed_ms, "error": "sync_dry_run_invalid_json"}
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    sync = payload.get("sync") if isinstance(payload.get("sync"), dict) else {}
    sync_metadata = sync.get("metadata") if isinstance(sync.get("metadata"), dict) else {}
    summary = {
        "ok": bool(payload.get("ok")),
        "source": payload.get("source"),
        "mode": payload.get("mode"),
        "dry_run": payload.get("dry_run"),
        "processed_rows": payload.get("processed_rows"),
        "source_rows_seen": payload.get("source_rows_seen"),
        "report": {key: report.get(key) for key in ("created", "updated", "unchanged", "skipped") if key in report},
        "sync": {key: sync.get(key) for key in ("id", "status", "dry_run", "rows_seen", "created", "updated", "unchanged", "skipped", "started_at", "finished_at") if key in sync},
        "refresh_existing": {
            "limit": sync_metadata.get("refresh_existing_limit"),
            "offset": sync_metadata.get("refresh_existing_offset"),
            "fetched": sync_metadata.get("refreshed_existing_rows"),
            "stale": sync_metadata.get("refreshed_stale_rows"),
        },
        "latency_ms": elapsed_ms,
    }
    return redact(summary)


def run_exact_id_freshness(env_values: dict[str, str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(env_values)
    public_base_url = os.environ.get("KONTEXT_SHADOW_MEM0_API_BASE_URL", "https://mem0-api.ionutrosu.xyz")
    base_url = env.get("MEM0_API_BASE_URL") or env.get("MEM0_BASE_URL") or ""
    if base_url.startswith("http://mem0") or base_url.startswith("https://mem0"):
        base_url = public_base_url
    if not base_url:
        base_url = public_base_url
    env["MEM0_API_BASE_URL"] = base_url
    env["MEM0_BASE_URL"] = base_url
    missing = [name for name in ("MEM0_API_KEY", "MEM0_USER_ID", "MEM0_BASE_URL") if not env.get(name)]
    if missing:
        return {"ok": False, "error": "missing_env", "missing": missing}
    scan_offset = read_exact_id_freshness_offset()
    cmd = [
        "docker", "compose", "-f", str(ROOT / "docker-compose.yml"),
        "exec", "-T", "-e", "MEM0_API_KEY", "-e", "MEM0_USER_ID",
        "-e", "MEM0_BASE_URL", "-e", "MEM0_API_BASE_URL",
        "kontext", "python", "-m", "kontext_v2.exact_id_freshness_cli",
        "--limit", str(EXACT_ID_FRESHNESS_LIMIT),
        "--offset", str(scan_offset),
        "--fetch-timeout", str(EXACT_ID_FETCH_TIMEOUT),
    ]
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=EXACT_ID_PROCESS_TIMEOUT)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if proc.returncode not in {0, 2}:
        return {"ok": False, "returncode": proc.returncode, "latency_ms": elapsed_ms, "error": "exact_id_freshness_failed"}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "returncode": proc.returncode, "latency_ms": elapsed_ms, "error": "exact_id_freshness_invalid_json"}
    payload["latency_ms"] = elapsed_ms
    if payload.get("ok"):
        write_exact_id_freshness_state(payload)
    return redact(payload)


def service_pass_counts(reliability: dict[str, Any]) -> dict[str, str]:
    counts: dict[str, str] = {}
    profiles = reliability.get("profiles") if isinstance(reliability.get("profiles"), dict) else {}
    for profile in profiles.values():
        if not isinstance(profile, dict):
            continue
        services = profile.get("services") if isinstance(profile.get("services"), dict) else {}
        for name, service in services.items():
            eval_summary = service.get("eval_summary") if isinstance(service, dict) else {}
            if isinstance(eval_summary, dict):
                counts[name] = f"{eval_summary.get('passed', 0)}/{eval_summary.get('cases', 0)}"
    return counts


def main() -> int:
    now = utc_now()
    ts = timestamp(now)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    env_values = parse_env_file(ENV_FILE)

    endpoints = {name: summarize_endpoint(name, http_json(url)) for name, url in ENDPOINTS.items()}
    reliability = run_reliability(env_values)
    sync_dry_run = run_sync_dry_run(env_values)
    exact_id_freshness = run_exact_id_freshness(env_values)

    report = redact(
        {
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "host": "kontext-vps",
            "root": str(ROOT),
            "mode": "shadow_read_only_mem0_source_of_truth",
            "sync_cap": SYNC_CAP,
            "sync_refresh_existing_limit": SYNC_REFRESH_EXISTING_LIMIT,
            "top_k": TOP_K,
            "profile": PROFILE,
            "endpoints": endpoints,
            "sync_dry_run": sync_dry_run,
            "exact_id_freshness": exact_id_freshness,
            "mcp_reliability_eval": reliability,
        }
    )
    report["ok"] = all(item.get("ok") for item in endpoints.values()) and bool(reliability.get("ok")) and bool(sync_dry_run.get("ok")) and bool(exact_id_freshness.get("ok"))

    report_path = REPORT_DIR / f"kontext-shadow-report-{ts}.json"
    latest_path = REPORT_DIR / "latest.json"
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    report_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    os.chmod(report_path, 0o600)
    os.chmod(latest_path, 0o600)

    counts = service_pass_counts(reliability)
    print(
        "wrote=" + str(report_path)
        + " ok=" + str(report["ok"]).lower()
        + " sync_dry_run=" + str(sync_dry_run.get("ok")).lower()
        + " exact_id_freshness=" + str(exact_id_freshness.get("ok")).lower()
        + " pass_counts=" + json.dumps(counts, sort_keys=True)
    )
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
