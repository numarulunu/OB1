from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kontext_goal_scorecard.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kontext_goal_scorecard", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def readiness_payload(ok: bool = False) -> dict:
    return {
        "ok": ok,
        "ready_for_user_cutover_review": ok,
        "production_cutover_enabled": False,
        "status_counts": {"pass": 8 if ok else 7, "fail": 0 if ok else 1, "missing": 0},
        "gates": [
            {"name": "expanded_live_mcp_retrieval", "status": "pass", "evidence": {"private_raw": "must not render"}},
            {
                "name": "official_judged_accuracy",
                "status": "pass" if ok else "fail",
                "evidence": {
                    "actual_judged_accuracy_proven": ok,
                    "blocked_by": [] if ok else ["missing verification report: beam1"],
                },
            },
        ],
    }


def runbook_payload(ready: bool = False, proven: bool = False, verified_questions: int = 0, weighted_accuracy: float = 0.0) -> dict:
    return {
        "ok": True,
        "mode": "judged-paid-runbook",
        "runs_model_calls": False,
        "actual_judged_accuracy_proven": proven,
        "ready_to_run_paid_batch": ready,
        "required_user_approval": not proven,
        "missing_env_vars": [] if ready else ["OPENAI_API_KEY"],
        "blocking_items": [] if proven else [
            "OPENAI_API_KEY missing from run environment",
            "explicit paid-run approval required",
            "paid verification reports missing",
        ],
        "summary": {
            "estimated_llm_calls": {"total_calls": 9},
            "estimated_tokens": {"total_tokens": 25300},
            "max_cost_usd_total": 0.07,
            "verified_questions": verified_questions,
            "verified_weighted_accuracy": weighted_accuracy,
        },
        "commands": [{"command": "python3 secret-free command"}],
    }


def beam_gate_payload(top_k50: float = 0.75) -> dict:
    return {
        "ok": True,
        "mode": "beam-diagnostics-gate",
        "runs_model_calls": False,
        "label": "hard78-cross",
        "blocked_by": [],
        "summary": {
            "question_count": 8,
            "missing_evidence_count": 0,
            "top_k50_rate": top_k50,
            "top_k200_rate": 1.0,
            "mrr50": 0.0522,
            "max_first_hit_rank": 74,
        },
    }


def state_behavior_payload(ok: bool = True) -> dict:
    return {
        "ok": ok,
        "passed": 7 if ok else 6,
        "total": 7,
        "writes_applied": 0,
        "raw_memory_text": "must not render",
    }


def test_scorecard_reports_current_blocked_state_without_raw_text():
    module = load_module()
    scorecard = module.build_scorecard(
        readiness_payload(ok=False),
        runbook_payload(ready=False, proven=False),
        beam_gate_payload(top_k50=0.75),
        state_behavior_payload(ok=True),
    )
    rendered = json.dumps(scorecard)

    assert scorecard["ok"] is False
    assert scorecard["generated_at"].endswith("Z")
    assert scorecard["mode"] == "kontext-goal-scorecard"
    assert scorecard["runs_model_calls"] is False
    assert scorecard["goal_complete_candidate"] is False
    assert scorecard["production_cutover_enabled"] is False
    assert scorecard["summary"]["blocker_count"] == 4
    assert "official judged accuracy is not proven" in scorecard["blockers"]
    assert "OPENAI_API_KEY missing from run environment" in scorecard["blockers"]
    assert "BEAM hard-slice top-k50 remains below 1.0" in scorecard["quality_gaps"]
    assert scorecard["gates"]["beam_hard_slice"]["status"] == "weak"
    assert "must not render" not in rendered
    assert "secret-free command" not in rendered


def test_scorecard_can_become_completion_candidate_with_all_green_inputs():
    module = load_module()
    scorecard = module.build_scorecard(
        readiness_payload(ok=True),
        runbook_payload(ready=False, proven=True, verified_questions=30, weighted_accuracy=1.0),
        beam_gate_payload(top_k50=1.0),
        state_behavior_payload(ok=True),
    )

    assert scorecard["ok"] is True
    assert scorecard["goal_complete_candidate"] is True
    assert scorecard["blockers"] == []
    assert scorecard["quality_gaps"] == []
    assert scorecard["gates"]["official_judged_accuracy"]["status"] == "pass"
    assert scorecard["gates"]["beam_hard_slice"]["status"] == "pass"


def test_scorecard_rejects_boolean_only_judged_claim():
    module = load_module()
    scorecard = module.build_scorecard(
        readiness_payload(ok=True),
        runbook_payload(ready=False, proven=True, verified_questions=3, weighted_accuracy=1.0),
        beam_gate_payload(top_k50=1.0),
        state_behavior_payload(ok=True),
    )

    assert scorecard["ok"] is False
    assert scorecard["goal_complete_candidate"] is False
    assert "official judged accuracy is not proven" in scorecard["blockers"]
    assert scorecard["gates"]["official_judged_accuracy"]["actual_judged_accuracy_proven"] is False
    assert scorecard["gates"]["official_judged_accuracy"]["min_questions"] == 30


def test_cli_writes_scorecard(tmp_path, capsys):
    module = load_module()
    readiness = tmp_path / "readiness.json"
    runbook = tmp_path / "runbook.json"
    beam = tmp_path / "beam.json"
    state = tmp_path / "state.json"
    output = tmp_path / "scorecard.json"
    write_json(readiness, readiness_payload(ok=False))
    write_json(runbook, runbook_payload(ready=False, proven=False))
    write_json(beam, beam_gate_payload(top_k50=0.75))
    write_json(state, state_behavior_payload(ok=True))

    code = module.main(
        [
            "--cutover-readiness",
            str(readiness),
            "--judged-runbook",
            str(runbook),
            "--beam-cross-gate",
            str(beam),
            "--state-behavior",
            str(state),
            "--output",
            str(output),
        ]
    )

    assert code == 2
    assert json.loads(output.read_text(encoding="utf-8"))["goal_complete_candidate"] is False
    assert json.loads(capsys.readouterr().out)["runs_model_calls"] is False
