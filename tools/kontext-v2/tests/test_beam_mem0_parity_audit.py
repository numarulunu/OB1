import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "beam_mem0_parity_audit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("beam_mem0_parity_audit", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _report(
    backend: str,
    *,
    dataset: str = "beam_1M",
    run_id: str = "unit-beam",
    top_k_values: list[int] | None = None,
    rows: list[dict] | None = None,
) -> dict:
    return {
        "dataset": dataset,
        "run_id": run_id,
        "retrieval_backend": backend,
        "mode": "predict-only-sweep",
        "top_k_values": top_k_values or [20],
        "max_top_k": max(top_k_values or [20]),
        "total_questions": len(rows or []),
        "questions": rows or [],
    }


def _question(
    question_id: str,
    category: str,
    matched: bool,
    *,
    first_hit: int | None = None,
    ids: list[str] | None = None,
    hashes: list[str] | None = None,
) -> dict:
    return {
        "question_id": question_id,
        "category": category,
        "retrieval_evaluable": True,
        "first_hit_top_k": first_hit,
        "matched_by_top_k": {"20": matched},
        "miss_reason_by_top_k": {"20": "matched" if matched else "no_expected_terms_or_evidence"},
        "result_ids_at_max_top_k": ids or [],
        "result_hashes_at_max_top_k": hashes or [],
    }


def test_beam_mem0_parity_audit_reports_sanitized_overlap_buckets():
    module = load_module()
    kontext = _report(
        "kontext",
        rows=[
            _question("q1", "preference_following", True, first_hit=3, ids=["a", "b"], hashes=["ha", "hb"]),
            _question("q2", "knowledge_update", False, ids=["c"], hashes=["hc"]),
            _question("q3", "instruction_following", True, first_hit=9, ids=["d"], hashes=["hd"]),
        ],
    )
    mem0 = _report(
        "legacy-mem0-offline",
        rows=[
            _question("q1", "preference_following", True, first_hit=2, ids=["a", "x"], hashes=["ha", "hx"]),
            _question("q2", "knowledge_update", True, first_hit=7, ids=["m"], hashes=["hm"]),
            _question("q3", "instruction_following", False, ids=["z"], hashes=["hz"]),
        ],
    )

    audit = module.build_beam_mem0_parity_audit(kontext, mem0, top_k=20)
    rendered = json.dumps(audit, sort_keys=True)

    assert audit["ok"] is True
    assert audit["summary"]["both_hit"] == 1
    assert audit["summary"]["mem0_only"] == 1
    assert audit["summary"]["kontext_only"] == 1
    assert audit["categories"]["knowledge_update"]["mem0_only"] == 1
    assert audit["raw_payload_scan"]["hit_count"] == 0
    assert "ground_truth_answer" not in rendered
    assert "retrieved_memories_by_top_k" not in rendered
    assert "sk-" not in rendered


def test_beam_mem0_parity_audit_rejects_mixed_dataset_without_override():
    module = load_module()
    kontext = _report("kontext", dataset="beam_1M", rows=[_question("q1", "x", True)])
    mem0 = _report("legacy-mem0-offline", dataset="beam_500K", rows=[_question("q1", "x", True)])

    try:
        module.build_beam_mem0_parity_audit(kontext, mem0, top_k=20, allow_mixed=False)
    except ValueError as exc:
        assert "mixed dataset" in str(exc)
    else:
        raise AssertionError("expected mixed datasets to be rejected")


def test_beam_mem0_parity_audit_rejects_raw_payload_fields():
    module = load_module()
    kontext = _report(
        "kontext",
        rows=[
            {
                **_question("q1", "x", True),
                "question": "raw benchmark text must not be in public reports",
            }
        ],
    )
    mem0 = _report("legacy-mem0-offline", rows=[_question("q1", "x", True)])

    try:
        module.build_beam_mem0_parity_audit(kontext, mem0, top_k=20)
    except ValueError as exc:
        assert "raw payload" in str(exc)
    else:
        raise AssertionError("expected raw payload fields to be rejected")
