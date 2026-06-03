from __future__ import annotations

import argparse
from collections import deque
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECENT_SHADOW_WINDOWS = (20, 50, 100)
MIN_RECENT_HEAD_TO_HEAD_ROWS = 50
REQUIRED_WRITE_CANARY_TOOLS = ("save", "update", "flag_memory")
READ_AFTER_WRITE_TOOLS = {"save", "update", "submit_memory_override"}
DEFAULT_MAX_INPUT_AGE_HOURS = 48.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def parse_report_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_hours(then: datetime, now: datetime) -> float:
    return round(max((now - then).total_seconds(), 0.0) / 3600, 3)


def input_freshness_report(
    path: str | Path,
    payload: dict[str, Any],
    max_age_hours: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    target = Path(path)
    now = now or datetime.now(timezone.utc)
    max_age = max(float(max_age_hours), 0.0)
    if max_age <= 0:
        return {
            "status": "pass",
            "summary": "freshness check disabled",
            "basename": target.name,
            "max_age_hours": max_age,
        }
    if not target.exists():
        return {
            "status": "fail",
            "summary": "input file is missing",
            "basename": target.name,
            "max_age_hours": max_age,
        }

    reasons: list[str] = []
    mtime = datetime.fromtimestamp(target.stat().st_mtime, timezone.utc)
    mtime_age = age_hours(mtime, now)
    if mtime_age > max_age:
        reasons.append(f"mtime age {mtime_age}h exceeds {max_age:g}h")

    generated_at = parse_report_timestamp(payload.get("generated_at"))
    generated_at_age: float | None = None
    if payload.get("generated_at") and generated_at is None:
        reasons.append("generated_at is not parseable")
    elif generated_at is not None:
        generated_at_age = age_hours(generated_at, now)
        if generated_at_age > max_age:
            reasons.append(f"generated_at age {generated_at_age}h exceeds {max_age:g}h")

    status = "fail" if reasons else "pass"
    return {
        "status": status,
        "summary": "; ".join(reasons) if reasons else "input evidence is fresh",
        "basename": target.name,
        "max_age_hours": max_age,
        "mtime_age_hours": mtime_age,
        "generated_at_present": generated_at is not None,
        "generated_at_age_hours": generated_at_age,
    }


def read_recent_jsonl(path: str | Path, recent_limit: int) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(recent_limit, 1))
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return list(rows)


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def add_check(
    checks: dict[str, dict[str, Any]],
    name: str,
    status: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    checks[name] = {
        "status": status,
        "summary": summary,
        "evidence": evidence or {},
    }


def input_freshness_check(input_freshness: dict[str, dict[str, Any]] | None) -> tuple[str, str, dict[str, Any], list[str]]:
    if not input_freshness:
        return "pass", "input freshness was not checked", {}, []
    failures = {
        name: report
        for name, report in input_freshness.items()
        if isinstance(report, dict) and report.get("status") == "fail"
    }
    status = "fail" if failures else "pass"
    attention = [str(report.get("summary") or f"{name} input is stale") for name, report in failures.items()]
    summary = "all cutover evidence inputs are fresh" if not failures else f"{len(failures)} cutover evidence input(s) are stale"
    return status, summary, input_freshness, attention


def named_gate(report: dict[str, Any], name: str) -> dict[str, Any]:
    gates = report.get("gates")
    if isinstance(gates, dict):
        gate = gates.get(name)
        return gate if isinstance(gate, dict) else {}
    if isinstance(gates, list):
        for gate in gates:
            if isinstance(gate, dict) and gate.get("name") == name:
                return gate
    return {}


def short_pass(value: dict[str, Any]) -> str:
    return f"{safe_int(value.get('passed'))}/{safe_int(value.get('cases'))}"


def retrieval_check(readiness: dict[str, Any], gate_name: str) -> tuple[str, str, dict[str, Any]]:
    gate = named_gate(readiness, gate_name)
    if not gate:
        return "warn", f"{gate_name} report is missing", {}
    evidence = gate.get("evidence") if isinstance(gate.get("evidence"), dict) else {}
    kontext = evidence.get("kontext") if isinstance(evidence.get("kontext"), dict) else {}
    mem0 = evidence.get("mem0") if isinstance(evidence.get("mem0"), dict) else {}
    status = "pass" if gate.get("status") == "pass" else "fail"
    rendered = {
        "kontext_passed": short_pass(kontext),
        "mem0_passed": short_pass(mem0),
        "kontext_mrr": safe_float(kontext.get("first_satisfying_mrr")),
        "mem0_mrr": safe_float(mem0.get("first_satisfying_mrr")),
        "kontext_avg_latency_ms": safe_float(kontext.get("avg_latency_ms")),
        "mem0_avg_latency_ms": safe_float(mem0.get("avg_latency_ms")),
    }
    summary = (
        f"Kontext {rendered['kontext_passed']} vs Mem0 {rendered['mem0_passed']}; "
        f"latency {rendered['kontext_avg_latency_ms']}ms vs {rendered['mem0_avg_latency_ms']}ms"
    )
    return status, summary, rendered


def ob1_retrieval_quality_check(report: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str]]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    groups = report.get("groups") if isinstance(report.get("groups"), dict) else {}
    protected = groups.get("protected_history") if isinstance(groups.get("protected_history"), dict) else {}
    project = groups.get("project_continuity") if isinstance(groups.get("project_continuity"), dict) else {}
    failed = safe_int(summary.get("failed"))
    protected_failed = safe_int(protected.get("failed"))
    project_failed = safe_int(project.get("failed"))
    ok = (
        report.get("ok") is True
        and report.get("runs_model_calls") is False
        and failed == 0
        and safe_int(protected.get("cases")) >= 10
        and safe_int(project.get("cases")) >= 15
        and protected_failed == 0
        and project_failed == 0
    )
    evidence = {
        "mode": str(report.get("mode") or ""),
        "runs_model_calls": report.get("runs_model_calls") is True,
        "total_passed": short_pass(summary),
        "protected_history_passed": short_pass(protected),
        "project_continuity_passed": short_pass(project),
        "total_failed": failed,
        "protected_history_failed": protected_failed,
        "project_continuity_failed": project_failed,
    }
    status = "pass" if ok else "fail"
    summary_text = (
        f"OB1 gate {evidence['total_passed']}; "
        f"protected {evidence['protected_history_passed']}; "
        f"project {evidence['project_continuity_passed']}"
    )
    attention = [] if ok else ["OB1 retrieval quality gate failed"]
    return status, summary_text, evidence, attention


def scorecard_check(scorecard: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str]]:
    blockers = [str(item) for item in scorecard.get("blockers") or []]
    quality_gaps = [str(item) for item in scorecard.get("quality_gaps") or []]
    production_enabled = scorecard.get("production_cutover_enabled") is True
    ok = (
        scorecard.get("ok") is True
        and scorecard.get("goal_complete_candidate") is True
        and not blockers
        and not quality_gaps
        and not production_enabled
    )
    status = "pass" if ok else "fail"
    evidence = {
        "goal_complete_candidate": scorecard.get("goal_complete_candidate") is True,
        "blocker_count": safe_int((scorecard.get("summary") or {}).get("blocker_count")),
        "quality_gap_count": safe_int((scorecard.get("summary") or {}).get("quality_gap_count")),
        "production_cutover_enabled": production_enabled,
        "source_of_truth": scorecard.get("source_of_truth"),
    }
    attention: list[str] = []
    if status == "fail":
        attention.append("scorecard is not green")
    if production_enabled:
        attention.append("production cutover is enabled unexpectedly")
    summary = "final scorecard is green" if status == "pass" else "final scorecard has blockers or gaps"
    return status, summary, evidence, attention


def readiness_check(readiness: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str]]:
    counts = readiness.get("status_counts") if isinstance(readiness.get("status_counts"), dict) else {}
    ok = (
        readiness.get("ok") is True
        and readiness.get("ready_for_user_cutover_review") is True
        and safe_int(counts.get("fail")) == 0
        and safe_int(counts.get("missing")) == 0
    )
    status = "pass" if ok else "fail"
    evidence = {
        "ready_for_user_cutover_review": readiness.get("ready_for_user_cutover_review") is True,
        "pass": safe_int(counts.get("pass")),
        "fail": safe_int(counts.get("fail")),
        "missing": safe_int(counts.get("missing")),
        "production_cutover_enabled": readiness.get("production_cutover_enabled") is True,
    }
    attention = [] if ok else ["cutover readiness is not green"]
    summary = f"{evidence['pass']} pass, {evidence['fail']} fail, {evidence['missing']} missing"
    return status, summary, evidence, attention


def judged_check(readiness: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str]]:
    gate = named_gate(readiness, "official_judged_accuracy")
    if not gate:
        return "warn", "official judged gate is missing from readiness report", {}, ["official judged gate missing"]
    evidence = gate.get("evidence") if isinstance(gate.get("evidence"), dict) else {}
    ok = gate.get("status") == "pass" and evidence.get("actual_judged_accuracy_proven") is True
    rendered = {
        "actual_judged_accuracy_proven": evidence.get("actual_judged_accuracy_proven") is True,
        "weighted_accuracy": evidence.get("weighted_accuracy"),
        "total_questions": evidence.get("total_questions"),
        "total_cost_usd": evidence.get("total_cost_usd"),
    }
    return (
        "pass" if ok else "fail",
        "official judged gate is proven" if ok else "official judged gate is not proven",
        rendered,
        [] if ok else ["official judged gate is not proven"],
    )


def canary_check(readiness: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str]]:
    gate = named_gate(readiness, "public_mcp_canary_dry_run")
    if not gate:
        return "warn", "public canary gate is missing", {}, ["public canary gate missing"]
    evidence = gate.get("evidence") if isinstance(gate.get("evidence"), dict) else {}
    writes_applied = safe_int(evidence.get("writes_applied_total"))
    ok = gate.get("status") == "pass" and writes_applied == 0
    rendered = {"writes_applied_total": writes_applied}
    return (
        "pass" if ok else "fail",
        "public canary is dry-run safe" if ok else "public canary applied writes or failed",
        rendered,
        [] if ok else ["public canary is not dry-run safe"],
    )


def retirement_gate_check(retirement_gate: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str]]:
    counts = retirement_gate.get("status_counts") if isinstance(retirement_gate.get("status_counts"), dict) else {}
    gates = retirement_gate.get("gates") if isinstance(retirement_gate.get("gates"), list) else []
    failing_gates = [
        str(gate.get("name") or "")
        for gate in gates
        if isinstance(gate, dict) and gate.get("status") != "pass"
    ]
    monitor = named_gate(retirement_gate, "legacy_mem0_frozen_and_unused")
    monitor_evidence = monitor.get("evidence") if isinstance(monitor.get("evidence"), dict) else {}
    blockers = [str(item) for item in retirement_gate.get("blockers") or []]
    ok = retirement_gate.get("ok") is True and safe_int(counts.get("fail")) == 0
    status = "pass" if ok else "fail"
    evidence = {
        "decision": str(retirement_gate.get("decision") or ""),
        "pass": safe_int(counts.get("pass")),
        "fail": safe_int(counts.get("fail")),
        "failing_gates": [name for name in failing_gates if name],
        "blocker_count": len(blockers),
        "legacy_mem0_monitor_hours": safe_float(monitor_evidence.get("monitor_hours")),
        "legacy_mem0_recent_read_count": safe_int(monitor_evidence.get("recent_read_count")),
        "legacy_mem0_recent_write_count": safe_int(monitor_evidence.get("recent_write_count")),
        "legacy_mem0_writes_enabled": monitor_evidence.get("writes_enabled") is True,
    }
    summary = (
        f"retirement gate {evidence['pass']} pass, {evidence['fail']} fail"
        if counts
        else "retirement gate status is unavailable"
    )
    return status, summary, evidence, [] if ok else ["retirement gate is not green"]


def usage_check(usage_report: dict[str, Any] | None, max_total_tokens: int) -> tuple[str, str, dict[str, Any], list[str]]:
    if not usage_report:
        return "warn", "usage report was not provided", {}, ["usage report missing"]
    summary = usage_report.get("summary") if isinstance(usage_report.get("summary"), dict) else {}
    total_tokens = safe_int(summary.get("total_tokens"))
    malformed = safe_int(summary.get("malformed_rows"))
    ok = usage_report.get("ok") is True and malformed == 0 and (max_total_tokens <= 0 or total_tokens <= max_total_tokens)
    status = "pass" if ok else "warn"
    origins = usage_report.get("origins") if isinstance(usage_report.get("origins"), dict) else {}
    origin_summary = {
        str(origin): {
            "searches": safe_int(data.get("searches")) if isinstance(data, dict) else 0,
            "ingestion_rows": safe_int(data.get("ingestion_rows")) if isinstance(data, dict) else 0,
            "avg_search_latency_ms": safe_float(data.get("avg_search_latency_ms")) if isinstance(data, dict) else 0.0,
        }
        for origin, data in sorted(origins.items())
    }
    evidence = {
        "total_tokens": total_tokens,
        "malformed_rows": malformed,
        "origin_summary": origin_summary,
    }
    attention = [] if ok else ["usage report has warnings"]
    return status, "usage telemetry is clean" if ok else "usage telemetry needs review", evidence, attention


def client_check(
    client_status: dict[str, Any] | None,
    expected_origins: list[str],
) -> tuple[str, str, dict[str, Any], list[str]]:
    if not client_status:
        return "warn", "client status report was not provided", {}, ["client status report missing"]
    origins = client_status.get("origins") if isinstance(client_status.get("origins"), dict) else {}
    rendered: dict[str, Any] = {}
    attention: list[str] = []
    hard_fail = False
    warn = False
    for origin in expected_origins:
        data = origins.get(origin) if isinstance(origins.get(origin), dict) else {}
        status = str(data.get("status") or "missing")
        rendered[origin] = {
            "status": status,
            "hook_count": safe_int(data.get("hook_count")),
            "search_count": safe_int(data.get("search_count")),
            "ingest_rows": safe_int(data.get("ingest_rows")),
            "errors": safe_int(data.get("errors")),
        }
        if status in {"missing", "error"}:
            hard_fail = True
            attention.append(f"{origin} client status is {status}")
        elif status != "healthy":
            warn = True
            attention.append(f"{origin} client status is {status}")
    status = "fail" if hard_fail else "warn" if warn else "pass"
    summary = "expected clients are healthy" if status == "pass" else "one or more expected clients need review"
    return status, summary, rendered, attention


def avg_float(rows: list[dict[str, Any]], key: str) -> float:
    values = [safe_float(row.get(key)) for row in rows if safe_float(row.get(key)) > 0]
    return round(sum(values) / len(values), 3) if values else 0.0


def percentile_float(rows: list[dict[str, Any]], key: str, percentile: float) -> float:
    values = sorted(safe_float(row.get(key)) for row in rows if safe_float(row.get(key)) > 0)
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 3)
    clamped = min(max(float(percentile), 0.0), 1.0)
    index = min(int(round((len(values) - 1) * clamped)), len(values) - 1)
    return round(values[index], 3)


def latency_winner(mem0_avg: float, kontext_avg: float) -> str:
    if mem0_avg <= 0 or kontext_avg <= 0:
        return "unknown"
    delta = round(kontext_avg - mem0_avg, 3)
    if abs(delta) <= 100:
        return "tie"
    return "kontext" if delta < 0 else "mem0"


def summarize_shadow_window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows_seen = len(rows)
    error_count = sum(1 for row in rows if str(row.get("kontext_error") or "").strip())
    mem0_nonzero = [row for row in rows if safe_int(row.get("mem0_count")) > 0]
    kontext_zero_when_mem0_nonzero = sum(1 for row in mem0_nonzero if safe_int(row.get("kontext_count")) == 0)
    shared_any_count = sum(1 for row in rows if row.get("shared_any") is True)
    shared_first_count = sum(1 for row in rows if row.get("shared_first") is True)
    mem0_avg = avg_float(rows, "mem0_latency_ms")
    kontext_avg = avg_float(rows, "kontext_latency_ms")
    return {
        "rows_seen": rows_seen,
        "error_count": error_count,
        "mem0_nonzero_rows": len(mem0_nonzero),
        "kontext_zero_when_mem0_nonzero_count": kontext_zero_when_mem0_nonzero,
        "shared_any_rate": round(shared_any_count / rows_seen, 4) if rows_seen else 0.0,
        "shared_first_rate": round(shared_first_count / rows_seen, 4) if rows_seen else 0.0,
        "avg_mem0_latency_ms": mem0_avg,
        "avg_kontext_latency_ms": kontext_avg,
        "p95_mem0_latency_ms": percentile_float(rows, "mem0_latency_ms", 0.95),
        "p95_kontext_latency_ms": percentile_float(rows, "kontext_latency_ms", 0.95),
        "avg_latency_delta_ms": round(kontext_avg - mem0_avg, 3) if mem0_avg and kontext_avg else 0.0,
        "latency_winner": latency_winner(mem0_avg, kontext_avg),
    }


def shadow_comparison_check(rows: list[dict[str, Any]] | None) -> tuple[str, str, dict[str, Any], list[str]]:
    if rows is None:
        return "warn", "real-world shadow comparison log was not provided", {}, ["real-world shadow comparison log missing"]
    comparisons = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("event") == "kontext_shadow_compare"
    ]
    if not comparisons:
        return (
            "warn",
            "real-world shadow comparison has no rows yet",
            {"rows_seen": 0},
            ["real-world shadow comparison has no rows yet"],
        )

    rows_seen = len(comparisons)
    origins: dict[str, int] = {}
    for row in comparisons:
        origin = str(row.get("origin") or "unknown")[:80]
        origins[origin] = origins.get(origin, 0) + 1

    evidence = summarize_shadow_window(comparisons)
    evidence["origin_counts"] = dict(sorted(origins.items()))
    evidence["recent_windows"] = {
        f"last_{window}": summarize_shadow_window(comparisons[-window:])
        for window in RECENT_SHADOW_WINDOWS
    }
    error_count = safe_int(evidence.get("error_count"))
    mem0_nonzero_count = safe_int(evidence.get("mem0_nonzero_rows"))
    kontext_zero_when_mem0_nonzero = safe_int(evidence.get("kontext_zero_when_mem0_nonzero_count"))
    attention: list[str] = []
    if error_count:
        attention.append("real-world Kontext shadow searches have errors")
    if kontext_zero_when_mem0_nonzero:
        attention.append("Kontext returned zero rows for one or more Mem0 nonzero searches")

    if error_count == rows_seen or (mem0_nonzero_count and kontext_zero_when_mem0_nonzero == mem0_nonzero_count):
        status = "fail"
    elif error_count or kontext_zero_when_mem0_nonzero:
        status = "warn"
    else:
        status = "pass"
    summary = "real-world Mem0 searches are mirrored to Kontext" if status == "pass" else "real-world shadow comparison needs review"
    return status, summary, evidence, attention


def summarize_write_audit(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    if rows is None:
        return {
            "status": "pending",
            "rows_seen": 0,
            "error_count": 0,
            "tool_status": {tool: "pending" for tool in REQUIRED_WRITE_CANARY_TOOLS},
            "summary": "write audit log was not provided",
        }
    audits = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("event") == "kontext_write_audit"
    ]
    tool_status = {tool: "pending" for tool in REQUIRED_WRITE_CANARY_TOOLS}
    action_counts: dict[str, int] = {}
    error_count = 0
    read_after_write_failures = 0
    writes_applied_total = 0
    for row in audits:
        tool = str(row.get("tool") or "")
        action_counts[tool] = action_counts.get(tool, 0) + 1
        error = str(row.get("kontext_error") or row.get("fetch_error") or "")
        if error:
            error_count += 1
        writes_applied = safe_int(row.get("writes_applied"))
        writes_applied_total += writes_applied
        apply_ok = str(row.get("kontext_mode") or "") == "apply" and writes_applied > 0 and not error
        if tool in READ_AFTER_WRITE_TOOLS:
            read_ok = row.get("read_after_write_checked") is True and row.get("read_after_write_ok") is True
            if row.get("read_after_write_checked") is True and row.get("read_after_write_ok") is not True:
                read_after_write_failures += 1
            passed = apply_ok and read_ok
        else:
            passed = apply_ok
        if tool in tool_status:
            if passed:
                tool_status[tool] = "pass"
            elif error:
                tool_status[tool] = "fail"
    if not audits:
        status = "pending"
        summary = "write audit has no rows yet"
    elif error_count or read_after_write_failures or any(value == "fail" for value in tool_status.values()):
        status = "fail"
        summary = "write audit has errors or read-after-write failures"
    elif all(value == "pass" for value in tool_status.values()):
        status = "pass"
        summary = "write canary save/update/flag evidence is green"
    else:
        status = "pending"
        summary = "write canary evidence is incomplete"
    return {
        "status": status,
        "rows_seen": len(audits),
        "error_count": error_count,
        "read_after_write_failures": read_after_write_failures,
        "writes_applied_total": writes_applied_total,
        "tool_status": dict(sorted(tool_status.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "summary": summary,
    }


def summarize_rollback_drill(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "status": "pending",
            "summary": "rollback drill report was not provided",
            "check_status": {},
            "attention_count": 0,
        }
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    check_status = {
        str(name): str(check.get("status") or "warn")
        for name, check in checks.items()
        if isinstance(check, dict)
    }
    failures = [name for name, status in check_status.items() if status == "fail"]
    warnings = [name for name, status in check_status.items() if status == "warn"]
    if report.get("ok") is True and not failures:
        status = "pass"
        summary = "rollback drill is green"
    elif failures:
        status = "fail"
        summary = "rollback drill failed"
    else:
        status = "pending"
        summary = "rollback drill evidence is incomplete"
    return {
        "status": status,
        "summary": summary,
        "check_status": dict(sorted(check_status.items())),
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "attention_count": len(report.get("attention_items") or []),
    }


def summarize_checks(checks: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks.values():
        status = str(check.get("status") or "warn")
        counts[status if status in counts else "warn"] += 1
    return counts


def recommendation_from_summary(summary: dict[str, int]) -> str:
    if summary.get("fail", 0):
        return "rollback_or_hold_mem0_primary"
    if summary.get("warn", 0):
        return "continue_canary_but_review_warnings"
    return "continue_kontext_primary_canary"


def transition_assessment_from_checks(
    *,
    checks: dict[str, dict[str, Any]],
    production_cutover_enabled: bool,
    write_audit: dict[str, Any] | None = None,
    rollback_drill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    remaining: list[dict[str, str]] = []
    blockers: list[str] = []

    for name, check in checks.items():
        if check.get("status") == "fail":
            blockers.append(f"{name} failed")

    shadow = checks.get("real_world_shadow_compare", {})
    shadow_evidence = shadow.get("evidence") if isinstance(shadow.get("evidence"), dict) else {}
    recent_windows = shadow_evidence.get("recent_windows") if isinstance(shadow_evidence.get("recent_windows"), dict) else {}
    last_50 = recent_windows.get("last_50") if isinstance(recent_windows.get("last_50"), dict) else {}
    recent_rows = safe_int(last_50.get("rows_seen"))
    recent_errors = safe_int(last_50.get("error_count"))
    recent_zero_regressions = safe_int(last_50.get("kontext_zero_when_mem0_nonzero_count"))

    if recent_rows < MIN_RECENT_HEAD_TO_HEAD_ROWS:
        remaining.append(
            {
                "key": "recent_head_to_head_rows",
                "status": "pending",
                "detail": f"collect at least {MIN_RECENT_HEAD_TO_HEAD_ROWS} recent real-world Mem0-vs-Kontext shadow rows",
            }
        )
    if recent_errors or recent_zero_regressions:
        blockers.append("recent real-world shadow comparison has errors or zero-result regressions")

    write_canary = write_audit or summarize_write_audit(None)
    write_status = str(write_canary.get("status") or "pending")
    if write_status == "fail":
        blockers.append("write canary audit has errors or read-after-write failures")
    if write_status != "pass":
        remaining.append(
            {
                "key": "write_canary",
                "status": "pending" if write_status != "fail" else "failed",
                "detail": "run a real Kontext write-canary profile with save/update/flag read-after-write checks before production writes",
            }
        )

    rollback = rollback_drill or summarize_rollback_drill(None)
    rollback_status = str(rollback.get("status") or "pending")
    if rollback_status == "fail":
        blockers.append("rollback drill failed")
    if rollback_status != "pass":
        remaining.append(
            {
                "key": "rollback_drill",
                "status": "pending" if rollback_status != "fail" else "failed",
                "detail": "prove clients can be switched back to Mem0 and Kontext can be restored from backup without data loss",
            }
        )

    remaining.append(
        {
            "key": "production_cutover",
            "status": "blocked",
            "detail": "keep production cutover disabled until recent observation, write canary, and rollback drill are green",
        }
    )

    if production_cutover_enabled:
        blockers.append("production cutover is already enabled")

    if blockers:
        decision = "hold_mem0_primary_fix_regressions"
    elif recent_rows < MIN_RECENT_HEAD_TO_HEAD_ROWS:
        decision = "keep_mem0_primary_observe_head_to_head"
    elif write_status != "pass":
        decision = "keep_mem0_primary_start_write_canary"
    elif rollback_status != "pass":
        decision = "keep_mem0_primary_run_rollback_drill"
    else:
        decision = "ready_for_production_cutover_review"

    return {
        "decision": decision,
        "recent_head_to_head_rows": recent_rows,
        "recent_latency_winner": str(last_50.get("latency_winner") or "unknown"),
        "recent_avg_mem0_latency_ms": safe_float(last_50.get("avg_mem0_latency_ms")),
        "recent_avg_kontext_latency_ms": safe_float(last_50.get("avg_kontext_latency_ms")),
        "remaining_work_keys": [item["key"] for item in remaining],
        "remaining_work": remaining,
        "blockers": blockers,
        "write_canary": write_canary,
        "rollback_drill": rollback,
    }


def retirement_transition_assessment(retirement_gate: dict[str, Any]) -> dict[str, Any]:
    gate = named_gate(retirement_gate, "legacy_mem0_frozen_and_unused")
    evidence = gate.get("evidence") if isinstance(gate.get("evidence"), dict) else {}
    recent_reads = safe_int(evidence.get("recent_read_count"))
    recent_writes = safe_int(evidence.get("recent_write_count"))
    monitor_hours = safe_float(evidence.get("monitor_hours"))
    writes_enabled = evidence.get("writes_enabled") is True
    remaining: list[dict[str, Any]] = []
    blockers = [str(item) for item in retirement_gate.get("blockers") or []]

    if retirement_gate.get("ok") is True:
        decision = "mem0_runtime_can_be_disabled_after_final_delete_approval"
    elif not writes_enabled and recent_reads == 0 and recent_writes == 0 and monitor_hours < 48:
        decision = "continue_mem0_frozen_monitor"
        remaining.append(
            {
                "key": "legacy_mem0_monitor",
                "status": "pending",
                "detail": "continue the 48h no-Mem0-traffic monitor",
                "monitor_hours": monitor_hours,
            }
        )
    else:
        decision = "fix_retirement_gate_blockers"

    remaining.append(
        {
            "key": "final_delete_approval",
            "status": "blocked",
            "detail": "hard deletion of Mem0 data still requires explicit final approval",
        }
    )
    return {
        "decision": decision,
        "remaining_work_keys": [str(item["key"]) for item in remaining],
        "remaining_work": remaining,
        "blockers": blockers,
        "retirement_gate": {
            "ok": retirement_gate.get("ok") is True,
            "decision": str(retirement_gate.get("decision") or ""),
            "monitor_hours": monitor_hours,
            "recent_read_count": recent_reads,
            "recent_write_count": recent_writes,
            "writes_enabled": writes_enabled,
        },
    }


def build_observer_report(
    *,
    scorecard: dict[str, Any] | None = None,
    cutover_readiness: dict[str, Any] | None = None,
    retirement_gate: dict[str, Any] | None = None,
    usage_report: dict[str, Any] | None = None,
    client_status: dict[str, Any] | None = None,
    ob1_retrieval_quality: dict[str, Any] | None = None,
    shadow_comparison_rows: list[dict[str, Any]] | None = None,
    write_audit_rows: list[dict[str, Any]] | None = None,
    rollback_drill: dict[str, Any] | None = None,
    expected_origins: list[str] | None = None,
    label: str = "kontext-primary-canary",
    max_total_tokens: int = 0,
    input_freshness: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expected = [origin.strip().lower() for origin in (expected_origins or ["codex"]) if origin.strip()]
    checks: dict[str, dict[str, Any]] = {}
    attention: list[str] = []

    if input_freshness is not None:
        status, summary, evidence, items = input_freshness_check(input_freshness)
        add_check(checks, "input_freshness", status, summary, evidence)
        attention.extend(items)

    if retirement_gate is not None:
        status, summary, evidence, items = retirement_gate_check(retirement_gate)
        add_check(checks, "retirement_gate", status, summary, evidence)
        attention.extend(items)
        counts = summarize_checks(checks)
        transition_assessment = retirement_transition_assessment(retirement_gate)
        decision = str(transition_assessment.get("decision") or "")
        recommendation = (
            "rollback_or_hold_mem0_primary"
            if counts["fail"] and decision == "fix_retirement_gate_blockers"
            else decision
        )
        return {
            "ok": retirement_gate.get("ok") is True and counts["fail"] == 0,
            "mode": "kontext-cutover-observer",
            "runs_model_calls": False,
            "generated_at": utc_now(),
            "label": label,
            "source_of_truth": "kontext",
            "production_cutover_enabled": False,
            "expected_origins": expected,
            "recommendation": recommendation,
            "summary": counts,
            "transition_assessment": transition_assessment,
            "attention_items": list(dict.fromkeys(attention)),
            "checks": checks,
            "notes": [
                "This observer is using the Kontext Mem0 retirement gate as the active runtime-dependency gate.",
                "It reads sanitized reports only and does not call models.",
                "It does not approve hard deletion of Mem0 data.",
            ],
        }

    scorecard = scorecard or {}
    cutover_readiness = cutover_readiness or {}

    status, summary, evidence, items = scorecard_check(scorecard)
    add_check(checks, "scorecard", status, summary, evidence)
    attention.extend(items)

    status, summary, evidence, items = readiness_check(cutover_readiness)
    add_check(checks, "cutover_readiness", status, summary, evidence)
    attention.extend(items)

    for gate_name in ("base_live_mcp_retrieval", "expanded_live_mcp_retrieval"):
        status, summary, evidence = retrieval_check(cutover_readiness, gate_name)
        add_check(checks, gate_name, status, summary, evidence)
        if status == "fail":
            attention.append(f"{gate_name} failed")

    if ob1_retrieval_quality is not None:
        status, summary, evidence, items = ob1_retrieval_quality_check(ob1_retrieval_quality)
        add_check(checks, "ob1_retrieval_quality", status, summary, evidence)
        attention.extend(items)

    status, summary, evidence, items = canary_check(cutover_readiness)
    add_check(checks, "public_mcp_canary_dry_run", status, summary, evidence)
    attention.extend(items)

    status, summary, evidence, items = judged_check(cutover_readiness)
    add_check(checks, "official_judged_accuracy", status, summary, evidence)
    attention.extend(items)

    status, summary, evidence, items = usage_check(usage_report, max_total_tokens)
    add_check(checks, "usage_health", status, summary, evidence)
    attention.extend(items)

    status, summary, evidence, items = client_check(client_status, expected)
    add_check(checks, "client_activity", status, summary, evidence)
    attention.extend(items)

    status, summary, evidence, items = shadow_comparison_check(shadow_comparison_rows)
    add_check(checks, "real_world_shadow_compare", status, summary, evidence)
    attention.extend(items)

    counts = summarize_checks(checks)
    production_cutover_enabled = (
        scorecard.get("production_cutover_enabled") is True
        or cutover_readiness.get("production_cutover_enabled") is True
    )
    transition_assessment = transition_assessment_from_checks(
        checks=checks,
        production_cutover_enabled=production_cutover_enabled,
        write_audit=summarize_write_audit(write_audit_rows),
        rollback_drill=summarize_rollback_drill(rollback_drill),
    )
    recommendation = recommendation_from_summary(counts)
    return {
        "ok": counts["fail"] == 0,
        "mode": "kontext-cutover-observer",
        "runs_model_calls": False,
        "generated_at": utc_now(),
        "label": label,
        "source_of_truth": "mem0",
        "production_cutover_enabled": production_cutover_enabled,
        "expected_origins": expected,
        "recommendation": recommendation,
        "summary": counts,
        "transition_assessment": transition_assessment,
        "attention_items": list(dict.fromkeys(attention)),
        "checks": checks,
        "notes": [
            "This observer reads sanitized reports only.",
            "It does not call models and does not include raw memory text, raw chats, secrets, tokens, or log paths.",
            "Warnings mean continue the canary but review the listed item; failures mean hold or roll back to Mem0 primary.",
        ],
    }


def format_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Kontext Cutover Observer",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Label: `{report.get('label')}`",
        f"- OK: `{str(report.get('ok')).lower()}`",
        f"- Recommendation: `{report.get('recommendation')}`",
        f"- Source of truth: `{report.get('source_of_truth')}`",
        f"- Production cutover enabled: `{str(report.get('production_cutover_enabled')).lower()}`",
        "",
        "## Summary",
        "",
    ]
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines.extend(
        [
            f"- Pass: {summary.get('pass', 0)}",
            f"- Warn: {summary.get('warn', 0)}",
            f"- Fail: {summary.get('fail', 0)}",
            "",
            "## Checks",
            "",
        ]
    )
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    if "retirement_gate" in checks:
        retirement = checks["retirement_gate"]
        evidence = retirement.get("evidence") if isinstance(retirement.get("evidence"), dict) else {}
        lines.extend(
            [
                "",
                "## Retirement Gate",
                "",
                f"- Status: `{retirement.get('status')}`",
                f"- Decision: `{evidence.get('decision')}`",
                f"- Monitor hours: `{evidence.get('legacy_mem0_monitor_hours')}`",
                "",
            ]
        )
    for name, check in checks.items():
        lines.append(f"- `{name}`: `{check.get('status')}` - {check.get('summary')}")
    assessment = report.get("transition_assessment") if isinstance(report.get("transition_assessment"), dict) else {}
    if assessment:
        lines.extend(
            [
                "",
                "## Transition Assessment",
                "",
                f"- Decision: `{assessment.get('decision')}`",
                f"- Recent head-to-head rows: `{assessment.get('recent_head_to_head_rows')}`",
                f"- Recent latency winner: `{assessment.get('recent_latency_winner')}`",
                f"- Write canary: `{(assessment.get('write_canary') or {}).get('status')}`",
                f"- Rollback drill: `{(assessment.get('rollback_drill') or {}).get('status')}`",
                "",
                "### Remaining Work",
                "",
            ]
        )
        for item in assessment.get("remaining_work") or []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('key')}`: {item.get('detail')}")
    attention = report.get("attention_items") if isinstance(report.get("attention_items"), list) else []
    if attention:
        lines.extend(["", "## Attention", ""])
        for item in attention:
            lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def append_history(path: str | Path, report: dict[str, Any]) -> None:
    row = {
        "generated_at": report.get("generated_at"),
        "ok": report.get("ok") is True,
        "recommendation": report.get("recommendation"),
        "summary": report.get("summary"),
        "transition_decision": (report.get("transition_assessment") or {}).get("decision")
        if isinstance(report.get("transition_assessment"), dict)
        else None,
        "attention_count": len(report.get("attention_items") or []),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a compact Kontext cutover observer report from sanitized gate reports.")
    parser.add_argument("--scorecard")
    parser.add_argument("--cutover-readiness")
    parser.add_argument("--retirement-gate")
    parser.add_argument("--usage-report")
    parser.add_argument("--client-status")
    parser.add_argument("--ob1-retrieval-quality-report")
    parser.add_argument("--shadow-comparison-log")
    parser.add_argument("--shadow-recent-limit", type=int, default=500)
    parser.add_argument("--write-audit-log")
    parser.add_argument("--write-audit-recent-limit", type=int, default=500)
    parser.add_argument("--rollback-drill-report")
    parser.add_argument("--expected-origin", action="append", default=[])
    parser.add_argument("--label", default="kontext-primary-canary")
    parser.add_argument("--max-total-tokens", type=int, default=0)
    parser.add_argument("--max-input-age-hours", type=float, default=DEFAULT_MAX_INPUT_AGE_HOURS)
    parser.add_argument("--output")
    parser.add_argument("--markdown-output")
    parser.add_argument("--history-log")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.retirement_gate and (not args.scorecard or not args.cutover_readiness):
        parser.error("--scorecard and --cutover-readiness are required unless --retirement-gate is provided")
    scorecard = load_json(args.scorecard) if args.scorecard else None
    cutover_readiness = load_json(args.cutover_readiness) if args.cutover_readiness else None
    retirement_gate = load_json(args.retirement_gate) if args.retirement_gate else None
    max_input_age_hours = max(float(args.max_input_age_hours), 0.0)
    input_freshness: dict[str, dict[str, Any]] = {}
    if args.retirement_gate:
        input_freshness["retirement_gate"] = input_freshness_report(args.retirement_gate, retirement_gate or {}, max_input_age_hours)
    if args.scorecard:
        input_freshness["scorecard"] = input_freshness_report(args.scorecard, scorecard or {}, max_input_age_hours)
    if args.cutover_readiness:
        input_freshness["cutover_readiness"] = input_freshness_report(args.cutover_readiness, cutover_readiness or {}, max_input_age_hours)
    ob1_retrieval_quality = load_json(args.ob1_retrieval_quality_report) if args.ob1_retrieval_quality_report else None
    if args.ob1_retrieval_quality_report:
        input_freshness["ob1_retrieval_quality"] = input_freshness_report(
            args.ob1_retrieval_quality_report,
            ob1_retrieval_quality or {},
            max_input_age_hours,
        )
    report = build_observer_report(
        scorecard=scorecard,
        cutover_readiness=cutover_readiness,
        retirement_gate=retirement_gate,
        usage_report=load_json(args.usage_report) if args.usage_report else None,
        client_status=load_json(args.client_status) if args.client_status else None,
        ob1_retrieval_quality=ob1_retrieval_quality,
        shadow_comparison_rows=read_recent_jsonl(args.shadow_comparison_log, args.shadow_recent_limit)
        if args.shadow_comparison_log
        else None,
        write_audit_rows=read_recent_jsonl(args.write_audit_log, args.write_audit_recent_limit)
        if args.write_audit_log
        else None,
        rollback_drill=load_json(args.rollback_drill_report) if args.rollback_drill_report else None,
        expected_origins=args.expected_origin or ["codex"],
        label=args.label,
        max_total_tokens=max(args.max_total_tokens, 0),
        input_freshness=input_freshness,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        markdown = Path(args.markdown_output)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(format_markdown(report), encoding="utf-8")
    if args.history_log:
        append_history(args.history_log, report)
    print(text)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
