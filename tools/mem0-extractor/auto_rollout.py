#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from apply_report import build_apply_plan, client_from_args, execute_plan, proposals_from_report, validate_report_quality, write_json
from audit_report import audit_report, write_audit_markdown
from batch_orchestrator import (
    DEFAULT_OUTPUT_ROOT,
    apply_args_for,
    batch_ranges,
    build_batch_paths,
    extraction_args_for,
    load_env_file,
    load_manifest,
    manifest_path,
    save_manifest,
    update_batch_manifest,
    write_jsonl_batch,
)
from extract_session import DEFAULT_CACHE_PATH, build_report, read_input
from extractor import metadata_quality_report
from grading import junk_reason
from report_repair import repair_source_ids
from review_export import write_review_files
from schemas import MemoryProposal, normalize_proposal

TRANSIENT_ERROR_TERMS = ("URLError", "TimeoutError", "gaierror", "HTTPError", "ConnectionError", "timed out", "temporarily")


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def append_log(path: str | Path, event: str, **fields: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def executed_output_path(output_root: str | Path, run_id: str, batch_index: int) -> Path:
    root = Path(output_root)
    return root / f"{run_id}-batch-{batch_index:03d}-apply-executed.json"


def curated_report_path(output_root: str | Path, run_id: str, batch_index: int) -> Path:
    root = Path(output_root)
    return root / f"{run_id}-batch-{batch_index:03d}-report-curated.json"


def curated_audit_path(output_root: str | Path, run_id: str, batch_index: int) -> Path:
    root = Path(output_root)
    return root / f"{run_id}-batch-{batch_index:03d}-curated-audit.json"


def all_proposals_are_non_writes(report: dict[str, Any]) -> bool:
    proposals = report.get("proposals") or []
    return all(normalize_proposal(row).action in {"skip", "ask_user"} for row in proposals if isinstance(row, dict))


def auto_skip_junk_proposals(report: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    curated = json.loads(json.dumps(report, ensure_ascii=False))
    changed: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(curated.get("proposals") or [], start=1):
        if not isinstance(row, dict):
            continue
        proposal = normalize_proposal(row)
        reason = junk_reason(proposal.content)
        next_row = dict(row)
        if proposal.action in {"save", "update"} and reason:
            next_row["action"] = "skip"
            next_row["signal_strength"] = 1
            next_row["memory_tier"] = "cold"
            next_row["current_status"] = "review"
            next_row["reason"] = f"auto_skip_after_audit: {reason}"
            changed.append({"proposal_index": index, "reason": reason})
        rows.append(next_row)
    curated["proposals"] = rows
    normalized = [normalize_proposal(row) for row in rows]
    curated.setdefault("summary", {})["proposal_count"] = len(rows)
    curated["summary"]["metadata_quality"] = metadata_quality_report(normalized)
    audit = audit_report(curated)
    return curated, changed, audit


def report_quality_allows_execute(report: dict[str, Any]) -> bool:
    quality = (report.get("summary") or {}).get("metadata_quality") or {}
    return quality.get("status") != "failed" or all_proposals_are_non_writes(report)


def summary_counts_from_results(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"save": 0, "update": 0, "skip": 0, "ask_user": 0, "failed": 0}
    for row in results:
        action = row.get("action") if row.get("action") in counts else "skip"
        if action in {"save", "update"} and not row.get("executed"):
            counts["failed"] += 1
        else:
            counts[action] += 1
    return counts


def reconcile_execution_report(output_path: str | Path, retry_results: dict[int, dict[str, Any]], retry_note: str) -> dict[str, Any]:
    output = Path(output_path)
    data = read_json(output)
    summary = data.setdefault("summary", {})
    results = list(summary.get("results") or [])
    for index, retry_result in retry_results.items():
        if 0 <= index < len(results):
            results[index] = retry_result
    summary["results"] = results
    summary["counts"] = summary_counts_from_results(results)
    notes = list(summary.get("reconciled_retries") or [])
    notes.append(retry_note)
    summary["reconciled_retries"] = notes
    write_json(output, data)
    return data


def output_has_clean_execution(path: str | Path) -> bool:
    output = Path(path)
    if not output.exists():
        return False
    try:
        data = read_json(output)
    except Exception:  # noqa: BLE001 - corrupt/incomplete output must be retried.
        return False
    counts = (data.get("summary") or {}).get("counts") or {}
    return int(counts.get("failed") or 0) == 0


def transient_error(exc: BaseException) -> bool:
    text = f"{exc.__class__.__name__}: {exc}"
    return any(term.lower() in text.lower() for term in TRANSIENT_ERROR_TERMS)


def with_retries(action, attempts: int, sleep_seconds: float, log_path: Path, event: str):
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - this is the retry boundary for network/model calls.
            last_exc = exc
            append_log(log_path, f"{event}_failed", attempt=attempt, error=f"{exc.__class__.__name__}: {exc}")
            if attempt >= attempts or not transient_error(exc):
                raise
            time.sleep(sleep_seconds * attempt)
    raise RuntimeError(f"{event} failed: {last_exc}")


def write_apply_plan(report: dict[str, Any], output_path: Path, args: argparse.Namespace, review_prefix: str) -> dict[str, Any]:
    proposals = proposals_from_report(report)
    client = client_from_args(apply_args_for(args))
    plans = build_apply_plan(proposals, client)
    summary = execute_plan(plans, client, execute=False) if client is not None else execute_plan(plans, mem0_client=None, execute=False)
    review_files = write_review_files(Path(args.output_root) / "reviews", review_prefix, plans, preview_chars=args.review_preview_chars)
    output_summary = {**summary, "plan_count": len(plans), "review_files": review_files}
    output = {"summary": output_summary, "plans": [plan.to_dict() for plan in plans]}
    write_json(output_path, output)
    return output


def execute_report(report_path: Path, output_path: Path, args: argparse.Namespace, review_prefix: str) -> dict[str, Any]:
    report = read_json(report_path)
    validate_report_quality(report, execute=True, allow_quality_failed=all_proposals_are_non_writes(report))
    proposals = proposals_from_report(report)
    client = client_from_args(apply_args_for(args))
    plans = build_apply_plan(proposals, client)
    summary = execute_plan(plans, client, execute=True) if client is not None else execute_plan(plans, mem0_client=None, execute=False)
    review_files = write_review_files(Path(args.output_root) / "reviews", review_prefix, plans, preview_chars=args.review_preview_chars)
    output_summary = {**summary, "plan_count": len(plans), "review_files": review_files}
    output = {"summary": output_summary, "plans": [plan.to_dict() for plan in plans]}
    write_json(output_path, output)
    return output


def retry_failed_execution_rows(batch_index: int, report_path: Path, output_path: Path, args: argparse.Namespace, log_path: Path) -> dict[str, Any]:
    data = read_json(output_path)
    results = (data.get("summary") or {}).get("results") or []
    plans = data.get("plans") or []
    failed_indexes = [index for index, row in enumerate(results) if row.get("action") in {"save", "update"} and not row.get("executed")]
    if not failed_indexes:
        return data

    source = read_json(report_path)
    source_proposals = source.get("proposals") or []
    retry_results: dict[int, dict[str, Any]] = {}
    for index in failed_indexes:
        plan = plans[index] if index < len(plans) else {}
        proposal_row = (plan.get("proposal") if isinstance(plan, dict) else None) or (source_proposals[index] if index < len(source_proposals) else None)
        if not isinstance(proposal_row, dict):
            continue
        retry_report = json.loads(json.dumps(source, ensure_ascii=False))
        retry_report["proposals"] = [proposal_row]
        retry_report.setdefault("summary", {})["proposal_count"] = 1
        retry_report_path = Path(args.output_root) / f"{args.run_id}-batch-{batch_index:03d}-retry-{index + 1:03d}-report.json"
        retry_output_path = Path(args.output_root) / f"{args.run_id}-batch-{batch_index:03d}-retry-{index + 1:03d}-executed.json"
        write_json(retry_report_path, retry_report)
        append_log(log_path, "retry_failed_row", batch=batch_index, proposal_index=index + 1)
        retry_output = with_retries(
            lambda: execute_report(retry_report_path, retry_output_path, args, f"{args.run_id}-batch-{batch_index:03d}-retry-{index + 1:03d}"),
            args.write_retries,
            args.retry_sleep_seconds,
            log_path,
            "execute_retry_row",
        )
        retry_summary = retry_output.get("summary") or {}
        retry_summary_results = retry_summary.get("results") or []
        if retry_summary_results:
            retry_results[index] = retry_summary_results[0]
    if retry_results:
        return reconcile_execution_report(output_path, retry_results, retry_note=f"batch-{batch_index:03d}-row-retries")
    return read_json(output_path)


def process_batch_for_rollout(batch_index: int, rows: list[dict[str, Any]], args: argparse.Namespace, log_path: Path) -> tuple[Path, dict[str, Any]]:
    paths = build_batch_paths(args.output_root, args.run_id, batch_index)
    manifest_file = manifest_path(args.output_root, args.run_id)
    manifest = load_manifest(manifest_file)
    start = (batch_index - 1) * args.batch_size
    end = start + len(rows)
    update_batch_manifest(manifest, manifest_file, batch_index, "running", paths, {"start": start, "end": end})
    write_jsonl_batch(paths["input"], rows)

    if not paths["repaired_report"].exists():
        report = with_retries(
            lambda: build_report(extraction_args_for(args, paths["input"], paths["report"])),
            args.extract_retries,
            args.retry_sleep_seconds,
            log_path,
            "extract_batch",
        )
        write_json(paths["report"], report)
        repaired, repair_counts = repair_source_ids(report)
        write_json(paths["repaired_report"], repaired)
    else:
        repaired = read_json(paths["repaired_report"])
        repair_counts = (repaired.get("summary") or {}).get("source_id_repair") or {}

    report_path = paths["repaired_report"]
    audit = audit_report(repaired)
    write_json(paths["audit"], audit)
    write_audit_markdown(paths["audit_markdown"], audit)
    changed: list[dict[str, Any]] = []
    if (audit.get("summary") or {}).get("status") == "failed":
        curated, changed, curated_audit = auto_skip_junk_proposals(repaired)
        if not changed or (curated_audit.get("summary") or {}).get("status") == "failed":
            raise RuntimeError(f"audit failed and could not be auto-curated: {curated_audit.get('summary') or audit.get('summary')}")
        report_path = curated_report_path(args.output_root, args.run_id, batch_index)
        audit_path = curated_audit_path(args.output_root, args.run_id, batch_index)
        write_json(report_path, curated)
        write_json(audit_path, curated_audit)
        audit = curated_audit
        append_log(log_path, "auto_curated_batch", batch=batch_index, changed=changed)

    report = read_json(report_path)
    if not report_quality_allows_execute(report):
        raise RuntimeError(f"metadata quality failed and report has write proposals: {(report.get('summary') or {}).get('metadata_quality')}")

    apply_output = with_retries(
        lambda: write_apply_plan(report, paths["apply"], args, Path(paths["apply"]).stem),
        args.plan_retries,
        args.retry_sleep_seconds,
        log_path,
        "plan_batch",
    )
    counts = {
        "input_rows": len(rows),
        "candidate_count": report.get("summary", {}).get("candidate_count", 0),
        "proposal_count": len(proposals_from_report(report)),
        "repair": {**repair_counts, "auto_skipped_after_audit": len(changed)},
        "audit": audit.get("summary") or {},
        "apply": (apply_output.get("summary") or {}).get("counts") or {},
        "executed": False,
    }
    manifest = load_manifest(manifest_file)
    update_batch_manifest(manifest, manifest_file, batch_index, "completed", {**paths, "repaired_report": report_path}, {"start": start, "end": end, **counts})
    return report_path, counts


def update_manifest_after_execute(batch_index: int, args: argparse.Namespace, output: dict[str, Any]) -> None:
    manifest_file = manifest_path(args.output_root, args.run_id)
    manifest = load_manifest(manifest_file)
    row = manifest.setdefault("batches", {}).setdefault(str(batch_index), {})
    counts = row.setdefault("counts", {})
    counts["apply"] = (output.get("summary") or {}).get("counts") or {}
    counts["executed"] = True
    row["counts"] = counts
    row["error"] = ""
    row["status"] = "completed"
    save_manifest(manifest_file, manifest)


def execute_batch_for_rollout(batch_index: int, report_path: Path, args: argparse.Namespace, log_path: Path) -> dict[str, Any]:
    output_path = executed_output_path(args.output_root, args.run_id, batch_index)
    if output_has_clean_execution(output_path):
        return read_json(output_path)
    output = with_retries(
        lambda: execute_report(report_path, output_path, args, f"{args.run_id}-batch-{batch_index:03d}-executed"),
        args.write_retries,
        args.retry_sleep_seconds,
        log_path,
        "execute_batch",
    )
    if int(((output.get("summary") or {}).get("counts") or {}).get("failed") or 0):
        output = retry_failed_execution_rows(batch_index, report_path, output_path, args, log_path)
    counts = (output.get("summary") or {}).get("counts") or {}
    if int(counts.get("failed") or 0):
        raise RuntimeError(f"batch {batch_index} still has failed writes: {counts}")
    update_manifest_after_execute(batch_index, args, output)
    return output


def preferred_report_path(args: argparse.Namespace, batch_index: int) -> Path:
    curated = curated_report_path(args.output_root, args.run_id, batch_index)
    if curated.exists():
        return curated
    return build_batch_paths(args.output_root, args.run_id, batch_index)["repaired_report"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autonomous resume-safe Mem0 extractor rollout.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-batches", type=int, default=0, help="Process at most this many new/executable batches; 0 means until done.")
    parser.add_argument("--log", default="")
    parser.add_argument("--stop-file", default="")
    parser.add_argument("--deterministic-only", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--llm-provider", choices=["openrouter", "openai"], default="openrouter")
    parser.add_argument("--model", default="qwen/qwen3.6-flash")
    parser.add_argument("--llm-env-file", default="")
    parser.add_argument("--llm-api-key-env", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--cache", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--mem0-env-file", default="")
    parser.add_argument("--mem0-api-key-env", default="MEM0_API_KEY")
    parser.add_argument("--mem0-base-url", default="https://mem0-api.ionutrosu.xyz")
    parser.add_argument("--user-id", default="ionut")
    parser.add_argument("--agent-id", default="mem0-extractor")
    parser.add_argument("--review-preview-chars", type=int, default=180)
    parser.add_argument("--extract-retries", type=int, default=3)
    parser.add_argument("--plan-retries", type=int, default=3)
    parser.add_argument("--write-retries", type=int, default=3)
    parser.add_argument("--retry-sleep-seconds", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.offline:
        print("error=auto rollout requires live Mem0 planning/execution", file=sys.stderr)
        return 2
    if not args.deterministic_only:
        load_env_file(args.llm_env_file)
    load_env_file(args.mem0_env_file)

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log) if args.log else output_root / "_auto-rollout.log"
    rows = read_input(Path(args.input).expanduser())
    ranges = batch_ranges(len(rows), args.batch_size)
    manifest_file = manifest_path(output_root, args.run_id)
    manifest = load_manifest(manifest_file)
    manifest["summary"] = {
        "input": str(Path(args.input).expanduser()),
        "run_id": args.run_id,
        "batch_size": args.batch_size,
        "total_rows": len(rows),
        "total_batches": len(ranges),
        "execute": True,
        "offline": False,
        "deterministic_only": bool(args.deterministic_only),
        "model": args.model if not args.deterministic_only else "deterministic",
        "auto_rollout": True,
    }
    save_manifest(manifest_file, manifest)
    append_log(log_path, "auto_rollout_started", total_batches=len(ranges), max_batches=args.max_batches)

    processed = 0
    for batch_index, (start, end) in enumerate(ranges, start=1):
        if args.stop_file and Path(args.stop_file).exists():
            append_log(log_path, "stop_file_seen", batch=batch_index, stop_file=args.stop_file)
            break
        if args.max_batches and processed >= args.max_batches:
            break
        if output_has_clean_execution(executed_output_path(output_root, args.run_id, batch_index)):
            continue
        append_log(log_path, "batch_started", batch=batch_index, start=start, end=end)
        try:
            report_path = preferred_report_path(args, batch_index)
            if not report_path.exists() or not build_batch_paths(output_root, args.run_id, batch_index)["apply"].exists():
                report_path, _ = process_batch_for_rollout(batch_index, rows[start:end], args, log_path)
            output = execute_batch_for_rollout(batch_index, report_path, args, log_path)
        except Exception as exc:  # noqa: BLE001 - top-level batch guard must preserve resume state.
            manifest = load_manifest(manifest_file)
            paths = build_batch_paths(output_root, args.run_id, batch_index)
            update_batch_manifest(manifest, manifest_file, batch_index, "failed", paths, {"start": start, "end": end}, error=f"{exc.__class__.__name__}: {exc}")
            append_log(log_path, "batch_failed", batch=batch_index, error=f"{exc.__class__.__name__}: {exc}")
            print(json.dumps({"status": "failed", "batch": batch_index, "error": f"{exc.__class__.__name__}: {exc}", "log": str(log_path)}, sort_keys=True))
            return 1
        counts = (output.get("summary") or {}).get("counts") or {}
        append_log(log_path, "batch_completed", batch=batch_index, counts=counts)
        processed += 1

    manifest = load_manifest(manifest_file)
    completed = sorted(int(index) for index, row in (manifest.get("batches") or {}).items() if row.get("status") == "completed")
    print(
        json.dumps(
            {
                "status": "ok",
                "processed_batches": processed,
                "completed_batches": len(completed),
                "last_completed": completed[-1] if completed else None,
                "total_batches": len(ranges),
                "log": str(log_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    append_log(log_path, "auto_rollout_finished", processed_batches=processed, completed_batches=len(completed), last_completed=completed[-1] if completed else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
