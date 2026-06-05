from pathlib import Path


API = (
    Path(__file__).resolve().parents[3]
    / "handoffs"
    / "kontext-frontend-claude-design-2026-05-13"
    / "source"
    / "cloud"
    / "api.py"
)


def test_handoff_dashboard_config_write_has_in_app_token_gate():
    text = API.read_text(encoding="utf-8")

    assert "KONTEXT_DASHBOARD_CONFIG_TOKEN" in text
    assert "def _require_dashboard_config_auth" in text
    assert "def set_dashboard_config(body: dict, request: Request)" in text
    assert "hmac.compare_digest" in text
