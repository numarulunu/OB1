from __future__ import annotations

from kontext_v2.schema import SCHEMA_SQL, apply_schema


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.commits = 0

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def commit(self) -> None:
        self.commits += 1


def test_apply_schema_is_replayable_and_avoids_destructive_table_operations():
    conn = FakeConnection()

    apply_schema(conn)
    apply_schema(conn)

    rendered = "\n".join(conn.executed).upper()
    assert conn.commits == 2
    assert conn.executed == [SCHEMA_SQL, SCHEMA_SQL]
    assert "CREATE TABLE IF NOT EXISTS" in rendered
    assert "CREATE INDEX IF NOT EXISTS" in rendered
    assert "DROP TABLE" not in rendered
    assert "TRUNCATE" not in rendered
    assert "DELETE FROM" not in rendered
