import csv

from review_export import build_review_rows, write_csv_review, write_markdown_review
from schemas import ActionPlan, normalize_proposal


def sample_plan():
    proposal = normalize_proposal(
        {
            "action": "save",
            "content": "This is a deliberately long memory card. " * 12,
            "domains": ["ai", "systems"],
            "memory_type": "workflow",
            "signal_strength": 7,
            "memory_tier": "active",
            "current_status": "active",
            "source_ids": ["row-1"],
            "reason": "useful workflow",
        }
    )
    return ActionPlan(action="save", proposal=proposal, reason="offline_no_dedupe")


def test_build_review_rows_are_compact_and_do_not_dump_full_content():
    rows = build_review_rows([sample_plan()], preview_chars=80)

    assert rows[0]["proposal_id"] == "p0001"
    assert rows[0]["action"] == "save"
    assert rows[0]["memory_type"] == "workflow"
    assert rows[0]["signal_strength"] == "7"
    assert len(rows[0]["preview"]) <= 80
    assert rows[0]["preview"].endswith("...")


def test_write_review_exports_markdown_and_csv(tmp_path):
    rows = build_review_rows([sample_plan()], preview_chars=80)
    markdown_path = tmp_path / "review.md"
    csv_path = tmp_path / "review.csv"

    write_markdown_review(markdown_path, rows)
    write_csv_review(csv_path, rows)

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "| proposal_id | action |" in markdown
    assert "p0001" in markdown

    csv_rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))
    assert csv_rows[0]["proposal_id"] == "p0001"
    assert csv_rows[0]["preview"].endswith("...")
