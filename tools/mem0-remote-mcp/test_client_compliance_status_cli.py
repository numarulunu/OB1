import json
import subprocess
import sys

from client_compliance_status_cli import build_client_status_report, format_client_status_report


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')


def test_client_status_merges_hook_retrieval_and_ingestion_logs(tmp_path):
    hook_log = tmp_path / 'hooks.jsonl'
    retrieval_log = tmp_path / 'retrieval.jsonl'
    audit_log = tmp_path / 'audit.jsonl'
    write_jsonl(hook_log, [{'ts': '2026-05-10T00:00:00Z', 'origin': 'codex', 'hook_type': 'user_prompt'}])
    write_jsonl(retrieval_log, [{'ts': '2026-05-10T00:01:00Z', 'origin': 'codex', 'event': 'search', 'result_count': 1}])
    write_jsonl(audit_log, [{'ts': '2026-05-10T00:02:00Z', 'origin': 'codex', 'action': 'skip'}])

    report = build_client_status_report(
        audit_log=audit_log,
        retrieval_log=retrieval_log,
        hook_log=hook_log,
        expected_origins=['codex', 'claude'],
    )

    assert report['origins']['codex']['status'] == 'healthy'
    assert report['origins']['codex']['hook_seen'] is True
    assert report['origins']['codex']['search_seen'] is True
    assert report['origins']['codex']['ingest_seen'] is True
    assert report['origins']['claude']['status'] == 'missing'


def test_client_status_classifies_partial_clients(tmp_path):
    hook_log = tmp_path / 'hooks.jsonl'
    retrieval_log = tmp_path / 'retrieval.jsonl'
    audit_log = tmp_path / 'audit.jsonl'
    write_jsonl(hook_log, [{'ts': '2026-05-10T00:00:00Z', 'origin': 'claude', 'hook_type': 'session_start'}])
    write_jsonl(retrieval_log, [{'ts': '2026-05-10T00:01:00Z', 'origin': 'perplexity', 'event': 'search', 'result_count': 1}])
    write_jsonl(audit_log, [{'ts': '2026-05-10T00:02:00Z', 'origin': 'chatgpt', 'action': 'save'}])

    report = build_client_status_report(
        audit_log=audit_log,
        retrieval_log=retrieval_log,
        hook_log=hook_log,
        expected_origins=['claude', 'perplexity', 'chatgpt'],
    )

    assert report['origins']['claude']['status'] == 'hook_only'
    assert report['origins']['perplexity']['status'] == 'search_only'
    assert report['origins']['chatgpt']['status'] == 'write_only'


def test_client_status_report_is_safe_and_cli_json_works(tmp_path):
    hook_log = tmp_path / 'hooks.jsonl'
    retrieval_log = tmp_path / 'retrieval.jsonl'
    audit_log = tmp_path / 'audit.jsonl'
    write_jsonl(hook_log, [{'ts': '2026-05-10T00:00:00Z', 'origin': 'codex', 'hook_type': 'user_prompt'}])
    write_jsonl(retrieval_log, [{'ts': '2026-05-10T00:01:00Z', 'origin': 'codex', 'query_preview': 'private query', 'result_count': 1}])
    write_jsonl(audit_log, [{'ts': '2026-05-10T00:02:00Z', 'origin': 'codex', 'action': 'save', 'preview': 'raw private text'}])

    text = format_client_status_report(
        build_client_status_report(
            audit_log=audit_log,
            retrieval_log=retrieval_log,
            hook_log=hook_log,
            expected_origins=['codex'],
        )
    )
    assert 'codex: healthy' in text
    assert 'private query' not in text
    assert 'raw private text' not in text

    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/client_compliance_status_cli.py',
            '--audit-log',
            str(audit_log),
            '--retrieval-log',
            str(retrieval_log),
            '--hook-log',
            str(hook_log),
            '--expected-origin',
            'codex',
            '--json',
        ],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload['origins']['codex']['status'] == 'healthy'
