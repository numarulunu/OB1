import json
import subprocess
import sys
from pathlib import Path

from apply_report import build_apply_plan, execute_plan, proposal_metadata
from schemas import normalize_proposal


class FakeMem0:
    def __init__(self, results=None):
        self.results = results or []
        self.saved = []
        self.updated = []

    def search(self, query, top_k=5, domains=None, memory_tiers=None):
        return {"results": self.results}

    def save(self, content, metadata):
        self.saved.append((content, metadata))
        return {"id": f"saved-{len(self.saved)}"}

    def update(self, memory_id, content, metadata):
        self.updated.append((memory_id, content, metadata))
        return {"id": memory_id, "updated": True}


def sample_proposal(content="Mem0 is the operational brain."):
    return normalize_proposal(
        {
            "action": "save",
            "content": content,
            "domains": ["ai", "systems"],
            "memory_type": "decision",
            "signal_strength": 8,
            "current_status": "active",
            "memory_tier": "active",
            "source_ids": ["row-1", "row-2"],
            "reason": "durable project state",
        }
    )


def test_proposal_metadata_keeps_tiers_signal_and_source_ids():
    metadata = proposal_metadata(sample_proposal())

    assert metadata == {
        "source": "mem0-extractor",
        "policy_version": "human-memory-v2",
        "domains": ["ai", "systems"],
        "memory_type": "decision",
        "signal_strength": 8,
        "current_status": "active",
        "memory_tier": "active",
        "source_ids": ["row-1", "row-2"],
        "source_id_count": 2,
    }


def test_build_apply_plan_uses_dedupe_when_client_is_available():
    existing = [{"id": "mem-1", "text": "Mem0 is the operational brain.", "metadata": {"domains": ["ai"]}}]

    plans = build_apply_plan([sample_proposal()], mem0_client=FakeMem0(existing))

    assert len(plans) == 1
    assert plans[0].action == "skip"
    assert plans[0].existing_id == "mem-1"
    assert plans[0].reason == "exact_duplicate"


def test_build_apply_plan_without_client_honors_explicit_update_id():
    proposal = normalize_proposal({**sample_proposal().to_dict(), "action": "update", "existing_id": "mem-2"})

    plans = build_apply_plan([proposal], mem0_client=None)

    assert plans[0].action == "update"
    assert plans[0].existing_id == "mem-2"
    assert plans[0].reason == "explicit_existing_id"


def test_execute_plan_dry_run_does_not_write():
    fake = FakeMem0()
    plans = build_apply_plan([sample_proposal()], mem0_client=None)

    summary = execute_plan(plans, fake, execute=False)

    assert summary["dry_run"] is True
    assert summary["counts"] == {"save": 1, "update": 0, "skip": 0, "ask_user": 0, "failed": 0}
    assert fake.saved == []
    assert fake.updated == []


def test_execute_plan_execute_saves_and_updates_only():
    fake = FakeMem0()
    update = normalize_proposal({**sample_proposal("Updated memory.").to_dict(), "action": "update", "existing_id": "mem-3"})
    plans = build_apply_plan([sample_proposal(), update], mem0_client=None)

    summary = execute_plan(plans, fake, execute=True)

    assert summary["dry_run"] is False
    assert summary["counts"]["save"] == 1
    assert summary["counts"]["update"] == 1
    assert fake.saved[0][0] == "Mem0 is the operational brain."
    assert fake.saved[0][1]["memory_tier"] == "active"
    assert fake.updated[0][0] == "mem-3"


def test_cli_writes_offline_dry_run_plan_without_mem0_key(tmp_path):
    report = tmp_path / "report.json"
    output = tmp_path / "apply-plan.json"
    report.write_text(
        json.dumps({"proposals": [sample_proposal().to_dict()]}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("apply_report.py")),
            "--report",
            str(report),
            "--output",
            str(output),
            "--offline",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    assert printed["dry_run"] is True
    assert printed["plan_count"] == 1
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["summary"]["counts"]["save"] == 1
    assert saved["plans"][0]["proposal"]["content"] == "Mem0 is the operational brain."


def test_failed_metadata_quality_blocks_execute_without_override():
    from apply_report import validate_report_quality

    report = {"summary": {"metadata_quality": {"status": "failed", "warnings": ["pattern_overuse"]}}}

    try:
        validate_report_quality(report, execute=True, allow_quality_failed=False)
    except RuntimeError as exc:
        assert "metadata quality failed" in str(exc)
    else:
        raise AssertionError("expected quality gate failure")


def test_failed_metadata_quality_allows_dry_run_review():
    from apply_report import validate_report_quality

    report = {"summary": {"metadata_quality": {"status": "failed", "warnings": ["pattern_overuse"]}}}

    validate_report_quality(report, execute=False, allow_quality_failed=False)


def test_cli_can_write_compact_review_exports(tmp_path):
    report = tmp_path / "report.json"
    output = tmp_path / "apply-plan.json"
    review_dir = tmp_path / "review"
    report.write_text(
        json.dumps({"proposals": [sample_proposal("This memory card should be visible only as a compact preview. " * 8).to_dict()]}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("apply_report.py")),
            "--report",
            str(report),
            "--output",
            str(output),
            "--offline",
            "--review-dir",
            str(review_dir),
            "--review-prefix",
            "batch-review",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    markdown_path = Path(printed["review_files"]["markdown"])
    csv_path = Path(printed["review_files"]["csv"])
    assert markdown_path.exists()
    assert csv_path.exists()
    assert "p0001" in markdown_path.read_text(encoding="utf-8")
