#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from apply_report import build_apply_plan, client_from_args, execute_plan, proposals_from_report, validate_report_quality, write_json
from audit_report import audit_report, write_audit_markdown
from extract_session import DEFAULT_CACHE_PATH, build_report, read_input
from report_repair import repair_source_ids
from review_export import write_review_files

DEFAULT_OUTPUT_ROOT = Path(".local") / "mem0-extractor" / "batch-runs"


def batch_ranges(total_rows: int, batch_size: int) -> list[tuple[int, int]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [(start, min(start + batch_size, total_rows)) for start in range(0, total_rows, batch_size)]


def write_jsonl_batch(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def build_batch_paths(output_root: str | Path, run_id: str, batch_index: int) -> dict[str, Path]:
    root = Path(output_root)
    stem = f"{run_id}-batch-{batch_index:03d}"
    return {
        "input": root / f"{stem}-input.jsonl",
        "report": root / f"{stem}-report.json",
        "repaired_report": root / f"{stem}-report-repaired.json",
        "audit": root / f"{stem}-audit.json",
        "audit_markdown": root / "reviews" / f"{stem}-audit.md",
        "apply": root / f"{stem}-apply.json",
        "review_dir": root / "reviews",
    }


def manifest_path(output_root: str | Path, run_id: str) -> Path:
    return Path(output_root) / f"{run_id}-manifest.json"


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_file = Path(path)
    if not manifest_file.exists():
        return {"version": 1, "summary": {}, "batches": {}}
    return read_json(manifest_file)


def save_manifest(path: str | Path, manifest: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def manifest_is_batch_done(manifest: dict[str, Any], batch_index: int) -> bool:
    row = (manifest.get("batches") or {}).get(str(batch_index)) or {}
    return row.get("status") == "completed"


def update_batch_manifest(
    manifest: dict[str, Any],
    path: str | Path,
    batch_index: int,
    status: str,
    paths: dict[str, str | Path],
    counts: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    manifest.setdefault("batches", {})[str(batch_index)] = {
        "status": status,
        "paths": {key: str(value) for key, value in paths.items() if key != "review_dir"},
        "counts": counts or {},
        "error": error,
    }
    save_manifest(path, manifest)


def load_env_file(path: str) -> None:
    if not path:
        return
    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise SystemExit(f"Env file not found: {env_path}")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def extraction_args_for(args: argparse.Namespace, batch_input: Path, report_output: Path) -> SimpleNamespace:
    return SimpleNamespace(
        input=str(batch_input),
        max_candidates=args.batch_size,
        deterministic_only=args.deterministic_only,
        include_prompt=False,
        dry_run=True,
        env_file=args.llm_env_file,
        llm_provider=args.llm_provider,
        api_key_env=args.llm_api_key_env,
        model=args.model,
        base_url=args.base_url,
        timeout=args.timeout,
        cache=args.cache,
        output=str(report_output),
    )


def apply_args_for(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        offline=args.offline,
        env_file=args.mem0_env_file,
        api_key_env=args.mem0_api_key_env,
        base_url=args.mem0_base_url,
        user_id=args.user_id,
        agent_id=args.agent_id,
    )


def write_audit_json(path: str | Path, audit: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def process_batch(rows: list[dict[str, Any]], args: argparse.Namespace, paths: dict[str, Path]) -> dict[str, Any]:
    write_jsonl_batch(paths["input"], rows)

    report = build_report(extraction_args_for(args, paths["input"], paths["report"]))
    write_json(paths["report"], report)

    repaired, repair_counts = repair_source_ids(report)
    write_json(paths["repaired_report"], repaired)

    audit = audit_report(repaired)
    write_audit_json(paths["audit"], audit)
    write_audit_markdown(paths["audit_markdown"], audit)
    audit_summary = audit.get("summary") or {}
    if audit_summary.get("status") == "failed" or (args.fail_on_soft and audit_summary.get("soft_flag_count")):
        raise RuntimeError(f"audit failed: {audit_summary}")

    validate_report_quality(repaired, execute=args.execute, allow_quality_failed=args.allow_quality_failed)
    proposals = proposals_from_report(repaired)
    client = client_from_args(apply_args_for(args))
    plans = build_apply_plan(proposals, client)
    summary = execute_plan(plans, client, execute=args.execute) if client is not None else execute_plan(plans, mem0_client=None, execute=False)
    review_files = write_review_files(paths["review_dir"], Path(paths["apply"]).stem, plans, preview_chars=args.review_preview_chars)
    output_summary = {**summary, "plan_count": len(plans), "review_files": review_files}
    write_json(paths["apply"], {"summary": output_summary, "plans": [plan.to_dict() for plan in plans]})
    return {
        "input_rows": len(rows),
        "candidate_count": repaired.get("summary", {}).get("candidate_count", 0),
        "proposal_count": len(proposals),
        "repair": repair_counts,
        "audit": audit_summary,
        "apply": output_summary.get("counts", {}),
        "executed": bool(args.execute),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resume-safe batch orchestrator for Mem0 extractor reports.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--stop-after-batches", type=int, default=0, help="Process at most this many new batches in this run.")
    parser.add_argument("--deterministic-only", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-quality-failed", action="store_true")
    parser.add_argument("--fail-on-soft", action="store_true")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute and args.offline:
        print("error=Cannot use --execute with --offline.", file=sys.stderr)
        return 2
    if not args.deterministic_only:
        load_env_file(args.llm_env_file)
    if not args.offline:
        load_env_file(args.mem0_env_file)

    rows = read_input(Path(args.input).expanduser())
    ranges = batch_ranges(len(rows), args.batch_size)
    output_root = Path(args.output_root)
    manifest_file = manifest_path(output_root, args.run_id)
    manifest = load_manifest(manifest_file)
    manifest["summary"] = {
        "input": str(Path(args.input).expanduser()),
        "run_id": args.run_id,
        "batch_size": args.batch_size,
        "total_rows": len(rows),
        "total_batches": len(ranges),
        "execute": bool(args.execute),
        "offline": bool(args.offline),
        "deterministic_only": bool(args.deterministic_only),
        "model": args.model if not args.deterministic_only else "deterministic",
    }
    save_manifest(manifest_file, manifest)

    processed = 0
    skipped = 0
    failed = 0
    for batch_index, (start, end) in enumerate(ranges, start=1):
        if args.stop_after_batches and processed >= args.stop_after_batches:
            break
        if manifest_is_batch_done(manifest, batch_index):
            skipped += 1
            continue
        paths = build_batch_paths(output_root, args.run_id, batch_index)
        update_batch_manifest(manifest, manifest_file, batch_index, "running", paths, {"start": start, "end": end})
        try:
            counts = process_batch(rows[start:end], args, paths)
        except Exception as exc:  # noqa: BLE001 - batch runner must preserve failure in manifest.
            failed += 1
            update_batch_manifest(manifest, manifest_file, batch_index, "failed", paths, {"start": start, "end": end}, error=f"{exc.__class__.__name__}: {exc}")
            break
        processed += 1
        update_batch_manifest(manifest, manifest_file, batch_index, "completed", paths, {"start": start, "end": end, **counts})

    print(
        json.dumps(
            {
                "manifest": str(manifest_file),
                "processed_batches": processed,
                "skipped_batches": skipped,
                "failed_batches": failed,
                "total_batches": len(ranges),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
