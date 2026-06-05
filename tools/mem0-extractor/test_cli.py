import json
import subprocess
import sys
from pathlib import Path


def test_cli_dry_run_writes_candidate_report(tmp_path):
    input_path = tmp_path / "session.jsonl"
    output_path = tmp_path / "report.json"
    rows = [
        {"id": "m1", "role": "user", "content": "Family-origin relationship event shaped trust and ambition."},
        {"id": "m2", "role": "assistant", "content": "Speaker diarization was inferred from conversational cues."},
    ]
    input_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("extract_session.py")),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--dry-run",
            "--deterministic-only",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["summary"]["input_rows"] == 2
    assert report["summary"]["candidate_count"] == 1
    assert report["candidates"][0]["source_id"] == "m1"
    assert "Family-origin" in report["candidates"][0]["text_preview"]
