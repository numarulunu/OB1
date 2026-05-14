from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_locomo_tiny_fixture(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("dataset") != "locomo_tiny":
        raise ValueError("Expected locomo_tiny fixture")
    if not isinstance(data.get("conversations"), list):
        raise ValueError("Fixture conversations must be a list")
    if not isinstance(data.get("questions"), list):
        raise ValueError("Fixture questions must be a list")
    return data
