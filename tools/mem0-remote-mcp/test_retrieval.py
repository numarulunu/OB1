from retrieval import (
    extract_entities,
    lexical_score,
    lexical_tokens,
    merge_ranked_memories,
    normalize_memory_tier,
    route_query,
    rank_memories,
    score_memory,
)


def memory(memory_id, text, score=0.0, **metadata):
    return {
        'id': memory_id,
        'title': text[:90],
        'text': text,
        'score': score,
        'metadata': metadata,
    }


def test_retrieval_helpers_keep_existing_lexical_behavior():
    tokens = lexical_tokens('temporary connector test memory and calibration phrase')

    assert 'memory' not in tokens
    assert lexical_score('temporary connector test memory and calibration phrase', tokens, 'calibration phrase is blue copper lantern') > 0
    assert lexical_score('temporary connector test memory and calibration phrase', tokens, 'only calibration appears') == 0
    assert normalize_memory_tier('archive') == 'active'
    assert [row['id'] for row in merge_ranked_memories([{'id': 'a'}, {'id': 'b'}], [{'id': 'a'}, {'id': 'c'}], 3)] == ['a', 'b', 'c']


def test_score_memory_fuses_signal_metadata_with_text_relevance():
    query = 'Mem0 MCP ingestion systems'
    specific = memory(
        'specific',
        'The hosted Mem0 MCP ingestion layer auto-writes durable AI systems memories.',
        score=0.22,
        domains=['ai', 'systems'],
        memory_type='project_state',
        signal_strength=9,
        current_status='active',
        memory_tier='active',
    )
    generic = memory(
        'generic',
        'A generic note about retrieval and context.',
        score=0.99,
        domains=['ai'],
        memory_type='note',
        signal_strength=1,
        current_status='active',
        memory_tier='active',
    )

    assert score_memory(query, specific) > score_memory(query, generic)


def test_cold_tier_is_penalized_unless_explicitly_requested():
    query = 'Mem0 MCP ingestion systems'
    active = memory(
        'active',
        'Mem0 MCP ingestion systems architecture.',
        domains=['systems'],
        signal_strength=5,
        current_status='active',
        memory_tier='active',
    )
    cold = memory(
        'cold',
        'Mem0 MCP ingestion systems architecture.',
        domains=['systems'],
        signal_strength=5,
        current_status='active',
        memory_tier='cold',
    )

    assert score_memory(query, cold) < score_memory(query, active)
    assert score_memory(query, cold, requested_tiers={'cold'}) > score_memory(query, cold)


def test_historical_relationship_and_identity_context_remains_retrievable():
    query = 'mother relationship identity formative pattern'
    historical = memory(
        'historical',
        'Formative relationship pattern with mother shaped identity, trust, and attachment responses.',
        score=0.2,
        domains=['relationships', 'psychology', 'family'],
        memory_type='formative_event',
        signal_strength=9,
        current_status='active',
        memory_tier='historical',
    )
    generic_active = memory(
        'generic-active',
        'Active generic note about relationships.',
        score=0.95,
        domains=['relationships'],
        memory_type='note',
        signal_strength=2,
        current_status='active',
        memory_tier='active',
    )

    assert rank_memories(query, [generic_active, historical], top_k=2)[0]['id'] == 'historical'


def test_entity_hits_boost_people_and_project_queries():
    query = 'Luiza attachment escalation pattern'
    entity_match = memory(
        'luiza',
        'Luiza dynamic: anxious attachment escalation creates pressure and withdrawal cycles.',
        score=0.2,
        domains=['relationships', 'psychology'],
        memory_type='relationship_pattern',
        signal_strength=8,
        current_status='active',
        memory_tier='active',
    )
    generic = memory(
        'generic',
        'Relationship conflict can involve escalation and withdrawal cycles.',
        score=0.98,
        domains=['relationships'],
        memory_type='note',
        signal_strength=2,
        current_status='active',
        memory_tier='active',
    )

    assert 'luiza' in extract_entities(query)
    assert rank_memories(query, [generic, entity_match], top_k=2)[0]['id'] == 'luiza'



def test_route_query_classifies_trivial_sensitive_and_infrastructure_queries():
    trivial = route_query('ok')
    sensitive = route_query('what do you know about my mother and family patterns?')
    infrastructure = route_query('how is the Mem0 MCP Docker VPS setup wired?')

    assert trivial.should_search is False
    assert trivial.mode == 'skip'
    assert sensitive.should_search is True
    assert sensitive.mode == 'sensitive_deep'
    assert {'relationships', 'psychology', 'family'} <= set(sensitive.domains)
    assert sensitive.candidate_multiplier >= 8
    assert infrastructure.should_search is True
    assert infrastructure.mode == 'project'
    assert {'ai', 'systems', 'infrastructure'} <= set(infrastructure.domains)


def test_hybrid_ranking_prefers_exact_entity_and_domain_over_generic_vector_hit():
    query = 'mother psychiatry ward episodes relationship pattern'
    exact = memory(
        'mother-clinical',
        'Mother clinical context: two psychiatry ward episodes shaped the family-origin trust and nervous-system pattern.',
        score=0.05,
        domains=['relationships', 'psychology', 'family'],
        memory_type='formative_event',
        signal_strength=9,
        current_status='active',
        memory_tier='historical',
    )
    generic = memory(
        'generic-psychology',
        'Mother relationship pattern can involve family stress after difficult episodes.',
        score=0.99,
        domains=['psychology'],
        memory_type='note',
        signal_strength=2,
        current_status='active',
        memory_tier='active',
    )

    assert rank_memories(query, [generic, exact], top_k=2)[0]['id'] == 'mother-clinical'
