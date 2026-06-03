from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_kontext_cutover_observer.sh"
SERVICE = ROOT / "deploy" / "systemd" / "kontext-cutover-observer.service"
TIMER = ROOT / "deploy" / "systemd" / "kontext-cutover-observer.timer"


def test_runner_script_is_bounded_and_secret_safe():
    text = RUNNER.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert "_kontext_cutover_observer.log" in text
    assert "retrieval_query_retention.py" in text
    assert "retrieval-query-retention-latest.json" in text
    assert "KONTEXT_RETRIEVAL_RETENTION_APPLY" in text
    assert 'KONTEXT_RETRIEVAL_RETENTION_APPLY:-0' in text
    assert "PYTHONPATH=/app" in text
    assert "RETENTION_APPLY_ARGS=()" in text
    assert "RETENTION_APPLY_ARGS=(--apply)" in text
    assert "kontext_cutover_observer.py" in text
    assert "kontext_ob1_retrieval_quality_gate.py" in text
    assert "usage_report_cli.py" in text
    assert "client_compliance_status_cli.py" in text
    assert "mem0-kontext-shadow-comparison.jsonl" in text
    assert "--shadow-comparison-log" in text
    assert "--ob1-retrieval-quality-report" in text
    assert "--max-input-age-hours" in text
    assert "KONTEXT_OBSERVER_MAX_INPUT_AGE_HOURS" in text
    assert "observer-history.jsonl" in text
    assert "transition_assessment" in text
    assert "latest_matching_report" in text
    assert "kontext-goal-scorecard-*.json" in text
    assert "kontext-cutover-readiness-*.json" in text
    assert "OB1_RETRIEVAL_QUALITY_REPORT" in text
    assert "ob1-retrieval-quality-gate-latest.json" in text
    assert "KONTEXT_OB1_RETRIEVAL_TIMEOUT_SECONDS" in text
    assert "ob1-retrieval-quality-gate" in text
    assert "timeout_or_error" in text
    assert "OB1_RETRIEVAL_QUALITY_FATAL" in text
    assert "OB1 retrieval quality fallback report present; suppressing fatal exit" in text
    assert "OBSERVER_FATAL" in text
    assert "cutover observer report present; suppressing fatal exit" in text
    assert "judged-complete-20260522" not in text
    assert "readiness-judged-20260522" not in text
    assert ".env" not in text
    assert "cat " not in text


def test_systemd_service_runs_only_the_observer_runner():
    text = SERVICE.read_text(encoding="utf-8")

    assert "Type=oneshot" in text
    assert "ExecStart=/opt/kontext/scripts/run_kontext_cutover_observer.sh" in text
    assert "User=root" in text
    assert "EnvironmentFile" not in text
    assert ".env" not in text


def test_systemd_timer_runs_every_six_hours_persistently():
    text = TIMER.read_text(encoding="utf-8")

    assert "OnBootSec=10min" in text
    assert "OnUnitActiveSec=6h" in text
    assert "RandomizedDelaySec=5min" in text
    assert "Persistent=true" in text
    assert "WantedBy=timers.target" in text
