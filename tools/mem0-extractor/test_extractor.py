from extractor import build_extraction_prompt, parse_llm_response
from schemas import Candidate, normalize_memory_tier, normalize_proposal


def test_normalize_memory_tier_defaults_invalid_values_to_active():
    assert normalize_memory_tier("archive") == "active"
    assert normalize_memory_tier(" historical ") == "historical"


def test_normalize_proposal_clamps_signal_and_cleans_domains():
    proposal = normalize_proposal(
        {
            "action": "save",
            "content": " Durable memory. ",
            "domains": [" Psychology ", "", "AI"],
            "memory_type": "identity_shaping",
            "signal_strength": 99,
            "memory_tier": "archive",
            "current_status": "active",
            "source_ids": ["row-1", ""],
            "reason": "test",
        }
    )

    assert proposal.signal_strength == 10
    assert proposal.memory_tier == "active"
    assert proposal.domains == ["psychology", "ai"]
    assert proposal.source_ids == ["row-1"]


def test_build_extraction_prompt_contains_user_policy_and_candidates_only():
    candidates = [
        Candidate(
            source_id="row-1",
            text="Synthetic formative relationship context.",
            domains=["relationships"],
            signal_strength=9,
            memory_type="identity_shaping",
            memory_tier="historical",
            reasons=["family_origin"],
        )
    ]

    prompt = build_extraction_prompt(candidates, session_label="synthetic-session")

    assert "do not flatten psychology" in prompt.lower()
    assert "junk should be deleted, not hidden as cold" in prompt.lower()
    assert "Synthetic formative relationship context." in prompt
    assert "source_id" in prompt


def test_parse_llm_response_accepts_fenced_json_and_normalizes_proposals():
    raw = '''```json
    {"proposals":[{"action":"save","content":"A durable memory.","domains":["AI"],"memory_type":"pattern","signal_strength":7,"memory_tier":"cold","current_status":"dormant","source_ids":["row-1"],"reason":"useful"}]}
    ```'''

    result = parse_llm_response(raw)

    assert len(result.proposals) == 1
    assert result.proposals[0].domains == ["ai"]
    assert result.proposals[0].memory_tier == "cold"


def test_candidate_prompt_exposes_suggested_memory_type():
    candidate = Candidate(
        source_id="row-7",
        text="Decision: use Mem0 as the operational brain.",
        domains=["ai", "systems"],
        signal_strength=8,
        memory_type="decision",
        memory_tier="active",
    )

    prompt_row = candidate.to_prompt_dict()

    assert prompt_row["suggested_memory_type"] == "decision"


def test_build_extraction_prompt_makes_pattern_last_resort():
    prompt = build_extraction_prompt([], session_label="synthetic-session")

    lower = prompt.lower()
    assert "type decision tree" in lower
    assert "pattern is a last-resort" in lower
    assert "chosen architecture/policy" in lower


def test_parse_llm_response_repairs_lazy_pattern_for_decisions():
    raw = '''{"proposals":[{"action":"save","content":"Decision: use Mem0 as the operational brain and keep OB1 as source history.","domains":["ai","systems"],"memory_type":"pattern","signal_strength":8,"memory_tier":"active","current_status":"active","source_ids":["row-1"],"reason":"durable"}]}'''

    result = parse_llm_response(raw)

    assert result.proposals[0].memory_type == "decision"
    assert "retyped_from_pattern_to_decision" in result.risk_notes


def test_parse_llm_response_repairs_relationship_pattern():
    raw = '''{"proposals":[{"action":"save","content":"A relationship dynamic with a close person shaped attachment, trust, and conflict patterns.","domains":["relationships","psychology"],"memory_type":"pattern","signal_strength":9,"memory_tier":"historical","current_status":"active","source_ids":["row-1"],"reason":"durable"}]}'''

    result = parse_llm_response(raw)

    assert result.proposals[0].memory_type == "relationship"


def test_metadata_quality_fails_when_pattern_dominates():
    from extractor import metadata_quality_report

    raw = '''{"proposals":[
      {"action":"save","content":"One generic pattern.","domains":["ai"],"memory_type":"pattern","signal_strength":7,"memory_tier":"active","current_status":"active","source_ids":["a"]},
      {"action":"save","content":"Second generic pattern.","domains":["ai"],"memory_type":"pattern","signal_strength":7,"memory_tier":"active","current_status":"active","source_ids":["b"]},
      {"action":"save","content":"A decision memory.","domains":["ai"],"memory_type":"decision","signal_strength":7,"memory_tier":"active","current_status":"active","source_ids":["c"]}
    ]}'''
    result = parse_llm_response(raw)

    quality = metadata_quality_report(result.proposals, max_pattern_ratio=0.6)

    assert quality["status"] == "failed"
    assert quality["pattern_count"] == 2


def test_metadata_quality_fails_generic_vocal_sludge_save():
    from extractor import metadata_quality_report

    proposal = normalize_proposal(
        {
            "action": "save",
            "content": "The lesson covered larynx height and resonance choices for a student's vocal exercise.",
            "domains": ["vocality"],
            "memory_type": "lesson",
            "signal_strength": 6,
            "memory_tier": "active",
            "current_status": "active",
            "source_ids": ["row-1"],
        }
    )

    quality = metadata_quality_report([proposal])

    assert quality["status"] == "failed"
    assert "generic_vocal_lesson_sludge" in quality["warnings"]


def test_metadata_quality_fails_generic_finance_save():
    from extractor import metadata_quality_report

    proposal = normalize_proposal(
        {
            "action": "save",
            "content": "The S&P 500 index tracks large-cap companies and dividend reinvestment compounds returns.",
            "domains": ["money_execution"],
            "memory_type": "lesson",
            "signal_strength": 5,
            "memory_tier": "active",
            "current_status": "active",
            "source_ids": ["row-1"],
        }
    )

    quality = metadata_quality_report([proposal])

    assert quality["status"] == "failed"
    assert "generic_business_or_finance_fact" in quality["warnings"]


def test_metadata_quality_flags_low_signal_protected_domain_without_blocking():
    from extractor import metadata_quality_report

    proposal = normalize_proposal(
        {
            "action": "save",
            "content": "A relationship memory with meaningful but under-scored attachment context.",
            "domains": ["relationships", "psychology"],
            "memory_type": "relationship",
            "signal_strength": 5,
            "memory_tier": "historical",
            "current_status": "active",
            "source_ids": ["row-1"],
        }
    )

    quality = metadata_quality_report([proposal])

    assert quality["status"] == "passed"
    assert "protected_domain_low_signal" in quality["warnings"]
