
from retrieval import expand_query, rank_memories


def memory(memory_id, text, score=0.0, **metadata):
    return {
        'id': memory_id,
        'title': text[:90],
        'text': text,
        'score': score,
        'metadata': metadata,
    }


def test_expand_query_adds_deterministic_intent_terms_for_stuck_queries():
    expanded = expand_query("I'm stuck and cannot start")

    assert expanded.startswith("I'm stuck and cannot start")
    assert 'blocked execution friction procrastination avoidance workflow next step' in expanded


def test_query_expansion_improves_rank_for_implicit_workflow_blockers():
    rows = [
        memory(
            'generic',
            'A generic note about feeling bad.',
            score=0.95,
            domains=['psychology'],
            memory_type='note',
            signal_strength=2,
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'execution-pattern',
            'Execution pattern: blocked workflow friction and procrastination often appear when decisions are unclear.',
            score=0.2,
            domains=['workflow', 'psychology'],
            memory_type='execution_pattern',
            signal_strength=9,
            current_status='active',
            memory_tier='active',
        ),
    ]

    assert rank_memories("I'm stuck", rows, top_k=2)[0]['id'] == 'execution-pattern'


def test_query_expansion_links_relationship_language_to_attachment_patterns():
    rows = [
        memory(
            'generic',
            'A generic note about conflict.',
            score=0.93,
            domains=['relationships'],
            memory_type='note',
            signal_strength=2,
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'attachment-pattern',
            'Attachment pattern: anxious escalation and avoidant withdrawal shape relationship trust dynamics.',
            score=0.2,
            domains=['relationships', 'psychology'],
            memory_type='relationship_pattern',
            signal_strength=9,
            current_status='active',
            memory_tier='historical',
        ),
    ]

    assert rank_memories('partner conflict', rows, top_k=2)[0]['id'] == 'attachment-pattern'
