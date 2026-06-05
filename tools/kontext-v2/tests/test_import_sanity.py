from __future__ import annotations

import py_compile
from pathlib import Path


def test_all_kontext_v2_modules_compile():
    package_root = Path(__file__).resolve().parents[1] / "kontext_v2"
    failures: list[str] = []

    for path in sorted(package_root.rglob("*.py")):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path.relative_to(package_root)}: {exc.msg}")

    assert failures == []
