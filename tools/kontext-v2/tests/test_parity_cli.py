import subprocess
import sys


def test_parity_cli_help_runs():
    result = subprocess.run(
        [sys.executable, "tools/kontext-v2/parity_eval.py", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Kontext V2 parity eval" in result.stdout
    assert "--cases" in result.stdout
    assert "--kontext-database-url" in result.stdout

def test_parity_cli_dry_run_accepts_live_import_options_without_secret_output(tmp_path):
    output = tmp_path / "parity.json"
    result = subprocess.run(
        [
            sys.executable,
            "tools/kontext-v2/parity_eval.py",
            "--dry-run",
            "--live-import",
            "--seed-from-cases",
            "--mem0-base-url",
            "https://mem0.example.test",
            "--mem0-api-key-env",
            "FAKE_MEM0_SECRET_ENV",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    rendered = result.stdout + result.stderr + output.read_text(encoding="utf-8")
    assert "--live-import" not in result.stderr
    assert "FAKE_MEM0_SECRET_ENV" not in rendered
    assert "mem0.example.test" not in rendered
    assert '"mode": "dry_run"' in rendered


def test_parity_cli_dry_run_allows_top_k_50_for_parity_sweeps(tmp_path):
    output = tmp_path / "parity.json"
    subprocess.run(
        [
            sys.executable,
            "tools/kontext-v2/parity_eval.py",
            "--dry-run",
            "--top-k",
            "99",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert '"top_k": 50' in output.read_text(encoding="utf-8")
