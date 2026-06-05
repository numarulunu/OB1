from types import SimpleNamespace

from extract_session import build_report


def test_build_report_includes_metadata_quality_for_proposals(tmp_path, monkeypatch):
    input_path = tmp_path / "session.jsonl"
    output_text = '{"id":"row-1","content":"Decision: use Mem0 as the operational brain."}\n'
    input_path.write_text(output_text, encoding="utf-8")

    args = SimpleNamespace(
        input=str(input_path),
        max_candidates=10,
        deterministic_only=True,
        include_prompt=False,
        dry_run=True,
    )

    report = build_report(args)

    assert "metadata_quality" in report["summary"]
    assert report["summary"]["metadata_quality"]["status"] == "passed"
