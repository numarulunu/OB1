#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dedupe import plan_proposal_action
from mem0_client import Mem0Client, Mem0ClientConfig
from review_export import write_review_files
from schemas import ActionPlan, MemoryProposal, normalize_proposal

POLICY_VERSION = "human-memory-v2"
SOURCE = "mem0-extractor"
DEFAULT_BASE_URL = "https://mem0-api.ionutrosu.xyz"


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


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, obj: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_report_proposals(path: str | Path) -> list[MemoryProposal]:
    return proposals_from_report(read_json(path))



def proposals_from_report(report: Any) -> list[MemoryProposal]:
    rows = report.get("proposals") if isinstance(report, dict) else []
    proposals = []
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("content") or "").strip():
            proposals.append(normalize_proposal(row))
    return proposals


def validate_report_quality(report: Any, execute: bool = False, allow_quality_failed: bool = False) -> None:
    if not execute or allow_quality_failed or not isinstance(report, dict):
        return
    quality = (report.get("summary") or {}).get("metadata_quality") or {}
    if isinstance(quality, dict) and quality.get("status") == "failed":
        warnings = ",".join(str(item) for item in quality.get("warnings") or [])
        detail = f" ({warnings})" if warnings else ""
        raise RuntimeError(f"metadata quality failed{detail}; rerun extraction or pass --allow-quality-failed")

def proposal_metadata(proposal: MemoryProposal) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "policy_version": POLICY_VERSION,
        "domains": proposal.domains,
        "memory_type": proposal.memory_type,
        "signal_strength": proposal.signal_strength,
        "current_status": proposal.current_status,
        "memory_tier": proposal.memory_tier,
        "source_ids": proposal.source_ids,
        "source_id_count": len(proposal.source_ids),
    }


def offline_action_plan(proposal: MemoryProposal) -> ActionPlan:
    if proposal.action == "update" and proposal.existing_id:
        return ActionPlan(action="update", proposal=proposal, existing_id=proposal.existing_id, reason="explicit_existing_id")
    if proposal.action in {"skip", "ask_user"}:
        return ActionPlan(action=proposal.action, proposal=proposal, existing_id=proposal.existing_id, reason=proposal.reason)
    return ActionPlan(action="save", proposal=proposal, existing_id="", reason="offline_no_dedupe")


def build_apply_plan(proposals: list[MemoryProposal], mem0_client: Any | None) -> list[ActionPlan]:
    plans: list[ActionPlan] = []
    for proposal in proposals:
        if mem0_client is None:
            plans.append(offline_action_plan(proposal))
        else:
            plans.append(plan_proposal_action(proposal, mem0_client))
    return plans


def empty_counts() -> dict[str, int]:
    return {"save": 0, "update": 0, "skip": 0, "ask_user": 0, "failed": 0}


def execute_plan(plans: list[ActionPlan], mem0_client: Any, execute: bool = False) -> dict[str, Any]:
    counts = empty_counts()
    results: list[dict[str, Any]] = []
    for plan in plans:
        action = plan.action if plan.action in counts else "skip"
        if action in {"skip", "ask_user"} or not execute:
            counts[action] += 1
            results.append({"action": action, "existing_id": plan.existing_id, "reason": plan.reason, "executed": False})
            continue

        metadata = proposal_metadata(plan.proposal)
        try:
            if action == "save":
                response = mem0_client.save(plan.proposal.content, metadata)
            elif action == "update" and plan.existing_id:
                response = mem0_client.update(plan.existing_id, plan.proposal.content, metadata)
            else:
                counts["failed"] += 1
                results.append({"action": action, "existing_id": plan.existing_id, "reason": "missing_existing_id", "executed": False})
                continue
            counts[action] += 1
            results.append({"action": action, "existing_id": plan.existing_id, "reason": plan.reason, "executed": True, "response": response})
        except Exception as exc:  # noqa: BLE001 - batch apply should report per-row failures.
            counts["failed"] += 1
            results.append({"action": action, "existing_id": plan.existing_id, "reason": exc.__class__.__name__, "executed": False})
    return {"dry_run": not execute, "counts": counts, "results": results}


def client_from_args(args: argparse.Namespace) -> Mem0Client | None:
    if args.offline:
        return None
    load_env_file(args.env_file)
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"Missing API key env var: {args.api_key_env}")
    return Mem0Client(
        Mem0ClientConfig(
            base_url=args.base_url,
            api_key=api_key,
            user_id=args.user_id,
            agent_id=args.agent_id,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or apply Mem0 extractor proposal reports.")
    parser.add_argument("--report", required=True, help="Report JSON from extract_session.py")
    parser.add_argument("--output", required=True, help="Write local apply plan/report JSON")
    parser.add_argument("--execute", action="store_true", help="Actually save/update Mem0. Omit for dry-run.")
    parser.add_argument("--offline", action="store_true", help="Do not contact Mem0; no dedupe/search is performed.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--env-file", default="")
    parser.add_argument("--api-key-env", default="MEM0_API_KEY")
    parser.add_argument("--user-id", default="ionut")
    parser.add_argument("--agent-id", default="mem0-extractor")
    parser.add_argument("--allow-quality-failed", action="store_true", help="Allow --execute even when report metadata_quality failed.")
    parser.add_argument("--review-dir", default="", help="Optional directory for compact Markdown and CSV review exports.")
    parser.add_argument("--review-prefix", default="mem0-apply-review", help="Filename prefix for --review-dir outputs.")
    parser.add_argument("--review-preview-chars", type=int, default=180, help="Max proposal preview characters in review exports.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute and args.offline:
        print("error=Cannot use --execute with --offline.", file=sys.stderr)
        return 2
    report = read_json(args.report)
    try:
        validate_report_quality(report, execute=args.execute, allow_quality_failed=args.allow_quality_failed)
    except RuntimeError as exc:
        print(f"error={exc}", file=sys.stderr)
        return 2
    proposals = proposals_from_report(report)
    client = client_from_args(args)
    plans = build_apply_plan(proposals, client)
    summary = execute_plan(plans, client, execute=args.execute) if client is not None else execute_plan(plans, mem0_client=None, execute=False)
    output_summary = {**summary, "plan_count": len(plans)}
    if args.review_dir:
        output_summary["review_files"] = write_review_files(
            args.review_dir,
            args.review_prefix,
            plans,
            preview_chars=args.review_preview_chars,
        )
    output = {"summary": output_summary, "plans": [plan.to_dict() for plan in plans]}
    write_json(args.output, output)
    printed = {"dry_run": summary["dry_run"], "plan_count": len(plans), "counts": summary["counts"]}
    if "review_files" in output_summary:
        printed["review_files"] = output_summary["review_files"]
    print(json.dumps(printed, ensure_ascii=False, sort_keys=True))
    return 0 if summary["counts"].get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
