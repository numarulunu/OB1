from pathlib import Path


COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_local_postgres_port_is_loopback_only_and_password_is_externalized():
    text = COMPOSE.read_text(encoding="utf-8")

    assert '"127.0.0.1:55434:5432"' in text
    assert '"55434:5432"' not in text
    assert "KONTEXT_POSTGRES_PASSWORD" in text
    assert "POSTGRES_PASSWORD: kontext_v2" not in text
