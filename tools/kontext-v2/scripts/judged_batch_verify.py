from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any


STRICT_GATES = {
    "model_calls",
    "usage",
    "provider",
    "answerer_model",
    "judge_model",
    "question_count",
    "accuracy",
    "cost",
    "mem0_target",
    "raw_payload",
}
MIN_BATCH_QUESTIONS = 30


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


def option_value(argv: list[str], name: str) -> str | None:
    try:
        index = argv.index(name)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def verification_path_from_command(command: str) -> str | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    return option_value(argv, "--verification-output")


def load_batch_packet(path: str | Path) -> dict[str, Any]:
    packet = load_json(path)
    if packet.get("mode") != "judged-batch-approval-packet":
        raise ValueError("batch packet must have mode=judged-batch-approval-packet")
    return packet


def verification_specs(packet: dict[str, Any]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for row in packet.get("commands") or []:
        row = row if isinstance(row, dict) else {}
        suite = str(row.get("suite") or "unknown")
        path = verification_path_from_command(str(row.get("command_template") or ""))
        specs.append({"suite": suite, "path": path or ""})
    return specs


def failed_gate_names(report: dict[str, Any]) -> list[str]:
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
    failed = []
    for name, gate in sorted(gates.items()):
        if isinstance(gate, dict) and gate.get("ok") is not True:
            failed.append(str(name))
    return failed


def missing_strict_gates(report: dict[str, Any]) -> list[str]:
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
    return sorted(STRICT_GATES - set(gates.keys()))


def suite_summary(suite: str, path: str, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    cost = report.get("estimated_cost_usd") if isinstance(report.get("estimated_cost_usd"), dict) else {}
    usage = report.get("actual_usage") if isinstance(report.get("actual_usage"), dict) else {}
    return {
        "suite": suite,
        "verification_report": path,
        "ok": report.get("ok") is True,
        "dataset": report.get("dataset"),
        "run_id": report.get("run_id"),
        "provider": report.get("provider"),
        "runs_model_calls": report.get("runs_model_calls") is True,
        "cutoff": summary.get("cutoff"),
        "total": safe_int(summary.get("total")),
        "passed": safe_int(summary.get("passed")),
        "accuracy": round(safe_float(summary.get("accuracy")), 4),
        "mem0_target_accuracy": summary.get("mem0_target_accuracy"),
        "accuracy_delta_vs_mem0_target": summary.get("accuracy_delta_vs_mem0_target"),
        "cost_usd": format_float(safe_float(cost.get("total_usd"))),
        "usage_tokens": safe_int(usage.get("total_tokens")),
        "failed_gates": failed_gate_names(report),
        "missing_strict_gates": missing_strict_gates(report),
    }


def build_batch_verification(batch_packet_path: str | Path) -> dict[str, Any]:
    packet = load_batch_packet(batch_packet_path)
    blocked_by: list[str] = []
    suites: list[dict[str, Any]] = []
    total_questions = 0
    total_passed = 0
    total_cost = 0.0
    total_usage = 0

    if packet.get("ok") is not True:
        blocked_by.append("batch approval packet is not ok")
    if packet.get("runs_model_calls") is True:
        blocked_by.append("batch approval packet already ran model calls")

    for spec in verification_specs(packet):
        suite = spec["suite"]
        path = spec["path"]
        if not path or not Path(path).exists():
            blocked_by.append(f"missing verification report: {suite}")
            continue
        report = load_json(path)
        row = suite_summary(suite, path, report)
        suites.append(row)
        total_questions += row["total"]
        total_passed += row["passed"]
        total_cost += float(row["cost_usd"])
        total_usage += row["usage_tokens"]
        if row["ok"] is not True:
            blocked_by.append(f"failed suite verification: {suite}")
        if report.get("mode") != "judged-benchmark-verification":
            blocked_by.append(f"invalid verification report mode: {suite}")
        if row["runs_model_calls"] is not True:
            blocked_by.append(f"suite did not prove model calls: {suite}")
        if row["missing_strict_gates"]:
            blocked_by.append(f"missing strict verification gates: {suite}")

    weighted_accuracy = round(total_passed / total_questions, 4) if total_questions else 0.0
    suite_count = len(verification_specs(packet))
    if total_questions < MIN_BATCH_QUESTIONS:
        blocked_by.append("question_count")
    all_ok = len(blocked_by) == 0 and len(suites) == suite_count and suite_count > 0
    return {
        "ok": all_ok,
        "mode": "judged-batch-verification",
        "runs_model_calls": False,
        "actual_judged_accuracy_proven": all_ok,
        "batch_packet": str(batch_packet_path),
        "blocked_by": blocked_by,
        "summary": {
            "suite_count": suite_count,
            "suites_verified": len(suites),
            "total_questions": total_questions,
            "total_passed": total_passed,
            "weighted_accuracy": weighted_accuracy,
            "total_cost_usd": format_float(total_cost),
            "total_usage_tokens": total_usage,
            "min_batch_questions": MIN_BATCH_QUESTIONS,
        },
        "suites": suites,
        "notes": [
            "This batch verifier does not call answerer or judge models.",
            "It accepts only strict per-suite judged-benchmark-verification reports.",
            "It reports aggregate counts, costs, usage, and gate names only.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate strict judged benchmark verification reports for a batch.")
    parser.add_argument("--batch-packet", required=True)
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_batch_verification(args.batch_packet)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
