from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "beam_failure_audit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("beam_failure_audit", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def assert_public_report_has_no_raw_payload(value):
    forbidden_keys = {
        "question",
        "ground_truth_answer",
        "retrieved_memories_by_top_k",
        "memory",
        "messages",
        "content",
        "conversation",
        "generated_answer",
        "judge_response",
        "judge_responses",
        "structured_evidence",
        "resolved_state",
        "beam_deterministic_state",
    }

    def walk(node):
        if isinstance(node, dict):
            assert not (set(node) & forbidden_keys)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)


def test_beam_failure_audit_classifies_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "top_k_values": [20],
        "questions": [
            {
                "question_id": "beam-q1",
                "category": "instruction_following",
                "question": "private raw question one",
                "ground_truth_answer": "private raw answer one",
                "first_hit_top_k": 3,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory one"}]},
            },
            {
                "question_id": "beam-q2",
                "category": "preference_following",
                "question": "private raw question two",
                "ground_truth_answer": "private raw answer two",
                "first_hit_top_k": None,
                "retrieved_memories_by_top_k": {"20": []},
            },
            {
                "question_id": "beam-q3",
                "category": "knowledge_update",
                "question": "private raw question three",
                "ground_truth_answer": "private raw answer three",
                "first_hit_top_k": 5,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory three"}]},
            },
        ],
    }
    run = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "summary": {"20": {"passed": 1, "total": 3, "accuracy": 0.3333}},
        "questions": [
            {
                "question_id": "beam-q1",
                "category": "instruction_following",
                "question_hash": "hash-q1",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_evidence_windows": True,
                        "beam_structured_evidence": True,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                        "judge_scores": [0.1, 0.2, 0.3],
                    }
                },
            },
            {
                "question_id": "beam-q2",
                "category": "preference_following",
                "question_hash": "hash-q2",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.0,
                        "memories_evaluated": 0,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                        "judge_scores": [0.0, 0.0, 0.0],
                    }
                },
            },
            {
                "question_id": "beam-q3",
                "category": "knowledge_update",
                "question_hash": "hash-q3",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.6,
                        "memories_evaluated": 20,
                        "beam_evidence_windows": True,
                        "beam_structured_evidence": True,
                        "judge_count": 3,
                        "judge_pass_count": 1,
                        "judge_scores": [0.2, 0.6, 0.6],
                    }
                },
            },
        ],
    }
    verification = {"ok": False, "summary": {"cutoff": 20, "passed": 1, "total": 3, "accuracy": 0.3333}}
    debug = {
        "records": [
            {
                "question_id": "beam-q1",
                "generated_answer": "private generated answer one with Evidence: label",
                "structured_evidence": "candidate_facts: private raw memory one",
                "judge_responses": ["private judge response"],
            },
            {
                "question_id": "beam-q3",
                "generated_answer": "private generated answer three",
                "structured_evidence": "candidate_facts: private raw memory three",
                "judge_responses": ["private split judge response"],
            },
        ]
    }

    report = module.build_audit_report(bundle, run, verification, debug=debug, cutoff=20)
    rendered = json.dumps(report)

    assert report["ok"] is True
    assert report["failures_total"] == 3
    assert report["failure_classes"]["answer_format_or_task_mismatch"] == 1
    assert report["failure_classes"]["evidence_missing_top20"] == 1
    assert report["failure_classes"]["judge_instability"] == 1
    assert report["category_breakdown"]["instruction_following"]["failed"] == 1
    assert report["gates"]["raw_payload"]["hit_count"] == 0
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private generated answer" not in rendered
    assert "private judge response" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_reports_sanitized_term_coverage_without_terms(tmp_path):
    module = load_module()
    bundle = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "top_k_values": [20],
        "questions": [
            {
                "question_id": "beam-q1",
                "category": "information_extraction",
                "question": "private raw question",
                "ground_truth_answer": "alpha beta gamma delta",
                "first_hit_top_k": 1,
                "retrieved_memories_by_top_k": {"20": [{"memory": "alpha beta gamma delta hidden source"}]},
            }
        ],
    }
    run = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "summary": {"20": {"passed": 0, "total": 1, "accuracy": 0.0}},
        "questions": [
            {
                "question_id": "beam-q1",
                "category": "information_extraction",
                "question_hash": "hash-q1",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_structured_evidence": True,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            }
        ],
    }
    debug = {
        "records": [
            {
                "question_id": "beam-q1",
                "generated_answer": "alpha only",
                "structured_evidence": "alpha beta gamma delta hidden source",
                "judge_responses": ["private judge response"],
            }
        ]
    }

    report = module.build_audit_report(bundle, run, verification={}, debug=debug, cutoff=20)
    rendered = json.dumps(report)
    coverage = report["coverage_summary"]
    row = report["failed_questions"][0]["term_coverage"]

    assert coverage["synthesis_loss"] == 1
    assert row["ground_truth_term_count"] == 4
    assert row["retrieved_overlap_ratio"] == 1.0
    assert row["structured_overlap_ratio"] == 1.0
    assert row["generated_overlap_ratio"] == 0.25
    assert row["coverage_class"] == "synthesis_loss"
    assert "alpha" not in rendered
    assert "beta" not in rendered
    assert "private raw question" not in rendered
    assert "private judge response" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_keeps_safe_candidate_and_resolver_diagnostics(tmp_path):
    module = load_module()
    bundle = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "top_k_values": [20],
        "questions": [
            {
                "question_id": "beam-q1",
                "category": "knowledge_update",
                "question": "private raw question",
                "ground_truth_answer": "private raw answer",
                "first_hit_top_k": 1,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory"}]},
            }
        ],
    }
    run = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "summary": {"20": {"passed": 0, "total": 1, "accuracy": 0.0}},
        "questions": [
            {
                "question_id": "beam-q1",
                "category": "knowledge_update",
                "question_hash": "hash-q1",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.0,
                        "memories_evaluated": 20,
                        "beam_direct_span_candidate": True,
                        "beam_answer_candidate_count": 2,
                        "beam_answer_selected_candidate_index": 1,
                        "beam_answer_candidate_summaries": [
                            {
                                "id": "candidate_1",
                                "kind": "normal",
                                "answer_hash": "hash-normal",
                                "answer_chars": 42,
                                "question_overlap_terms": 2,
                                "question_term_count": 5,
                                "question_overlap_ratio": 0.4,
                                "specific_question_overlap_terms": 1,
                                "specific_question_term_count": 2,
                                "specific_question_overlap_ratio": 0.5,
                                "ground_truth_overlap_terms": 0,
                                "ground_truth_term_count": 3,
                                "ground_truth_overlap_ratio": 0.0,
                                "state_marker_present": False,
                            },
                            {
                                "id": "candidate_2",
                                "kind": "direct_span",
                                "answer_hash": "hash-direct",
                                "answer_chars": 120,
                                "question_overlap_terms": 4,
                                "question_term_count": 5,
                                "question_overlap_ratio": 0.8,
                                "specific_question_overlap_terms": 2,
                                "specific_question_term_count": 2,
                                "specific_question_overlap_ratio": 1.0,
                                "ground_truth_overlap_terms": 3,
                                "ground_truth_term_count": 3,
                                "ground_truth_overlap_ratio": 1.0,
                                "state_marker_present": True,
                            },
                        ],
                        "beam_state_resolver_status": "ambiguous",
                        "beam_state_resolution_rule": "latest_knowledge_update",
                        "supporting_event_hashes": ["hash-a", "hash-b"],
                        "beam_state_resolver_supporting_event_hashes": ["hash-a"],
                        "beam_state_verifier_supporting_event_hashes": ["hash-b"],
                        "beam_direct_span_candidate_fused": True,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            }
        ],
    }

    report = module.build_audit_report(bundle, run, verification={}, debug={}, cutoff=20)
    rendered = json.dumps(report)
    row = report["failed_questions"][0]
    direct = row["candidate_summaries"][1]

    assert row["failure_class"] == "beam_direct_span_candidate_not_selected"
    assert row["beam_direct_span_candidate_fused"] is True
    assert row["candidate_summaries"][0]["index"] == 1
    assert row["candidate_summaries"][0]["selected"] is True
    assert direct["index"] == 2
    assert direct["selected"] is False
    assert direct["kind"] == "direct_span"
    assert direct["specific_question_overlap_ratio"] == 1.0
    assert direct["ground_truth_overlap_terms"] == 3
    assert direct["state_marker_present"] is True
    assert row["beam_state_resolver_status"] == "ambiguous"
    assert row["beam_state_resolution_rule"] == "latest_knowledge_update"
    assert row["supporting_event_hash_count"] == 2
    assert row["beam_state_resolver_supporting_event_hash_count"] == 1
    assert row["beam_state_verifier_supporting_event_hash_count"] == 1
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_typed_object_failures_when_flagged(monkeypatch):
    module = load_module()
    monkeypatch.setenv("KONTEXT_TYPED_OBJECT_SUMMARY", "1")
    bundle = {
        "dataset": "beam_1M",
        "run_id": "typed-private-slice",
        "top_k_values": [20],
        "questions": [
            {
                "question_id": question_id,
                "category": "preference_following",
                "question": f"private typed question {question_id}",
                "ground_truth_answer": f"private typed answer {question_id}",
                "first_hit_top_k": 1,
                "retrieved_memories_by_top_k": {"20": [{"memory": f"private typed memory {question_id}"}]},
            }
            for question_id in ["missing", "schema", "status", "relation", "value"]
        ],
    }
    cutoff_results = {
        "missing": {"typed_state_required": True},
        "schema": {
            "typed_state_required": True,
            "typed_state_v2": {"schema_version": "bad", "status": "active", "event_relation": "supports"},
        },
        "status": {
            "expected_typed_state_v2": {"status": "active", "event_relation": "supports"},
            "typed_state_v2": {"schema_version": "typed-state-v2", "status": "cancelled", "event_relation": "supports"},
        },
        "relation": {
            "expected_typed_state_v2": {"status": "active", "event_relation": "supersedes"},
            "typed_state_v2": {"schema_version": "typed-state-v2", "status": "active", "event_relation": "supports"},
        },
        "value": {
            "expected_typed_state_v2": {"status": "active", "event_relation": "supports", "value_hash": "expected"},
            "typed_state_v2": {
                "schema_version": "typed-state-v2",
                "status": "active",
                "event_relation": "supports",
                "value_hash": "actual",
            },
        },
    }
    run = {
        "dataset": "beam_1M",
        "run_id": "typed-private-slice",
        "summary": {"20": {"passed": 0, "total": 5, "accuracy": 0.0}},
        "questions": [
            {
                "question_id": question_id,
                "category": "preference_following",
                "question_hash": f"hash-{question_id}",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.1,
                        "memories_evaluated": 20,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                        **result,
                    }
                },
            }
            for question_id, result in cutoff_results.items()
        ],
    }

    report = module.build_audit_report(bundle, run, verification={}, debug={}, cutoff=20)
    rendered = json.dumps(report)

    assert report["failure_classes"]["typed_object_missing"] == 1
    assert report["failure_classes"]["typed_object_invalid_schema"] == 1
    assert report["failure_classes"]["typed_object_status_mismatch"] == 1
    assert report["failure_classes"]["typed_object_relation_mismatch"] == 1
    assert report["failure_classes"]["typed_object_value_mismatch"] == 1
    assert "private typed question" not in rendered
    assert "private typed memory" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_state_reducer_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "top_k_values": [20],
        "questions": [
            {
                "question_id": "empty",
                "category": "instruction_following",
                "question": "private raw question empty",
                "ground_truth_answer": "private raw answer empty",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory empty"}]},
            },
            {
                "question_id": "invalid",
                "category": "preference_following",
                "question": "private raw question invalid",
                "ground_truth_answer": "private raw answer invalid",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory invalid"}]},
            },
            {
                "question_id": "nodirect",
                "category": "knowledge_update",
                "question": "private raw question nodirect",
                "ground_truth_answer": "private raw answer nodirect",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory nodirect"}]},
            },
            {
                "question_id": "wrong",
                "category": "instruction_following",
                "question": "private raw question wrong",
                "ground_truth_answer": "private raw answer wrong",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory wrong"}]},
            },
        ],
    }
    run_questions = []
    for question_id, status, direct in [
        ("empty", "empty", ""),
        ("invalid", "invalid_json", ""),
        ("nodirect", "ok", ""),
        ("wrong", "ok", "private direct answer"),
    ]:
        run_questions.append(
            {
                "question_id": question_id,
                "category": "instruction_following",
                "question_hash": f"hash-{question_id}",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_state_reducer": True,
                        "beam_state_parser_status": status,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                        "judge_scores": [0.2, 0.2, 0.2],
                    }
                },
            }
        )
        if direct:
            run_questions[-1]["cutoff_results"]["20"]["resolved_state_hash"] = "hash-direct"
    run = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "summary": {"20": {"passed": 0, "total": 4, "accuracy": 0.0}},
        "questions": run_questions,
    }
    debug = {
        "records": [
            {"question_id": "empty", "resolved_state": {"parser_status": "empty", "direct_answer": ""}},
            {"question_id": "invalid", "resolved_state": {"parser_status": "invalid_json", "direct_answer": ""}},
            {"question_id": "nodirect", "resolved_state": {"parser_status": "ok", "direct_answer": ""}},
            {"question_id": "wrong", "resolved_state": {"parser_status": "ok", "direct_answer": "private direct answer"}},
        ]
    }

    report = module.build_audit_report(bundle, run, verification={}, debug=debug, cutoff=20)
    rendered = json.dumps(report)

    assert report["failure_classes"]["state_reducer_empty"] == 1
    assert report["failure_classes"]["state_reducer_invalid_json"] == 1
    assert report["failure_classes"]["state_reducer_no_direct_answer"] == 1
    assert report["failure_classes"]["answer_synthesis_wrong_after_state"] == 1
    assert report["gates"]["raw_payload"]["hit_count"] == 0
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private direct answer" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_direct_answer_bypass_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "top_k_values": [20],
        "questions": [
            {
                "question_id": "used",
                "category": "instruction_following",
                "question": "private raw question used",
                "ground_truth_answer": "private raw answer used",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory used"}]},
            },
            {
                "question_id": "notused",
                "category": "preference_following",
                "question": "private raw question notused",
                "ground_truth_answer": "private raw answer notused",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory notused"}]},
            },
            {
                "question_id": "focused",
                "category": "instruction_following",
                "question": "private raw question focused",
                "ground_truth_answer": "private raw answer focused",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory focused"}]},
            },
        ],
    }
    run = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "summary": {"20": {"passed": 0, "total": 2, "accuracy": 0.0}},
        "questions": [
            {
                "question_id": "used",
                "category": "instruction_following",
                "question_hash": "hash-used",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_state_reducer": True,
                        "beam_state_parser_status": "ok",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": True,
                        "direct_answer_hash": "hash-direct",
                        "judge_count": 3,
                        "judge_pass_count": 0,
                        "judge_scores": [0.2, 0.2, 0.2],
                    }
                },
            },
            {
                "question_id": "notused",
                "category": "preference_following",
                "question_hash": "hash-notused",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_state_reducer": True,
                        "beam_state_parser_status": "ok",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": False,
                        "beam_direct_answer_bypass_reason": "missing_supporting_event_hashes",
                        "judge_count": 3,
                        "judge_pass_count": 0,
                        "judge_scores": [0.2, 0.2, 0.2],
                    }
                },
            },
            {
                "question_id": "focused",
                "category": "instruction_following",
                "question_hash": "hash-focused",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": False,
                        "beam_focused_state_answer": True,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                        "judge_scores": [0.2, 0.2, 0.2],
                    }
                },
            },
        ],
    }
    debug = {
        "records": [
            {"question_id": "used", "resolved_state": {"parser_status": "ok", "direct_answer": "private direct answer"}},
            {"question_id": "notused", "resolved_state": {"parser_status": "ok", "direct_answer": "private fallback answer"}},
        ]
    }

    report = module.build_audit_report(bundle, run, verification={}, debug=debug, cutoff=20)
    rendered = json.dumps(report)

    assert report["failure_classes"]["direct_answer_bypass_wrong"] == 1
    assert report["failure_classes"]["direct_answer_bypass_not_used"] == 1
    assert report["failure_classes"]["focused_state_answer_wrong"] == 1
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private direct answer" not in rendered
    assert "private fallback answer" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_state_verifier_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "top_k_values": [20],
        "questions": [
            {
                "question_id": "corrected",
                "category": "preference_following",
                "question": "private raw question corrected",
                "ground_truth_answer": "private raw answer corrected",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory corrected"}]},
            },
            {
                "question_id": "rejected",
                "category": "instruction_following",
                "question": "private raw question rejected",
                "ground_truth_answer": "private raw answer rejected",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory rejected"}]},
            },
            {
                "question_id": "uncertain",
                "category": "instruction_following",
                "question": "private raw question uncertain",
                "ground_truth_answer": "private raw answer uncertain",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory uncertain"}]},
            },
            {
                "question_id": "support-mismatch",
                "category": "instruction_following",
                "question": "private raw question mismatch",
                "ground_truth_answer": "private raw answer mismatch",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory mismatch"}]},
            },
        ],
    }
    run = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "summary": {"20": {"passed": 0, "total": 4, "accuracy": 0.0}},
        "questions": [
            {
                "question_id": "corrected",
                "category": "preference_following",
                "question_hash": "hash-corrected",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_state_verifier": True,
                        "beam_state_verifier_status": "corrected",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": True,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            },
            {
                "question_id": "rejected",
                "category": "instruction_following",
                "question_hash": "hash-rejected",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_state_verifier": True,
                        "beam_state_verifier_status": "rejected",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": False,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            },
            {
                "question_id": "uncertain",
                "category": "instruction_following",
                "question_hash": "hash-uncertain",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_state_verifier": True,
                        "beam_state_verifier_status": "uncertain",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": False,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            },
            {
                "question_id": "support-mismatch",
                "category": "instruction_following",
                "question_hash": "hash-support-mismatch",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_state_verifier": True,
                        "beam_state_verifier_status": "valid",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": False,
                        "beam_direct_answer_bypass_reason": "verifier_valid_support_mismatch",
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            },
        ],
    }
    debug = {
        "records": [
            {
                "question_id": "corrected",
                "beam_state_verifier_response": "private verifier corrected text",
                "beam_state_verifier": {"verdict": "corrected", "corrected_direct_answer": "private corrected answer"},
            },
            {
                "question_id": "rejected",
                "beam_state_verifier_response": "private verifier rejected text",
                "beam_state_verifier": {"verdict": "rejected", "corrected_direct_answer": "private rejected answer"},
            },
            {
                "question_id": "uncertain",
                "beam_state_verifier_response": "private verifier uncertain text",
                "beam_state_verifier": {"verdict": "uncertain", "corrected_direct_answer": "private uncertain answer"},
            },
            {
                "question_id": "support-mismatch",
                "beam_state_verifier_response": "private verifier mismatch text",
                "beam_state_verifier": {"verdict": "valid", "corrected_direct_answer": "private mismatch answer"},
            },
        ]
    }

    report = module.build_audit_report(bundle, run, verification={}, debug=debug, cutoff=20)
    rendered = json.dumps(report)

    assert report["failure_classes"]["state_verifier_corrected_wrong"] == 1
    assert report["failure_classes"]["state_verifier_rejected"] == 1
    assert report["failure_classes"]["state_verifier_uncertain"] == 1
    assert report["failure_classes"]["state_verifier_support_mismatch"] == 1
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private verifier" not in rendered
    assert "private corrected answer" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_deterministic_resolver_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "top_k_values": [20],
        "questions": [
            {
                "question_id": "wrong",
                "category": "preference_following",
                "question": "private raw question wrong",
                "ground_truth_answer": "private raw answer wrong",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory wrong"}]},
            },
            {
                "question_id": "ambiguous",
                "category": "instruction_following",
                "question": "private raw question ambiguous",
                "ground_truth_answer": "private raw answer ambiguous",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory ambiguous"}]},
            },
            {
                "question_id": "nosupport",
                "category": "knowledge_update",
                "question": "private raw question nosupport",
                "ground_truth_answer": "private raw answer nosupport",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory nosupport"}]},
            },
            {
                "question_id": "notused",
                "category": "preference_following",
                "question": "private raw question notused",
                "ground_truth_answer": "private raw answer notused",
                "first_hit_top_k": 2,
                "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory notused"}]},
            },
        ],
    }
    run = {
        "dataset": "beam_1M",
        "run_id": "private-beam-slice",
        "summary": {"20": {"passed": 0, "total": 4, "accuracy": 0.0}},
        "questions": [
            {
                "question_id": "wrong",
                "category": "preference_following",
                "question_hash": "hash-wrong",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_deterministic_state_resolver": True,
                        "beam_state_resolver_status": "resolved",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": True,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            },
            {
                "question_id": "ambiguous",
                "category": "instruction_following",
                "question_hash": "hash-ambiguous",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_deterministic_state_resolver": True,
                        "beam_state_resolver_status": "ambiguous",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": False,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            },
            {
                "question_id": "nosupport",
                "category": "knowledge_update",
                "question_hash": "hash-nosupport",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_deterministic_state_resolver": True,
                        "beam_state_resolver_status": "no_support",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": False,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            },
            {
                "question_id": "notused",
                "category": "preference_following",
                "question_hash": "hash-notused",
                "cutoff_results": {
                    "20": {
                        "judgment": "FAIL",
                        "score": 0.2,
                        "memories_evaluated": 20,
                        "beam_deterministic_state_resolver": True,
                        "beam_state_resolver_status": "resolved",
                        "beam_direct_answer_bypass": True,
                        "beam_direct_answer_used": False,
                        "judge_count": 3,
                        "judge_pass_count": 0,
                    }
                },
            },
        ],
    }
    debug = {
        "records": [
            {"question_id": "wrong", "beam_deterministic_state": {"direct_answer": "private wrong direct"}},
            {"question_id": "ambiguous", "beam_deterministic_state": {"uncertainty": "private ambiguous state"}},
            {"question_id": "nosupport", "beam_deterministic_state": {"uncertainty": "private no support"}},
            {"question_id": "notused", "beam_deterministic_state": {"direct_answer": "private not used"}},
        ]
    }

    report = module.build_audit_report(bundle, run, verification={}, debug=debug, cutoff=20)
    rendered = json.dumps(report)

    assert report["failure_classes"]["deterministic_resolver_wrong"] == 1
    assert report["failure_classes"]["deterministic_resolver_ambiguous"] == 1
    assert report["failure_classes"]["deterministic_resolver_no_support"] == 1
    assert report["failure_classes"]["deterministic_resolver_not_used"] == 1


def test_beam_failure_audit_classifies_extractive_candidate_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle_path = tmp_path / "bundle.json"
    run_path = tmp_path / "run.json"
    debug_path = tmp_path / "debug.json"

    bundle_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private",
                "questions": [
                    {
                        "question_id": "extractive-wrong",
                        "category": "preference_following",
                        "question": "private question",
                        "ground_truth_answer": "private answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private answer evidence"}]},
                    },
                    {
                        "question_id": "extractive-not-selected",
                        "category": "instruction_following",
                        "question": "private question",
                        "ground_truth_answer": "private answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private answer evidence"}]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private",
                "summary": {"20": {"total": 2, "passed": 0, "avg_score": 0.0}},
                "questions": [
                    {
                        "question_id": "extractive-wrong",
                        "category": "preference_following",
                        "question_hash": "hash1",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_extractive_candidate": True,
                                "beam_answer_candidate_count": 3,
                                "beam_answer_selected_candidate_index": 3,
                            }
                        },
                    },
                    {
                        "question_id": "extractive-not-selected",
                        "category": "instruction_following",
                        "question_hash": "hash2",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_extractive_candidate": True,
                                "beam_answer_candidate_count": 3,
                                "beam_answer_selected_candidate_index": 1,
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_path.write_text(
        json.dumps(
            {
                "mode": "private-judged-debug",
                "records": [
                    {"question_id": "extractive-wrong", "generated_answer": "private wrong extractive"},
                    {"question_id": "extractive-not-selected", "generated_answer": "private wrong fallback"},
                ],
            }
        ),
        encoding="utf-8",
    )

    report = module.build_audit_report(
        module.load_json(bundle_path),
        module.load_json(run_path),
        debug=module.load_json(debug_path),
        cutoff=20,
        bundle_path=bundle_path,
        run_path=run_path,
        debug_path=debug_path,
    )
    rendered = json.dumps(report)

    assert report["failure_classes"]["beam_extractive_candidate_wrong"] == 1
    assert report["failure_classes"]["beam_extractive_candidate_not_selected"] == 1
    assert "private question" not in rendered
    assert "private answer" not in rendered
    assert "private wrong" not in rendered
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private wrong direct" not in rendered
    assert "private ambiguous state" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_state_direct_candidate_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle_path = tmp_path / "bundle.json"
    run_path = tmp_path / "run.json"
    debug_path = tmp_path / "debug.json"
    bundle_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "mode": "private-judged-input-bundle",
                "questions": [
                    {
                        "question_id": "state-direct-wrong",
                        "category": "preference_following",
                        "question": "private raw question",
                        "ground_truth_answer": "private raw answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory"}]},
                    },
                    {
                        "question_id": "state-direct-not-selected",
                        "category": "instruction_following",
                        "question": "private raw question 2",
                        "ground_truth_answer": "private raw answer 2",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory 2"}]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private",
                "summary": {"20": {"total": 2, "passed": 0, "avg_score": 0.0}},
                "questions": [
                    {
                        "question_id": "state-direct-wrong",
                        "category": "preference_following",
                        "question_hash": "hash1",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_state_direct_candidate": True,
                                "beam_state_direct_candidate_used": True,
                                "beam_answer_candidate_count": 4,
                                "beam_answer_selected_candidate_index": 4,
                            }
                        },
                    },
                    {
                        "question_id": "state-direct-not-selected",
                        "category": "instruction_following",
                        "question_hash": "hash2",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_state_direct_candidate": True,
                                "beam_state_direct_candidate_used": False,
                                "beam_answer_candidate_count": 4,
                                "beam_answer_selected_candidate_index": 2,
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_path.write_text(
        json.dumps(
            {
                "mode": "private-judged-debug",
                "records": [
                    {"question_id": "state-direct-wrong", "generated_answer": "private wrong state direct"},
                    {"question_id": "state-direct-not-selected", "generated_answer": "private wrong fallback"},
                ],
            }
        ),
        encoding="utf-8",
    )

    report = module.build_audit_report(
        module.load_json(bundle_path),
        module.load_json(run_path),
        debug=module.load_json(debug_path),
        cutoff=20,
        bundle_path=bundle_path,
        run_path=run_path,
        debug_path=debug_path,
    )
    rendered = json.dumps(report)

    assert report["failure_classes"]["beam_state_direct_candidate_wrong"] == 1
    assert report["failure_classes"]["beam_state_direct_candidate_not_selected"] == 1
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private wrong" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_ranked_state_memory_candidate_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle_path = tmp_path / "bundle.json"
    run_path = tmp_path / "run.json"
    debug_path = tmp_path / "debug.json"
    bundle_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "mode": "private-judged-input-bundle",
                "questions": [
                    {
                        "question_id": "ranked-wrong",
                        "category": "preference_following",
                        "question": "private raw question",
                        "ground_truth_answer": "private raw answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory"}]},
                    },
                    {
                        "question_id": "ranked-not-selected",
                        "category": "instruction_following",
                        "question": "private raw question 2",
                        "ground_truth_answer": "private raw answer 2",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory 2"}]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private",
                "summary": {"20": {"total": 2, "passed": 0, "avg_score": 0.0}},
                "questions": [
                    {
                        "question_id": "ranked-wrong",
                        "category": "preference_following",
                        "question_hash": "hash1",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_ranked_state_memory_candidate": True,
                                "beam_ranked_state_memory_candidate_used": True,
                                "beam_answer_candidate_count": 3,
                                "beam_answer_selected_candidate_index": 3,
                            }
                        },
                    },
                    {
                        "question_id": "ranked-not-selected",
                        "category": "instruction_following",
                        "question_hash": "hash2",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_ranked_state_memory_candidate": True,
                                "beam_ranked_state_memory_candidate_used": False,
                                "beam_answer_candidate_count": 3,
                                "beam_answer_selected_candidate_index": 1,
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_path.write_text(
        json.dumps(
            {
                "mode": "private-judged-debug",
                "records": [
                    {"question_id": "ranked-wrong", "generated_answer": "private wrong ranked"},
                    {"question_id": "ranked-not-selected", "generated_answer": "private wrong fallback"},
                ],
            }
        ),
        encoding="utf-8",
    )

    report = module.build_audit_report(
        module.load_json(bundle_path),
        module.load_json(run_path),
        debug=module.load_json(debug_path),
        cutoff=20,
        bundle_path=bundle_path,
        run_path=run_path,
        debug_path=debug_path,
    )
    rendered = json.dumps(report)

    assert report["failure_classes"]["beam_ranked_state_memory_candidate_wrong"] == 1
    assert report["failure_classes"]["beam_ranked_state_memory_candidate_not_selected"] == 1
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private wrong" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_direct_span_candidate_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle_path = tmp_path / "bundle.json"
    run_path = tmp_path / "run.json"
    debug_path = tmp_path / "debug.json"
    bundle_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "mode": "private-judged-input-bundle",
                "questions": [
                    {
                        "question_id": "span-wrong",
                        "category": "knowledge_update",
                        "question": "private raw question",
                        "ground_truth_answer": "private raw answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory"}]},
                    },
                    {
                        "question_id": "span-not-selected",
                        "category": "knowledge_update",
                        "question": "private raw question 2",
                        "ground_truth_answer": "private raw answer 2",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory 2"}]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private",
                "summary": {"20": {"total": 2, "passed": 0, "avg_score": 0.0}},
                "questions": [
                    {
                        "question_id": "span-wrong",
                        "category": "knowledge_update",
                        "question_hash": "hash1",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_deterministic_state_resolver": True,
                                "beam_state_resolver_status": "resolved",
                                "beam_direct_span_candidate": True,
                                "beam_direct_span_candidate_used": True,
                                "beam_answer_candidate_count": 4,
                                "beam_answer_selected_candidate_index": 3,
                                "beam_answer_candidate_summaries": [
                                    {"id": "candidate_1", "kind": "normal", "answer_hash": "normalhash", "answer_chars": 12},
                                    {"id": "candidate_2", "kind": "alternate", "answer_hash": "althash", "answer_chars": 11},
                                    {"id": "candidate_3", "kind": "direct_span", "answer_hash": "spanhash", "answer_chars": 17},
                                    {"id": "candidate_4", "kind": "typed_projection", "answer_hash": "typedhash", "answer_chars": 17},
                                ],
                                "beam_answer_selector_status": "ok",
                                "generated_answer_hash": "spanhash",
                            }
                        },
                    },
                    {
                        "question_id": "span-not-selected",
                        "category": "knowledge_update",
                        "question_hash": "hash2",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_deterministic_state_resolver": True,
                                "beam_state_resolver_status": "resolved",
                                "beam_direct_span_candidate": True,
                                "beam_direct_span_candidate_used": False,
                                "beam_answer_candidate_count": 4,
                                "beam_answer_selected_candidate_index": 1,
                                "beam_answer_candidate_summaries": [
                                    {"id": "candidate_1", "kind": "normal", "answer_hash": "normalhash2", "answer_chars": 12},
                                    {"id": "candidate_2", "kind": "alternate", "answer_hash": "althash2", "answer_chars": 11},
                                    {"id": "candidate_3", "kind": "direct_span", "answer_hash": "spanhash2", "answer_chars": 17},
                                    {"id": "candidate_4", "kind": "typed_projection", "answer_hash": "typedhash2", "answer_chars": 17},
                                ],
                                "beam_answer_selector_status": "ok",
                                "generated_answer_hash": "normalhash2",
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_path.write_text(
        json.dumps(
            {
                "mode": "private-judged-debug",
                "records": [
                    {"question_id": "span-wrong", "generated_answer": "private wrong span"},
                    {"question_id": "span-not-selected", "generated_answer": "private wrong normal"},
                ],
            }
        ),
        encoding="utf-8",
    )

    report = module.build_audit_report(
        module.load_json(bundle_path),
        module.load_json(run_path),
        debug=module.load_json(debug_path),
        cutoff=20,
        bundle_path=bundle_path,
        run_path=run_path,
        debug_path=debug_path,
    )
    rendered = json.dumps(report)

    assert report["failure_classes"]["beam_direct_span_candidate_wrong"] == 1
    assert report["failure_classes"]["beam_direct_span_candidate_not_selected"] == 1
    failed = {row["question_hash"]: row for row in report["failed_questions"]}
    assert failed["hash1"]["selected_candidate_kind"] == "direct_span"
    assert failed["hash1"]["candidate_summaries"][2]["answer_hash"] == "spanhash"
    assert failed["hash2"]["selected_candidate_kind"] == "normal"
    assert failed["hash2"]["generated_answer_hash"] == "normalhash2"
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private wrong" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_typed_projection_candidate_failures_without_raw_payload(tmp_path):
    module = load_module()
    bundle_path = tmp_path / "bundle.json"
    run_path = tmp_path / "run.json"
    debug_path = tmp_path / "debug.json"
    bundle_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "mode": "private-judged-input-bundle",
                "questions": [
                    {
                        "question_id": "typed-wrong",
                        "category": "preference_following",
                        "question": "private raw question",
                        "ground_truth_answer": "private raw answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory"}]},
                    },
                    {
                        "question_id": "typed-not-selected",
                        "category": "instruction_following",
                        "question": "private raw question 2",
                        "ground_truth_answer": "private raw answer 2",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory 2"}]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private",
                "summary": {"20": {"total": 2, "passed": 0, "avg_score": 0.0}},
                "questions": [
                    {
                        "question_id": "typed-wrong",
                        "category": "preference_following",
                        "question_hash": "hash1",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_typed_projection_candidate": True,
                                "beam_typed_projection_candidate_used": True,
                                "beam_answer_candidate_count": 3,
                                "beam_answer_selected_candidate_index": 3,
                                "beam_answer_candidate_summaries": [
                                    {"id": "candidate_1", "kind": "normal", "answer_hash": "normalhash", "answer_chars": 12},
                                    {"id": "candidate_2", "kind": "alternate", "answer_hash": "althash", "answer_chars": 11},
                                    {"id": "candidate_3", "kind": "typed_projection", "answer_hash": "typedhash", "answer_chars": 17},
                                ],
                                "beam_answer_selector_status": "typed_projection_direct_bypass",
                                "beam_state_verifier_status": "uncertain",
                                "beam_direct_answer_bypass_reason": "verifier_uncertain",
                                "generated_answer_hash": "typedhash",
                            }
                        },
                    },
                    {
                        "question_id": "typed-not-selected",
                        "category": "instruction_following",
                        "question_hash": "hash2",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_typed_projection_candidate": True,
                                "beam_typed_projection_candidate_used": False,
                                "beam_answer_candidate_count": 3,
                                "beam_answer_selected_candidate_index": 2,
                                "beam_answer_candidate_summaries": [
                                    {"id": "candidate_1", "kind": "normal", "answer_hash": "normalhash2", "answer_chars": 12},
                                    {"id": "candidate_2", "kind": "alternate", "answer_hash": "althash2", "answer_chars": 11},
                                    {"id": "candidate_3", "kind": "typed_projection", "answer_hash": "typedhash2", "answer_chars": 17},
                                ],
                                "beam_answer_selector_status": "ok",
                                "beam_state_verifier_status": "uncertain",
                                "beam_direct_answer_bypass_reason": "verifier_uncertain",
                                "generated_answer_hash": "althash2",
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_path.write_text(
        json.dumps(
            {
                "mode": "private-judged-debug",
                "records": [
                    {"question_id": "typed-wrong", "generated_answer": "private wrong typed"},
                    {"question_id": "typed-not-selected", "generated_answer": "private wrong alternate"},
                ],
            }
        ),
        encoding="utf-8",
    )

    report = module.build_audit_report(
        module.load_json(bundle_path),
        module.load_json(run_path),
        debug=module.load_json(debug_path),
        cutoff=20,
        bundle_path=bundle_path,
        run_path=run_path,
        debug_path=debug_path,
    )
    rendered = json.dumps(report)

    assert report["failure_classes"]["beam_typed_projection_candidate_wrong"] == 1
    assert report["failure_classes"]["beam_typed_projection_candidate_not_selected"] == 1
    failed = {row["question_hash"]: row for row in report["failed_questions"]}
    assert failed["hash1"]["selected_candidate_kind"] == "typed_projection"
    assert failed["hash1"]["candidate_summaries"][2]["answer_hash"] == "typedhash"
    assert failed["hash2"]["selected_candidate_kind"] == "alternate"
    assert failed["hash2"]["generated_answer_hash"] == "althash2"
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private wrong" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_retrieved_excerpt_direct_bypass_without_raw_payload(tmp_path):
    module = load_module()
    bundle_path = tmp_path / "bundle.json"
    run_path = tmp_path / "run.json"
    debug_path = tmp_path / "debug.json"
    bundle_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "mode": "private-judged-input-bundle",
                "questions": [
                    {
                        "question_id": "excerpt-wrong",
                        "category": "instruction_following",
                        "question": "private raw question",
                        "ground_truth_answer": "private raw answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private",
                "summary": {"20": {"total": 1, "passed": 0, "avg_score": 0.0}},
                "questions": [
                    {
                        "question_id": "excerpt-wrong",
                        "category": "instruction_following",
                        "question_hash": "hash1",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_retrieved_excerpt_direct_bypass": True,
                                "beam_retrieved_excerpt_direct_bypass_used": True,
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_path.write_text(
        json.dumps(
            {
                "mode": "private-judged-debug",
                "records": [{"question_id": "excerpt-wrong", "generated_answer": "private wrong excerpt"}],
            }
        ),
        encoding="utf-8",
    )

    report = module.build_audit_report(
        module.load_json(bundle_path),
        module.load_json(run_path),
        debug=module.load_json(debug_path),
        cutoff=20,
        bundle_path=bundle_path,
        run_path=run_path,
        debug_path=debug_path,
    )
    rendered = json.dumps(report)

    assert report["failure_classes"]["beam_retrieved_excerpt_direct_bypass_wrong"] == 1
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private wrong" not in rendered
    assert_public_report_has_no_raw_payload(report)


def test_beam_failure_audit_classifies_memory_atomizer_failure_without_raw_payload(tmp_path):
    module = load_module()
    bundle_path = tmp_path / "bundle.json"
    run_path = tmp_path / "run.json"
    debug_path = tmp_path / "debug.json"
    bundle_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "mode": "private-judged-input-bundle",
                "questions": [
                    {
                        "question_id": "atomizer-wrong",
                        "category": "preference_following",
                        "question": "private raw question",
                        "ground_truth_answer": "private raw answer",
                        "retrieved_memories_by_top_k": {"20": [{"memory": "private raw memory"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "dataset": "beam_1M",
                "run_id": "beam-private",
                "summary": {"20": {"total": 1, "passed": 0, "avg_score": 0.0}},
                "questions": [
                    {
                        "question_id": "atomizer-wrong",
                        "category": "preference_following",
                        "question_hash": "hash1",
                        "cutoff_results": {
                            "20": {
                                "judgment": "FAIL",
                                "score": 0.0,
                                "judge_count": 3,
                                "judge_pass_count": 0,
                                "memories_evaluated": 20,
                                "beam_memory_atomizer": True,
                                "beam_memory_atomizer_hash": "hash-only",
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_path.write_text(
        json.dumps(
            {
                "mode": "private-judged-debug",
                "records": [
                    {
                        "question_id": "atomizer-wrong",
                        "beam_memory_atomizer": "private atomized evidence",
                        "generated_answer": "private wrong answer",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = module.build_audit_report(
        module.load_json(bundle_path),
        module.load_json(run_path),
        debug=module.load_json(debug_path),
        cutoff=20,
        bundle_path=bundle_path,
        run_path=run_path,
        debug_path=debug_path,
    )
    rendered = json.dumps(report)

    assert report["failure_classes"]["beam_memory_atomizer_wrong"] == 1
    assert "private raw question" not in rendered
    assert "private raw answer" not in rendered
    assert "private raw memory" not in rendered
    assert "private atomized" not in rendered
    assert "private wrong" not in rendered
    assert_public_report_has_no_raw_payload(report)
