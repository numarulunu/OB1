from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class KontextV2Config:
    service_name: str
    database_url: str
    write_mode: str
    embedding_dimension: int
    embedding_model: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KontextV2Config":
        values = os.environ if env is None else env

        return cls(
            service_name=values.get("KONTEXT_V2_SERVICE_NAME", "kontext-v2"),
            database_url=values.get(
                "KONTEXT_V2_DATABASE_URL",
                "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2",
            ),
            write_mode=values.get("KONTEXT_V2_WRITE_MODE", "dry_run"),
            embedding_dimension=int(values.get("KONTEXT_V2_EMBEDDING_DIM", "16")),
            embedding_model=values.get(
                "KONTEXT_V2_EMBEDDING_MODEL",
                "deterministic-test-v1",
            ),
        )
