from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def bool_at(row: dict[str, Any], key: str) -> bool:
    return row.get(key) is True


def gate(name: str, ok: bool, evidence: dict[str, Any], blocker: str) -> dict[str, Any]:
    result = {
        "name": name,
        "status": "pass" if ok else "fail",
        "evidence": evidence,
    }
    if not ok:
        result["blocker"] = blocker
    return result


def client_routing_gate(payload: dict[str, Any]) -> dict[str, Any]:
    clients = payload.get("client_routing") if isinstance(payload.get("client_routing"), dict) else {}
    required = ["codex", "claude", "chatgpt_visible"]
    missing = [name for name in required if name not in clients]
    mem0_clients = [
        name
        for name, row in clients.items()
        if isinstance(row, dict) and (row.get("mem0") is True or row.get("mem0_runtime") is True)
    ]
    kontext_clients = [
        name
        for name, row in clients.items()
        if isinstance(row, dict) and row.get("kontext") is True
    ]
    ok = not missing and not mem0_clients and all(name in kontext_clients for name in required)
    return gate(
        "client_routing_kontext_only",
        ok,
        {
            "required_clients": required,
            "kontext_clients": sorted(kontext_clients),
            "mem0_runtime_clients": sorted(mem0_clients),
            "missing_clients": sorted(missing),
        },
        "one or more normal clients still route to Mem0 or lack Kontext routing",
    )


def kontext_health_gate(payload: dict[str, Any]) -> dict[str, Any]:
    health = payload.get("kontext_health") if isinstance(payload.get("kontext_health"), dict) else {}
    mirror_count = int(health.get("mirror_count") or health.get("memory_count") or 0)
    ok = health.get("ok") is True and mirror_count > 0
    return gate(
        "kontext_health",
        ok,
        {"ok": health.get("ok") is True, "mirror_count": mirror_count},
        "Kontext health or memory count is not proven",
    )


def sync_proof_gate(payload: dict[str, Any]) -> dict[str, Any]:
    proof = payload.get("sync_proof") if isinstance(payload.get("sync_proof"), dict) else {}
    visible = int(proof.get("mem0_visible_ids") or proof.get("source_ids") or 0)
    matches = int(proof.get("kontext_matches") or proof.get("matches") or 0)
    missing = int(proof.get("missing") or proof.get("missing_count") or 0)
    ok = visible > 0 and matches == visible and missing == 0
    return gate(
        "mem0_visible_id_set_synced",
        ok,
        {"mem0_visible_ids": visible, "kontext_matches": matches, "missing": missing},
        "Mem0-visible ID set is not fully represented in Kontext",
    )


def _score_row(row: dict[str, Any]) -> dict[str, Any]:
    passed = int(row.get("passed") or 0)
    total = int(row.get("total") or 0)
    accuracy = round(passed / total, 4) if total else 0.0
    return {
        "passed": passed,
        "total": total,
        "accuracy": accuracy,
        "raw_payload_hits": int(row.get("raw_payload_hits") or row.get("raw_payload_hit_count") or 0),
        "runs_model_calls": row.get("runs_model_calls") is True,
    }


def replacement_quality_evidence(quality: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    locomo = _score_row(quality.get("locomo30") if isinstance(quality.get("locomo30"), dict) else {})
    longmem = _score_row(quality.get("longmemeval30") if isinstance(quality.get("longmemeval30"), dict) else {})
    beam = quality.get("paired_beam") if isinstance(quality.get("paired_beam"), dict) else {}
    beam_questions = int(beam.get("questions") or 0)
    beam_kontext = int(beam.get("kontext_passed") or 0)
    beam_mem0 = int(beam.get("mem0_passed") or 0)
    beam_delta = beam_kontext - beam_mem0
    beam_raw_hits = int(beam.get("raw_payload_hits") or beam.get("raw_payload_hit_count") or 0)
    evidence = {
        "replacement_gate": str(quality.get("name") or ""),
        "locomo30": locomo,
        "longmemeval30": longmem,
        "paired_beam": {
            "questions": beam_questions,
            "same_question_hashes": beam.get("same_question_hashes") is True,
            "kontext_passed": beam_kontext,
            "mem0_passed": beam_mem0,
            "legacy_backend": str(beam.get("legacy_backend") or ""),
            "raw_payload_hits": beam_raw_hits,
            "runs_model_calls": beam.get("runs_model_calls") is True,
        },
        "beam_delta_kontext_minus_mem0": beam_delta,
    }
    locomo_ok = locomo["total"] >= 30 and locomo["accuracy"] >= 0.8 and locomo["raw_payload_hits"] == 0 and locomo["runs_model_calls"]
    longmem_ok = longmem["total"] >= 30 and longmem["accuracy"] >= 0.8 and longmem["raw_payload_hits"] == 0 and longmem["runs_model_calls"]
    beam_ok = (
        beam_questions >= 6
        and beam.get("same_question_hashes") is True
        and beam_delta >= 0
        and beam_raw_hits == 0
        and beam.get("runs_model_calls") is True
        and str(beam.get("legacy_backend") or "") == "legacy-mem0-offline"
    )
    return locomo_ok and longmem_ok and beam_ok, evidence


def quality_gate(payload: dict[str, Any]) -> dict[str, Any]:
    quality = payload.get("quality_gate") if isinstance(payload.get("quality_gate"), dict) else {}
    if str(quality.get("name") or "") == "locomo_longmem_paired_beam_replacement":
        ok, evidence = replacement_quality_evidence(quality)
    else:
        ok = quality.get("ok") is True
        evidence = {"ok": ok, "name": str(quality.get("name") or "")}
    return gate(
        "retirement_quality_gate",
        ok,
        evidence,
        "neither BEAM nor a stricter real-use replacement gate has passed",
    )


def typed_state_trial_gate(payload: dict[str, Any]) -> dict[str, Any]:
    trial = payload.get("typed_state_trial") if isinstance(payload.get("typed_state_trial"), dict) else {}
    counts = trial.get("counts") if isinstance(trial.get("counts"), dict) else {}
    state_questions = int(counts.get("state_questions") or trial.get("state_questions") or 0)
    projection_loss = int(
        counts.get("projection_selection_loss_questions")
        or trial.get("projection_selection_loss_questions")
        or 0
    )
    extraction_loss = int(
        counts.get("state_event_extraction_loss_questions")
        or trial.get("state_event_extraction_loss_questions")
        or 0
    )
    raw_hits = int(trial.get("raw_payload_hits") or trial.get("raw_payload_hit_count") or 0)
    mode = str(trial.get("mode") or "")
    runs_model_calls = trial.get("runs_model_calls") is True
    ok = (
        trial.get("ok") is True
        and mode in {"judged_bundle_state_trial", "benchmark_state_trial"}
        and state_questions >= 6
        and projection_loss == 0
        and extraction_loss == 0
        and raw_hits == 0
        and not runs_model_calls
    )
    return gate(
        "typed_state_trial_gate",
        ok,
        {
            "ok": trial.get("ok") is True,
            "mode": mode,
            "state_questions": state_questions,
            "projection_selection_loss_questions": projection_loss,
            "state_event_extraction_loss_questions": extraction_loss,
            "payload_hit_count": raw_hits,
            "runs_model_calls": runs_model_calls,
        },
        "typed-state trial evidence is missing, too small, leaked raw payload, used paid model calls, or has projection/extraction loss",
    )


def canary_gate(payload: dict[str, Any]) -> dict[str, Any]:
    canary = payload.get("canary") if isinstance(payload.get("canary"), dict) else {}
    writes = int(canary.get("writes_applied") or 0)
    tools = int(canary.get("tool_count") or 0)
    ok = canary.get("ok") is True and writes == 0 and tools >= 16
    return gate(
        "kontext_mcp_canary",
        ok,
        {"ok": canary.get("ok") is True, "writes_applied": writes, "tool_count": tools},
        "Kontext MCP canary is not green or dry-run safe",
    )


def write_audit_gate(payload: dict[str, Any]) -> dict[str, Any]:
    audit = payload.get("write_audit") if isinstance(payload.get("write_audit"), dict) else {}
    errors = int(audit.get("error_count") or 0)
    ok = audit.get("ok") is True and audit.get("read_after_write_ok") is True and errors == 0
    return gate(
        "kontext_write_safety",
        ok,
        {"ok": audit.get("ok") is True, "read_after_write_ok": audit.get("read_after_write_ok") is True, "error_count": errors},
        "Kontext write safety or read-after-write proof is not green",
    )


def legacy_mem0_gate(payload: dict[str, Any]) -> dict[str, Any]:
    legacy = payload.get("legacy_mem0") if isinstance(payload.get("legacy_mem0"), dict) else {}
    writes_enabled = legacy.get("writes_enabled") is True
    runtime_enabled = legacy.get("runtime_enabled") is not False
    recent_writes = int(legacy.get("recent_write_count") or 0)
    recent_reads = int(legacy.get("recent_read_count") or 0)
    monitor_hours = float(legacy.get("monitor_hours") or 0)
    monitor_satisfied = recent_reads == 0 and monitor_hours >= 48
    runtime_disabled = runtime_enabled is False
    ok = not writes_enabled and recent_writes == 0 and (runtime_disabled or monitor_satisfied)
    return gate(
        "legacy_mem0_frozen_and_unused",
        ok,
        {
            "writes_enabled": writes_enabled,
            "runtime_enabled": runtime_enabled,
            "recent_write_count": recent_writes,
            "recent_read_count": recent_reads,
            "monitor_hours": monitor_hours,
            "monitor_requirement": "satisfied_by_runtime_disable" if runtime_disabled else "satisfied_by_elapsed_time" if monitor_satisfied else "pending",
        },
        "legacy Mem0 is still writable, still runtime-enabled, still receiving writes, or has not completed a 48h no-read monitor",
    )


def backup_gate(payload: dict[str, Any]) -> dict[str, Any]:
    backups = payload.get("backups") if isinstance(payload.get("backups"), dict) else {}
    ok = backups.get("kontext_db") is True and backups.get("mem0_export") is True
    return gate(
        "final_backups_exist",
        ok,
        {"kontext_db": backups.get("kontext_db") is True, "mem0_export": backups.get("mem0_export") is True},
        "final Kontext DB backup and Mem0 export are not both verified",
    )


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    gates = [
        client_routing_gate(payload),
        kontext_health_gate(payload),
        sync_proof_gate(payload),
        quality_gate(payload),
        typed_state_trial_gate(payload),
        canary_gate(payload),
        write_audit_gate(payload),
        legacy_mem0_gate(payload),
        backup_gate(payload),
    ]
    status_counts = {
        "pass": sum(1 for row in gates if row["status"] == "pass"),
        "fail": sum(1 for row in gates if row["status"] == "fail"),
    }
    blockers = [row["blocker"] for row in gates if row["status"] == "fail"]
    ok = status_counts["fail"] == 0
    return {
        "mode": "kontext-mem0-retirement-gate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "decision": "mem0_runtime_can_be_disabled_after_final_delete_approval" if ok else "keep_mem0_available_fix_blockers",
        "status_counts": status_counts,
        "gates": gates,
        "blockers": blockers,
        "notes": [
            "This report contains counts and booleans only.",
            "It does not approve hard deletion of Mem0 data.",
        ],
    }


def load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a sanitized Kontext-only retirement gate report.")
    parser.add_argument("--input", required=True, help="Sanitized gate input JSON.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = build_report(load_json(args.input))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"ok={str(report['ok']).lower()} pass={report['status_counts']['pass']} fail={report['status_counts']['fail']} output={output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
