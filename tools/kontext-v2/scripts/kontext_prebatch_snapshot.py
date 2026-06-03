from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Any


DEFAULT_SNAPSHOT_PATHS = [
    "tools/kontext-v2/kontext_v2/models.py",
    "tools/kontext-v2/kontext_v2/repository.py",
    "tools/kontext-v2/kontext_v2/schema.py",
    "tools/kontext-v2/kontext_v2/state_ingestion.py",
    "tools/kontext-v2/kontext_v2/state_model.py",
    "tools/kontext-v2/kontext_v2/retrieval.py",
    "tools/kontext-v2/kontext_v2/benchmarks/state_trial.py",
    "tools/kontext-v2/tests/test_retrieval_state_projection.py",
    "tools/kontext-v2/tests/test_state_behavior_eval.py",
    "tools/kontext-v2/tests/test_state_model.py",
    "tools/kontext-v2/tests/test_state_repository.py",
]


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[3]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_to(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "mode": "kontext-prebatch-snapshot",
        "runs_model_calls": False,
        "blocked_by": [reason],
    }


def git_status(repo_root: Path, relative_path: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--", relative_path],
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").rstrip()


def head_exists(repo_root: Path, relative_path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", f"HEAD:{relative_path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _has_staged_status(status: str) -> bool:
    if not status:
        return False
    return status[0] not in {" ", "?"}


def create_snapshot(
    repo_root: str | Path,
    output_dir: str | Path,
    source_paths: Iterable[str],
    *,
    git_status_func: Callable[[Path, str], str] = git_status,
    head_exists_func: Callable[[Path, str], bool] = head_exists,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    output = Path(output_dir).resolve()
    output_relative = _relative_to(output, repo)
    if output_relative is None:
        return _blocked("snapshot output must stay inside the repository")
    if output.exists():
        return _blocked("snapshot output already exists")

    normalized_sources = [str(path).replace("\\", "/").strip().lstrip("/") for path in source_paths if str(path).strip()]
    if not normalized_sources:
        return _blocked("at least one source path is required")

    rows: list[dict[str, Any]] = []
    for relative_path in normalized_sources:
        source = (repo / relative_path).resolve()
        if _relative_to(source, repo) != relative_path:
            return _blocked(f"source path must stay inside repository: {relative_path}")
        if not source.is_file():
            return _blocked(f"source file does not exist: {relative_path}")
        status = git_status_func(repo, relative_path)
        if _has_staged_status(status):
            return _blocked(f"target has staged git changes: {relative_path}")
        rows.append(
            {
                "relative_path": relative_path,
                "git_status": status,
                "head_exists": bool(head_exists_func(repo, relative_path)),
                "bytes": source.stat().st_size,
                "sha256": sha256_file(source),
                "snapshot_path": str((output / relative_path).relative_to(output).as_posix()),
            }
        )

    output.mkdir(parents=True)
    for row in rows:
        relative_path = row["relative_path"]
        destination = output / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative_path, destination)

    manifest = {
        "ok": True,
        "mode": "kontext-prebatch-snapshot",
        "runs_model_calls": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo),
        "snapshot_root": str(output),
        "snapshot_relative": output_relative,
        "file_count": len(rows),
        "files": rows,
        "notes": [
            "This is a local rollback snapshot for production-relevant dirty files.",
            "The manifest records paths, sizes, hashes, and git status only; it does not include file contents.",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a local prebatch snapshot for production-relevant Kontext files.")
    parser.add_argument("--repo-root", default=str(repo_root_from_script()))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--path", action="append", dest="paths", help="Repo-relative path to snapshot. Repeatable.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = create_snapshot(args.repo_root, args.output_dir, args.paths or DEFAULT_SNAPSHOT_PATHS)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
