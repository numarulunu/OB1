#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from grading import junk_reason
from schemas import MemoryProposal, normalize_proposal, normalize_text

HARD_FLAG_PENALTY = 18
SOFT_FLAG_PENALTY = 6
PROTECTED_DOMAINS = {"psychology", "relationships", "family_origin"}


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, obj: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def proposal_rows(report: Any) -> list[MemoryProposal]:
    rows = report.get("proposals") if isinstance(report, dict) else []
    proposals: list[MemoryProposal] = []
    for row in rows or []:
        if isinstance(row, dict) and normalize_text(row.get("content")):
            proposals.append(normalize_proposal(row))
    return proposals


def candidate_source_ids(report: Any) -> set[str]:
    ids: set[str] = set()
    for row in (report.get("candidates") if isinstance(report, dict) else []) or []:
        if isinstance(row, dict):
            source_id = normalize_text(row.get("source_id"))
            if source_id:
                ids.add(source_id)
    return ids


def resolve_source_id(source_id: str, known_sources: set[str]) -> str:
    if source_id in known_sources:
        return source_id
    if len(source_id) < 8:
        return ""
    matches = [known for known in known_sources if known.startswith(source_id)]
    if len(matches) == 1:
        return matches[0]
    return ""


def compact_key(content: str) -> str:
    text = normalize_text(content).lower()
    return "".join(ch for ch in text if ch.isalnum() or ch.isspace())[:260]


def add_issue(issues: list[dict[str, Any]], flag: str, severity: str, proposal_index: int | None = None, detail: str = "") -> None:
    row: dict[str, Any] = {"flag": flag, "severity": severity}
    if proposal_index is not None:
        row["proposal_index"] = proposal_index
    if detail:
        row["detail"] = detail
    issues.append(row)


def audit_proposals(report: Any, proposals: list[MemoryProposal]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    known_sources = candidate_source_ids(report)
    duplicate_counts = Counter(compact_key(proposal.content) for proposal in proposals)
    input_rows = int((report.get("summary") or {}).get("input_rows") or 0) if isinstance(report, dict) else 0
    proposal_rate = len(proposals) / input_rows if input_rows else 0.0

    if input_rows and proposal_rate > 0.45:
        add_issue(issues, "high_proposal_rate", "soft", detail=f"{proposal_rate:.3f}")
    if input_rows and proposal_rate < 0.03 and len(proposals) < 5:
        add_issue(issues, "low_proposal_rate", "soft", detail=f"{proposal_rate:.3f}")

    for index, proposal in enumerate(proposals, start=1):
        if proposal.action in {"skip", "ask_user"}:
            continue
        if duplicate_counts[compact_key(proposal.content)] > 1:
            add_issue(issues, "duplicate_content", "hard", index)
        if proposal.action in {"save", "update"} and not proposal.source_ids:
            add_issue(issues, "missing_source_ids", "hard", index)
        for source_id in proposal.source_ids:
            if known_sources and not resolve_source_id(source_id, known_sources):
                add_issue(issues, "unknown_source_id", "hard", index, source_id)
        if proposal.action in {"save", "update"} and not proposal.domains:
            add_issue(issues, "missing_domains", "hard", index)
        if proposal.action in {"save", "update"} and proposal.signal_strength < 4:
            add_issue(issues, "low_signal_save", "hard", index)
        if proposal.action in {"save", "update"} and len(proposal.content) < 48:
            add_issue(issues, "too_short", "soft", index)
        if proposal.action in {"save", "update"} and len(proposal.content) > 1600:
            add_issue(issues, "too_long", "soft", index)
        reason = junk_reason(proposal.content)
        if reason:
            add_issue(issues, reason, "hard", index)
        if PROTECTED_DOMAINS.intersection(proposal.domains) and proposal.signal_strength < 7:
            add_issue(issues, "protected_domain_low_signal", "soft", index)
        if proposal.memory_type == "pattern" and not any(term in proposal.content.lower() for term in ("recurring", "pattern", "tends to", "often", "repeated")):
            add_issue(issues, "weak_pattern_type", "soft", index)
        if proposal.memory_tier == "cold" and proposal.signal_strength >= 8:
            add_issue(issues, "high_signal_cold_tier", "soft", index)

    return issues


def audit_report(report: Any) -> dict[str, Any]:
    proposals = proposal_rows(report)
    issues = audit_proposals(report, proposals)
    hard_flags = [issue["flag"] for issue in issues if issue["severity"] == "hard"]
    soft_flags = [issue["flag"] for issue in issues if issue["severity"] == "soft"]
    hard_count = len(hard_flags)
    soft_count = len(soft_flags)
    quality_score = max(0, 100 - hard_count * HARD_FLAG_PENALTY - soft_count * SOFT_FLAG_PENALTY)
    type_counts = Counter(proposal.memory_type for proposal in proposals)
    tier_counts = Counter(proposal.memory_tier for proposal in proposals)
    domain_counts = Counter(domain for proposal in proposals for domain in proposal.domains)

    return {
        "summary": {
            "status": "failed" if hard_count else "passed",
            "quality_score": quality_score,
            "proposal_count": len(proposals),
            "hard_flag_count": hard_count,
            "soft_flag_count": soft_count,
            "hard_flags": sorted(set(hard_flags)),
            "soft_flags": sorted(set(soft_flags)),
            "memory_types": dict(type_counts),
            "memory_tiers": dict(tier_counts),
            "top_domains": dict(domain_counts.most_common(12)),
        },
        "issues": issues,
    }


def write_audit_markdown(path: str | Path, audit: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = audit.get("summary") or {}
    lines = [
        "# Mem0 Extractor Audit",
        "",
        f"- status: `{summary.get('status')}`",
        f"- quality_score: `{summary.get('quality_score')}`",
        f"- proposals: `{summary.get('proposal_count')}`",
        f"- hard_flags: `{summary.get('hard_flag_count')}` {summary.get('hard_flags')}",
        f"- soft_flags: `{summary.get('soft_flag_count')}` {summary.get('soft_flags')}",
        "",
        "## Distribution",
        "",
        f"- memory_types: `{summary.get('memory_types')}`",
        f"- memory_tiers: `{summary.get('memory_tiers')}`",
        f"- top_domains: `{summary.get('top_domains')}`",
        "",
        "## Issues",
        "",
    ]
    issues = audit.get("issues") or []
    if not issues:
        lines.append("No audit issues.")
    else:
        lines.append("| severity | flag | proposal_index | detail |")
        lines.append("| --- | --- | --- | --- |")
        for issue in issues[:200]:
            lines.append(
                "| {severity} | {flag} | {proposal_index} | {detail} |".format(
                    severity=issue.get("severity", ""),
                    flag=issue.get("flag", ""),
                    proposal_index=issue.get("proposal_index", ""),
                    detail=str(issue.get("detail", "")).replace("|", "\\|"),
                )
            )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a Mem0 extractor report for quality issues before apply.")
    parser.add_argument("--report", required=True, help="Extractor report JSON")
    parser.add_argument("--output", required=True, help="Audit JSON output")
    parser.add_argument("--markdown-output", default="", help="Optional compact Markdown output")
    parser.add_argument("--fail-on-soft", action="store_true", help="Exit non-zero when soft issues exist.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    audit = audit_report(read_json(args.report))
    write_json(args.output, audit)
    if args.markdown_output:
        write_audit_markdown(args.markdown_output, audit)
    summary = audit["summary"]
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if summary["status"] == "failed" or (args.fail_on_soft and summary["soft_flag_count"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
