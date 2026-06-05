from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kontext_prebatch_snapshot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kontext_prebatch_snapshot", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_prebatch_snapshot_copies_targets_and_writes_manifest_without_raw_content(tmp_path):
    module = load_module()
    repo = tmp_path / "repo"
    source = repo / "tools" / "kontext-v2" / "kontext_v2" / "state_model.py"
    source.parent.mkdir(parents=True)
    source.write_text("private production code marker\n", encoding="utf-8")
    output = repo / "tools" / "kontext-v2" / "_prebatch_test"

    result = module.create_snapshot(
        repo,
        output,
        ["tools/kontext-v2/kontext_v2/state_model.py"],
        git_status_func=lambda _repo, _path: "?? tools/kontext-v2/kontext_v2/state_model.py",
        head_exists_func=lambda _repo, _path: False,
    )
    rendered = json.dumps(result)

    copied = output / "tools" / "kontext-v2" / "kontext_v2" / "state_model.py"
    assert copied.read_text(encoding="utf-8") == "private production code marker\n"
    assert (output / "manifest.json").is_file()
    assert result["ok"] is True
    assert result["file_count"] == 1
    assert result["files"][0]["relative_path"] == "tools/kontext-v2/kontext_v2/state_model.py"
    assert result["files"][0]["head_exists"] is False
    assert result["files"][0]["git_status"].startswith("??")
    assert "private production code marker" not in rendered


def test_prebatch_snapshot_rejects_output_outside_repo(tmp_path):
    module = load_module()
    repo = tmp_path / "repo"
    source = repo / "tools" / "kontext-v2" / "kontext_v2" / "state_model.py"
    source.parent.mkdir(parents=True)
    source.write_text("content\n", encoding="utf-8")

    result = module.create_snapshot(
        repo,
        tmp_path / "outside",
        ["tools/kontext-v2/kontext_v2/state_model.py"],
        git_status_func=lambda _repo, _path: "?? tools/kontext-v2/kontext_v2/state_model.py",
        head_exists_func=lambda _repo, _path: False,
    )

    assert result["ok"] is False
    assert result["blocked_by"] == ["snapshot output must stay inside the repository"]


def test_prebatch_snapshot_blocks_staged_target_files(tmp_path):
    module = load_module()
    repo = tmp_path / "repo"
    source = repo / "tools" / "kontext-v2" / "kontext_v2" / "state_model.py"
    source.parent.mkdir(parents=True)
    source.write_text("content\n", encoding="utf-8")

    result = module.create_snapshot(
        repo,
        repo / "tools" / "kontext-v2" / "_prebatch_test",
        ["tools/kontext-v2/kontext_v2/state_model.py"],
        git_status_func=lambda _repo, _path: "M  tools/kontext-v2/kontext_v2/state_model.py",
        head_exists_func=lambda _repo, _path: True,
    )

    assert result["ok"] is False
    assert result["blocked_by"] == ["target has staged git changes: tools/kontext-v2/kontext_v2/state_model.py"]


def test_prebatch_snapshot_allows_unstaged_modified_target_files(tmp_path):
    module = load_module()
    repo = tmp_path / "repo"
    source = repo / "tools" / "kontext-v2" / "kontext_v2" / "retrieval.py"
    source.parent.mkdir(parents=True)
    source.write_text("content\n", encoding="utf-8")

    result = module.create_snapshot(
        repo,
        repo / "tools" / "kontext-v2" / "_prebatch_test",
        ["tools/kontext-v2/kontext_v2/retrieval.py"],
        git_status_func=lambda _repo, _path: " M tools/kontext-v2/kontext_v2/retrieval.py",
        head_exists_func=lambda _repo, _path: True,
    )

    assert result["ok"] is True
    assert result["files"][0]["git_status"].startswith(" M ")
