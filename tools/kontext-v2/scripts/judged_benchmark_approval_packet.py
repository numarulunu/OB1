from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _private_paths import validate_private_debug_output_path as _validate_private_debug_output_path

DEFAULT_ANSWER_INPUT_TOKENS = 4_000
DEFAULT_ANSWER_OUTPUT_TOKENS = 300
DEFAULT_JUDGE_INPUT_TOKENS = 1_500
DEFAULT_JUDGE_OUTPUT_TOKENS = 120
DEFAULT_PAID_MAX_MEMORIES = 10
DEFAULT_PAID_MEMORY_MAX_CHARS = 1200
DEFAULT_PAID_TOTAL_MAX_CHARS = 18000


def load_bundle(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input bundle must be a JSON object")
    return payload


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def typed_object_summary_enabled() -> bool:
    return _env_bool("KONTEXT_TYPED_OBJECT_SUMMARY")


def _sanitized_count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, count in value.items():
        try:
            out[str(key)] = int(count)
        except (TypeError, ValueError):
            continue
    return dict(sorted(out.items()))


def sanitized_typed_object_status_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "total": int(value.get("total") or 0),
        "valid_schema": int(value.get("valid_schema") or 0),
        "statuses": _sanitized_count_map(value.get("statuses")),
        "event_relations": _sanitized_count_map(value.get("event_relations")),
    }


def parse_cutoffs(value: str | None, bundle: dict[str, Any]) -> list[int]:
    if value:
        values = [int(item.strip()) for item in value.split(",") if item.strip()]
    else:
        values = [int(item) for item in bundle.get("top_k_values") or []]
    normalized = sorted({min(max(item, 1), 200) for item in values})
    if not normalized:
        raise ValueError("at least one cutoff is required")
    return normalized


def selected_questions(bundle: dict[str, Any], max_questions: int | None, question_offset: int = 0) -> int:
    return len(selected_question_rows(bundle, max_questions, question_offset))


def selected_question_rows(bundle: dict[str, Any], max_questions: int | None, question_offset: int = 0) -> list[dict[str, Any]]:
    questions = bundle.get("questions") if isinstance(bundle.get("questions"), list) else []
    rows = [row for row in questions if isinstance(row, dict)]
    rows = rows[max(int(question_offset or 0), 0) :]
    if max_questions is not None:
        return rows[: max(max_questions, 0)]
    return rows


def infer_mode(bundle: dict[str, Any], requested_mode: str | None) -> str:
    if requested_mode:
        return requested_mode
    dataset = str(bundle.get("dataset") or "").lower()
    if "beam" in dataset:
        return "beam-rubric"
    return "answerer-judge"


def format_number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 4)


def validate_private_debug_output_path(value: str | None) -> str | None:
    return _validate_private_debug_output_path(value)


def existing_output_path_error(value: str, label: str) -> str | None:
    if Path(value).exists():
        return f"{label} path already exists"
    return None


def positive_number_error(args: argparse.Namespace) -> str | None:
    fields = [
        "max_cost_usd",
        "answer_input_usd_per_1m",
        "answer_output_usd_per_1m",
        "judge_input_usd_per_1m",
        "judge_output_usd_per_1m",
    ]
    for field in fields:
        value = float(getattr(args, field))
        if not math.isfinite(value) or value <= 0:
            return f"{field} must be positive"
    return None


def memory_limited_paid_path(args: argparse.Namespace) -> bool:
    return any(
        bool(getattr(args, name, False))
        for name in [
            "temporal_fact_extraction",
            "locomo_evidence_windows",
            "beam_evidence_windows",
            "beam_answer_contract",
            "beam_structured_evidence",
            "beam_turn_neighborhoods",
            "beam_category_synthesis",
            "beam_state_reducer",
            "beam_answer_candidate_selector",
            "beam_extractive_candidate",
            "beam_state_direct_candidate",
            "beam_direct_span_candidate",
            "beam_ranked_state_memory_candidate",
            "beam_typed_projection_candidate",
            "beam_memory_atomizer",
            "beam_state_ledger",
            "beam_state_verifier",
            "beam_deterministic_state_resolver",
            "beam_focused_state_answer",
            "longmemeval_evidence_windows",
            "longmemeval_structured_evidence",
        ]
    )


def effective_answer_limits(args: argparse.Namespace) -> dict[str, int | None]:
    if not memory_limited_paid_path(args):
        return {
            "answer_max_memories": getattr(args, "answer_max_memories", None),
            "answer_memory_max_chars": getattr(args, "answer_memory_max_chars", None),
            "answer_total_max_chars": getattr(args, "answer_total_max_chars", None),
        }
    return {
        "answer_max_memories": getattr(args, "answer_max_memories", None) or DEFAULT_PAID_MAX_MEMORIES,
        "answer_memory_max_chars": getattr(args, "answer_memory_max_chars", None) or DEFAULT_PAID_MEMORY_MAX_CHARS,
        "answer_total_max_chars": getattr(args, "answer_total_max_chars", None) or DEFAULT_PAID_TOTAL_MAX_CHARS,
    }


def judge_units(rows: list[dict[str, Any]], mode: str, override: float | None) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    if override is not None:
        per_question = max(float(override), 0.0)
        return per_question * len(rows), per_question
    if mode != "beam-rubric":
        return float(len(rows)), 1.0
    total = 0
    for row in rows:
        rubric = row.get("rubric") if isinstance(row.get("rubric"), list) else []
        total += max(len(rubric), 1)
    return float(total), float(total) / len(rows)


def cost_from_estimate(
    answer_calls: int,
    judge_calls: int,
    args: argparse.Namespace,
) -> tuple[dict[str, int], dict[str, float]]:
    tokens = {
        "answer_input_tokens": answer_calls * DEFAULT_ANSWER_INPUT_TOKENS,
        "answer_output_tokens": answer_calls * DEFAULT_ANSWER_OUTPUT_TOKENS,
        "judge_input_tokens": judge_calls * DEFAULT_JUDGE_INPUT_TOKENS,
        "judge_output_tokens": judge_calls * DEFAULT_JUDGE_OUTPUT_TOKENS,
    }
    tokens["total_input_tokens"] = tokens["answer_input_tokens"] + tokens["judge_input_tokens"]
    tokens["total_output_tokens"] = tokens["answer_output_tokens"] + tokens["judge_output_tokens"]
    tokens["total_tokens"] = sum(
        tokens[key]
        for key in ["answer_input_tokens", "answer_output_tokens", "judge_input_tokens", "judge_output_tokens"]
    )
    answer = tokens["answer_input_tokens"] * args.answer_input_usd_per_1m / 1_000_000
    answer += tokens["answer_output_tokens"] * args.answer_output_usd_per_1m / 1_000_000
    judge = tokens["judge_input_tokens"] * args.judge_input_usd_per_1m / 1_000_000
    judge += tokens["judge_output_tokens"] * args.judge_output_usd_per_1m / 1_000_000
    return tokens, {"answerer_usd": round(answer, 6), "judge_usd": round(judge, 6), "total_usd": round(answer + judge, 6)}


def command_template(args: argparse.Namespace, judge_units_per_question: float = 1.0, expected_questions: int | None = None) -> str:
    answer_limits = effective_answer_limits(args)
    parts = [
        "python3",
        "/opt/kontext/scripts/judged_benchmark_run.py",
        "--input-bundle",
        args.input_bundle,
        "--output",
        args.run_output,
        "--cutoffs",
        args.cutoffs,
        "--max-questions",
        str(args.max_questions),
        "--question-offset",
        str(max(int(getattr(args, "question_offset", 0) or 0), 0)),
        "--execute",
        "--provider",
        "openai-compatible",
        "--approve-cost",
        "--max-cost-usd",
        str(args.max_cost_usd),
        "--answerer-model",
        args.answerer_model,
        "--judge-model",
        args.judge_model,
        "--api-key-env",
        args.api_key_env,
        "--base-url",
        args.base_url,
        "--answer-input-usd-per-1m",
        str(args.answer_input_usd_per_1m),
        "--answer-output-usd-per-1m",
        str(args.answer_output_usd_per_1m),
        "--judge-input-usd-per-1m",
        str(args.judge_input_usd_per_1m),
        "--judge-output-usd-per-1m",
        str(args.judge_output_usd_per_1m),
    ]
    if judge_units_per_question != 1.0:
        parts.extend(["--judge-units-per-question", str(judge_units_per_question)])
    if answer_limits["answer_max_memories"] is not None:
        parts.extend(["--answer-max-memories", str(answer_limits["answer_max_memories"])])
    if answer_limits["answer_memory_max_chars"] is not None:
        parts.extend(["--answer-memory-max-chars", str(answer_limits["answer_memory_max_chars"])])
    if answer_limits["answer_total_max_chars"] is not None:
        parts.extend(["--answer-total-max-chars", str(answer_limits["answer_total_max_chars"])])
    if getattr(args, "temporal_fact_extraction", False):
        parts.append("--temporal-fact-extraction")
    if getattr(args, "locomo_evidence_windows", False):
        parts.append("--locomo-evidence-windows")
    if getattr(args, "beam_evidence_windows", False):
        parts.append("--beam-evidence-windows")
    if getattr(args, "beam_answer_contract", False):
        parts.append("--beam-answer-contract")
    if getattr(args, "beam_structured_evidence", False):
        parts.append("--beam-structured-evidence")
    if getattr(args, "beam_turn_neighborhoods", False):
        parts.append("--beam-turn-neighborhoods")
    if getattr(args, "beam_category_synthesis", False):
        parts.append("--beam-category-synthesis")
    if getattr(args, "beam_state_reducer", False):
        parts.append("--beam-state-reducer")
    if getattr(args, "beam_direct_answer_bypass", False):
        parts.append("--beam-direct-answer-bypass")
    if getattr(args, "beam_broad_support_bypass", False):
        parts.append("--beam-broad-support-bypass")
    if getattr(args, "beam_disable_corrected_bypass", False):
        parts.append("--beam-disable-corrected-bypass")
    if getattr(args, "beam_strict_direct_bypass", False):
        parts.append("--beam-strict-direct-bypass")
    if getattr(args, "beam_verified_state_only", False):
        parts.append("--beam-verified-state-only")
    if getattr(args, "beam_answer_candidate_selector", False):
        parts.append("--beam-answer-candidate-selector")
    if getattr(args, "beam_extractive_candidate", False):
        parts.append("--beam-extractive-candidate")
    if getattr(args, "beam_state_direct_candidate", False):
        parts.append("--beam-state-direct-candidate")
    if getattr(args, "beam_direct_span_candidate", False):
        parts.append("--beam-direct-span-candidate")
    if getattr(args, "beam_ranked_state_memory_candidate", False):
        parts.append("--beam-ranked-state-memory-candidate")
    if getattr(args, "beam_ranked_state_memory_direct_bypass", False):
        parts.append("--beam-ranked-state-memory-direct-bypass")
    if getattr(args, "beam_retrieved_excerpt_direct_bypass", False):
        parts.append("--beam-retrieved-excerpt-direct-bypass")
    if getattr(args, "beam_typed_projection_candidate", False):
        parts.append("--beam-typed-projection-candidate")
    if getattr(args, "beam_memory_atomizer", False):
        parts.append("--beam-memory-atomizer")
    if getattr(args, "beam_state_ledger", False):
        parts.append("--beam-state-ledger")
    if getattr(args, "beam_state_verifier", False):
        parts.append("--beam-state-verifier")
    if getattr(args, "beam_deterministic_state_resolver", False):
        parts.append("--beam-deterministic-state-resolver")
    if getattr(args, "beam_focused_state_answer", False):
        parts.append("--beam-focused-state-answer")
    if getattr(args, "longmemeval_evidence_windows", False):
        parts.append("--longmemeval-evidence-windows")
    if getattr(args, "longmemeval_structured_evidence", False):
        parts.append("--longmemeval-structured-evidence")
    if getattr(args, "private_debug_output", None):
        parts.extend(["--private-debug-output", args.private_debug_output])
    if getattr(args, "omit_temperature", False):
        parts.append("--omit-temperature")
    if getattr(args, "reasoning_effort", None):
        parts.extend(["--reasoning-effort", str(args.reasoning_effort)])
    parts.extend(
        [
        "--verification-output",
        args.verification_output,
        "--verify-cutoff",
        str(parse_cutoffs(args.cutoffs, {})[-1]),
        "--verify-min-accuracy",
        str(args.min_accuracy),
        "--verify-min-questions",
        str(expected_questions if expected_questions is not None else args.max_questions),
        "--verify-mem0-target-accuracy",
        str(args.mem0_target_accuracy),
        "--verify-max-cost-usd",
        str(args.max_cost_usd),
        "--verify-require-model-calls",
        "--verify-require-usage",
        "--verify-expected-provider",
        "openai-compatible",
        "--verify-expected-answerer-model",
        args.answerer_model,
        "--verify-expected-judge-model",
        args.judge_model,
        "--verify-expected-questions",
        str(expected_questions if expected_questions is not None else args.max_questions),
        ]
    )
    return " ".join(shlex.quote(part) for part in parts)


def command_wrapper_template(args: argparse.Namespace, command: str) -> str:
    env_name = str(args.api_key_env)
    base_url = str(args.base_url)
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"RUN_OUTPUT={shlex.quote(str(args.run_output))}",
            f"VERIFICATION_OUTPUT={shlex.quote(str(args.verification_output))}",
            'if [ -e "$RUN_OUTPUT" ]; then echo "run output already exists" >&2; exit 3; fi',
            'if [ -e "$VERIFICATION_OUTPUT" ]; then echo "verification output already exists" >&2; exit 3; fi',
            f'if [ -z "${{{env_name}:-}}" ]; then echo "{env_name} is missing" >&2; exit 4; fi',
            "python3 - <<'PY'",
            "import os",
            "import sys",
            "import urllib.parse",
            "import urllib.request",
            f"env_name = {env_name!r}",
            f"base_url = {base_url!r}",
            "parsed = urllib.parse.urlsplit(base_url)",
            "path = parsed.path.rstrip('/')",
            "if path.endswith('/chat/completions'):",
            "    path = path[: -len('/chat/completions')] + '/models'",
            "elif path.endswith('/responses'):",
            "    path = path[: -len('/responses')] + '/models'",
            "elif path.endswith('/v1'):",
            "    path = path + '/models'",
            "else:",
            "    path = '/v1/models'",
            "models_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))",
            "try:",
            "    req = urllib.request.Request(models_url, headers={'Authorization': 'Bearer ' + os.environ[env_name]}, method='GET')",
            "    with urllib.request.urlopen(req, timeout=30) as response:",
            "        response.read(1)",
            "except Exception as exc:",
            "    status = getattr(exc, 'code', None)",
            "    print(f'auth preflight failed: {status or type(exc).__name__}', file=sys.stderr)",
            "    raise SystemExit(5)",
            "PY",
            command,
        ]
    )


def blocked(reason: str, **extra: Any) -> dict[str, Any]:
    packet = {
        "ok": False,
        "mode": "judged-benchmark-approval-packet",
        "runs_model_calls": False,
        "approval_required": False,
        "reason": reason,
    }
    packet.update(extra)
    return packet


def build_packet(args: argparse.Namespace) -> dict[str, Any]:
    bundle = load_bundle(args.input_bundle)
    if bundle.get("mode") != "private-judged-input-bundle":
        return blocked("input bundle mode is not private-judged-input-bundle")
    if bundle.get("contains_live_user_memory") is True:
        return blocked("input bundle contains live/user memory")
    private_debug_error = validate_private_debug_output_path(getattr(args, "private_debug_output", None))
    if private_debug_error:
        return blocked(private_debug_error)
    run_output_error = existing_output_path_error(args.run_output, "run output")
    if run_output_error:
        return blocked(run_output_error)
    verification_output_error = existing_output_path_error(args.verification_output, "verification output")
    if verification_output_error:
        return blocked(verification_output_error)
    pricing_error = positive_number_error(args)
    if pricing_error:
        return blocked(pricing_error)
    cutoffs = parse_cutoffs(args.cutoffs, bundle)
    rows = selected_question_rows(bundle, args.max_questions, getattr(args, "question_offset", 0))
    question_count = len(rows)
    benchmark_mode = infer_mode(bundle, getattr(args, "mode", None))
    judge_units_total, judge_units_per_question = judge_units(
        rows,
        benchmark_mode,
        getattr(args, "judge_units_per_question", None),
    )
    base_answer_calls = question_count * len(cutoffs)
    temporal_extraction_calls = base_answer_calls if getattr(args, "temporal_fact_extraction", False) else 0
    beam_structured_calls = base_answer_calls if getattr(args, "beam_structured_evidence", False) else 0
    beam_state_calls = base_answer_calls if getattr(args, "beam_state_reducer", False) else 0
    beam_verifier_calls = base_answer_calls if getattr(args, "beam_state_verifier", False) else 0
    beam_deterministic_calls = base_answer_calls if getattr(args, "beam_deterministic_state_resolver", False) else 0
    beam_focused_calls = base_answer_calls if getattr(args, "beam_focused_state_answer", False) else 0
    beam_selector_calls = base_answer_calls if getattr(args, "beam_answer_candidate_selector", False) else 0
    beam_selector_extra_answer_calls = base_answer_calls if getattr(args, "beam_answer_candidate_selector", False) else 0
    beam_extractive_candidate_calls = (
        base_answer_calls
        if getattr(args, "beam_answer_candidate_selector", False)
        and getattr(args, "beam_extractive_candidate", False)
        else 0
    )
    beam_ranked_state_memory_candidate_calls = (
        base_answer_calls
        if getattr(args, "beam_answer_candidate_selector", False)
        and getattr(args, "beam_ranked_state_memory_candidate", False)
        else 0
    )
    beam_memory_atomizer_calls = base_answer_calls if getattr(args, "beam_memory_atomizer", False) else 0
    bypass_skipped_answer_calls = (
        base_answer_calls
        if getattr(args, "beam_direct_answer_bypass", False)
        and (
            getattr(args, "beam_state_reducer", False)
            or getattr(args, "beam_deterministic_state_resolver", False)
        )
        and not getattr(args, "beam_answer_candidate_selector", False)
        else 0
    )
    longmemeval_structured_calls = base_answer_calls if getattr(args, "longmemeval_structured_evidence", False) else 0
    answer_generation_calls = base_answer_calls - bypass_skipped_answer_calls
    answer_calls = (
        answer_generation_calls
        + temporal_extraction_calls
        + beam_structured_calls
        + beam_state_calls
        + beam_verifier_calls
        + beam_focused_calls
        + beam_selector_calls
        + beam_selector_extra_answer_calls
        + beam_extractive_candidate_calls
        + beam_ranked_state_memory_candidate_calls
        + beam_memory_atomizer_calls
        + longmemeval_structured_calls
    )
    judge_calls = format_number(judge_units_total * len(cutoffs))
    tokens, cost = cost_from_estimate(answer_calls, judge_calls, args)
    if cost["total_usd"] > float(args.max_cost_usd):
        return blocked(
            "estimated cost exceeds max_cost_usd",
            estimated_cost_usd=cost,
            max_cost_usd=args.max_cost_usd,
        )
    estimated_llm_calls = {
        "answer_calls": answer_calls,
        "judge_calls": judge_calls,
        "total_calls": answer_calls + judge_calls,
    }
    if temporal_extraction_calls:
        estimated_llm_calls["temporal_fact_extraction_calls"] = temporal_extraction_calls
    if beam_structured_calls:
        estimated_llm_calls["beam_structured_evidence_calls"] = beam_structured_calls
    if beam_state_calls:
        estimated_llm_calls["beam_state_reducer_calls"] = beam_state_calls
    if beam_verifier_calls:
        estimated_llm_calls["beam_state_verifier_calls"] = beam_verifier_calls
    if beam_deterministic_calls:
        estimated_llm_calls["beam_deterministic_state_resolver_calls"] = beam_deterministic_calls
    if beam_focused_calls:
        estimated_llm_calls["beam_focused_state_answer_calls"] = beam_focused_calls
    if beam_selector_calls:
        estimated_llm_calls["beam_answer_candidate_selector_calls"] = beam_selector_calls
        estimated_llm_calls["beam_answer_candidate_extra_answer_calls"] = beam_selector_extra_answer_calls
    if beam_extractive_candidate_calls:
        estimated_llm_calls["beam_extractive_candidate_calls"] = beam_extractive_candidate_calls
    if beam_ranked_state_memory_candidate_calls:
        estimated_llm_calls["beam_ranked_state_memory_candidate_calls"] = beam_ranked_state_memory_candidate_calls
    if beam_memory_atomizer_calls:
        estimated_llm_calls["beam_memory_atomizer_calls"] = beam_memory_atomizer_calls
    if bypass_skipped_answer_calls:
        estimated_llm_calls["beam_direct_answer_bypass_skipped_answer_calls"] = bypass_skipped_answer_calls
    if longmemeval_structured_calls:
        estimated_llm_calls["longmemeval_structured_evidence_calls"] = longmemeval_structured_calls
    answer_prompt_caps = effective_answer_limits(args)
    answer_prompt_caps = {key: value for key, value in answer_prompt_caps.items() if value is not None}
    command = command_template(args, judge_units_per_question, question_count)
    packet = {
        "ok": True,
        "mode": "judged-benchmark-approval-packet",
        "runs_model_calls": False,
        "approval_required": True,
        "dataset": str(bundle.get("dataset") or "unknown"),
        "run_id": str(bundle.get("run_id") or "unknown"),
        "retrieval_backend": str(bundle.get("retrieval_backend") or "kontext"),
        "input_bundle": args.input_bundle,
        "run_output": args.run_output,
        "verification_output": args.verification_output,
        "question_offset": max(int(getattr(args, "question_offset", 0) or 0), 0),
        "selected_questions": question_count,
        "top_k_values": cutoffs,
        "benchmark_mode": benchmark_mode,
        "temporal_fact_extraction": bool(getattr(args, "temporal_fact_extraction", False)),
        "locomo_evidence_windows": bool(getattr(args, "locomo_evidence_windows", False)),
        "beam_evidence_windows": bool(getattr(args, "beam_evidence_windows", False)),
        "beam_answer_contract": bool(getattr(args, "beam_answer_contract", False)),
        "beam_structured_evidence": bool(getattr(args, "beam_structured_evidence", False)),
        "beam_turn_neighborhoods": bool(getattr(args, "beam_turn_neighborhoods", False)),
        "beam_category_synthesis": bool(getattr(args, "beam_category_synthesis", False)),
        "beam_state_reducer": bool(getattr(args, "beam_state_reducer", False)),
        "beam_direct_answer_bypass": bool(getattr(args, "beam_direct_answer_bypass", False)),
        "beam_broad_support_bypass": bool(getattr(args, "beam_broad_support_bypass", False)),
        "beam_disable_corrected_bypass": bool(getattr(args, "beam_disable_corrected_bypass", False)),
        "beam_strict_direct_bypass": bool(getattr(args, "beam_strict_direct_bypass", False)),
        "beam_verified_state_only": bool(getattr(args, "beam_verified_state_only", False)),
        "beam_answer_candidate_selector": bool(getattr(args, "beam_answer_candidate_selector", False)),
        "beam_extractive_candidate": bool(getattr(args, "beam_extractive_candidate", False)),
        "beam_state_direct_candidate": bool(getattr(args, "beam_state_direct_candidate", False)),
        "beam_direct_span_candidate": bool(getattr(args, "beam_direct_span_candidate", False)),
        "beam_ranked_state_memory_candidate": bool(getattr(args, "beam_ranked_state_memory_candidate", False)),
        "beam_ranked_state_memory_direct_bypass": bool(getattr(args, "beam_ranked_state_memory_direct_bypass", False)),
        "beam_retrieved_excerpt_direct_bypass": bool(getattr(args, "beam_retrieved_excerpt_direct_bypass", False)),
        "beam_typed_projection_candidate": bool(getattr(args, "beam_typed_projection_candidate", False)),
        "beam_memory_atomizer": bool(getattr(args, "beam_memory_atomizer", False)),
        "beam_state_ledger": bool(getattr(args, "beam_state_ledger", False)),
        "beam_state_verifier": bool(getattr(args, "beam_state_verifier", False)),
        "beam_deterministic_state_resolver": bool(getattr(args, "beam_deterministic_state_resolver", False)),
        "beam_focused_state_answer": bool(getattr(args, "beam_focused_state_answer", False)),
        "longmemeval_evidence_windows": bool(getattr(args, "longmemeval_evidence_windows", False)),
        "longmemeval_structured_evidence": bool(getattr(args, "longmemeval_structured_evidence", False)),
        "private_debug_output": getattr(args, "private_debug_output", None),
        "omit_temperature": bool(getattr(args, "omit_temperature", False)),
        "reasoning_effort": getattr(args, "reasoning_effort", None),
        "answer_prompt_caps": answer_prompt_caps,
        "judge_units_total": format_number(judge_units_total),
        "judge_units_per_question": format_number(judge_units_per_question),
        "estimated_llm_calls": estimated_llm_calls,
        "estimated_tokens": tokens,
        "estimated_cost_usd": cost,
        "max_cost_usd": args.max_cost_usd,
        "min_accuracy": args.min_accuracy,
        "mem0_target_accuracy": args.mem0_target_accuracy,
        "required_env_vars": [args.api_key_env],
        "command_template": command,
        "command_wrapper_template": command_wrapper_template(args, command),
        "notes": [
            "This packet does not call answerer or judge models.",
            "Run only after explicit approval for the stated max_cost_usd.",
            "The command references an API-key environment variable by name only; it does not contain the secret value.",
            "For beam-rubric mode, judge_units_per_question controls repeated judge calls and the conservative cost estimate.",
        ],
    }
    if typed_object_summary_enabled():
        packet["typed_object_status_summary"] = sanitized_typed_object_status_summary(
            bundle.get("typed_object_status_summary")
        )
    return packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a sanitized approval packet for a paid judged benchmark micro-slice.")
    parser.add_argument("--input-bundle", required=True)
    parser.add_argument("--output")
    parser.add_argument("--run-output", required=True)
    parser.add_argument("--verification-output", required=True)
    parser.add_argument("--cutoffs", default="50")
    parser.add_argument("--max-questions", type=int, required=True)
    parser.add_argument("--question-offset", type=int, default=0)
    parser.add_argument("--answerer-model", required=True)
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="https://api.openai.com/v1/chat/completions")
    parser.add_argument("--max-cost-usd", type=float, required=True)
    parser.add_argument("--mem0-target-accuracy", type=float, required=True)
    parser.add_argument("--min-accuracy", type=float)
    parser.add_argument("--mode", choices=["answerer-judge", "beam-rubric"])
    parser.add_argument("--judge-units-per-question", type=float)
    parser.add_argument("--answer-max-memories", type=int)
    parser.add_argument("--answer-memory-max-chars", type=int)
    parser.add_argument("--answer-total-max-chars", type=int)
    parser.add_argument("--temporal-fact-extraction", action="store_true")
    parser.add_argument("--locomo-evidence-windows", action="store_true")
    parser.add_argument("--beam-evidence-windows", action="store_true")
    parser.add_argument("--beam-answer-contract", action="store_true")
    parser.add_argument("--beam-structured-evidence", action="store_true")
    parser.add_argument("--beam-turn-neighborhoods", action="store_true")
    parser.add_argument("--beam-category-synthesis", action="store_true")
    parser.add_argument("--beam-state-reducer", action="store_true")
    parser.add_argument("--beam-direct-answer-bypass", action="store_true")
    parser.add_argument("--beam-broad-support-bypass", action="store_true")
    parser.add_argument("--beam-disable-corrected-bypass", action="store_true")
    parser.add_argument("--beam-strict-direct-bypass", action="store_true")
    parser.add_argument("--beam-verified-state-only", action="store_true")
    parser.add_argument("--beam-answer-candidate-selector", action="store_true")
    parser.add_argument("--beam-extractive-candidate", action="store_true")
    parser.add_argument("--beam-state-direct-candidate", action="store_true")
    parser.add_argument("--beam-direct-span-candidate", action="store_true")
    parser.add_argument("--beam-ranked-state-memory-candidate", action="store_true")
    parser.add_argument("--beam-ranked-state-memory-direct-bypass", action="store_true")
    parser.add_argument("--beam-retrieved-excerpt-direct-bypass", action="store_true")
    parser.add_argument("--beam-typed-projection-candidate", action="store_true")
    parser.add_argument("--beam-memory-atomizer", action="store_true")
    parser.add_argument("--beam-state-ledger", action="store_true")
    parser.add_argument("--beam-state-verifier", action="store_true")
    parser.add_argument("--beam-deterministic-state-resolver", action="store_true")
    parser.add_argument("--beam-focused-state-answer", action="store_true")
    parser.add_argument("--longmemeval-evidence-windows", action="store_true")
    parser.add_argument("--longmemeval-structured-evidence", action="store_true")
    parser.add_argument("--private-debug-output")
    parser.add_argument("--omit-temperature", action="store_true")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--answer-input-usd-per-1m", type=float, required=True)
    parser.add_argument("--answer-output-usd-per-1m", type=float, required=True)
    parser.add_argument("--judge-input-usd-per-1m", type=float, required=True)
    parser.add_argument("--judge-output-usd-per-1m", type=float, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_accuracy is None:
        args.min_accuracy = args.mem0_target_accuracy
    if args.output:
        output_error = existing_output_path_error(args.output, "approval packet output")
        if output_error:
            packet = blocked(output_error)
            print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
            return 2
    packet = build_packet(args)
    text = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if packet.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
