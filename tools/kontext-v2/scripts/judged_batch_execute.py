from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any


EXPECTED_RUNNER = "/opt/kontext/scripts/judged_benchmark_run.py"
ALLOWED_PYTHON_COMMANDS = {"python", "python3"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
UNSAFE_COMMAND_MARKERS = ["\n", "\r", ";", "&&", "||", "|", "`", "$(", ">", "<"]


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def safe_float(value: Any) -> float:
    if isinstance(value, dict):
        value = value.get("total_usd")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def format_float(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 6)


def command_text_is_unsafe(command: str) -> bool:
    if any(pattern.search(command) for pattern in SECRET_PATTERNS):
        return True
    return any(marker in command for marker in UNSAFE_COMMAND_MARKERS)


def option_value(argv: list[str], name: str) -> str | None:
    try:
        index = argv.index(name)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def validate_command(suite: str, command: str, required_env_vars: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    if not command or command_text_is_unsafe(command):
        return None, "unsafe command template"
    try:
        argv = shlex.split(command)
    except ValueError:
        return None, "unsafe command template"
    if len(argv) < 2 or argv[0] not in ALLOWED_PYTHON_COMMANDS or argv[1] != EXPECTED_RUNNER:
        return None, "unexpected command runner"
    required_flags = [
        "--input-bundle",
        "--output",
        "--execute",
        "--provider",
        "--max-questions",
        "--approve-cost",
        "--max-cost-usd",
        "--api-key-env",
        "--verification-output",
    ]
    missing = [flag for flag in required_flags if flag not in argv]
    if missing:
        return None, "command is missing required judged-run flags"
    if option_value(argv, "--provider") != "openai-compatible":
        return None, "command provider is not openai-compatible"
    api_key_env = option_value(argv, "--api-key-env")
    if not api_key_env or api_key_env not in required_env_vars:
        return None, "command api-key env is not in batch required env vars"
    try:
        command_max_cost = float(option_value(argv, "--max-cost-usd") or "")
    except ValueError:
        return None, "command max cost is invalid"
    try:
        max_questions = int(option_value(argv, "--max-questions") or "")
        verify_expected_questions = int(option_value(argv, "--verify-expected-questions") or "")
    except ValueError:
        return None, "command max questions is invalid"
    if max_questions != verify_expected_questions:
        return None, "command max questions does not match verification expected questions"
    return (
        {
            "suite": suite,
            "argv": argv,
            "api_key_env": api_key_env,
            "max_cost_usd": format_float(command_max_cost),
            "max_questions": max_questions,
            "output": option_value(argv, "--output"),
            "verification_output": option_value(argv, "--verification-output"),
        },
        None,
    )


def load_batch_packet(path: str | Path) -> dict[str, Any]:
    packet = load_json(path)
    if packet.get("mode") != "judged-batch-approval-packet":
        raise ValueError("batch packet must have mode=judged-batch-approval-packet")
    return packet


def validate_batch_packet(packet: dict[str, Any]) -> list[str]:
    blocked_by: list[str] = []
    if packet.get("ok") is not True:
        blocked_by.append("batch approval packet is not ok")
    if packet.get("runs_model_calls") is True:
        blocked_by.append("batch approval packet already ran model calls")
    if packet.get("approval_required") is not True:
        blocked_by.append("batch approval packet does not require approval")
    if packet.get("actual_judged_accuracy_proven") is True:
        blocked_by.append("batch approval packet already claims judged accuracy")
    if not isinstance(packet.get("commands"), list) or not packet.get("commands"):
        blocked_by.append("batch approval packet has no commands")
    return blocked_by


def summarize_packet(packet: dict[str, Any], commands: list[dict[str, Any]], missing_env_vars: list[str]) -> dict[str, Any]:
    summary = packet.get("summary") if isinstance(packet.get("summary"), dict) else {}
    estimated_calls = summary.get("estimated_llm_calls") if isinstance(summary.get("estimated_llm_calls"), dict) else {}
    estimated_tokens = summary.get("estimated_tokens") if isinstance(summary.get("estimated_tokens"), dict) else {}
    return {
        "suite_count": safe_int(summary.get("suite_count")) or len(commands),
        "command_count": len(commands),
        "estimated_llm_calls": estimated_calls,
        "estimated_tokens": estimated_tokens,
        "estimated_cost_usd": summary.get("estimated_cost_usd"),
        "max_cost_usd_total": format_float(safe_float(summary.get("max_cost_usd_total"))),
        "required_env_vars": list(packet.get("required_env_vars") or []),
        "missing_env_vars": missing_env_vars,
    }


def build_execution_plan(
    batch_packet_path: str | Path,
    execute: bool = False,
    preflight: bool = False,
    approve_cost: bool = False,
    max_total_cost_usd: float | None = None,
    check_env: bool = False,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    packet = load_batch_packet(batch_packet_path)
    blocked_by = validate_batch_packet(packet)
    required_env_vars = {str(name) for name in packet.get("required_env_vars") or []}
    parsed_commands: list[dict[str, Any]] = []

    for row in packet.get("commands") or []:
        row = row if isinstance(row, dict) else {}
        command, reason = validate_command(str(row.get("suite") or "unknown"), str(row.get("command_template") or ""), required_env_vars)
        if reason:
            blocked_by.append(reason)
            continue
        if command:
            parsed_commands.append(command)

    env = os.environ if environ is None else environ
    should_check_env = check_env or execute or preflight
    missing_env_vars = sorted(name for name in required_env_vars if should_check_env and not env.get(name))
    if preflight:
        if max_total_cost_usd is None:
            blocked_by.append("max total cost ceiling is required")
        elif safe_float(packet.get("summary", {}).get("max_cost_usd_total")) > float(max_total_cost_usd):
            blocked_by.append("batch max cost exceeds approved ceiling")
        if missing_env_vars:
            blocked_by.append("required env vars are missing")
    elif not execute:
        blocked_by.extend(["execution flag is required", "cost approval flag is required"])
    else:
        if not approve_cost:
            blocked_by.append("cost approval flag is required")
        if max_total_cost_usd is None:
            blocked_by.append("max total cost ceiling is required")
        elif safe_float(packet.get("summary", {}).get("max_cost_usd_total")) > float(max_total_cost_usd):
            blocked_by.append("batch max cost exceeds approved ceiling")
        if missing_env_vars:
            blocked_by.append("required env vars are missing")

    safety_blockers = [
        reason
        for reason in blocked_by
        if reason
        not in {
            "execution flag is required",
            "cost approval flag is required",
        }
    ]
    preflight_ready = preflight and not safety_blockers
    execution_enabled = execute and approve_cost and not safety_blockers
    return {
        "ok": len(safety_blockers) == 0,
        "mode": "judged-batch-execution-plan",
        "runs_model_calls": False,
        "preflight": bool(preflight),
        "preflight_ready": bool(preflight_ready),
        "approval_required": not execution_enabled,
        "execution_enabled": execution_enabled,
        "batch_packet": str(batch_packet_path),
        "blocked_by": blocked_by,
        "summary": summarize_packet(packet, parsed_commands, missing_env_vars),
        "commands": [
            {
                "suite": command["suite"],
                "argv": command["argv"],
                "api_key_env": command["api_key_env"],
                "max_cost_usd": command["max_cost_usd"],
                "max_questions": command["max_questions"],
                "output": command["output"],
                "verification_output": command["verification_output"],
            }
            for command in parsed_commands
        ],
        "notes": [
            "This planning step does not call answerer or judge models.",
            "Preflight mode validates the future paid-run prerequisites without launching commands.",
            "Execution requires --execute, --approve-cost, a total cost ceiling, and required env vars by name.",
            "Commands are parsed with shlex and executed without a shell when execution is explicitly enabled.",
        ],
    }


def verification_actual_cost(path: str | None) -> float:
    if not path:
        return 0.0
    try:
        payload = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0.0
    return safe_float(payload.get("estimated_cost_usd"))


def skipped_command_result(command: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "suite": command.get("suite"),
        "status": "skipped",
        "reason": reason,
        "returncode": None,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "output": command.get("output"),
        "verification_output": command.get("verification_output"),
    }


def execute_plan(plan: dict[str, Any], max_runtime_seconds: int = 900) -> dict[str, Any]:
    if plan.get("execution_enabled") is not True:
        return plan
    command_results = []
    blocked_by: list[str] = []
    actual_cost = 0.0
    commands = [row for row in plan.get("commands") or [] if isinstance(row, dict)]
    max_total_cost = safe_float(plan.get("summary", {}).get("max_cost_usd_total"))
    start = time.monotonic()

    for index, command in enumerate(commands):
        next_max_cost = safe_float(command.get("max_cost_usd"))
        if max_total_cost and actual_cost + next_max_cost > max_total_cost:
            reason = f"batch running cost would exceed approved ceiling before {command.get('suite')}"
            blocked_by.append(reason)
            command_results.extend(skipped_command_result(row, reason) for row in commands[index:])
            break
        elapsed = time.monotonic() - start
        remaining_timeout = max(float(max_runtime_seconds) - elapsed, 0.0)
        if remaining_timeout <= 0:
            reason = f"command timed out: {command.get('suite')}"
            blocked_by.append(reason)
            command_results.extend(skipped_command_result(row, reason) for row in commands[index:])
            break
        argv = command.get("argv") if isinstance(command.get("argv"), list) else []
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=remaining_timeout)
        except subprocess.TimeoutExpired:
            reason = f"command timed out: {command.get('suite')}"
            blocked_by.append(reason)
            command_results.append(
                {
                    "suite": command.get("suite"),
                    "status": "timeout",
                    "returncode": None,
                    "stdout_bytes": 0,
                    "stderr_bytes": 0,
                    "output": command.get("output"),
                    "verification_output": command.get("verification_output"),
                }
            )
            command_results.extend(skipped_command_result(row, reason) for row in commands[index + 1 :])
            break
        suite_cost = verification_actual_cost(command.get("verification_output"))
        actual_cost += suite_cost
        command_results.append(
            {
                "suite": command.get("suite"),
                "status": "completed" if proc.returncode == 0 else "failed",
                "returncode": proc.returncode,
                "stdout_bytes": len(proc.stdout or ""),
                "stderr_bytes": len(proc.stderr or ""),
                "output": command.get("output"),
                "verification_output": command.get("verification_output"),
                "actual_cost_usd": format_float(suite_cost),
            }
        )
        if proc.returncode != 0:
            reason = f"command failed: {command.get('suite')}"
            blocked_by.append(reason)
            command_results.extend(skipped_command_result(row, reason) for row in commands[index + 1 :])
            break
        if max_total_cost and actual_cost > max_total_cost:
            reason = f"batch running cost exceeded approved ceiling after {command.get('suite')}"
            blocked_by.append(reason)
            command_results.extend(skipped_command_result(row, reason) for row in commands[index + 1 :])
            break
    ok = len(blocked_by) == 0
    summary = dict(plan.get("summary") or {})
    summary["actual_cost_usd"] = format_float(actual_cost)
    return {
        "ok": ok,
        "mode": "judged-batch-execution-result",
        "runs_model_calls": True,
        "batch_packet": plan.get("batch_packet"),
        "blocked_by": blocked_by,
        "summary": summary,
        "commands": command_results,
        "notes": [
            "Command stdout/stderr was not embedded to keep the batch report sanitized.",
            "Inspect per-suite sanitized run and verification reports for accepted judged evidence.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and optionally execute a judged benchmark batch approval packet.")
    parser.add_argument("--batch-packet", required=True)
    parser.add_argument("--output")
    parser.add_argument("--execute", action="store_true", help="Actually launch the guarded judged benchmark commands.")
    parser.add_argument("--preflight", action="store_true", help="Validate paid-run prerequisites without launching commands.")
    parser.add_argument("--approve-cost", action="store_true", help="Required with --execute.")
    parser.add_argument("--max-total-cost-usd", type=float, help="Required total ceiling with --execute.")
    parser.add_argument("--max-runtime-seconds", type=int, default=900, help="Batch wall-clock timeout budget for guarded execution.")
    parser.add_argument("--check-env", action="store_true", help="Check required env var names without printing values.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = build_execution_plan(
        args.batch_packet,
        execute=args.execute,
        preflight=args.preflight,
        approve_cost=args.approve_cost,
        max_total_cost_usd=args.max_total_cost_usd,
        check_env=args.check_env or args.execute or args.preflight,
    )
    result = execute_plan(plan, max_runtime_seconds=args.max_runtime_seconds) if args.execute and plan.get("execution_enabled") else plan
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
