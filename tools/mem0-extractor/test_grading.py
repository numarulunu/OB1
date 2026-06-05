from grading import grade_text


def test_relationship_family_origin_gets_high_protection():
    text = (
        "A formative family-origin relationship event changed how the user handles trust, "
        "attachment, ambition, and conflict with close people."
    )

    grade = grade_text(text, source_id="row-1")

    assert grade.keep_candidate is True
    assert grade.signal_strength >= 8
    assert grade.memory_tier == "historical"
    assert {"family_origin", "relationships", "psychology"} <= set(grade.domains)
    assert grade.memory_type == "identity_shaping"


def test_generic_vocal_lesson_sludge_is_dropped():
    text = "The lesson covered pharyngeal vowel tuning and speaker diarization labels for a student exercise."

    grade = grade_text(text, source_id="row-2")

    assert grade.keep_candidate is False
    assert grade.drop_reason in {"generic_vocal_lesson_sludge", "transcript_process_junk"}


def test_own_voice_and_opera_context_is_preserved():
    text = "The user's own baritone fach, passaggio behavior, and opera role direction matter for audition planning."

    grade = grade_text(text, source_id="row-3")

    assert grade.keep_candidate is True
    assert grade.signal_strength >= 7
    assert "opera" in grade.domains
    assert "vocality" in grade.domains


def test_ai_systems_workflow_memory_is_candidate_but_not_psychology_weighted():
    text = "Decision: use Mem0 as the operational brain and keep OB1 as upstream source history for cleanup."

    grade = grade_text(text, source_id="row-4")

    assert grade.keep_candidate is True
    assert grade.signal_strength >= 6
    assert "ai" in grade.domains
    assert "systems" in grade.domains
    assert grade.memory_tier == "active"


def test_project_status_is_not_generic_pattern():
    text = "Current status: the Mem0 extractor apply planner is pending live dedupe validation before broad use."

    grade = grade_text(text, source_id="row-5")

    assert grade.keep_candidate is True
    assert grade.memory_type == "project_state"


def test_repeatable_pipeline_is_workflow():
    text = "The extraction workflow runs deterministic grading, LLM distillation, cached proposals, and guarded apply planning."

    grade = grade_text(text, source_id="row-6")

    assert grade.keep_candidate is True
    assert grade.memory_type == "workflow"


def test_emotionally_intense_identity_memory_gets_top_signal():
    text = (
        "A raw embarrassing relationship episode became identity-shaping and changed the user's trust, "
        "attachment, ambition, and conflict patterns."
    )

    grade = grade_text(text, source_id="row-7")

    assert grade.keep_candidate is True
    assert grade.signal_strength >= 9
    assert grade.memory_type == "identity_shaping"


def test_generic_finance_fact_without_user_context_is_dropped():
    text = "The S&P 500 index tracks large-cap companies and dividend reinvestment can compound returns over time."

    grade = grade_text(text, source_id="row-8")

    assert grade.keep_candidate is False
    assert grade.drop_reason == "generic_business_or_finance_fact"


def test_teacher_student_vocal_lesson_is_dropped_without_user_voice_or_method_signal():
    text = "The teacher explained larynx height and resonance choices for a student's vocal lesson exercise."

    grade = grade_text(text, source_id="row-9")

    assert grade.keep_candidate is False
    assert grade.drop_reason == "generic_vocal_lesson_sludge"


def test_reusable_vocality_method_is_preserved():
    text = "Vocality teaching method: compact the user's core rules for diagnosing passaggio and assigning exercises."

    grade = grade_text(text, source_id="row-10")

    assert grade.keep_candidate is True
    assert grade.signal_strength >= 7
    assert "vocality" in grade.domains
