from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any


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


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def shell_command(parts: list[str | Path | float | int]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def find_gate(readiness: dict[str, Any], name: str) -> dict[str, Any] | None:
    gates = readiness.get("gates")
    if not isinstance(gates, list):
        return None
    for gate in gates:
        if isinstance(gate, dict) and gate.get("name") == name:
            return gate
    return None


def summary_from_packet(packet: dict[str, Any], preflight: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    packet_summary = packet.get("summary") if isinstance(packet.get("summary"), dict) else {}
    preflight_summary = preflight.get("summary") if isinstance(preflight.get("summary"), dict) else {}
    verification_summary = verification.get("summary") if isinstance(verification.get("summary"), dict) else {}
    return {
        "suite_count": safe_int(packet_summary.get("suite_count")),
        "command_count": safe_int(preflight_summary.get("command_count")) or len(packet.get("commands") or []),
        "estimated_llm_calls": packet_summary.get("estimated_llm_calls") if isinstance(packet_summary.get("estimated_llm_calls"), dict) else {},
        "estimated_tokens": packet_summary.get("estimated_tokens") if isinstance(packet_summary.get("estimated_tokens"), dict) else {},
        "estimated_cost_usd": packet_summary.get("estimated_cost_usd") if isinstance(packet_summary.get("estimated_cost_usd"), dict) else {},
        "max_cost_usd_total": format_float(safe_float(packet_summary.get("max_cost_usd_total") or preflight_summary.get("max_cost_usd_total"))),
        "verified_suites": safe_int(verification_summary.get("suites_verified")),
        "verified_questions": safe_int(verification_summary.get("total_questions")),
        "verified_weighted_accuracy": verification_summary.get("weighted_accuracy"),
        "verified_cost_usd": verification_summary.get("total_cost_usd"),
        "verified_usage_tokens": safe_int(verification_summary.get("total_usage_tokens")),
    }


def env_lists(packet: dict[str, Any], preflight: dict[str, Any]) -> tuple[list[str], list[str]]:
    preflight_summary = preflight.get("summary") if isinstance(preflight.get("summary"), dict) else {}
    required = as_string_list(packet.get("required_env_vars")) or as_string_list(preflight_summary.get("required_env_vars"))
    missing = as_string_list(preflight_summary.get("missing_env_vars")) or as_string_list(preflight.get("missing_env_vars"))
    return sorted(dict.fromkeys(required)), sorted(dict.fromkeys(missing))


def build_commands(
    batch_packet_path: str | Path,
    preflight_report_path: str | Path,
    batch_verification_path: str | Path,
    cutover_readiness_path: str | Path,
    max_total_cost_usd: float,
    execution_output: str | Path | None = None,
    shadow_report: str | Path | None = None,
    expanded_eval: str | Path | None = None,
    canary_smoke: str | Path | None = None,
    readiness_output: str | Path | None = None,
) -> list[dict[str, Any]]:
    execution_output = execution_output or str(Path(str(preflight_report_path)).with_name("judged-batch-execute-result.json"))
    readiness_output = readiness_output or cutover_readiness_path
    commands = [
        {
            "name": "preflight",
            "runs_model_calls": False,
            "command": shell_command(
                [
                    "python3",
                    "/opt/kontext/scripts/judged_batch_execute.py",
                    "--batch-packet",
                    batch_packet_path,
                    "--preflight",
                    "--max-total-cost-usd",
                    max_total_cost_usd,
                    "--output",
                    preflight_report_path,
                ]
            ),
        },
        {
            "name": "paid_execute_after_explicit_approval",
            "runs_model_calls": True,
            "command": shell_command(
                [
                    "python3",
                    "/opt/kontext/scripts/judged_batch_execute.py",
                    "--batch-packet",
                    batch_packet_path,
                    "--execute",
                    "--approve-cost",
                    "--max-total-cost-usd",
                    max_total_cost_usd,
                    "--output",
                    execution_output,
                ]
            ),
        },
        {
            "name": "verify_after_paid_execute",
            "runs_model_calls": False,
            "command": shell_command(
                [
                    "python3",
                    "/opt/kontext/scripts/judged_batch_verify.py",
                    "--batch-packet",
                    batch_packet_path,
                    "--output",
                    batch_verification_path,
                ]
            ),
        },
    ]
    if shadow_report and expanded_eval and canary_smoke:
        commands.append(
            {
                "name": "cutover_readiness_after_paid_verify",
                "runs_model_calls": False,
                "command": shell_command(
                    [
                        "python3",
                        "/opt/kontext/scripts/kontext_cutover_readiness.py",
                        "--shadow-report",
                        shadow_report,
                        "--expanded-eval",
                        expanded_eval,
                        "--canary-smoke",
                        canary_smoke,
                        "--judged-verification",
                        batch_verification_path,
                        "--output",
                        readiness_output,
                    ]
                ),
            }
        )
    return commands


def build_runbook(
    batch_packet_path: str | Path,
    preflight_report_path: str | Path,
    batch_verification_path: str | Path,
    cutover_readiness_path: str | Path,
    execution_output: str | Path | None = None,
    shadow_report: str | Path | None = None,
    expanded_eval: str | Path | None = None,
    canary_smoke: str | Path | None = None,
    readiness_output: str | Path | None = None,
) -> dict[str, Any]:
    packet = load_json(batch_packet_path)
    preflight = load_json(preflight_report_path)
    verification = load_json(batch_verification_path)
    readiness = load_json(cutover_readiness_path)

    input_issues: list[str] = []
    if packet.get("mode") != "judged-batch-approval-packet":
        input_issues.append("batch packet mode is invalid")
    if preflight.get("mode") != "judged-batch-execution-plan":
        input_issues.append("preflight report mode is invalid")
    if verification.get("mode") != "judged-batch-verification":
        input_issues.append("batch verification mode is invalid")

    required_env_vars, missing_env_vars = env_lists(packet, preflight)
    summary = summary_from_packet(packet, preflight, verification)
    judged_gate = find_gate(readiness, "official_judged_accuracy")
    packet_ok = packet.get("ok") is True and not input_issues
    preflight_ready = preflight.get("preflight_ready") is True and not missing_env_vars
    actual_proven = verification.get("ok") is True and verification.get("actual_judged_accuracy_proven") is True
    required_user_approval = packet.get("approval_required") is True and not actual_proven
    cutover_ready = readiness.get("ready_for_user_cutover_review") is True
    ready_to_run = packet_ok and preflight_ready and required_user_approval and not actual_proven

    blocking_items = list(input_issues)
    if packet.get("ok") is not True:
        blocking_items.append("batch approval packet is not ok")
    if preflight.get("preflight_ready") is not True and not missing_env_vars:
        blocking_items.append("paid-run preflight is not ready")
    blocking_items.extend(f"{name} missing from run environment" for name in missing_env_vars)
    if required_user_approval:
        blocking_items.append("explicit paid-run approval required")
    if not actual_proven:
        blocking_items.append("paid verification reports missing")
    judged_gate_passed = (
        not judged_gate
        or judged_gate.get("ok") is True
        or judged_gate.get("status") == "pass"
    )
    if not judged_gate_passed:
        blocking_items.append("cutover readiness official_judged_accuracy gate is failing")

    max_total_cost = safe_float(summary.get("max_cost_usd_total"))
    if actual_proven and cutover_ready:
        next_action = "Review cutover readiness for a separate user-approved production cutover decision."
    elif missing_env_vars:
        next_action = (
            "Configure the named API key env var in the run environment, rerun preflight, "
            "then get explicit approval before executing the paid judged batch."
        )
    elif ready_to_run:
        next_action = "Get explicit approval to run the guarded paid judged batch under the stated cost ceiling."
    else:
        next_action = "Resolve the listed blockers, regenerate this runbook, then rerun the no-call preflight."

    return {
        "ok": not input_issues,
        "mode": "judged-paid-runbook",
        "runs_model_calls": False,
        "actual_judged_accuracy_proven": actual_proven,
        "ready_to_run_paid_batch": ready_to_run,
        "required_user_approval": required_user_approval,
        "production_cutover_enabled": readiness.get("production_cutover_enabled") is True,
        "paths": {
            "batch_packet": str(batch_packet_path),
            "preflight_report": str(preflight_report_path),
            "batch_verification": str(batch_verification_path),
            "cutover_readiness": str(cutover_readiness_path),
        },
        "summary": summary,
        "required_env_vars": required_env_vars,
        "missing_env_vars": missing_env_vars,
        "status": {
            "batch_packet_ok": packet.get("ok") is True,
            "preflight_ready": preflight.get("preflight_ready") is True,
            "batch_verification_ok": verification.get("ok") is True,
            "cutover_ready_for_review": cutover_ready,
            "official_judged_accuracy_gate": judged_gate.get("status") if judged_gate else "missing",
        },
        "blocking_items": sorted(dict.fromkeys(blocking_items)),
        "next_action": next_action,
        "commands": build_commands(
            batch_packet_path,
            preflight_report_path,
            batch_verification_path,
            cutover_readiness_path,
            max_total_cost,
            execution_output=execution_output,
            shadow_report=shadow_report,
            expanded_eval=expanded_eval,
            canary_smoke=canary_smoke,
            readiness_output=readiness_output,
        ),
        "notes": [
            "This runbook does not call answerer or judge models.",
            "It reports env var names only and never needs API key values.",
            "The paid execution command must not be run without explicit approval and the stated cost ceiling.",
            "Kontext remains shadow/canary-only; this does not enable production writes or cutover.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a sanitized runbook for the Kontext judged paid benchmark gate.")
    parser.add_argument("--batch-packet", required=True)
    parser.add_argument("--preflight-report", required=True)
    parser.add_argument("--batch-verification", required=True)
    parser.add_argument("--cutover-readiness", required=True)
    parser.add_argument("--execution-output")
    parser.add_argument("--shadow-report")
    parser.add_argument("--expanded-eval")
    parser.add_argument("--canary-smoke")
    parser.add_argument("--readiness-output")
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_runbook(
        args.batch_packet,
        args.preflight_report,
        args.batch_verification,
        args.cutover_readiness,
        execution_output=args.execution_output,
        shadow_report=args.shadow_report,
        expanded_eval=args.expanded_eval,
        canary_smoke=args.canary_smoke,
        readiness_output=args.readiness_output,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
