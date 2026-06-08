from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "public_artifact_scan.py"


def load_module():
    spec = importlib.util.spec_from_file_location("public_artifact_scan", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_public_artifact_scan_allows_safe_boolean_beam_flags(tmp_path):
    module = load_module()
    artifact = tmp_path / "approval.json"
    artifact.write_text(
        json.dumps(
            {
                "ok": True,
                "beam_state_ledger": True,
                "beam_state_verifier": True,
                "command_template": "python3 /opt/kontext/scripts/judged_benchmark_run.py --api-key-env OPENAI_API_KEY",
            }
        ),
        encoding="utf-8",
    )

    report = module.build_report([str(artifact)])

    assert report["ok"] is True
    assert report["totals"]["raw_payload_hits"] == 0
    assert report["totals"]["private_path_hits"] == 0
    assert report["totals"]["secret_shaped_hits"] == 0


def test_public_artifact_scan_rejects_raw_payload_keys_without_echoing_values(tmp_path):
    module = load_module()
    artifact = tmp_path / "raw.json"
    artifact.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question": "private raw question",
                        "ground_truth_answer": "private raw answer",
                        "retrieved_memories_by_top_k": {"50": [{"memory": "private raw memory"}]},
                    }
                ],
                "cutoff_results": {"50": {"beam_state_verifier": {"raw": "private verifier response"}}},
            }
        ),
        encoding="utf-8",
    )

    report = module.build_report([str(artifact)])
    rendered = json.dumps(report)

    assert report["ok"] is False
    assert report["totals"]["raw_payload_hits"] == 5
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private verifier response" not in rendered
    assert "questions[0]/question" in rendered
    assert "cutoff_results/50/beam_state_verifier" in rendered


def test_public_artifact_scan_counts_private_paths_and_secret_shapes_without_values(tmp_path):
    module = load_module()
    artifact = tmp_path / "secret.json"
    artifact.write_text(
        json.dumps(
            {
                "private_debug_output": "/opt/kontext/private/judged-debug/private.json",
                "note": "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
                "env": "OPENAI_API_KEY=secret",
            }
        ),
        encoding="utf-8",
    )

    report = module.build_report([str(artifact)])
    rendered = json.dumps(report)

    assert report["ok"] is False
    assert report["totals"]["private_path_hits"] == 1
    assert report["totals"]["secret_shaped_hits"] == 2
    assert "/opt/kontext/private/judged-debug/private.json" not in rendered
    assert "Bearer abcdefghijklmnopqrstuvwxyz0123456789" not in rendered
    assert "OPENAI_API_KEY=secret" not in rendered
