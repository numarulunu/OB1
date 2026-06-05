import json

from audit_ledger import AuditLedger
from core import Mem0Client, Mem0Config
from ingestion import IngestionProposal, parse_extraction_response


def test_audit_ledger_appends_and_detects_source_hash(tmp_path):
    ledger = AuditLedger(tmp_path / 'nested' / 'audit.jsonl')

    row = ledger.append({'action': 'save', 'source_hash': 'abc123', 'preview': 'short preview'})

    assert row['action'] == 'save'
    assert row['source_hash'] == 'abc123'
    assert row['preview'] == 'short preview'
    assert row['ts'].endswith('Z')
    assert ledger.has_source_hash('abc123') is True
    assert ledger.has_source_hash('missing') is False

    lines = (tmp_path / 'nested' / 'audit.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])['source_hash'] == 'abc123'


def test_audit_ledger_ignores_malformed_legacy_rows(tmp_path):
    path = tmp_path / 'audit.jsonl'
    path.write_text('{bad json\n' + json.dumps({'source_hash': 'known'}) + '\n', encoding='utf-8')
    ledger = AuditLedger(path)

    assert ledger.has_source_hash('known') is True
    assert ledger.has_source_hash('missing') is False


class ListLedger:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(dict(row))
        return self.rows[-1]


def proposal(action='save', content='Ionut wants Mem0 ingestion to auto-write after every exchange using Qwen.', **kwargs):
    return IngestionProposal(
        action=action,
        content=content,
        domains=kwargs.get('domains', ['ai', 'systems']),
        memory_type=kwargs.get('memory_type', 'decision'),
        signal_strength=kwargs.get('signal_strength', 9),
        current_status=kwargs.get('current_status', 'active'),
        memory_tier=kwargs.get('memory_tier', 'active'),
        confidence=kwargs.get('confidence', 0.9),
        reason=kwargs.get('reason', 'explicit decision'),
        existing_id=kwargs.get('existing_id', ''),
        flag_type=kwargs.get('flag_type', ''),
    )


def test_apply_save_proposal_writes_memory_and_audit_row():
    calls = []

    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if path == '/search':
                return {'results': []}
            if path == '/memories':
                return {'id': 'new-memory'}
            raise AssertionError(f'unexpected request: {method} {path}')

    ledger = ListLedger()
    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret', client_name='codex'))

    result = client.apply_ingestion_proposal(proposal(), ledger, 'hash-1', 'preview', 'codex')

    assert result['action'] == 'save'
    assert calls[-1][0:2] == ('POST', '/memories')
    assert calls[-1][2]['metadata']['source_hash'] == 'hash-1'
    assert ledger.rows[-1]['action'] == 'save'
    assert ledger.rows[-1]['source_hash'] == 'hash-1'


def test_apply_save_proposal_records_safe_llm_metadata_in_audit_row():
    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            if path == '/search':
                return {'results': []}
            if path == '/memories':
                return {'id': 'new-memory'}
            raise AssertionError(f'unexpected request: {method} {path}')

    ledger = ListLedger()
    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    client.apply_ingestion_proposal(
        proposal(),
        ledger,
        'hash-usage',
        'preview',
        'codex',
        llm_metadata={'model': 'qwen/test', 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}},
    )

    assert ledger.rows[-1]['llm'] == {'model': 'qwen/test', 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
    assert 'api_key' not in json.dumps(ledger.rows[-1])


def test_apply_proposal_audit_row_omits_raw_preview_content_and_reason():
    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            if path == '/search':
                return {'results': []}
            if path == '/memories':
                return {'id': 'new-memory'}
            raise AssertionError(f'unexpected request: {method} {path}')

    ledger = ListLedger()
    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))
    raw_preview = 'user: raw private exchange text that must not be written to audit'
    raw_content = 'raw private proposed memory content that must not be written to audit'
    raw_reason = 'raw private reason that must not be written to audit'

    client.apply_ingestion_proposal(
        proposal(content=raw_content, reason=raw_reason),
        ledger,
        'hash-redaction',
        raw_preview,
        'codex',
    )

    rendered = json.dumps(ledger.rows[-1])
    assert raw_preview not in rendered
    assert raw_content not in rendered
    assert raw_reason not in rendered
    assert ledger.rows[-1]['preview'].startswith('preview ')
    assert ledger.rows[-1]['proposal']['content_len'] == len(raw_content)
    assert 'content_hash' in ledger.rows[-1]['proposal']


def test_apply_exact_duplicate_skips_and_audits():
    calls = []
    content = 'Ionut wants Mem0 ingestion to auto-write after every exchange using Qwen.'

    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if path == '/search':
                return {'results': [{'id': 'existing', 'memory': content, 'metadata': {}}]}
            raise AssertionError(f'unexpected request: {method} {path}')

    ledger = ListLedger()
    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.apply_ingestion_proposal(proposal(content=content), ledger, 'hash-2', 'preview', 'codex')

    assert result['action'] == 'skip'
    assert result['existing_id'] == 'existing'
    assert not any(call[0:2] == ('POST', '/memories') for call in calls)
    assert ledger.rows[-1]['action'] == 'skip'


def test_apply_update_with_existing_id_updates_and_audits():
    calls = []

    class FakeClient(Mem0Client):
        text = 'Old memory.'
        metadata = {'domains': ['ai'], 'memory_type': 'decision'}

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if method == 'GET':
                return {'id': 'mem-1', 'memory': self.text, 'metadata': self.metadata}
            if method == 'PUT':
                self.text = body['text']
                self.metadata = body['metadata']
                return {'updated': True}
            raise AssertionError(f'unexpected request: {method} {path}')

    ledger = ListLedger()
    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))
    update = proposal(action='update', content='New memory.', existing_id='mem-1')

    result = client.apply_ingestion_proposal(update, ledger, 'hash-3', 'preview', 'claude')

    assert result['action'] == 'update'
    assert ('PUT', '/memories/mem-1') == calls[1][0:2]
    assert ledger.rows[-1]['action'] == 'update'


def test_apply_flag_delete_candidate_never_deletes():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            raise AssertionError(f'unexpected request: {method} {path}')

    ledger = ListLedger()
    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))
    flag = proposal(action='flag', content='Duplicate memory.', flag_type='delete_candidate')

    result = client.apply_ingestion_proposal(flag, ledger, 'hash-4', 'preview', 'chatgpt')

    assert result['action'] == 'flag'
    assert result['flag_type'] == 'delete_candidate'
    assert calls == []
    assert ledger.rows[-1]['action'] == 'flag'


def test_fake_qwen_response_can_be_parsed_and_applied():
    raw = json.dumps(
        {
            'proposals': [
                {
                    'action': 'save',
                    'content': 'Ionut wants Mem0 ingestion to auto-write after every substantive exchange using Qwen.',
                    'domains': ['ai', 'systems'],
                    'memory_type': 'decision',
                    'signal_strength': 9,
                    'current_status': 'active',
                    'memory_tier': 'active',
                    'confidence': 0.92,
                    'reason': 'explicit architecture decision',
                }
            ]
        }
    )
    calls = []

    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if path == '/search':
                return {'results': []}
            if path == '/memories':
                return {'id': 'new-memory'}
            raise AssertionError(f'unexpected request: {method} {path}')

    ledger = ListLedger()
    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.apply_ingestion_proposal(parse_extraction_response(raw)[0], ledger, 'hash-5', 'preview', 'codex')

    assert result['action'] == 'save'
    assert calls[-1][2]['metadata']['source_hash'] == 'hash-5'
    assert ledger.rows[-1]['action'] == 'save'


def test_delete_candidate_from_parser_is_flag_only_when_applied():
    raw = json.dumps({'proposals': [{'action': 'delete', 'content': 'Duplicate memory.', 'confidence': 0.9}]})
    ledger = ListLedger()
    client = Mem0Client(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.apply_ingestion_proposal(parse_extraction_response(raw)[0], ledger, 'hash-6', 'preview', 'codex')

    assert result['action'] == 'flag'
    assert result['flag_type'] == 'delete_candidate'
    assert ledger.rows[-1]['flag_type'] == 'delete_candidate'
