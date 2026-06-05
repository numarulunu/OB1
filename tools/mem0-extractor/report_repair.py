#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from audit_report import resolve_source_id
from schemas import normalize_text


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, obj: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def candidate_source_ids(report: dict[str, Any]) -> set[str]:
    return {normalize_text(row.get("source_id")) for row in report.get("candidates") or [] if isinstance(row, dict) and normalize_text(row.get("source_id"))}


def repair_source_ids(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    known_sources = candidate_source_ids(report)
    repaired = json.loads(json.dumps(report, ensure_ascii=False))
    counts = {"resolved_prefixes": 0, "removed_invalid_source_ids": 0, "proposal_actions_changed_to_ask_user": 0}

    for proposal in repaired.get("proposals") or []:
        if not isinstance(proposal, dict):
            continue
        original = [normalize_text(source_id) for source_id in proposal.get("source_ids") or [] if normalize_text(source_id)]
        clean: list[str] = []
        for source_id in original:
            resolved = resolve_source_id(source_id, known_sources)
            if resolved:
                if resolved != source_id:
                    counts["resolved_prefixes"] += 1
                if resolved not in clean:
                    clean.append(resolved)
            else:
                counts["removed_invalid_source_ids"] += 1
        proposal["source_ids"] = clean
        if original and not clean and proposal.get("action") in {"save", "update"}:
            proposal["action"] = "ask_user"
            proposal["reason"] = (normalize_text(proposal.get("reason")) + " provenance repair could not resolve source_ids").strip()
            counts["proposal_actions_changed_to_ask_user"] += 1

    repaired.setdefault("summary", {})["source_id_repair"] = counts
    return repaired, counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair extractor report source_id provenance after LLM prefix/hallucination issues.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repaired, counts = repair_source_ids(read_json(args.report))
    write_json(args.output, repaired)
    print(json.dumps({"output": args.output, **counts}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
