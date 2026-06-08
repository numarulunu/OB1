from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public_artifact_scan import raw_payload_hits, string_scan_counts


BEAM_BOOL_FLAGS = {
    "beam_evidence_windows": "--beam-evidence-windows",
    "beam_answer_contract": "--beam-answer-contract",
    "beam_structured_evidence": "--beam-structured-evidence",
    "beam_turn_neighborhoods": "--beam-turn-neighborhoods",
    "beam_category_synthesis": "--beam-category-synthesis",
    "beam_state_reducer": "--beam-state-reducer",
    "beam_direct_answer_bypass": "--beam-direct-answer-bypass",
    "beam_broad_support_bypass": "--beam-broad-support-bypass",
    "beam_disable_corrected_bypass": "--beam-disable-corrected-bypass",
    "beam_strict_direct_bypass": "--beam-strict-direct-bypass",
    "beam_verified_state_only": "--beam-verified-state-only",
    "beam_answer_candidate_selector": "--beam-answer-candidate-selector",
    "beam_extractive_candidate": "--beam-extractive-candidate",
    "beam_state_direct_candidate": "--beam-state-direct-candidate",
    "beam_direct_span_candidate": "--beam-direct-span-candidate",
    "beam_ranked_state_memory_candidate": "--beam-ranked-state-memory-candidate",
    "beam_ranked_state_memory_direct_bypass": "--beam-ranked-state-memory-direct-bypass",
    "beam_retrieved_excerpt_direct_bypass": "--beam-retrieved-excerpt-direct-bypass",
    "beam_typed_projection_candidate": "--beam-typed-projection-candidate",
    "beam_memory_atomizer": "--beam-memory-atomizer",
    "beam_state_ledger": "--beam-state-ledger",
    "beam_state_verifier": "--beam-state-verifier",
    "beam_deterministic_state_resolver": "--beam-deterministic-state-resolver",
    "beam_focused_state_answer": "--beam-focused-state-answer",
}


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def load_runner():
    path = SCRIPT_DIR / "judged_benchmark_run.py"
    spec = importlib.util.spec_from_file_location("judged_benchmark_run", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def command_value(parts: list[str], flag: str, default: str | None = None) -> str | None:
    if flag not in parts:
        return default
    index = parts.index(flag)
    if index + 1 >= len(parts):
        return default
    return parts[index + 1]


def optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value)


def fake_post_factory(counters: dict[str, int]):
    def fake_post(payload: dict[str, Any], _api_key: str, _base_url: str) -> dict[str, Any]:
        counters["synthetic_calls"] += 1
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        contents = [str(row.get("content") or "") for row in messages if isinstance(row, dict)]
        system = contents[0].lower() if contents else ""
        user = contents[1] if len(contents) > 1 else ""
        counters["max_prompt_chars"] = max(counters["max_prompt_chars"], sum(len(item) for item in contents))
        if "select the best beam candidate answer" in system:
            counters["selector_prompts"] += 1
            if "Safe selector metrics:" in user:
                counters["selector_metric_prompts"] += 1
            if "stronger specific-question overlap" in user:
                counters["selector_metric_rule_prompts"] += 1
            return {
                "text": '{"selected_id":"candidate_1","reason_code":"fake_no_call","confidence":0.5}',
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            }
        if "resolve beam current state" in system:
            return {
                "text": '{"active_state":"fake state","direct_answer":"fake direct answer","supporting_event_hashes":["aaa111aaa111"]}',
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            }
        if "verify beam resolved state" in system:
            return {
                "text": '{"verdict":"valid","corrected_direct_answer":"","supporting_event_hashes":["aaa111aaa111"],"reason_code":"fake_valid","confidence":0.8}',
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            }
        if "atomize beam retrieved memories" in system:
            return {
                "text": '{"facts":[{"fact":"fake fact","status":"active","support_hashes":["aaa111aaa111"]}],"current_answer_hint":"fake answer","uncertainty":""}',
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            }
        if "strict benchmark judge" in system:
            return {"text": '{"correct":true,"score":1.0}', "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}
        return {"text": "fake no-call answer", "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}

    return fake_post


def config_from_approval(runner: Any, approval: dict[str, Any], command_parts: list[str]) -> Any:
    return runner.ExternalRunConfig(
        approved=True,
        max_cost_usd=float(approval.get("max_cost_usd") or command_value(command_parts, "--max-cost-usd", "0")),
        answerer_model=str(command_value(command_parts, "--answerer-model") or "fake-answerer"),
        judge_model=str(command_value(command_parts, "--judge-model") or "fake-judge"),
        api_key="fake-key",
        base_url=str(command_value(command_parts, "--base-url") or "https://example.test/v1/chat/completions"),
        prices=runner.PriceConfig(
            float(command_value(command_parts, "--answer-input-usd-per-1m", "1") or 1),
            float(command_value(command_parts, "--answer-output-usd-per-1m", "1") or 1),
            float(command_value(command_parts, "--judge-input-usd-per-1m", "1") or 1),
            float(command_value(command_parts, "--judge-output-usd-per-1m", "1") or 1),
        ),
        judge_units_per_question=float(approval.get("judge_units_per_question") or command_value(command_parts, "--judge-units-per-question", "1") or 1),
        answer_max_memories=optional_int(command_value(command_parts, "--answer-max-memories")),
        answer_memory_max_chars=optional_int(command_value(command_parts, "--answer-memory-max-chars")),
        answer_total_max_chars=optional_int(command_value(command_parts, "--answer-total-max-chars")),
        omit_temperature=bool(approval.get("omit_temperature")),
        reasoning_effort=approval.get("reasoning_effort") or command_value(command_parts, "--reasoning-effort"),
        **{key: approval.get(key) is True or flag in command_parts for key, flag in BEAM_BOOL_FLAGS.items()},
    )


def build_diagnostic(approval_path: str | Path, input_bundle_path: str | Path) -> dict[str, Any]:
    approval = load_json(approval_path)
    command_parts = shlex.split(str(approval.get("command_template") or ""))
    runner = load_runner()
    counters = {
        "synthetic_calls": 0,
        "selector_prompts": 0,
        "selector_metric_prompts": 0,
        "selector_metric_rule_prompts": 0,
        "max_prompt_chars": 0,
    }
    result = runner.run_openai_compatible(
        runner.load_bundle(input_bundle_path),
        config_from_approval(runner, approval, command_parts),
        max_questions=int(command_value(command_parts, "--max-questions", str(approval.get("selected_questions") or 0)) or 0),
        question_offset=int(command_value(command_parts, "--question-offset", str(approval.get("question_offset") or 0)) or 0),
        cutoffs=str(command_value(command_parts, "--cutoffs", ",".join(str(item) for item in approval.get("top_k_values") or [])) or ""),
        http_post=fake_post_factory(counters),
        auth_probe=None,
    )
    report = {
        "ok": result.get("ok") is True,
        "mode": "judged-benchmark-fake-provider-diagnostic",
        "runs_model_calls": False,
        "source_approval": Path(approval_path).name,
        "selected_questions": len(result.get("questions") if isinstance(result.get("questions"), list) else []),
        "top_k_values": result.get("top_k_values") or approval.get("top_k_values") or [],
        "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
        **counters,
    }
    report["raw_payload_hits"] = len(raw_payload_hits(report))
    string_counts = string_scan_counts(json.dumps(report, ensure_ascii=False, sort_keys=True))
    report.update(string_counts)
    report["ok"] = report["ok"] and report["raw_payload_hits"] == 0 and report["private_path_hits"] == 0 and report["secret_shaped_hits"] == 0
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a no-call fake-provider judged benchmark diagnostic and write a sanitized count-only report.")
    parser.add_argument("--approval", required=True)
    parser.add_argument("--input-bundle", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_diagnostic(args.approval, args.input_bundle)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
