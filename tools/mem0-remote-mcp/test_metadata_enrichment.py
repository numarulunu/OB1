import json

import pytest

from metadata_enrichment import (
    MetadataEnrichmentAction,
    build_metadata_enrichment_plan,
    execute_metadata_enrichment_plan,
)


def memory(memory_id, text, **metadata):
    return {
        'id': memory_id,
        'text': text,
        'metadata': metadata,
    }


def test_relationship_psychology_plan_retypes_clinical_and_relationship_rows_without_text_leak():
    rows = [
        memory(
            'mother',
            'Mother clinical context: involuntary psychiatric ward episode, delusional thinking, antipsychotic medication, anosognosia.',
            domains=['family_origin', 'relationships', 'psychology'],
            memory_type='person',
            signal_strength=9,
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'luiza',
            'Luiza relationship pattern: anxious attachment escalation amplifies avoidant withdrawal under pressure.',
            domains=['relationships', 'psychology'],
            memory_type='note',
            signal_strength=9,
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'systems',
            'Mem0 MCP architecture.',
            domains=['ai', 'systems'],
            memory_type='project_state',
        ),
    ]

    plan = build_metadata_enrichment_plan(rows)
    payload = plan.to_dict()

    assert payload['summary'] == {'keep': 1, 'update_type': 2}
    assert [(action.memory_ids[0], action.memory_type) for action in plan.actions if action.action == 'update_type'] == [
        ('mother', 'clinical_context'),
        ('luiza', 'relationship_pattern'),
    ]
    rendered = json.dumps(payload, ensure_ascii=False)
    assert 'delusional thinking' not in rendered
    assert 'anxious attachment escalation' not in rendered


def test_metadata_enrichment_preserves_good_existing_types():
    row = memory(
        'pattern',
        'Relationship pattern: repeated escalation and withdrawal cycle.',
        domains=['relationships', 'psychology'],
        memory_type='relationship_pattern',
        signal_strength=8,
    )

    plan = build_metadata_enrichment_plan([row])

    assert plan.actions == [
        MetadataEnrichmentAction('keep', ['pattern'], reason='memory_type already specific', current_memory_type='relationship_pattern')
    ]


def test_execute_metadata_enrichment_requires_max_updates_and_writes_rollback(tmp_path):
    plan = build_metadata_enrichment_plan([
        memory(
            'mother',
            'Mother was hospitalized in a psychiatric ward after delusional escalation.',
            domains=['relationships', 'psychology', 'family'],
            memory_type='person',
        )
    ])

    class FakeClient:
        def __init__(self):
            self.updated = []

        def fetch(self, memory_id):
            return {
                'id': memory_id,
                'text': 'Fetched memory text stays out of stdout.',
                'metadata': {'domains': ['relationships', 'psychology'], 'memory_type': 'person'},
            }

        def update(self, memory_id, content, reason, **kwargs):
            self.updated.append((memory_id, content, reason, kwargs))
            return {'updated': True, 'id': memory_id}

    with pytest.raises(ValueError, match='max_updates'):
        execute_metadata_enrichment_plan(plan, FakeClient(), tmp_path / 'rollback.jsonl', tmp_path / 'maintenance.jsonl')

    client = FakeClient()
    result = execute_metadata_enrichment_plan(
        plan,
        client,
        tmp_path / 'rollback.jsonl',
        tmp_path / 'maintenance.jsonl',
        max_updates=1,
    )

    assert result['counts']['updated'] == 1
    assert client.updated[0][0] == 'mother'
    assert client.updated[0][3]['memory_type'] == 'clinical_context'
    assert (tmp_path / 'rollback.jsonl').read_text(encoding='utf-8')
    assert (tmp_path / 'maintenance.jsonl').read_text(encoding='utf-8')
