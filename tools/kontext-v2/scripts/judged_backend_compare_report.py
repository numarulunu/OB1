from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FORBIDDEN_RAW_KEYS = {
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
}


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def raw_payload_hits(value: Any) -> list[str]:
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = f"{path}/{key}" if path else str(key)
                if key in FORBIDDEN_RAW_KEYS:
                    hits.append(child_path)
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(value, "")
    return hits


def report_row(path: str | Path, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    cost = report.get("estimated_cost_usd") if isinstance(report.get("estimated_cost_usd"), dict) else {}
    raw_gate = report.get("gates", {}).get("raw_payload", {}) if isinstance(report.get("gates"), dict) else {}
    raw_hit_count = int(raw_gate.get("hit_count") or len(raw_payload_hits(report)))
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    accuracy = float(summary.get("accuracy") or (passed / total if total else 0.0))
    return {
        "suite": str(report.get("dataset") or "unknown"),
        "backend": str(report.get("retrieval_backend") or "kontext"),
        "run_id": str(report.get("run_id") or ""),
        "ok": bool(report.get("ok")),
        "questions": total,
        "passed": passed,
        "accuracy": round(accuracy, 4),
        "avg_score": round(float(summary.get("avg_score") or 0.0), 4),
        "cutoff": int(summary.get("cutoff") or 0),
        "provider": report.get("provider"),
        "answerer_model": report.get("answerer_model"),
        "judge_model": report.get("judge_model"),
        "cost_usd": round(float(cost.get("total_usd") or 0.0), 6),
        "raw_payload_hit_count": raw_hit_count,
        "source_path": str(path),
    }


def _unique(rows: list[dict[str, Any]], key: str) -> set[Any]:
    return {row.get(key) for row in rows}


def validate_comparable(rows: list[dict[str, Any]], allow_mixed: bool) -> None:
    if allow_mixed:
        return
    if len(_unique(rows, "cutoff")) > 1:
        raise ValueError("mixed cutoffs require --allow-mixed")
    if len(_unique(rows, "answerer_model")) > 1 or len(_unique(rows, "judge_model")) > 1:
        raise ValueError("mixed answerer/judge models require --allow-mixed")


def confidence_for_row(row: dict[str, Any], allow_mixed: bool) -> str:
    if row["raw_payload_hit_count"] or not row["ok"]:
        return "low"
    if row["questions"] < 30 or allow_mixed:
        return "medium"
    if row["backend"] == "legacy-mem0-offline":
        return "medium-high"
    return "high"


def interpretation_for_suite(kontext_accuracy: float | None, mem0_accuracy: float | None) -> str:
    if kontext_accuracy is None or mem0_accuracy is None:
        return "missing paired backend"
    delta = round(kontext_accuracy - mem0_accuracy, 4)
    if delta >= 0.10:
        return "kontext_leads"
    if delta <= -0.10:
        return "mem0_leads"
    if abs(delta) <= 0.05:
        return "inconclusive_within_5pp"
    return "small_delta"


def build_comparison_report(paths: list[str | Path], allow_mixed: bool = False) -> dict[str, Any]:
    rows = [report_row(path, load_json(path)) for path in paths]
    validate_comparable(rows, allow_mixed=allow_mixed)
    for row in rows:
        row["confidence"] = confidence_for_row(row, allow_mixed=allow_mixed)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["suite"]].append(row)

    suites: dict[str, dict[str, Any]] = {}
    for suite, suite_rows in sorted(grouped.items()):
        by_backend = {row["backend"]: row for row in suite_rows}
        kontext = by_backend.get("kontext")
        mem0 = by_backend.get("legacy-mem0-offline")
        kontext_accuracy = kontext.get("accuracy") if kontext else None
        mem0_accuracy = mem0.get("accuracy") if mem0 else None
        delta = None
        if kontext_accuracy is not None and mem0_accuracy is not None:
            delta = round(float(kontext_accuracy) - float(mem0_accuracy), 4)
        suites[suite] = {
            "backends": sorted(by_backend),
            "delta_kontext_minus_mem0": delta,
            "interpretation": interpretation_for_suite(kontext_accuracy, mem0_accuracy),
            "confidence": "low" if any(row["confidence"] == "low" for row in suite_rows) else min(
                (row["confidence"] for row in suite_rows),
                key=["low", "medium", "medium-high", "high"].index,
            ),
        }

    raw_payload_hit_count = sum(row["raw_payload_hit_count"] for row in rows)
    return {
        "ok": raw_payload_hit_count == 0 and all(row["ok"] for row in rows),
        "mode": "judged-backend-comparison",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_model_calls": False,
        "allow_mixed": bool(allow_mixed),
        "row_count": len(rows),
        "total_cost_usd": round(sum(row["cost_usd"] for row in rows), 6),
        "raw_payload_hit_count": raw_payload_hit_count,
        "rows": rows,
        "suites": suites,
        "notes": [
            "This report compares sanitized verification aggregates only.",
            "legacy-mem0-offline means the local legacy MCP ranker, not hosted Mem0 SaaS internals.",
            "No raw benchmark questions, answers, memories, generated answers, judge responses, prompts, or secrets are included.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a sanitized Kontext-vs-Mem0 judged comparison report.")
    parser.add_argument("--verification-report", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-mixed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_comparison_report(args.verification_report, allow_mixed=args.allow_mixed)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
