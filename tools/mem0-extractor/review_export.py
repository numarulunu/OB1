from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from schemas import ActionPlan, normalize_text

REVIEW_COLUMNS = [
    "proposal_id",
    "action",
    "memory_type",
    "signal_strength",
    "memory_tier",
    "current_status",
    "domains",
    "reason",
    "preview",
]


def compact_preview(value: Any, preview_chars: int = 180) -> str:
    text = normalize_text(value)
    if preview_chars < 8:
        preview_chars = 8
    if len(text) <= preview_chars:
        return text
    return text[: preview_chars - 3].rstrip() + "..."


def build_review_rows(plans: list[ActionPlan], preview_chars: int = 180) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, plan in enumerate(plans, start=1):
        proposal = plan.proposal
        rows.append(
            {
                "proposal_id": f"p{index:04d}",
                "action": plan.action,
                "memory_type": proposal.memory_type,
                "signal_strength": str(proposal.signal_strength),
                "memory_tier": proposal.memory_tier,
                "current_status": proposal.current_status,
                "domains": ",".join(proposal.domains),
                "reason": compact_preview(plan.reason or proposal.reason, 120),
                "preview": compact_preview(proposal.content, preview_chars),
            }
        )
    return rows


def _markdown_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_markdown_review(path: str | Path, rows: list[dict[str, str]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    header = "| " + " | ".join(REVIEW_COLUMNS) + " |"
    divider = "| " + " | ".join("---" for _ in REVIEW_COLUMNS) + " |"
    lines = [header, divider]
    for row in rows:
        lines.append("| " + " | ".join(_markdown_cell(row.get(column, "")) for column in REVIEW_COLUMNS) + " |")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv_review(path: str | Path, rows: list[dict[str, str]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_review_files(output_dir: str | Path, prefix: str, plans: list[ActionPlan], preview_chars: int = 180) -> dict[str, str]:
    root = Path(output_dir)
    safe_prefix = normalize_text(prefix).replace(" ", "-") or "mem0-apply-review"
    rows = build_review_rows(plans, preview_chars=preview_chars)
    markdown_path = root / f"{safe_prefix}.md"
    csv_path = root / f"{safe_prefix}.csv"
    write_markdown_review(markdown_path, rows)
    write_csv_review(csv_path, rows)
    return {"markdown": str(markdown_path), "csv": str(csv_path)}
