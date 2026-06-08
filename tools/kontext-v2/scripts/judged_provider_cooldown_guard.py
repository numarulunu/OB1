from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


DEFAULT_REPORTS_DIR = "/opt/kontext/reports/judged-plans"
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_OVERRIDE_ENV = "KONTEXT_OVERRIDE_PROVIDER_429_COOLDOWN"


def env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on", "override"}


def safe_load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def is_provider_429_failure(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is True:
        return False
    reason = str(payload.get("reason") or "").strip().lower()
    try:
        status = int(payload.get("error_status") or 0)
    except (TypeError, ValueError):
        status = 0
    try:
        completed_calls = int(payload.get("completed_calls") or 0)
    except (TypeError, ValueError):
        completed_calls = 0
    return status == 429 and completed_calls == 0 and "provider" in reason


def recent_provider_429_failures(reports_dir: str | Path, cooldown_minutes: int, *, now: float | None = None) -> list[dict[str, Any]]:
    base = Path(reports_dir)
    if not base.exists():
        return []
    current = time.time() if now is None else float(now)
    cutoff_seconds = max(int(cooldown_minutes), 0) * 60
    rows: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        age_seconds = max(0.0, current - path.stat().st_mtime)
        if age_seconds > cutoff_seconds:
            continue
        payload = safe_load_json(path)
        if is_provider_429_failure(payload):
            rows.append(
                {
                    "path_name": path.name,
                    "age_seconds": int(age_seconds),
                    "completed_calls": int(payload.get("completed_calls") or 0),
                    "error_status": 429,
                }
            )
    return rows


def build_report(reports_dir: str | Path, cooldown_minutes: int, override_env: str) -> dict[str, Any]:
    override = env_truthy(override_env)
    failures = [] if override else recent_provider_429_failures(reports_dir, cooldown_minutes)
    return {
        "ok": override or not failures,
        "mode": "judged-provider-cooldown-guard",
        "runs_model_calls": False,
        "reports_dir_name": Path(reports_dir).name,
        "cooldown_minutes": int(cooldown_minutes),
        "override_env": override_env,
        "override_active": override,
        "recent_429_failures": len(failures),
        "latest_failures": failures[:5],
        "reason": "override active" if override else ("recent provider 429 failure" if failures else "no recent provider 429 failure"),
        "notes": [
            "This guard performs no provider calls and reads only public judged-plan artifacts.",
            "It reports file names and counts only; it never reads provider env files or secrets.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Block paid judged runs when recent public artifacts show provider 429 rate limits.")
    parser.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--cooldown-minutes", type=int, default=DEFAULT_COOLDOWN_MINUTES)
    parser.add_argument("--override-env", default=DEFAULT_OVERRIDE_ENV)
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.reports_dir, args.cooldown_minutes, args.override_env)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
