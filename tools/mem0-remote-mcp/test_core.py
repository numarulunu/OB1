from core import Mem0Client, Mem0Config, lexical_score, lexical_tokens, sanitize_memory, title_for


def test_title_for_collapses_and_truncates_text():
    title = title_for('  This   memory has   irregular spacing and a long tail  ', max_len=28)

    assert title == 'This memory has irregular...'


def test_sanitize_memory_keeps_only_retrieval_safe_metadata():
    row = {
        'id': 'mem-1',
        'memory': 'Durable memory text.',
        'score': 0.91,
        'metadata': {
            'domains': ['ai', 'workflow'],
            'memory_type': 'pattern',
            'signal_strength': 9,
            'current_status': 'active',
            'memory_tier': 'historical',
            'source_ids': ['raw-chat-row'],
        },
    }

    assert sanitize_memory(row) == {
        'id': 'mem-1',
        'title': 'Durable memory text.',
        'text': 'Durable memory text.',
        'url': 'https://mem0.ionutrosu.xyz/memory/mem-1',
        'score': 0.91,
        'metadata': {
            'domains': ['ai', 'workflow'],
            'memory_type': 'pattern',
            'signal_strength': 9,
            'current_status': 'active',
            'memory_tier': 'historical',
        },
    }


def test_search_uses_mem0_filter_contract_and_domain_post_filter():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            return {
                'results': [
                    {'id': 'a', 'memory': 'AI workflow memory', 'metadata': {'domains': ['ai'], 'memory_tier': 'active'}},
                    {'id': 'b', 'memory': 'Opera memory', 'metadata': {'domains': ['opera'], 'memory_tier': 'historical'}},
                ]
            }

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.search('systems', top_k=100, domains=['ai'])

    assert calls == [('POST', '/search', {'query': 'systems', 'filters': {'user_id': 'ionut'}, 'top_k': 100})]
    assert [row['id'] for row in result['results']] == ['a']


def test_search_schedules_kontext_shadow_compare_without_changing_results(monkeypatch):
    calls = []

    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            return {
                'results': [
                    {'id': 'a', 'memory': 'AI workflow memory', 'metadata': {'domains': ['ai'], 'memory_tier': 'active'}},
                ]
            }

    def fake_shadow(config, **kwargs):
        calls.append((config, kwargs))
        return True

    monkeypatch.setattr('core.log_kontext_shadow_search_async', fake_shadow)
    client = FakeClient(
        Mem0Config(
            base_url='https://mem0.example.test',
            api_key='secret',
            client_name='codex',
            kontext_shadow_enabled=True,
            kontext_shadow_mcp_url='https://kontext.example/mcp/{token}',
            kontext_shadow_mcp_token='shadow-token',
            kontext_shadow_log='/tmp/shadow.jsonl',
        )
    )

    result = client.search('AI workflow', top_k=1, domains=['ai'])

    assert [row['id'] for row in result['results']] == ['a']
    assert len(calls) == 1
    config, kwargs = calls[0]
    assert config.ready() is True
    assert kwargs['origin'] == 'codex'
    assert kwargs['query'] == 'AI workflow'
    assert kwargs['top_k'] == 1
    assert kwargs['filters']['domains'] == ['ai']
    assert [row['id'] for row in kwargs['mem0_results']] == ['a']


def test_save_schedules_kontext_write_audit_without_changing_result(monkeypatch):
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            return {'id': 'new-memory'}

    def fake_audit(config, **kwargs):
        calls.append((config, kwargs))
        return True

    monkeypatch.setattr('core.log_kontext_write_audit_async', fake_audit)
    client = FakeClient(
        Mem0Config(
            base_url='https://mem0.example.test',
            api_key='secret',
            client_name='codex',
            kontext_write_audit_enabled=True,
            kontext_write_audit_mcp_url='https://kontext.example/mcp/{token}',
            kontext_write_audit_mcp_token='write-token',
            kontext_write_audit_log='/tmp/write-audit.jsonl',
        )
    )

    result = client.save(
        'Durable write canary memory.',
        domains=['ai'],
        memory_type='project_state',
        signal_strength=9,
        current_status='active',
        memory_tier='active',
    )

    assert result == {'saved': True, 'response': {'id': 'new-memory'}}
    assert len(calls) == 1
    config, kwargs = calls[0]
    assert config.ready() is True
    assert kwargs['origin'] == 'codex'
    assert kwargs['tool'] == 'save'
    assert kwargs['arguments']['content'] == 'Durable write canary memory.'
    assert kwargs['arguments']['domains'] == ['ai']
    assert kwargs['arguments']['memory_type'] == 'project_state'
    assert kwargs['mem0_result'] == result


def test_update_schedules_kontext_write_audit_without_changing_result(monkeypatch):
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            if method == 'GET':
                return {'id': 'mem-1', 'memory': 'Memory text.', 'metadata': {'domains': ['ai']}}
            if method == 'PUT':
                return {'updated': True}
            raise AssertionError(f'unexpected request: {method} {path}')

    def fake_audit(config, **kwargs):
        calls.append((config, kwargs))
        return True

    monkeypatch.setattr('core.log_kontext_write_audit_async', fake_audit)
    client = FakeClient(
        Mem0Config(
            base_url='https://mem0.example.test',
            api_key='secret',
            client_name='claude',
            kontext_write_audit_enabled=True,
            kontext_write_audit_mcp_url='https://kontext.example/mcp/{token}',
            kontext_write_audit_mcp_token='write-token',
            kontext_write_audit_log='/tmp/write-audit.jsonl',
        )
    )

    result = client.update('mem-1', 'Updated memory text.', 'write canary update', domains=['systems'])

    assert result['updated'] is True
    assert len(calls) == 1
    config, kwargs = calls[0]
    assert config.ready() is True
    assert kwargs['origin'] == 'claude'
    assert kwargs['tool'] == 'update'
    assert kwargs['arguments']['id'] == 'mem-1'
    assert kwargs['arguments']['content'] == 'Updated memory text.'
    assert kwargs['arguments']['reason'] == 'write canary update'
    assert kwargs['arguments']['domains'] == ['systems']
    assert kwargs['mem0_result'] == result


def test_search_filters_by_memory_type_and_current_status_after_fetching_candidates():
    calls = []

    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            return {
                'results': [
                    {
                        'id': 'match',
                        'memory': 'Mother relationship pattern with current psychology relevance.',
                        'metadata': {
                            'domains': ['psychology', 'relationships'],
                            'memory_type': 'relationship_pattern',
                            'current_status': 'active',
                            'memory_tier': 'active',
                        },
                    },
                    {
                        'id': 'wrong-type',
                        'memory': 'Mother relationship event.',
                        'metadata': {
                            'domains': ['psychology', 'relationships'],
                            'memory_type': 'event',
                            'current_status': 'active',
                            'memory_tier': 'active',
                        },
                    },
                    {
                        'id': 'wrong-status',
                        'memory': 'Mother relationship pattern that is resolved.',
                        'metadata': {
                            'domains': ['psychology', 'relationships'],
                            'memory_type': 'relationship_pattern',
                            'current_status': 'resolved',
                            'memory_tier': 'active',
                        },
                    },
                ]
            }

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.search(
        'mother relationship pattern',
        top_k=2,
        domains=['psychology'],
        memory_types=['relationship_pattern'],
        current_statuses=['active'],
    )

    assert calls == [('POST', '/search', {'query': 'mother relationship pattern', 'filters': {'user_id': 'ionut'}, 'top_k': 20})]
    assert [row['id'] for row in result['results']] == ['match']


def test_search_can_filter_by_memory_tier():
    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            return {
                'results': [
                    {'id': 'active', 'memory': 'AI workflow active memory', 'metadata': {'memory_tier': 'active'}},
                    {'id': 'historical', 'memory': 'AI workflow historical memory', 'metadata': {'memory_tier': 'historical'}},
                    {'id': 'cold', 'memory': 'AI workflow cold memory', 'metadata': {'memory_tier': 'cold'}},
                ]
            }

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.search('AI workflow', memory_tiers=['historical', 'cold'])

    assert [row['id'] for row in result['results']] == ['historical', 'cold']


def test_invalid_memory_tier_defaults_to_active():
    row = {'id': 'mem-3', 'memory': 'Memory with invalid tier.', 'metadata': {'memory_tier': 'archive'}}

    assert sanitize_memory(row)['metadata']['memory_tier'] == 'active'


def test_search_promotes_lexical_matches_before_vector_results():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if body and body.get('top_k') == 1000:
                return {
                    'results': [
                        {'id': 'semantic', 'memory': 'A related but wrong result.', 'metadata': {}},
                        {'id': 'canary', 'data': 'Canary test: Ionut calibration phrase is blue copper lantern.', 'domains': ['systems']},
                        {'id': 'other', 'data': 'Unrelated memory.'},
                    ]
                }
            return {'results': [{'id': 'semantic', 'memory': 'A related but wrong result.', 'metadata': {}}]}

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.search('temporary connector test memory and calibration phrase', top_k=5)

    assert result['results'][0]['id'] == 'canary'
    assert calls[1] == ('POST', '/search', {'query': 'temporary connector test memory and calibration phrase', 'filters': {'user_id': 'ionut'}, 'top_k': 1000})


def test_search_uses_postgres_lexical_search_when_configured():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            return {'results': [{'id': 'semantic', 'memory': 'A related but wrong result.', 'metadata': {}}]}

        def postgres_lexical_search(self, query, tokens, limit=10):
            return [sanitize_memory({'id': 'canary', 'data': 'Canary test: Ionut calibration phrase is blue copper lantern.'})]

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret', lexical_database_url='postgresql://example'))

    result = client.search('temporary connector test memory and calibration phrase', top_k=5)

    assert result['results'][0]['id'] == 'canary'
    assert len(calls) == 1


def test_fetch_sanitizes_single_memory():
    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            assert (method, path, body) == ('GET', '/memories/mem-1', None)
            return {'id': 'mem-1', 'memory': 'Fetched memory.', 'metadata': {'domains': ['psychology']}}

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    assert client.fetch('mem-1')['text'] == 'Fetched memory.'


def test_update_fetches_existing_memory_and_preserves_metadata():
    calls = []

    class FakeClient(Mem0Client):
        text = 'Old durable memory.'
        metadata = {
            'domains': ['old-domain'],
            'memory_type': 'pattern',
            'signal_strength': 5,
            'current_status': 'active',
            'memory_tier': 'active',
            'source_ids': ['raw-row-1'],
        }

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if method == 'GET':
                return {'id': 'mem-1', 'memory': self.text, 'metadata': self.metadata}
            if method == 'PUT':
                self.text = body['text']
                self.metadata = body['metadata']
                return {'message': 'Memory updated successfully'}
            raise AssertionError(f'unexpected request: {method} {path}')

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret', client_name='codex'))

    result = client.update(
        ' mem-1 ',
        ' New durable memory. ',
        reason='user corrected stale info',
        domains=[' psychology ', ''],
        current_status='active',
        memory_tier='historical',
    )

    assert result['updated'] is True
    assert result['before']['text'] == 'Old durable memory.'
    assert result['after']['text'] == 'New durable memory.'
    assert calls == [
        ('GET', '/memories/mem-1', None),
        (
            'PUT',
            '/memories/mem-1',
            {
                'text': 'New durable memory.',
                'metadata': {
                    'domains': ['psychology'],
                    'memory_type': 'pattern',
                    'signal_strength': 5,
                    'current_status': 'active',
                    'memory_tier': 'historical',
                    'source_ids': ['raw-row-1'],
                    'last_modified_by': 'codex',
                    'last_modified_via': 'hosted-mcp',
                    'last_modified_reason': 'user corrected stale info',
                },
            },
        ),
        ('GET', '/memories/mem-1', None),
    ]
    assert result['response'] == {'message': 'Memory updated successfully'}


def test_update_requires_reason():
    client = Mem0Client(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    try:
        client.update('mem-1', 'New memory', reason=' ')
    except ValueError as exc:
        assert 'reason is required' in str(exc)
    else:
        raise AssertionError('expected missing reason failure')


def test_delete_fetches_before_hard_delete():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if method == 'GET':
                return {'id': 'mem-1', 'memory': 'Outdated memory.', 'metadata': {'domains': ['systems']}}
            if method == 'DELETE':
                return {'deleted': True}
            raise AssertionError(f'unexpected request: {method} {path}')

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.delete(' mem-1 ', reason='user said it is outdated')

    assert result == {
        'deleted': True,
        'id': 'mem-1',
        'reason': 'user said it is outdated',
        'before': sanitize_memory({'id': 'mem-1', 'memory': 'Outdated memory.', 'metadata': {'domains': ['systems']}}),
        'response': {'deleted': True},
    }
    assert calls == [('GET', '/memories/mem-1', None), ('DELETE', '/memories/mem-1', None)]


def test_sanitize_memory_reads_mem0_payload_shape():
    row = {
        'id': 'mem-2',
        'data': 'Payload data memory.',
        'domains': ['systems'],
        'memory_type': 'canary_test',
        'signal_strength': 1,
        'current_status': 'temporary',
    }

    result = sanitize_memory(row)

    assert result['text'] == 'Payload data memory.'
    assert result['metadata']['domains'] == ['systems']
    assert result['metadata']['memory_type'] == 'canary_test'
    assert result['metadata']['memory_tier'] == 'active'


def test_lexical_score_requires_multiple_non_stopword_hits():
    tokens = lexical_tokens('temporary connector test memory and calibration phrase')

    assert 'memory' not in tokens
    assert lexical_score('temporary connector test memory and calibration phrase', tokens, 'calibration phrase is blue copper lantern') > 0
    assert lexical_score('temporary connector test memory and calibration phrase', tokens, 'only calibration appears') == 0


def test_save_posts_distilled_memory_with_infer_disabled_and_client_metadata():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            return {'id': 'new-memory'}

    client = FakeClient(
        Mem0Config(
            base_url='https://mem0.example.test',
            api_key='secret',
            agent_id='codex-live-memory',
            client_name='codex',
        )
    )

    result = client.save(
        ' Durable memory. ',
        domains=['ai', ' workflow ', ''],
        memory_type='decision',
        signal_strength=8,
        memory_tier='historical',
    )

    assert result == {'saved': True, 'response': {'id': 'new-memory'}}
    assert calls == [
        (
            'POST',
            '/memories',
            {
                'messages': [{'role': 'user', 'content': 'Durable memory.'}],
                'user_id': 'ionut',
                'agent_id': 'codex-live-memory',
                'metadata': {
                    'source': 'hosted-mcp',
                    'client': 'codex',
                    'domains': ['ai', 'workflow'],
                    'memory_type': 'decision',
                    'signal_strength': 8,
                    'current_status': 'active',
                    'memory_tier': 'historical',
                },
                'infer': False,
            },
        )
    ]


def test_save_merges_ingestion_metadata_extra():
    calls = []

    class FakeClient(Mem0Client):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            return {'id': 'new-memory'}

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret', client_name='codex'))

    client.save(
        ' Durable ingestion memory. ',
        domains=['ai'],
        memory_type='decision',
        signal_strength=9,
        metadata_extra={'source_hash': 'abc', 'ingestion_origin': 'codex', 'ignored': None},
    )

    metadata = calls[0][2]['metadata']
    assert metadata['source'] == 'hosted-mcp'
    assert metadata['client'] == 'codex'
    assert metadata['domains'] == ['ai']
    assert metadata['memory_type'] == 'decision'
    assert metadata['signal_strength'] == 9
    assert metadata['current_status'] == 'active'
    assert metadata['memory_tier'] == 'active'
    assert metadata['source_hash'] == 'abc'
    assert metadata['ingestion_origin'] == 'codex'
    assert 'ignored' not in metadata


def test_update_merges_ingestion_metadata_extra():
    calls = []

    class FakeClient(Mem0Client):
        text = 'Old memory.'
        metadata = {'domains': ['ai'], 'memory_type': 'decision', 'memory_tier': 'active'}

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if method == 'GET':
                return {'id': 'mem-1', 'memory': self.text, 'metadata': self.metadata}
            if method == 'PUT':
                self.text = body['text']
                self.metadata = body['metadata']
                return {'updated': True}
            raise AssertionError(f'unexpected request: {method} {path}')

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret', client_name='claude'))

    client.update('mem-1', 'New memory.', 'ingestion refinement', metadata_extra={'source_hash': 'abc'})

    metadata = calls[1][2]['metadata']
    assert metadata['domains'] == ['ai']
    assert metadata['memory_type'] == 'decision'
    assert metadata['memory_tier'] == 'active'
    assert metadata['source_hash'] == 'abc'
    assert metadata['last_modified_by'] == 'claude'
    assert metadata['last_modified_via'] == 'hosted-mcp'
    assert metadata['last_modified_reason'] == 'ingestion refinement'
