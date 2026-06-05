import json
import subprocess
import sys
from pathlib import Path

from audit_report import audit_report, write_audit_markdown


def proposal(content, **overrides):
    row = {
        "action": "save",
        "content": content,
        "domains": ["ai", "systems"],
        "memory_type": "workflow",
        "signal_strength": 8,
        "current_status": "active",
        "memory_tier": "active",
        "source_ids": ["row-1"],
        "reason": "useful durable memory",
    }
    row.update(overrides)
    return row


def base_report(proposals):
    return {
        "summary": {"input_rows": 4, "candidate_count": 4, "proposal_count": len(proposals)},
        "candidates": [
            {"source_id": "row-1", "domains": ["ai"], "memory_type": "workflow"},
            {"source_id": "row-2", "domains": ["systems"], "memory_type": "decision"},
        ],
        "proposals": proposals,
    }


def test_audit_passes_clean_report():
    report = base_report(
        [
            proposal("The Mem0 extractor workflow uses cached LLM distillation and guarded apply planning.", source_ids=["row-1"]),
            proposal("Decision: use Mem0 as the operational brain while keeping OB1 as source history.", memory_type="decision", source_ids=["row-2"]),
        ]
    )

    audit = audit_report(report)

    assert audit["summary"]["status"] == "passed"
    assert audit["summary"]["hard_flag_count"] == 0
    assert audit["summary"]["quality_score"] >= 90


def test_audit_fails_junk_missing_source_and_duplicates():
    duplicate = "The Mem0 extractor workflow uses cached LLM distillation and guarded apply planning."
    report = base_report(
        [
            proposal(duplicate, source_ids=["row-1"]),
            proposal(duplicate, source_ids=["row-1"]),
            proposal("The lesson covered larynx height and resonance choices for a student's vocal exercise.", domains=["vocality"], memory_type="lesson", source_ids=["row-2"]),
            proposal("A useful but orphaned memory card.", source_ids=[]),
        ]
    )

    audit = audit_report(report)

    assert audit["summary"]["status"] == "failed"
    assert "duplicate_content" in audit["summary"]["hard_flags"]
    assert "generic_vocal_lesson_sludge" in audit["summary"]["hard_flags"]
    assert "missing_source_ids" in audit["summary"]["hard_flags"]


def test_audit_flags_unknown_source_id_and_high_proposal_rate():
    report = base_report(
        [
            proposal(f"Durable workflow memory number {index} with enough detail to be meaningful.", source_ids=["missing-row"])
            for index in range(4)
        ]
    )

    audit = audit_report(report)

    assert audit["summary"]["status"] == "failed"
    assert "unknown_source_id" in audit["summary"]["hard_flags"]
    assert "high_proposal_rate" in audit["summary"]["soft_flags"]


def test_audit_accepts_unique_uuid_prefix_source_id():
    report = {
        "summary": {"input_rows": 4, "candidate_count": 1, "proposal_count": 1},
        "candidates": [{"source_id": "7ca71592-a70a-4918-85af-c9f83cf50baa"}],
        "proposals": [
            proposal(
                "Decision: keep source provenance even when models shorten UUIDs in proposal source IDs.",
                memory_type="decision",
                source_ids=["7ca71592"],
            )
        ],
    }

    audit = audit_report(report)

    assert "unknown_source_id" not in audit["summary"]["hard_flags"]


def test_audit_does_not_flag_weak_pattern_type_for_skipped_proposals():
    report = base_report(
        [
            proposal(
                "Low-signal proposal already rejected by the extractor.",
                action="skip",
                memory_type="pattern",
                signal_strength=2,
                memory_tier="cold",
            )
        ]
    )

    audit = audit_report(report)

    assert audit["summary"]["status"] == "passed"
    assert "weak_pattern_type" not in audit["summary"]["soft_flags"]


def test_write_audit_markdown_is_compact(tmp_path):
    audit = audit_report(base_report([proposal("Decision: keep compact audit reports for extractor QA.", memory_type="decision")]))
    output = tmp_path / "audit.md"

    write_audit_markdown(output, audit)

    text = output.read_text(encoding="utf-8")
    assert "# Mem0 Extractor Audit" in text
    assert "quality_score" in text
    assert "Decision: keep compact" not in text


def test_cli_writes_audit_json_and_markdown(tmp_path):
    report = tmp_path / "report.json"
    audit_json = tmp_path / "audit.json"
    audit_md = tmp_path / "audit.md"
    report.write_text(json.dumps(base_report([proposal("Decision: keep compact audit reports for extractor QA.", memory_type="decision")])) , encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("audit_report.py")),
            "--report",
            str(report),
            "--output",
            str(audit_json),
            "--markdown-output",
            str(audit_md),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    assert printed["status"] == "passed"
    assert audit_json.exists()
    assert audit_md.exists()
