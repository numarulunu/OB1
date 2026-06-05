import json

from audit_ledger import AuditLedger


def write_lines(path, rows):
    lines = []
    for row in rows:
        if row == 'malformed':
            lines.append('{bad json')
        else:
            lines.append(json.dumps(row, ensure_ascii=False))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_iter_recent_yields_recent_valid_rows_and_skips_malformed(tmp_path):
    path = tmp_path / 'audit.jsonl'
    write_lines(
        path,
        [
            {'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'save'},
            'malformed',
            {'ts': '2026-05-09T00:01:00Z', 'origin': 'claude', 'action': 'flag'},
            {'ts': '2026-05-09T00:02:00Z', 'origin': 'chatgpt', 'action': 'skip'},
        ],
    )
    ledger = AuditLedger(path)

    rows = list(ledger.iter_recent(limit=3))

    assert rows == [
        {'ts': '2026-05-09T00:01:00Z', 'origin': 'claude', 'action': 'flag'},
        {'ts': '2026-05-09T00:02:00Z', 'origin': 'chatgpt', 'action': 'skip'},
    ]


def test_iter_recent_accepts_utf8_bom_from_powershell_set_content(tmp_path):
    path = tmp_path / 'audit.jsonl'
    path.write_bytes(b'\xef\xbb\xbf' + json.dumps({'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'save'}).encode('utf-8') + b'\n')

    rows = list(AuditLedger(path).iter_recent())

    assert rows == [{'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'save'}]


def test_latest_by_origin_counts_actions_and_tracks_newest_timestamp(tmp_path):
    path = tmp_path / 'audit.jsonl'
    write_lines(
        path,
        [
            {'ts': '2026-05-09T00:00:00Z', 'origin': 'codex', 'action': 'save'},
            {'ts': '2026-05-09T00:01:00Z', 'origin': 'codex', 'action': 'flag'},
            {'ts': '2026-05-09T00:02:00Z', 'origin': 'claude', 'action': 'update'},
            {'ts': '2026-05-09T00:03:00Z', 'origin': 'codex', 'action': 'skip'},
        ],
    )
    ledger = AuditLedger(path)

    by_origin = ledger.latest_by_origin(limit=10)

    assert by_origin == {
        'codex': {
            'latest_ts': '2026-05-09T00:03:00Z',
            'actions': {'save': 1, 'flag': 1, 'skip': 1},
            'rows': 3,
        },
        'claude': {
            'latest_ts': '2026-05-09T00:02:00Z',
            'actions': {'update': 1},
            'rows': 1,
        },
    }


def test_latest_by_origin_returns_empty_for_missing_ledger(tmp_path):
    ledger = AuditLedger(tmp_path / 'missing.jsonl')

    assert ledger.latest_by_origin() == {}
