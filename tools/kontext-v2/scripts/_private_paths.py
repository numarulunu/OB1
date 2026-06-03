from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PRIVATE_ROOT = Path("/opt/kontext/private").resolve(strict=False)
PRIVATE_PATH_ERROR = "private debug output must be under /opt/kontext/private"


def _portable_path(value: str | os.PathLike[str]) -> Path:
    text = str(value)
    path = Path(text)
    if path.is_absolute() or not text.replace("\\", "/").startswith("/"):
        return path
    anchor = PRIVATE_ROOT.anchor or Path.cwd().anchor
    return Path(anchor) / text.replace("\\", "/").lstrip("/")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def strict_private_path(value: str | os.PathLike[str], root: Path = PRIVATE_ROOT) -> Path:
    path = _portable_path(value)
    if not path.is_absolute():
        raise ValueError(PRIVATE_PATH_ERROR)
    resolved_root = Path(root).resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    if not _is_relative_to(resolved_path, resolved_root):
        raise ValueError(PRIVATE_PATH_ERROR)
    return resolved_path


def validate_private_debug_output_path(value: str | None) -> str | None:
    if not value:
        return None
    try:
        strict_private_path(value)
    except (OSError, ValueError):
        return PRIVATE_PATH_ERROR
    return None


def write_private_json_exclusive(path_value: str, payload: dict[str, Any]) -> Path:
    output_path = strict_private_path(path_value)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not _is_relative_to(output_path.parent.resolve(strict=False), PRIVATE_ROOT):
        raise ValueError(PRIVATE_PATH_ERROR)
    try:
        os.chmod(output_path.parent, 0o700)
    except OSError:
        pass

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(output_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        try:
            output_path.unlink()
        except OSError:
            pass
        raise
    return output_path
