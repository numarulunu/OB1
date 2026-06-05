import json
import subprocess
import sys

from usage_report import build_usage_report, format_usage_report


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')


def test_usage_report_summarizes_hooks_searches_ingestion_and_tokens_without_raw_text(tmp_path):
    audit_log = tmp_path / 'audit.jsonl'
    retrieval_log = tmp_path / 'retrieval.jsonl'
    hook_log = tmp_path / 'hooks.jsonl'
    write_jsonl(
        audit_log,
        [
            {
                'ts': '2026-05-10T00:00:00Z',
                'origin': 'codex',
                'action': 'save',
                'preview': 'raw user words must not leak',
                'llm': {'usage': {'prompt_tokens': 100, 'completion_tokens': 25, 'total_tokens': 125}},
            },
            {'ts': '2026-05-10T00:01:00Z', 'origin': 'claude', 'action': 'skip'},
        ],
    )
    write_jsonl(
        retrieval_log,
        [
            {'ts': '2026-05-10T00:02:00Z', 'origin': 'codex', 'query_preview': 'private search', 'result_count': 3, 'latency_ms': 10},
            {'ts': '2026-05-10T00:03:00Z', 'origin': 'chatgpt', 'query_preview': 'other private search', 'result_count': 2, 'latency_ms': 30},
        ],
    )
    write_jsonl(
        hook_log,
        [
            {'ts': '2026-05-10T00:04:00Z', 'origin': 'codex', 'hook_type': 'user_prompt'},
            {'ts': '2026-05-10T00:05:00Z', 'origin': 'claude', 'hook_type': 'session_start'},
        ],
    )

    report = build_usage_report(audit_log=audit_log, retrieval_log=retrieval_log, hook_log=hook_log, recent_limit=100)
    rendered = json.dumps(report, ensure_ascii=False)

    assert report['summary']['hooks'] == 2
    assert report['summary']['searches'] == 2
    assert report['summary']['ingestion_rows'] == 2
    assert report['summary']['total_tokens'] == 125
    assert report['origins']['codex']['hooks'] == 1
    assert report['origins']['codex']['searches'] == 1
    assert report['origins']['codex']['ingestion_rows'] == 1
    assert report['origins']['codex']['total_tokens'] == 125
    assert report['origins']['chatgpt']['searches'] == 1
    assert 'raw user words' not in rendered
    assert 'private search' not in rendered


def test_format_usage_report_is_compact_and_human_readable(tmp_path):
    audit_log = tmp_path / 'audit.jsonl'
    retrieval_log = tmp_path / 'retrieval.jsonl'
    hook_log = tmp_path / 'hooks.jsonl'
    write_jsonl(audit_log, [{'origin': 'codex', 'action': 'save', 'usage': {'prompt_tokens': 5, 'completion_tokens': 7}}])
    write_jsonl(retrieval_log, [{'origin': 'codex', 'result_count': 1, 'latency_ms': 12}])
    write_jsonl(hook_log, [{'origin': 'codex', 'hook_type': 'user_prompt'}])

    text = format_usage_report(build_usage_report(audit_log=audit_log, retrieval_log=retrieval_log, hook_log=hook_log))

    assert 'Mem0 usage report' in text
    assert 'total tokens: 12' in text
    assert 'codex:' in text


def test_usage_report_cli_json(tmp_path):
    audit_log = tmp_path / 'audit.jsonl'
    retrieval_log = tmp_path / 'retrieval.jsonl'
    hook_log = tmp_path / 'hooks.jsonl'
    write_jsonl(audit_log, [{'origin': 'codex', 'action': 'save'}])
    write_jsonl(retrieval_log, [{'origin': 'codex', 'result_count': 1}])
    write_jsonl(hook_log, [{'origin': 'codex', 'hook_type': 'user_prompt'}])

    result = subprocess.run(
        [
            sys.executable,
            'tools/mem0-remote-mcp/usage_report_cli.py',
            '--audit-log',
            str(audit_log),
            '--retrieval-log',
            str(retrieval_log),
            '--hook-log',
            str(hook_log),
            '--json',
        ],
        cwd='.',
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload['origins']['codex']['hooks'] == 1
    assert payload['origins']['codex']['searches'] == 1
    assert payload['origins']['codex']['ingestion_rows'] == 1
