from retrieval import filter_memories, rank_memories


def memory(memory_id, text, score=0.0, **metadata):
    return {
        'id': memory_id,
        'title': text[:90],
        'text': text,
        'score': score,
        'metadata': metadata,
    }


def test_business_workflow_query_prefers_current_high_signal_context():
    rows = [
        memory(
            'generic',
            'The speaker diarization was inferred from conversational cues.',
            score=0.91,
            domains=['workflow'],
            memory_type='note',
            signal_strength=1,
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'vocality',
            'Vocality workflow: migrate students from Preply to owned systems and launch compact Skool curriculum assets.',
            score=0.31,
            domains=['business', 'workflow', 'vocality'],
            memory_type='project_state',
            signal_strength=9,
            current_status='active',
            memory_tier='active',
        ),
    ]

    assert rank_memories('Vocality Preply Skool workflow', rows, top_k=2)[0]['id'] == 'vocality'


def test_psychology_relationship_query_prefers_rich_pattern_over_generic_note():
    rows = [
        memory(
            'generic',
            'The user discussed relationship issues.',
            score=0.96,
            domains=['relationships'],
            memory_type='note',
            signal_strength=2,
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'pattern',
            'Relationship pattern: anxious escalation from the partner amplifies avoidant withdrawal under pressure.',
            score=0.25,
            domains=['relationships', 'psychology'],
            memory_type='relationship_pattern',
            signal_strength=9,
            current_status='active',
            memory_tier='historical',
        ),
    ]

    assert rank_memories('relationship anxious escalation withdrawal pattern', rows, top_k=2)[0]['id'] == 'pattern'


def test_opera_vocality_query_prefers_user_voice_signal_over_lesson_sludge():
    rows = [
        memory(
            'sludge',
            'Generic vocal anatomy lesson: lower larynx, breath, vowels, resonance, and passaggio terminology.',
            score=0.9,
            domains=['vocality'],
            memory_type='lesson_note',
            signal_strength=1,
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'career',
            'Opera career context: voice development and role direction should inform repertoire, auditions, and travel funding priorities.',
            score=0.35,
            domains=['opera', 'vocality'],
            memory_type='career_context',
            signal_strength=8,
            current_status='active',
            memory_tier='active',
        ),
    ]

    assert rank_memories('opera voice role auditions career', rows, top_k=2)[0]['id'] == 'career'


def test_ai_systems_query_prefers_mem0_architecture_over_generic_tool_note():
    rows = [
        memory(
            'tool-note',
            'A tool can search memory before answering.',
            score=0.95,
            domains=['ai'],
            memory_type='note',
            signal_strength=2,
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'architecture',
            'AI systems architecture: hosted Mem0 MCP connects ChatGPT, Codex, Claude, and Perplexity to one shared memory layer.',
            score=0.34,
            domains=['ai', 'systems', 'infrastructure'],
            memory_type='architecture_decision',
            signal_strength=9,
            current_status='active',
            memory_tier='active',
        ),
    ]

    assert rank_memories('Mem0 MCP ChatGPT Codex Claude shared memory architecture', rows, top_k=2)[0]['id'] == 'architecture'


def test_active_recent_memory_beats_older_historical_conflict_with_slightly_higher_vector_score():
    rows = [
        memory(
            'older-conflict',
            'Relationship state: Ionut and Luiza are fully together and should be treated as current.',
            score=0.75,
            domains=['relationships', 'psychology'],
            memory_type='relationship_pattern',
            signal_strength=8,
            current_status='active',
            memory_tier='historical',
            effective_at='2025-11-01T00:00:00Z',
            updated_at='2025-11-01T00:00:00Z',
        ),
        memory(
            'current-state',
            'Relationship state: Ionut and Luiza are paused, with practical logistics treated as current.',
            score=0.62,
            domains=['relationships', 'psychology'],
            memory_type='relationship_pattern',
            signal_strength=8,
            current_status='active',
            memory_tier='active',
            effective_at='2026-05-17T00:00:00Z',
            updated_at='2026-05-17T00:00:00Z',
        ),
    ]

    assert rank_memories('current relationship state Luiza logistics', rows, top_k=2)[0]['id'] == 'current-state'


def test_superseded_memory_is_penalized_below_current_successor():
    rows = [
        memory(
            'superseded',
            'AI memory architecture: use old local-only Kontext as the main database.',
            score=0.95,
            domains=['ai', 'systems', 'infrastructure'],
            memory_type='architecture_decision',
            signal_strength=9,
            current_status='active',
            memory_tier='active',
            superseded_by='current',
            superseded_at='2026-05-16T12:00:00Z',
            updated_at='2026-05-10T00:00:00Z',
        ),
        memory(
            'current',
            'AI memory architecture: Mem0 remains source of truth while Kontext V2 mirrors in shadow mode.',
            score=0.42,
            domains=['ai', 'systems', 'infrastructure'],
            memory_type='architecture_decision',
            signal_strength=9,
            current_status='active',
            memory_tier='active',
            effective_at='2026-05-17T00:00:00Z',
            updated_at='2026-05-17T00:00:00Z',
        ),
    ]

    assert rank_memories('AI memory architecture Kontext Mem0 source of truth', rows, top_k=2)[0]['id'] == 'current'


def test_effective_or_updated_at_breaks_close_score_ties_toward_newer_current_memory():
    rows = [
        memory('older', 'Current OB1 memory status: pipeline healthy.', score=0.5, domains=['ai'], memory_type='project_state', signal_strength=7, current_status='active', memory_tier='active', updated_at='2025-01-01T00:00:00Z'),
        memory('newer', 'Current OB1 memory status: pipeline healthy.', score=0.5, domains=['ai'], memory_type='project_state', signal_strength=7, current_status='active', memory_tier='active', effective_at='2026-05-17T00:00:00Z'),
    ]

    assert rank_memories('current OB1 memory status pipeline', rows, top_k=2)[0]['id'] == 'newer'


def test_cold_rows_are_available_when_explicitly_requested():
    rows = [
        memory(
            'active',
            'Active AI systems memory.',
            domains=['ai'],
            memory_type='note',
            current_status='active',
            memory_tier='active',
        ),
        memory(
            'cold',
            'Cold archived AI systems memory kept for completeness.',
            domains=['ai'],
            memory_type='note',
            current_status='resolved',
            memory_tier='cold',
        ),
    ]

    filtered = filter_memories(rows, memory_tiers={'cold'})

    assert [row['id'] for row in rank_memories('AI systems memory', filtered, requested_tiers={'cold'}, top_k=2)] == ['cold']
