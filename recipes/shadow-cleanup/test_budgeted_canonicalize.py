import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"
UUID_D = "44444444-4444-4444-8444-444444444444"


def load_budgeted():
    module_path = Path(__file__).with_name("budgeted_canonicalize.py")
    spec = importlib.util.spec_from_file_location("budgeted_canonicalize", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def thought(record_id, content, metadata=None):
    return {"id": record_id, "content": content, "metadata": metadata or {}}


def test_bucket_key_uses_source_project_type_and_topic_family():
    budgeted = load_budgeted()
    row = thought(
        UUID_A,
        "Parser status should not become durable memory.",
        {"source": "claude_history", "type": "context", "cwd": "C:/Tools/OB1", "topics": ["parser", "cleanup"]},
    )

    key = budgeted.bucket_key(row)

    assert key == "source:claude_history|project:c-tools-ob1|type:context|topic:parser"


def test_bucket_key_defaults_missing_metadata_safely():
    budgeted = load_budgeted()
    row = thought(UUID_A, "A row with no useful metadata.")

    key = budgeted.bucket_key(row)

    assert key == "source:missing|project:missing|type:missing|topic:missing"


def test_canonical_limit_is_strict_for_large_claude_buckets():
    budgeted = load_budgeted()

    assert budgeted.canonical_limit_for_bucket("claude_history", 50) == 3
    assert budgeted.canonical_limit_for_bucket("claude_history", 25) == 2
    assert budgeted.canonical_limit_for_bucket("claude_history", 5) == 1


def test_canonical_limit_preserves_more_kontext_than_claude():
    budgeted = load_budgeted()

    assert budgeted.canonical_limit_for_bucket("kontext", 50) == 8
    assert budgeted.canonical_limit_for_bucket("gemini", 50) == 4


def test_build_rewrite_packs_enforces_canonical_limit_and_pack_size():
    budgeted = load_budgeted()
    rows = [
        thought(
            f"{i:08d}-1111-4111-8111-111111111111",
            f"Claude row {i}",
            {"source": "claude_history", "type": "context", "cwd": "C:/Tools/OB1", "topics": ["parser"]},
        )
        for i in range(55)
    ]

    packs = budgeted.build_rewrite_packs(rows, source="claude_history", limit_rows=0)

    assert len(packs) == 2
    assert packs[0]["canonical_limit"] == 3
    assert packs[0]["record_count"] == 50
    assert packs[1]["canonical_limit"] == 1
    assert packs[1]["record_count"] == 5


def test_build_rewrite_packs_orders_dense_packs_first_for_limited_model_runs():
    budgeted = load_budgeted()
    small = thought(UUID_A, "Small bucket", {"source": "claude_history", "type": "context", "topics": ["aaa"]})
    large = [
        thought(
            f"{i:08d}-2222-4222-8222-222222222222",
            f"Large bucket {i}",
            {"source": "claude_history", "type": "context", "topics": ["zzz"]},
        )
        for i in range(5)
    ]

    packs = budgeted.build_rewrite_packs([small, *large], source="claude_history", limit_rows=0)

    assert packs[0]["record_count"] == 5
    assert packs[1]["record_count"] == 1



def test_build_rewrite_packs_can_override_max_pack_rows_for_model_reliability():
    budgeted = load_budgeted()
    rows = [
        thought(
            f"{i:08d}-3333-4333-8333-333333333333",
            f"Claude row {i}",
            {"source": "claude_history", "type": "context", "topics": ["dense"]},
        )
        for i in range(55)
    ]

    packs = budgeted.build_rewrite_packs(rows, source="claude_history", limit_rows=0, max_pack_rows=25)

    assert [pack["record_count"] for pack in packs] == [25, 25, 5]
    assert [pack["canonical_limit"] for pack in packs] == [2, 2, 1]



def test_build_rewrite_packs_can_limit_rows_for_pilot():
    budgeted = load_budgeted()
    rows = [
        thought(f"{i:08d}-1111-4111-8111-111111111111", f"Claude row {i}", {"source": "claude_history", "type": "context"})
        for i in range(20)
    ]

    packs = budgeted.build_rewrite_packs(rows, source="claude_history", limit_rows=7)

    assert sum(pack["record_count"] for pack in packs) == 7


def test_row_cache_key_is_stable_and_policy_scoped():
    budgeted = load_budgeted()
    row = thought(UUID_A, "  Durable Pattern  ", {"source": "claude_history", "topics": ["systems"]})

    first = budgeted.row_cache_key(row, "policy-a")
    second = budgeted.row_cache_key(thought(UUID_A, "durable   pattern", {"topics": ["systems"], "source": "claude_history"}), "policy-a")
    changed_policy = budgeted.row_cache_key(row, "policy-b")

    assert first == second
    assert first != changed_policy


def test_build_decision_cache_records_from_agent_json(tmp_path):
    budgeted = load_budgeted()
    rows = [
        thought(UUID_A, "Current opera career pattern", {"source": "claude_history"}),
        thought(UUID_B, "Generic diarization clutter", {"source": "claude_history"}),
        thought(UUID_C, "Risky relationship context", {"source": "claude_history"}),
    ]
    proposal_path = tmp_path / "agent-output.json"
    proposal_path.write_text(
        json.dumps(
            {
                "agent": "pilot",
                "proposals": [
                    {
                        "pack_id": "pack-a",
                        "canonical_memories": [{"content": "Opera career pattern", "source_ids": [UUID_A]}],
                        "delete_source_ids": [UUID_B],
                        "keep_source_ids": [],
                        "escalate_source_ids": [UUID_C],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proposals = budgeted.load_proposal_rows([proposal_path])
    cache_rows = budgeted.build_decision_cache_records(rows, proposals, "policy-a")

    assert [(row["row_id"], row["decision"]) for row in cache_rows] == [
        (UUID_A, "canonical"),
        (UUID_B, "delete"),
        (UUID_C, "escalate"),
    ]
    assert all(row["policy_version"] == "policy-a" for row in cache_rows)
    assert all(row["cache_key"] for row in cache_rows)


def test_build_wave_plan_coalesces_sparse_rows_into_dense_agent_packs():
    budgeted = load_budgeted()
    rows = [
        thought(
            f"{i:08d}-5555-4555-8555-555555555555",
            f"Sparse row {i}",
            {"source": "claude_history", "type": "context", "topics": [f"topic-{i}"]},
        )
        for i in range(45)
    ]

    plan = budgeted.build_wave_plan(rows, source="claude_history", policy_version="policy-a", max_pack_rows=20)

    assert [pack["record_count"] for pack in plan["packs"]] == [20, 20, 5]
    assert all(str(pack["cluster_key"]).startswith("agent-wave:claude_history:") for pack in plan["packs"])



def test_build_wave_plan_skips_cached_rows_and_deterministic_junk():
    budgeted = load_budgeted()
    cached = thought(UUID_A, "Already processed", {"source": "claude_history", "type": "context", "topics": ["systems"]})
    junk = thought(UUID_B, "The speaker diarization was successfully inferred from conversational cues.", {"source": "claude_history", "type": "context", "topics": ["voice"]})
    candidate = thought(UUID_C, "Current Vocality business pattern", {"source": "claude_history", "type": "context", "topics": ["business"]})
    cache_rows = [{"cache_key": budgeted.row_cache_key(cached, "policy-a"), "row_id": UUID_A, "policy_version": "policy-a", "decision": "delete"}]

    plan = budgeted.build_wave_plan(
        [cached, junk, candidate],
        source="claude_history",
        policy_version="policy-a",
        row_cache_records=cache_rows,
        max_pack_rows=20,
        exclude_junk=True,
    )

    assert plan["manifest"]["total_rows"] == 3
    assert plan["manifest"]["cached_rows"] == 1
    assert plan["manifest"]["deterministic_junk_rows"] == 1
    assert plan["manifest"]["candidate_rows"] == 1
    assert len(plan["packs"]) == 1
    assert plan["packs"][0]["items"][0]["id"] == UUID_C
    assert plan["packs"][0]["policy_version"] == "policy-a"
    assert plan["packs"][0]["pack_cache_key"]


def test_cache_index_and_wave_commands_write_local_artifacts(tmp_path):
    budgeted = load_budgeted()
    rows = [
        thought(UUID_A, "Current opera career pattern", {"source": "claude_history", "type": "context", "topics": ["career"]}),
        thought(UUID_B, "The speaker diarization was successfully inferred from conversational cues.", {"source": "claude_history", "type": "context", "topics": ["voice"]}),
        thought(UUID_C, "Current Vocality business pattern", {"source": "claude_history", "type": "context", "topics": ["business"]}),
    ]
    snapshot = tmp_path / "snapshot.jsonl"
    budgeted.write_jsonl(snapshot, rows)
    proposal = tmp_path / "agent-output.json"
    proposal.write_text(
        json.dumps({"proposals": [{"pack_id": "pack-a", "canonical_memories": [{"content": "Opera career pattern", "source_ids": [UUID_A]}], "delete_source_ids": [], "keep_source_ids": [], "escalate_source_ids": []}]}),
        encoding="utf-8",
    )
    cache_out = tmp_path / "cache.jsonl"

    budgeted.cmd_cache_index(SimpleNamespace(output_root=tmp_path, snapshot=snapshot, proposals=[proposal], policy_version="policy-a", output=cache_out))

    cache_text = cache_out.read_text(encoding="utf-8")
    assert UUID_A in cache_text
    assert "Current opera career pattern" not in cache_text

    packs_out = tmp_path / "packs.jsonl"
    manifest_out = tmp_path / "manifest.json"
    junk_out = tmp_path / "junk.jsonl"
    budgeted.cmd_wave(
        SimpleNamespace(
            output_root=tmp_path,
            snapshot=snapshot,
            source="claude_history",
            row_cache=[cache_out],
            policy_version="policy-a",
            limit_rows=0,
            max_pack_rows=20,
            include_junk=False,
            packs_output=packs_out,
            manifest_output=manifest_out,
            junk_output=junk_out,
        )
    )

    manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
    assert manifest["cached_rows"] == 1
    assert manifest["deterministic_junk_rows"] == 1
    assert manifest["candidate_rows"] == 1
    assert budgeted.read_jsonl(packs_out)[0]["items"][0]["id"] == UUID_C



def test_build_prompt_contains_hard_canonical_limit_and_every_id_rule():
    budgeted = load_budgeted()
    pack = {
        "pack_id": "pack1",
        "source": "claude_history",
        "canonical_limit": 3,
        "min_delete_ratio": 0.7,
        "items": [{"id": UUID_A, "content": "row a"}, {"id": UUID_B, "content": "row b"}],
    }

    prompt = budgeted.build_budgeted_prompt(pack)

    assert "at most 3 canonical_memories" in prompt
    assert "Every input id must appear exactly once" in prompt
    assert "delete_source_ids" in prompt
    assert "keep_source_ids" in prompt
    assert "escalate_source_ids" in prompt


def test_build_human_memory_prompt_is_lossy_and_escalates_uncertainty():
    budgeted = load_budgeted()
    pack = {
        "pack_id": "pack1",
        "source": "claude_history",
        "canonical_limit": 2,
        "min_delete_ratio": 0.7,
        "items": [{"id": UUID_A, "content": "row a"}, {"id": UUID_B, "content": "row b"}],
    }

    prompt = budgeted.build_human_memory_prompt(pack)

    assert "mimic human memory" in prompt
    assert "lossy" in prompt
    assert "Ask the user" in prompt
    assert "escalate_source_ids" in prompt
    assert "Every input id must appear exactly once" in prompt
    assert "transcript-cleanup" in prompt
    assert "default-delete student lessons" in prompt
    assert "role/Fach/repertoire direction" in prompt
    assert "Vocality teaching method" in prompt



def test_build_pack_prompt_selects_human_policy():
    budgeted = load_budgeted()
    pack = {"pack_id": "pack1", "canonical_limit": 1, "items": [{"id": UUID_A, "content": "row a"}]}

    prompt = budgeted.build_pack_prompt(pack, "human")

    assert "mimic human memory" in prompt



def test_build_verifier_prompt_contains_coverage_gate():
    budgeted = load_budgeted()
    pack = {"pack_id": "pack1", "items": [{"id": UUID_A, "content": "row a"}]}
    proposal = {"pack_id": "pack1", "canonical_memories": [], "delete_source_ids": [UUID_A], "keep_source_ids": [], "escalate_source_ids": []}

    prompt = budgeted.build_verifier_prompt(pack, proposal)

    assert "coverage_ok" in prompt
    assert "unsafe_delete_ids" in prompt
    assert "Do not approve deletion" in prompt


def test_build_repair_prompt_preserves_verifier_feedback_and_every_id_rule():
    budgeted = load_budgeted()
    pack = {
        "pack_id": "pack1",
        "canonical_limit": 2,
        "items": [{"id": UUID_A, "content": "row a"}, {"id": UUID_B, "content": "row b"}],
    }
    rejected_row = {
        "pack_id": "pack1",
        "proposal": {"canonical_memories": [], "delete_source_ids": [UUID_A], "keep_source_ids": [], "escalate_source_ids": [UUID_B]},
        "verification": {"coverage_ok": False, "unsafe_delete_ids": [UUID_B], "missing_facts": ["missing durable project decision"], "risk_notes": []},
    }

    prompt = budgeted.build_repair_prompt(pack, rejected_row)

    assert "Repair the cleanup proposal" in prompt
    assert "Every input id must appear exactly once" in prompt
    assert "unsafe_delete_ids" in prompt
    assert UUID_B in prompt
    assert "missing durable project decision" in prompt



def test_repairable_rows_by_id_selects_only_rejected_verified_rows(tmp_path):
    budgeted = load_budgeted()
    path = tmp_path / "verified.jsonl"
    rows = [
        {"pack_id": "ok", "proposal": {}, "verification": {"coverage_ok": True, "unsafe_delete_ids": []}},
        {"pack_id": "bad", "proposal": {"canonical_memories": []}, "verification": {"coverage_ok": False, "unsafe_delete_ids": [UUID_A]}},
        {"pack_id": "err", "proposal": {"canonical_memories": []}, "verification_error": "empty"},
        {"pack_id": "raw-error", "error": "proposal failed"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    repairable = budgeted.repairable_rows_by_id([path])

    assert list(repairable) == ["bad"]
    assert repairable["bad"]["verification"]["unsafe_delete_ids"] == [UUID_A]



def test_retry_operation_retries_budgeted_errors_then_returns():
    budgeted = load_budgeted()
    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise budgeted.BudgetedCanonicalizeError("temporary failure")
        return "ok"

    assert budgeted.retry_operation(flaky, retries=2, retry_delay=0, label="test") == "ok"
    assert attempts["count"] == 2



def test_retry_operation_raises_after_retries_exhausted():
    budgeted = load_budgeted()
    attempts = {"count": 0}

    def always_fails():
        attempts["count"] += 1
        raise budgeted.BudgetedCanonicalizeError("still bad")

    try:
        budgeted.retry_operation(always_fails, retries=1, retry_delay=0, label="test")
    except budgeted.BudgetedCanonicalizeError as exc:
        assert str(exc) == "still bad"
    else:
        raise AssertionError("retry_operation should have raised")
    assert attempts["count"] == 2



def test_parse_model_json_accepts_fenced_json():
    budgeted = load_budgeted()
    payload = {"pack_id": "p", "coverage_ok": True}

    parsed = budgeted.parse_model_json("```json\n" + json.dumps(payload) + "\n```")

    assert parsed == payload


def test_validate_proposal_requires_every_id_once():
    budgeted = load_budgeted()
    pack = {"pack_id": "p", "canonical_limit": 1, "items": [{"id": UUID_A}, {"id": UUID_B}]}
    proposal = {
        "pack_id": "p",
        "bucket_status": "complete",
        "canonical_memories": [{"content": "summary", "source_ids": [UUID_A], "confidence": 0.9}],
        "delete_source_ids": [],
        "keep_source_ids": [],
        "escalate_source_ids": [],
    }

    result = budgeted.validate_proposal(pack, proposal)

    assert result["ok"] is False
    assert result["missing_ids"] == [UUID_B]


def test_validate_proposal_rejects_canonical_limit_overflow():
    budgeted = load_budgeted()
    pack = {"pack_id": "p", "canonical_limit": 1, "items": [{"id": UUID_A}, {"id": UUID_B}]}
    proposal = {
        "pack_id": "p",
        "bucket_status": "complete",
        "canonical_memories": [
            {"content": "summary a", "source_ids": [UUID_A], "confidence": 0.9},
            {"content": "summary b", "source_ids": [UUID_B], "confidence": 0.9},
        ],
        "delete_source_ids": [],
        "keep_source_ids": [],
        "escalate_source_ids": [],
    }

    result = budgeted.validate_proposal(pack, proposal)

    assert result["ok"] is False
    assert result["canonical_limit_exceeded"] is True


def test_validate_verification_rejects_unknown_unsafe_delete_ids():
    budgeted = load_budgeted()
    pack = {"pack_id": "p", "items": [{"id": UUID_A}]}
    verification = {"pack_id": "p", "coverage_ok": False, "unsafe_delete_ids": [UUID_B], "missing_facts": [], "risk_notes": []}

    result = budgeted.validate_verification(pack, verification)

    assert result["ok"] is False
    assert result["unknown_ids"] == [UUID_B]


def test_build_apply_plan_requires_verified_rows_by_default():
    budgeted = load_budgeted()
    proposal_rows = [
        {
            "pack_id": "p",
            "proposal": {
                "canonical_memories": [{"content": "summary", "source_ids": [UUID_A], "confidence": 0.9}],
                "delete_source_ids": [UUID_A],
                "keep_source_ids": [],
                "escalate_source_ids": [],
            },
        }
    ]

    plan = budgeted.build_apply_plan(proposal_rows)

    assert plan["canonical_count"] == 0
    assert plan["delete_ids"] == []
    assert plan["skipped_unverified"] == 1


def test_apply_plan_never_deletes_keep_escalate_or_verifier_unsafe_ids():
    budgeted = load_budgeted()
    proposal_rows = [
        {
            "pack_id": "p",
            "verification": {"coverage_ok": True, "unsafe_delete_ids": [UUID_C]},
            "proposal": {
                "canonical_memories": [{"content": "summary", "source_ids": [UUID_A], "confidence": 0.9}],
                "delete_source_ids": [UUID_A, UUID_B, UUID_C],
                "keep_source_ids": [UUID_B],
                "escalate_source_ids": [UUID_D],
            },
        }
    ]

    plan = budgeted.build_apply_plan(proposal_rows)

    assert plan["canonical_count"] == 1
    assert plan["delete_ids"] == [UUID_A]
    assert plan["blocked_keep_or_escalate_ids"] == [UUID_B]
    assert plan["blocked_verifier_ids"] == [UUID_C]



def test_junk_label_flags_speaker_diarization_clutter():
    budgeted = load_budgeted()
    row = thought(
        UUID_A,
        "The speaker diarization was successfully inferred from conversational cues.",
        {"source": "claude_history", "type": "context", "topics": ["voice"]},
    )

    label = budgeted.junk_label_for_row(row)

    assert label["ok"] is True
    assert label["reason"] == "speaker_diarization_clutter"
    assert label["confidence"] >= 0.98



def test_junk_label_protects_budgeted_canonical_rows():
    budgeted = load_budgeted()
    row = thought(
        UUID_A,
        "Current durable project memory that came from canonical cleanup.",
        {"source": "shadow_cleanup", "created_by": "budgeted_canonicalize_v1", "import_mode": "budgeted_canonicalize_v1"},
    )

    label = budgeted.junk_label_for_row(row)

    assert label["ok"] is False
    assert label["reason"] == "protected_canonical"



def test_build_junk_proposal_omits_raw_content():
    budgeted = load_budgeted()
    row = thought(
        UUID_A,
        "Speaker identification must resolve aliases to canonical names.",
        {"source": "claude_history", "type": "context", "topics": ["voice"]},
    )

    proposal = budgeted.build_junk_proposal(row, {"ok": True, "reason": "speaker_identity_alias_clutter", "confidence": 0.99})

    assert proposal["id"] == UUID_A
    assert proposal["reason"] == "speaker_identity_alias_clutter"
    assert "content" not in proposal
    assert "content_hash" in proposal



def test_build_junk_apply_plan_filters_confidence_and_dedupes():
    budgeted = load_budgeted()
    rows = [
        {"id": UUID_A, "confidence": 0.99, "reason": "speaker_diarization_clutter"},
        {"id": UUID_A, "confidence": 0.99, "reason": "speaker_diarization_clutter"},
        {"id": UUID_B, "confidence": 0.91, "reason": "weak"},
        {"id": "not-a-uuid", "confidence": 1.0, "reason": "bad"},
    ]

    plan = budgeted.build_junk_apply_plan(rows, min_confidence=0.98)

    assert plan["delete_ids"] == [UUID_A]
    assert plan["skipped_low_confidence"] == 1
    assert plan["skipped_invalid_id"] == 1
