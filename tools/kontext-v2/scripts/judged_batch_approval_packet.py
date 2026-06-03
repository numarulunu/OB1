from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def blocked(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "mode": "judged-batch-approval-packet",
        "runs_model_calls": False,
        "approval_required": False,
        "reason": reason,
    }


def parse_packet_spec(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError("packet spec must be suite=path")
    suite, path = value.split("=", 1)
    suite = suite.strip()
    path = path.strip()
    if not suite or not path:
        raise ValueError("packet spec must be suite=path")
    return suite, Path(path)


def safe_cost_total(value: Any) -> float:
    if isinstance(value, dict):
        value = value.get("total_usd")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def add_numbers(target: dict[str, float], source: Any) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        if isinstance(value, (int, float)):
            target[key] = round(target.get(key, 0.0) + float(value), 6)


def normalize_numbers(values: dict[str, float]) -> dict[str, int | float]:
    normalized: dict[str, int | float] = {}
    for key, value in values.items():
        normalized[key] = int(value) if float(value).is_integer() else round(value, 6)
    return normalized


def command_has_secret(command: str) -> bool:
    return any(pattern.search(command) for pattern in SECRET_PATTERNS)


def readiness_ok_for_suite(readiness: dict[str, Any], suite: str) -> bool:
    suites = readiness.get("suites") if isinstance(readiness.get("suites"), dict) else {}
    row = suites.get(suite) if isinstance(suites.get(suite), dict) else {}
    raw_scan = row.get("public_raw_scan") if isinstance(row.get("public_raw_scan"), dict) else {}
    return row.get("ready_for_paid_judged_run") is True and int(raw_scan.get("hit_count") or 0) == 0


def build_batch_packet(readiness_report: str | Path, packet_specs: list[str]) -> dict[str, Any]:
    readiness = load_json(readiness_report)
    if readiness.get("ok") is not True:
        return blocked("readiness report is not fully ready")

    calls: dict[str, float] = {}
    tokens: dict[str, float] = {}
    cost_parts: dict[str, float] = {}
    max_cost_total = 0.0
    required_env_vars: set[str] = set()
    commands: list[dict[str, Any]] = []
    suites: list[dict[str, Any]] = []

    for spec in packet_specs:
        suite, path = parse_packet_spec(spec)
        if not readiness_ok_for_suite(readiness, suite):
            return blocked(f"suite is not ready: {suite}")
        packet = load_json(path)
        if packet.get("ok") is not True or packet.get("approval_required") is not True:
            return blocked(f"approval packet is not ready: {suite}")
        if packet.get("runs_model_calls") is True:
            return blocked(f"approval packet already ran model calls: {suite}")
        command = str(packet.get("command_template") or "")
        if command_has_secret(command):
            return blocked("approval packet contains unsafe command text")

        add_numbers(calls, packet.get("estimated_llm_calls"))
        add_numbers(tokens, packet.get("estimated_tokens"))
        cost = packet.get("estimated_cost_usd")
        if isinstance(cost, dict):
            add_numbers(cost_parts, cost)
        else:
            cost_parts["total_usd"] = round(cost_parts.get("total_usd", 0.0) + safe_cost_total(cost), 6)
        max_cost_total = round(max_cost_total + safe_cost_total(packet.get("max_cost_usd")), 6)
        for env_name in packet.get("required_env_vars") or []:
            required_env_vars.add(str(env_name))

        suites.append(
            {
                "suite": suite,
                "dataset": packet.get("dataset"),
                "run_id": packet.get("run_id"),
                "selected_questions": packet.get("selected_questions"),
                "top_k_values": packet.get("top_k_values"),
                "benchmark_mode": packet.get("benchmark_mode"),
                "estimated_llm_calls": packet.get("estimated_llm_calls"),
                "estimated_cost_usd": packet.get("estimated_cost_usd"),
                "max_cost_usd": packet.get("max_cost_usd"),
            }
        )
        commands.append({"suite": suite, "command_template": command})

    return {
        "ok": True,
        "mode": "judged-batch-approval-packet",
        "runs_model_calls": False,
        "approval_required": True,
        "actual_judged_accuracy_proven": False,
        "readiness_report": str(readiness_report),
        "required_env_vars": sorted(required_env_vars),
        "summary": {
            "suite_count": len(suites),
            "estimated_llm_calls": normalize_numbers(calls),
            "estimated_tokens": normalize_numbers(tokens),
            "estimated_cost_usd": normalize_numbers(cost_parts),
            "max_cost_usd_total": int(max_cost_total) if max_cost_total.is_integer() else max_cost_total,
            "blocked_by": ["explicit paid-run approval"],
        },
        "suites": suites,
        "commands": commands,
        "notes": [
            "This packet does not call answerer or judge models.",
            "Run commands only after explicit approval for the stated total max cost.",
            "Command templates reference API-key environment variable names only, not secret values.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create one sanitized approval packet for multiple judged benchmark runs.")
    parser.add_argument("--readiness-report", required=True)
    parser.add_argument("--packet", action="append", required=True, help="Suite approval packet mapping: suite=path")
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    packet = build_batch_packet(args.readiness_report, args.packet)
    text = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if packet.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
