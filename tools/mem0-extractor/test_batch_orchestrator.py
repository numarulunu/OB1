import json
import subprocess
import sys
from pathlib import Path

from batch_orchestrator import (
    batch_ranges,
    build_batch_paths,
    load_manifest,
    manifest_is_batch_done,
    update_batch_manifest,
    write_jsonl_batch,
)


def test_batch_ranges_split_rows_without_overlap():
    assert batch_ranges(10, 4) == [(0, 4), (4, 8), (8, 10)]


def test_build_batch_paths_are_stable(tmp_path):
    paths = build_batch_paths(tmp_path, "pilot", 3)

    assert paths["input"].name == "pilot-batch-003-input.jsonl"
    assert paths["report"].name == "pilot-batch-003-report.json"
    assert paths["repaired_report"].name == "pilot-batch-003-report-repaired.json"
    assert paths["audit"].name == "pilot-batch-003-audit.json"
    assert paths["apply"].name == "pilot-batch-003-apply.json"


def test_write_jsonl_batch_preserves_json_rows(tmp_path):
    output = tmp_path / "batch.jsonl"
    rows = [{"id": "a", "content": "one"}, {"id": "b", "content": "two"}]

    write_jsonl_batch(output, rows)

    loaded = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert loaded == rows


def test_manifest_update_and_resume_detection(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest = load_manifest(manifest_path)

    update_batch_manifest(
        manifest,
        manifest_path,
        batch_index=1,
        status="completed",
        paths={"report": tmp_path / "report.json"},
        counts={"save": 2, "skip": 1},
    )

    reloaded = load_manifest(manifest_path)
    assert manifest_is_batch_done(reloaded, 1)
    assert reloaded["batches"]["1"]["counts"] == {"save": 2, "skip": 1}


def test_cli_runs_deterministic_offline_batches_and_resumes(tmp_path):
    input_path = tmp_path / "input.jsonl"
    rows = [
        {"id": f"row-{index}", "content": f"Decision: use Mem0 as the operational brain for AI systems batch fixture {index}."}
        for index in range(5)
    ]
    input_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    output_root = tmp_path / "run"

    command = [
        sys.executable,
        str(Path(__file__).with_name("batch_orchestrator.py")),
        "--input",
        str(input_path),
        "--output-root",
        str(output_root),
        "--run-id",
        "pilot",
        "--batch-size",
        "2",
        "--deterministic-only",
        "--offline",
        "--stop-after-batches",
        "2",
    ]
    first = subprocess.run(command, text=True, capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    first_printed = json.loads(first.stdout)
    assert first_printed["processed_batches"] == 2
    assert first_printed["skipped_batches"] == 0

    second = subprocess.run(command, text=True, capture_output=True, check=False)

    assert second.returncode == 0, second.stderr
    second_printed = json.loads(second.stdout)
    assert second_printed["processed_batches"] == 1
    assert second_printed["skipped_batches"] == 2

    manifest = json.loads((output_root / "pilot-manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["total_batches"] == 3
    assert manifest["batches"]["1"]["status"] == "completed"
    assert manifest["batches"]["2"]["status"] == "completed"
    assert manifest["batches"]["3"]["status"] == "completed"
