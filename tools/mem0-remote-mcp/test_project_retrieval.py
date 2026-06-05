import json

from project_observations import ProjectObservationStore
from project_retrieval import ProjectObservationRetriever


def seed_observations(path):
    store = ProjectObservationStore(path)
    store.append({
        'timestamp': '2026-05-10T10:00:00Z',
        'origin': 'codex',
        'project_root': 'C:/Tools/OB1',
        'event_type': 'implementation',
        'title': 'Add hook capture',
        'summary': 'Do not expose TOKEN=private-token in compact search',
        'files_modified': ['tools/mem0-remote-mcp/client_hooks/mem0_context_hook.py'],
        'source_hash': 'obs-hook',
    })
    store.append({
        'timestamp': '2026-05-10T10:05:00Z',
        'origin': 'codex',
        'project_root': 'C:/Tools/OB1',
        'event_type': 'test',
        'title': 'Verify hook capture',
        'files_read': ['tools/mem0-remote-mcp/test_client_hooks.py'],
        'source_hash': 'obs-test',
    })
    store.append({
        'timestamp': '2026-05-10T10:10:00Z',
        'origin': 'codex',
        'project_root': 'C:/Tools/OB1',
        'event_type': 'implementation',
        'title': 'Build project retrieval',
        'files_modified': ['tools/mem0-remote-mcp/project_retrieval.py'],
        'source_hash': 'obs-retrieval',
    })


def test_project_search_returns_compact_index_and_safe_telemetry(tmp_path):
    log_path = tmp_path / 'observations.jsonl'
    telemetry_path = tmp_path / 'project-retrieval.jsonl'
    seed_observations(log_path)
    retriever = ProjectObservationRetriever(log_path, telemetry_log=telemetry_path)

    result = retriever.search('hook capture private-token', limit=5)

    assert result['rows'][0]['title'] == 'Add hook capture'
    assert set(result['rows'][0]) == {'id', 'date', 'type', 'title', 'project', 'files_count', 'token_estimate'}
    rendered = json.dumps(result, sort_keys=True)
    assert 'private-token' not in rendered
    telemetry = telemetry_path.read_text(encoding='utf-8')
    assert 'private-token' not in telemetry
    assert 'query_hash' in telemetry


def test_project_timeline_fetch_and_file_context(tmp_path):
    log_path = tmp_path / 'observations.jsonl'
    seed_observations(log_path)
    retriever = ProjectObservationRetriever(log_path)
    search = retriever.search('verify hook', limit=1)
    anchor_id = search['rows'][0]['id']

    timeline = retriever.timeline(anchor_id=anchor_id, before=1, after=1)
    fetched = retriever.fetch(anchor_id)
    file_context = retriever.file_context('tools/mem0-remote-mcp/test_client_hooks.py')

    assert [row['title'] for row in timeline['rows']] == ['Add hook capture', 'Verify hook capture', 'Build project retrieval']
    assert fetched['found'] is True
    assert fetched['row']['title'] == 'Verify hook capture'
    assert file_context['file_path'] == 'tools/mem0-remote-mcp/test_client_hooks.py'
    assert file_context['titles'] == ['Verify hook capture']
    assert file_context['recommend_full_file_read'] is False


def test_project_file_context_recommends_read_when_no_prior_context(tmp_path):
    log_path = tmp_path / 'observations.jsonl'
    seed_observations(log_path)
    retriever = ProjectObservationRetriever(log_path)

    context = retriever.file_context('tools/mem0-remote-mcp/missing.py')

    assert context['titles'] == []
    assert context['recommend_full_file_read'] is True
