from __future__ import annotations

from pathlib import Path

import pytest


FRONTEND_SRC = Path.home() / "Desktop" / "Claude" / "Kontext" / "static_dashboard" / "src"
KONTEXT_ROOT = Path(__file__).resolve().parents[1]


def _read_frontend_file(name: str) -> str:
    path = FRONTEND_SRC / name
    if not path.exists():
        pytest.skip(f"Kontext static dashboard frontend not present at {path}")
    return path.read_text(encoding="utf-8")


def test_cleanup_view_uses_live_checked_timestamp_not_frozen_literal():
    text = _read_frontend_file("view-decay.jsx")

    assert "2026-05-13T03:30:00Z" not in text
    assert 'delta="+4 wk"' not in text
    assert "cleanupCheckedAt" in text


def test_overview_sync_banner_depends_on_drift_state():
    text = _read_frontend_file("view-overview.jsx")

    assert "syncDriftKnown" in text
    assert "syncStateText" in text
    assert "Sync drift unknown" in text
    assert "Drift detected" in text


def test_settings_labels_kontext_primary_instead_of_mem0_connection():
    text = _read_frontend_file("view-settings.jsx")

    assert "Mem0 connection" not in text
    assert "Kontext primary" in text


def test_overview_keeps_write_preview_out_of_primary_metric_strip():
    text = _read_frontend_file("view-overview.jsx")

    assert 'Metric label="Dry-run writes"' not in text
    assert 'title="Dry-run writes"' not in text
    assert "Write preview" in text
    assert "dryRunItems.length ? <window.Card" in text


def test_frontend_removes_fake_legacy_totals_and_jargon_labels():
    entries = _read_frontend_file("entries.jsx")
    decay = _read_frontend_file("view-decay.jsx")
    inspector = _read_frontend_file("inspector.jsx")
    settings = _read_frontend_file("view-settings.jsx")

    assert "totals.canonical" not in entries
    assert "totals.devices" not in entries
    assert "totals.histOps" not in entries
    assert "Advanced timing left" not in decay
    assert "Telemetry" not in inspector
    assert "conf {" not in inspector
    assert "Slug" not in settings
    assert "draftSlug" not in settings


def test_shell_context_mode_link_is_conditional_not_always_visible():
    text = _read_frontend_file("shell.jsx")

    assert "context_mode" in text
    assert "http://localhost:4747" not in text
    assert "contextMode?.available" in text
    assert "contextMode.insight_url" in text


def test_dashboard_hotpatch_advertises_ctx_insight_for_ionut_vps_build():
    text = (KONTEXT_ROOT / "Dockerfile.dashboard-hotpatch").read_text(encoding="utf-8")

    assert "KONTEXT_CONTEXT_MODE_ENABLED=1" in text
    assert "KONTEXT_CONTEXT_MODE_INSIGHT_URL=http://localhost:4747" in text
