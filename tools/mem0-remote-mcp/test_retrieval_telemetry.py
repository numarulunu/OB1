
import json

from core import Mem0Client, Mem0Config
from retrieval_telemetry import RetrievalTelemetry, build_retrieval_stats


def test_retrieval_telemetry_writes_safe_search_event_without_memory_text(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'
    telemetry = RetrievalTelemetry(telemetry_path)

    telemetry.log_search(
        origin='codex',
        query='what do we know about the calibration phrase',
        filters={'domains': ['systems'], 'memory_tiers': ['active']},
        results=[{'id': 'mem-1', 'title': 'Calibration phrase result', 'text': 'RAW MEMORY TEXT MUST NOT LEAK'}],
        latency_ms=12.5,
    )

    rendered = telemetry_path.read_text(encoding='utf-8')
    row = json.loads(rendered)
    assert row['event'] == 'search'
    assert row['origin'] == 'codex'
    assert row['query_hash']
    assert row['query_preview'] == 'what do we know about the calibration phrase'
    assert row['filters'] == {'domains': ['systems'], 'memory_tiers': ['active']}
    assert row['result_count'] == 1
    assert row['results'] == [{'id': 'mem-1', 'title': 'Calibration phrase result'}]
    assert row['latency_ms'] == 12.5
    assert 'RAW MEMORY TEXT MUST NOT LEAK' not in rendered


def test_retrieval_stats_survive_malformed_rows_and_hide_queries(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'
    telemetry_path.write_text(
        '\n'.join(
            [
                json.dumps({'ts': '2026-05-09T00:00:00Z', 'event': 'search', 'origin': 'codex', 'query_preview': 'private query', 'result_count': 2, 'latency_ms': 10, 'results': [{'id': 'a', 'title': 'A'}]}),
                '{bad json',
                json.dumps({'ts': '2026-05-09T00:01:00Z', 'event': 'search', 'origin': 'claude', 'query_preview': 'another private query', 'result_count': 0, 'latency_ms': 30, 'results': []}),
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    stats = build_retrieval_stats(telemetry_path, recent_limit=10).to_dict()
    rendered = json.dumps(stats, ensure_ascii=False)

    assert stats['rows_seen'] == 2
    assert stats['malformed_rows'] == 1
    assert stats['origins'] == {'codex': 1, 'claude': 1}
    assert stats['result_count_total'] == 2
    assert stats['avg_latency_ms'] == 20.0
    assert stats['zero_result_count'] == 1
    assert stats['last_success_by_origin'] == {'codex': '2026-05-09T00:00:00Z', 'claude': '2026-05-09T00:01:00Z'}
    assert stats['top_result_ids'] == {'a': 1}
    assert 'private query' not in rendered



def test_client_search_uses_query_route_for_sensitive_candidate_depth(tmp_path):
    calls = []

    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            calls.append(body or {})
            return {
                'results': [
                    {
                        'id': 'mem-1',
                        'memory': 'Mother and family-origin psychology pattern.',
                        'metadata': {'domains': ['relationships', 'psychology', 'family'], 'memory_tier': 'historical'},
                    }
                ]
            }

    client = FakeClient(Mem0Config(base_url='https://mem0.example.test', api_key='secret'))

    result = client.search('what do you know about my mother and family patterns?', top_k=5)

    assert calls[0]['top_k'] == 50
    assert result['results'][0]['id'] == 'mem-1'


def test_client_search_records_retrieval_telemetry(tmp_path):
    telemetry_path = tmp_path / 'retrieval.jsonl'

    class FakeClient(Mem0Client):
        def lexical_search(self, query, limit=10):
            return []

        def request(self, method, path, body=None):
            return {
                'results': [
                    {'id': 'mem-1', 'memory': 'AI systems architecture memory.', 'metadata': {'domains': ['ai', 'systems'], 'memory_tier': 'active'}}
                ]
            }

    client = FakeClient(
        Mem0Config(
            base_url='https://mem0.example.test',
            api_key='secret',
            client_name='codex',
            retrieval_telemetry_log=str(telemetry_path),
        )
    )

    result = client.search('AI systems architecture', domains=['ai'])

    row = json.loads(telemetry_path.read_text(encoding='utf-8'))
    assert result['results'][0]['id'] == 'mem-1'
    assert row['origin'] == 'codex'
    assert row['filters']['domains'] == ['ai']
    assert row['result_count'] == 1
    assert row['results'] == [{'id': 'mem-1', 'title': 'AI systems architecture memory.'}]
    assert 'AI systems architecture memory.' in row['results'][0]['title']
    assert 'text' not in row['results'][0]
