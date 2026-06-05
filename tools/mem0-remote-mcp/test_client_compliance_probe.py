import json
import subprocess
import sys

from client_compliance_probe import build_canary_exchange, build_compliance_report, format_report


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')


def test_build_canary_exchange_contains_unique_phrase_without_secret_material():
    exchange = build_canary_exchange('codex', 'blue compliance candle')

    assert exchange == [
        {'role': 'user', 'content': 'Mem0 compliance canary for codex: blue compliance candle.'},
        {'role': 'assistant', 'content': 'Acknowledged the codex Mem0 compliance canary.'},
    ]
    assert 'token' not in json.dumps(exchange).lower()
    assert 'api key' not in json.dumps(exchange).lower()


def test_build_compliance_report_detects_present_missing_and_error_origins(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(
        audit_path,
        [
            {'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'save'},
            {'ts': '2026-05-09T00:01:00Z', 'origin': 'claude', 'action': 'skip'},
            {'ts': '2026-05-09T00:02:00Z', 'origin': 'chatgpt', 'action': 'extract', 'errors': ['timeout from provider']},
        ],
    )

    report = build_compliance_report(audit_path, expected_origins=['codex', 'claude', 'chatgpt', 'perplexity'])

    assert report['origins']['codex']['status'] == 'writing'
    assert report['origins']['claude']['status'] == 'seen_no_write'
    assert report['origins']['chatgpt']['status'] == 'error'
    assert report['origins']['perplexity']['status'] == 'missing'
    assert report['summary']['missing'] == ['perplexity']
    assert report['summary']['error_origins'] == ['chatgpt']


def test_format_report_is_safe_and_human_readable(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(audit_path, [{'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'save', 'preview': 'raw private text'}])

    text = format_report(build_compliance_report(audit_path, expected_origins=['codex', 'claude']))

    assert 'Mem0 client compliance' in text
    assert 'codex: writing' in text
    assert 'claude: missing' in text
    assert 'raw private text' not in text


def test_client_compliance_probe_cli_json(tmp_path):
    audit_path = tmp_path / 'audit.jsonl'
    write_jsonl(audit_path, [{'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'update'}])


    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/client_compliance_probe.py',
            '--audit-log',
            str(audit_path),
            '--expected-origin',
            'codex',
            '--expected-origin',
            'claude',
            '--json',
        ],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload['origins']['codex']['status'] == 'writing'
    assert payload['origins']['claude']['status'] == 'missing'
