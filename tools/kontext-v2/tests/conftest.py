from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault(
    "KONTEXT_V2_DATABASE_URL",
    "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2",
)


def pytest_configure():
    database_url = os.environ["KONTEXT_V2_DATABASE_URL"]
    parsed = urlparse(database_url)

    is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    is_expected_db = parsed.path == "/kontext_v2" and parsed.username == "kontext_v2"
    if not is_local or parsed.port != 55434 or not is_expected_db:
        raise RuntimeError(
            "Kontext V2 tests require a local disposable Postgres "
            "at kontext_v2@localhost:55434/kontext_v2. "
            "Refusing KONTEXT_V2_DATABASE_URL outside the local test database."
        )
