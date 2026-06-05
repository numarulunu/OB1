from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_benchmark_approval_packet.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_benchmark_approval_packet", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_bundle(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "dataset": "locomo10",
                "run_id": "private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [50],
                "questions": [
                    {
                        "question_id": "q1",
                        "category": "temporal",
                        "question": "private raw question",
                        "ground_truth_answer": "private raw answer",
                        "retrieved_memories_by_top_k": {"50": [{"id": "benchmark:one", "memory": "private raw memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def args(bundle_path: Path, **overrides):
    defaults = {
        "input_bundle": str(bundle_path),
        "output": None,
        "run_output": "/opt/kontext/reports/judged-plans/locomo1-paid-run.json",
        "verification_output": "/opt/kontext/reports/judged-plans/locomo1-paid-verification.json",
        "cutoffs": "50",
        "max_questions": 1,
        "question_offset": 0,
        "answerer_model": "answer-model",
        "judge_model": "judge-model",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "max_cost_usd": 1.0,
        "mem0_target_accuracy": 0.918,
        "min_accuracy": 0.918,
        "mode": None,
        "judge_units_per_question": None,
        "temporal_fact_extraction": False,
        "beam_evidence_windows": False,
        "beam_answer_contract": False,
        "beam_structured_evidence": False,
        "beam_turn_neighborhoods": False,
        "beam_category_synthesis": False,
        "beam_state_reducer": False,
        "beam_direct_answer_bypass": False,
        "beam_broad_support_bypass": False,
        "beam_disable_corrected_bypass": False,
        "beam_strict_direct_bypass": False,
        "beam_verified_state_only": False,
        "beam_answer_candidate_selector": False,
        "beam_extractive_candidate": False,
        "beam_state_ledger": False,
        "beam_state_verifier": False,
        "beam_deterministic_state_resolver": False,
        "beam_focused_state_answer": False,
        "private_debug_output": None,
        "omit_temperature": False,
        "answer_max_memories": None,
        "answer_memory_max_chars": None,
        "answer_total_max_chars": None,
        "answer_input_usd_per_1m": 1,
        "answer_output_usd_per_1m": 1,
        "judge_input_usd_per_1m": 1,
        "judge_output_usd_per_1m": 1,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def test_packet_summarizes_paid_run_without_raw_text_or_secrets(tmp_path):
    module = load_module()
    bundle = tmp_path / "private.json"
    write_bundle(bundle)

    packet = module.build_packet(args(bundle))
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["runs_model_calls"] is False
    assert packet["approval_required"] is True
    assert packet["dataset"] == "locomo10"
    assert packet["selected_questions"] == 1
    assert packet["top_k_values"] == [50]
    assert packet["estimated_llm_calls"] == {"answer_calls": 1, "judge_calls": 1, "total_calls": 2}
    assert packet["estimated_cost_usd"]["total_usd"] == 0.00592
    assert packet["required_env_vars"] == ["OPENAI_API_KEY"]
    assert "--approve-cost" in packet["command_template"]
    assert "--verification-output" in packet["command_template"]
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered


def test_packet_includes_sanitized_typed_object_summary_when_flagged(tmp_path, monkeypatch):
    module = load_module()
    bundle = tmp_path / "private.json"
    write_bundle(bundle)
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["typed_object_status_summary"] = {
        "total": 2,
        "valid_schema": 2,
        "statuses": {"active": 1, "cancelled": 1},
        "event_relations": {"supersedes": 1, "cancels": 1},
        "raw_value_text": "private raw typed value",
    }
    bundle.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("KONTEXT_TYPED_OBJECT_SUMMARY", "1")

    packet = module.build_packet(args(bundle))
    rendered = json.dumps(packet, sort_keys=True)

    assert packet["typed_object_status_summary"] == {
        "total": 2,
        "valid_schema": 2,
        "statuses": {"active": 1, "cancelled": 1},
        "event_relations": {"cancels": 1, "supersedes": 1},
    }
    assert "private raw typed value" not in rendered


def test_packet_includes_question_offset_for_chunked_paid_diagnostics(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {"question_id": "q1", "question": "private one", "ground_truth_answer": "private answer one"},
                    {"question_id": "q2", "question": "private two", "ground_truth_answer": "private answer two"},
                    {"question_id": "q3", "question": "private three", "ground_truth_answer": "private answer three"},
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(args(bundle, max_questions=1, question_offset=2, cutoffs="20"))
    rendered = json.dumps(packet)

    assert packet["question_offset"] == 2
    assert packet["selected_questions"] == 1
    assert "--question-offset 2" in packet["command_template"]
    assert "private three" not in rendered
    assert "private answer" not in rendered


def test_packet_counts_beam_rubric_judge_units_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [50, 200],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [
                            {"description": "private criterion one"},
                            {"description": "private criterion two"},
                            {"description": "private criterion three"},
                        ],
                        "retrieved_memories_by_top_k": {"50": [{"memory": "private beam memory"}]},
                    },
                    {
                        "question_id": "beam-q2",
                        "question": "private beam question two",
                        "ground_truth_answer": "private beam answer two",
                        "rubric": [{"description": "private criterion four"}],
                        "retrieved_memories_by_top_k": {"50": [{"memory": "private beam memory two"}]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="50,200",
            max_questions=2,
            max_cost_usd=0.05,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["benchmark_mode"] == "beam-rubric"
    assert packet["judge_units_total"] == 4
    assert packet["judge_units_per_question"] == 2.0
    assert packet["estimated_llm_calls"] == {"answer_calls": 4, "judge_calls": 8, "total_calls": 12}
    assert "--judge-units-per-question 2.0" in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_temporal_extraction_flags_and_call_count(tmp_path):
    module = load_module()
    bundle = tmp_path / "private.json"
    write_bundle(bundle)

    packet = module.build_packet(
        args(
            bundle,
            temporal_fact_extraction=True,
            omit_temperature=True,
        )
    )

    assert packet["ok"] is True
    assert packet["temporal_fact_extraction"] is True
    assert packet["omit_temperature"] is True
    assert packet["estimated_llm_calls"] == {
        "answer_calls": 2,
        "judge_calls": 1,
        "temporal_fact_extraction_calls": 1,
        "total_calls": 3,
    }
    assert "--temporal-fact-extraction" in packet["command_template"]
    assert "--omit-temperature" in packet["command_template"]


def test_packet_includes_answer_prompt_caps_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            answer_max_memories=10,
            answer_memory_max_chars=1200,
            answer_total_max_chars=18000,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["answer_prompt_caps"] == {
        "answer_max_memories": 10,
        "answer_memory_max_chars": 1200,
        "answer_total_max_chars": 18000,
    }
    assert "--answer-max-memories 10" in packet["command_template"]
    assert "--answer-memory-max-chars 1200" in packet["command_template"]
    assert "--answer-total-max-chars 18000" in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_evidence_windows_flag_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            beam_evidence_windows=True,
            answer_memory_max_chars=1200,
            answer_total_max_chars=18000,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_evidence_windows"] is True
    assert "--beam-evidence-windows" in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_answer_contract_flag_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            beam_answer_contract=True,
            answer_memory_max_chars=1200,
            answer_total_max_chars=18000,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_answer_contract"] is True
    assert "--beam-answer-contract" in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_structured_evidence_and_private_debug_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            judge_units_per_question=3,
            beam_structured_evidence=True,
            private_debug_output="/opt/kontext/private/judged-diagnostics/beam-debug.json",
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_structured_evidence"] is True
    assert packet["private_debug_output"] == "/opt/kontext/private/judged-diagnostics/beam-debug.json"
    assert packet["estimated_llm_calls"]["beam_structured_evidence_calls"] == 1
    assert packet["estimated_llm_calls"]["judge_calls"] == 3
    assert "--beam-structured-evidence" in packet["command_template"]
    assert "--private-debug-output /opt/kontext/private/judged-diagnostics/beam-debug.json" in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_beam_helper_packet_defaults_to_bounded_memory_caps(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    write_bundle(bundle)

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            beam_structured_evidence=True,
            beam_answer_candidate_selector=True,
            beam_typed_projection_candidate=True,
        )
    )

    assert packet["ok"] is True
    assert packet["answer_prompt_caps"] == {
        "answer_max_memories": module.DEFAULT_PAID_MAX_MEMORIES,
        "answer_memory_max_chars": module.DEFAULT_PAID_MEMORY_MAX_CHARS,
        "answer_total_max_chars": module.DEFAULT_PAID_TOTAL_MAX_CHARS,
    }
    assert f"--answer-max-memories {module.DEFAULT_PAID_MAX_MEMORIES}" in packet["command_template"]
    assert f"--answer-memory-max-chars {module.DEFAULT_PAID_MEMORY_MAX_CHARS}" in packet["command_template"]
    assert f"--answer-total-max-chars {module.DEFAULT_PAID_TOTAL_MAX_CHARS}" in packet["command_template"]


def test_packet_includes_beam_turn_neighborhood_and_category_synthesis_flags_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            beam_turn_neighborhoods=True,
            beam_category_synthesis=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_turn_neighborhoods"] is True
    assert packet["beam_category_synthesis"] is True
    assert "--beam-turn-neighborhoods" in packet["command_template"]
    assert "--beam-category-synthesis" in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_state_reducer_flag_and_extra_call_cost_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            beam_state_reducer=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_state_reducer"] is True
    assert packet["estimated_llm_calls"]["answer_calls"] == 2
    assert packet["estimated_llm_calls"]["judge_calls"] == 1
    assert "--beam-state-reducer" in packet["command_template"]
    assert "OPENAI_API_KEY" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_direct_answer_bypass_and_reduces_answer_calls_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            beam_state_reducer=True,
            beam_direct_answer_bypass=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_state_reducer"] is True
    assert packet["beam_direct_answer_bypass"] is True
    assert packet["estimated_llm_calls"]["answer_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_direct_answer_bypass_skipped_answer_calls"] == 1
    assert packet["estimated_llm_calls"]["judge_calls"] == 1
    assert "--beam-direct-answer-bypass" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_broad_support_bypass_without_extra_cost(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "instruction_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_state_reducer=True,
            beam_direct_answer_bypass=True,
            beam_broad_support_bypass=True,
            beam_state_verifier=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_broad_support_bypass"] is True
    assert packet["estimated_llm_calls"]["answer_calls"] == 2
    assert packet["estimated_llm_calls"]["judge_calls"] == 1
    assert "--beam-broad-support-bypass" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_disable_corrected_bypass_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "instruction_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_state_reducer=True,
            beam_direct_answer_bypass=True,
            beam_broad_support_bypass=True,
            beam_disable_corrected_bypass=True,
            beam_state_verifier=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_disable_corrected_bypass"] is True
    assert "--beam-disable-corrected-bypass" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_strict_direct_bypass_without_extra_cost(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "preference_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_state_reducer=True,
            beam_direct_answer_bypass=True,
            beam_broad_support_bypass=True,
            beam_strict_direct_bypass=True,
            beam_state_verifier=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_strict_direct_bypass"] is True
    assert packet["estimated_llm_calls"]["answer_calls"] == 2
    assert "--beam-strict-direct-bypass" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_verified_state_only_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "instruction_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_state_reducer=True,
            beam_direct_answer_bypass=True,
            beam_verified_state_only=True,
            beam_state_verifier=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_verified_state_only"] is True
    assert "--beam-verified-state-only" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_answer_candidate_selector_with_conservative_cost(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "instruction_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_state_reducer=True,
            beam_direct_answer_bypass=True,
            beam_answer_candidate_selector=True,
            beam_state_verifier=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_answer_candidate_selector"] is True
    assert packet["estimated_llm_calls"]["beam_answer_candidate_selector_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_answer_candidate_extra_answer_calls"] == 1
    assert "--beam-answer-candidate-selector" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_extractive_candidate_with_conservative_cost(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "preference_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_answer_candidate_selector=True,
            beam_extractive_candidate=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_extractive_candidate"] is True
    assert packet["estimated_llm_calls"]["beam_answer_candidate_selector_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_answer_candidate_extra_answer_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_extractive_candidate_calls"] == 1
    assert "--beam-answer-candidate-selector" in packet["command_template"]
    assert "--beam-extractive-candidate" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_state_direct_candidate_without_extra_calls(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "instruction_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_answer_candidate_selector=True,
            beam_state_reducer=True,
            beam_state_direct_candidate=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_state_direct_candidate"] is True
    assert packet["estimated_llm_calls"]["beam_answer_candidate_selector_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_answer_candidate_extra_answer_calls"] == 1
    assert "beam_state_direct_candidate_calls" not in packet["estimated_llm_calls"]
    assert "--beam-answer-candidate-selector" in packet["command_template"]
    assert "--beam-state-direct-candidate" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_ranked_state_memory_candidate_with_conservative_cost(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "instruction_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_answer_candidate_selector=True,
            beam_ranked_state_memory_candidate=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_ranked_state_memory_candidate"] is True
    assert packet["estimated_llm_calls"]["beam_answer_candidate_selector_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_answer_candidate_extra_answer_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_ranked_state_memory_candidate_calls"] == 1
    assert "--beam-answer-candidate-selector" in packet["command_template"]
    assert "--beam-ranked-state-memory-candidate" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_ranked_state_memory_direct_bypass_flag(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "private-beam-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "preference_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_answer_candidate_selector=True,
            beam_ranked_state_memory_candidate=True,
            beam_ranked_state_memory_direct_bypass=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_ranked_state_memory_candidate"] is True
    assert packet["beam_ranked_state_memory_direct_bypass"] is True
    assert "--beam-ranked-state-memory-candidate" in packet["command_template"]
    assert "--beam-ranked-state-memory-direct-bypass" in packet["command_template"]
    assert packet["estimated_llm_calls"]["beam_ranked_state_memory_candidate_calls"] == 1
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_retrieved_excerpt_direct_bypass_flag(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "private-beam-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "instruction_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_retrieved_excerpt_direct_bypass=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_retrieved_excerpt_direct_bypass"] is True
    assert "--beam-retrieved-excerpt-direct-bypass" in packet["command_template"]
    assert "beam_retrieved_excerpt_direct_bypass_calls" not in packet["estimated_llm_calls"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_typed_projection_candidate_without_extra_candidate_call(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "private-beam-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "preference_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_answer_candidate_selector=True,
            beam_typed_projection_candidate=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_typed_projection_candidate"] is True
    assert "--beam-typed-projection-candidate" in packet["command_template"]
    assert packet["estimated_llm_calls"]["beam_answer_candidate_selector_calls"] == 1
    assert "beam_typed_projection_candidate_calls" not in packet["estimated_llm_calls"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_memory_atomizer_with_conservative_cost(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "category": "preference_following",
                        "question": "private beam question",
                        "ground_truth_answer": "private beam answer",
                        "rubric": [{"description": "private criterion"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            beam_memory_atomizer=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_memory_atomizer"] is True
    assert packet["estimated_llm_calls"]["beam_memory_atomizer_calls"] == 1
    assert packet["estimated_llm_calls"]["answer_calls"] == 2
    assert "--beam-memory-atomizer" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_state_ledger_and_verifier_with_conservative_calls(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            beam_structured_evidence=True,
            beam_state_reducer=True,
            beam_direct_answer_bypass=True,
            beam_state_ledger=True,
            beam_state_verifier=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_state_ledger"] is True
    assert packet["beam_state_verifier"] is True
    assert packet["estimated_llm_calls"]["beam_structured_evidence_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_state_reducer_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_state_verifier_calls"] == 1
    assert packet["estimated_llm_calls"]["beam_direct_answer_bypass_skipped_answer_calls"] == 1
    assert packet["estimated_llm_calls"]["answer_calls"] == 3
    assert "--beam-state-ledger" in packet["command_template"]
    assert "--beam-state-verifier" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_deterministic_state_resolver_without_extra_llm_cost(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            beam_direct_answer_bypass=True,
            beam_state_ledger=True,
            beam_deterministic_state_resolver=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_deterministic_state_resolver"] is True
    assert packet["estimated_llm_calls"].get("beam_deterministic_state_resolver_calls") == 1
    assert packet["estimated_llm_calls"]["answer_calls"] == 0
    assert packet["estimated_llm_calls"]["judge_calls"] == 1
    assert "--beam-deterministic-state-resolver" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_beam_focused_state_answer_with_conservative_cost(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "beam-q1",
                        "question": "private beam question one",
                        "ground_truth_answer": "private beam answer one",
                        "rubric": [{"description": "private criterion one"}],
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private beam memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            mode="beam-rubric",
            cutoffs="20",
            max_questions=1,
            beam_state_reducer=True,
            beam_direct_answer_bypass=True,
            beam_state_ledger=True,
            beam_state_verifier=True,
            beam_focused_state_answer=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["beam_focused_state_answer"] is True
    assert packet["estimated_llm_calls"]["beam_focused_state_answer_calls"] == 1
    assert "--beam-focused-state-answer" in packet["command_template"]
    assert "sk-" not in packet["command_template"]
    assert "private beam question" not in rendered
    assert "private beam answer" not in rendered
    assert "private criterion" not in rendered
    assert "private beam memory" not in rendered


def test_packet_includes_longmemeval_packaging_flags_without_raw_text(tmp_path):
    module = load_module()
    bundle = tmp_path / "long-private.json"
    bundle.write_text(
        json.dumps(
            {
                "dataset": "longmemeval_s",
                "run_id": "long-private-slice",
                "mode": "private-judged-input-bundle",
                "runs_model_calls": False,
                "contains_raw_benchmark_text": True,
                "contains_live_user_memory": False,
                "top_k_values": [20],
                "questions": [
                    {
                        "question_id": "long-q1",
                        "question": "private long question one",
                        "ground_truth_answer": "private long answer one",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private long memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    packet = module.build_packet(
        args(
            bundle,
            cutoffs="20",
            max_questions=1,
            longmemeval_evidence_windows=True,
            longmemeval_structured_evidence=True,
        )
    )
    rendered = json.dumps(packet)

    assert packet["ok"] is True
    assert packet["longmemeval_evidence_windows"] is True
    assert packet["longmemeval_structured_evidence"] is True
    assert packet["estimated_llm_calls"]["longmemeval_structured_evidence_calls"] == 1
    assert "--longmemeval-evidence-windows" in packet["command_template"]
    assert "--longmemeval-structured-evidence" in packet["command_template"]
    assert "private long question" not in rendered
    assert "private long answer" not in rendered
    assert "private long memory" not in rendered


def test_packet_rejects_private_debug_path_outside_kontext_private(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    write_bundle(bundle)

    packet = module.build_packet(args(bundle, private_debug_output="/tmp/beam-debug.json"))

    assert packet["ok"] is False
    assert packet["approval_required"] is False
    assert packet["reason"] == "private debug output must be under /opt/kontext/private"


def test_packet_rejects_private_debug_path_that_escapes_with_parent_segments(tmp_path):
    module = load_module()
    bundle = tmp_path / "beam-private.json"
    write_bundle(bundle)

    packet = module.build_packet(args(bundle, private_debug_output="/opt/kontext/private/../reports/beam-debug.json"))

    assert packet["ok"] is False
    assert packet["approval_required"] is False
    assert packet["reason"] == "private debug output must be under /opt/kontext/private"


def test_packet_rejects_existing_paid_output_paths(tmp_path):
    module = load_module()
    bundle = tmp_path / "private.json"
    run_output = tmp_path / "paid-run.json"
    verification_output = tmp_path / "paid-verification.json"
    write_bundle(bundle)
    run_output.write_text("existing", encoding="utf-8")

    packet = module.build_packet(
        args(
            bundle,
            run_output=str(run_output),
            verification_output=str(verification_output),
        )
    )

    assert packet["ok"] is False
    assert packet["approval_required"] is False
    assert packet["reason"] == "run output path already exists"


def test_packet_rejects_estimated_cost_above_approved_cap(tmp_path):
    module = load_module()
    bundle = tmp_path / "private.json"
    write_bundle(bundle)

    packet = module.build_packet(args(bundle, max_cost_usd=0.001))

    assert packet["ok"] is False
    assert packet["approval_required"] is False
    assert packet["reason"] == "estimated cost exceeds max_cost_usd"
    assert packet["estimated_cost_usd"]["total_usd"] == 0.00592
    assert packet["max_cost_usd"] == 0.001


def test_packet_rejects_non_positive_pricing_inputs(tmp_path):
    module = load_module()
    bundle = tmp_path / "private.json"
    write_bundle(bundle)

    packet = module.build_packet(args(bundle, answer_input_usd_per_1m=0))

    assert packet["ok"] is False
    assert packet["approval_required"] is False
    assert packet["reason"] == "answer_input_usd_per_1m must be positive"


def test_packet_command_pins_min_questions_to_selected_slice(tmp_path):
    module = load_module()
    bundle = tmp_path / "private.json"
    write_bundle(bundle)

    packet = module.build_packet(args(bundle, max_questions=1))

    assert packet["ok"] is True
    assert "--verify-expected-questions 1" in packet["command_template"]
    assert "--verify-min-questions 1" in packet["command_template"]


def test_packet_includes_guarded_wrapper_template(tmp_path):
    module = load_module()
    bundle = tmp_path / "private.json"
    write_bundle(bundle)

    packet = module.build_packet(args(bundle))
    wrapper = packet["command_wrapper_template"]

    assert "set -euo pipefail" in wrapper
    assert "OPENAI_API_KEY" in wrapper
    assert "RUN_OUTPUT=" in wrapper
    assert "VERIFICATION_OUTPUT=" in wrapper
    assert "[ -e \"$RUN_OUTPUT\" ]" in wrapper
    assert "[ -e \"$VERIFICATION_OUTPUT\" ]" in wrapper
    assert "/v1/models" in wrapper
    assert packet["command_template"] in wrapper
    assert "sk-" not in wrapper


def test_packet_refuses_live_user_memory_bundle(tmp_path):
    module = load_module()
    bundle = tmp_path / "private.json"
    write_bundle(bundle)
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["contains_live_user_memory"] = True
    bundle.write_text(json.dumps(payload), encoding="utf-8")

    result = module.build_packet(args(bundle))

    assert result["ok"] is False
    assert result["approval_required"] is False
    assert result["reason"] == "input bundle contains live/user memory"


def test_cli_writes_packet(tmp_path, capsys):
    module = load_module()
    bundle = tmp_path / "private.json"
    output = tmp_path / "packet.json"
    write_bundle(bundle)

    code = module.main(
        [
            "--input-bundle",
            str(bundle),
            "--output",
            str(output),
            "--run-output",
            "/tmp/run.json",
            "--verification-output",
            "/tmp/verify.json",
            "--cutoffs",
            "50",
            "--max-questions",
            "1",
            "--answerer-model",
            "answer-model",
            "--judge-model",
            "judge-model",
            "--api-key-env",
            "OPENAI_API_KEY",
            "--max-cost-usd",
            "0.01",
            "--mem0-target-accuracy",
            "0.918",
            "--answer-input-usd-per-1m",
            "1",
            "--answer-output-usd-per-1m",
            "1",
            "--judge-input-usd-per-1m",
            "1",
            "--judge-output-usd-per-1m",
            "1",
        ]
    )

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_refuses_to_overwrite_existing_packet_output(tmp_path, capsys):
    module = load_module()
    bundle = tmp_path / "private.json"
    output = tmp_path / "packet.json"
    write_bundle(bundle)
    output.write_text("existing packet", encoding="utf-8")

    code = module.main(
        [
            "--input-bundle",
            str(bundle),
            "--output",
            str(output),
            "--run-output",
            "/tmp/run.json",
            "--verification-output",
            "/tmp/verify.json",
            "--cutoffs",
            "50",
            "--max-questions",
            "1",
            "--answerer-model",
            "answer-model",
            "--judge-model",
            "judge-model",
            "--api-key-env",
            "OPENAI_API_KEY",
            "--max-cost-usd",
            "0.01",
            "--mem0-target-accuracy",
            "0.918",
            "--answer-input-usd-per-1m",
            "1",
            "--answer-output-usd-per-1m",
            "1",
            "--judge-input-usd-per-1m",
            "1",
            "--judge-output-usd-per-1m",
            "1",
        ]
    )

    assert code == 2
    assert output.read_text(encoding="utf-8") == "existing packet"
    assert json.loads(capsys.readouterr().out)["reason"] == "approval packet output path already exists"
