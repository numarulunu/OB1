from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


RAW_PAYLOAD_KEYS = {
    "answer",
    "content",
    "conversation",
    "ground_truth",
    "ground_truth_answer",
    "memory",
    "message",
    "messages",
    "question",
    "raw_answer",
    "raw_memory",
    "raw_question",
    "retrieved_memories",
    "retrieved_memories_by_top_k",
    "text",
}


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def hit_metric(summary: dict[str, Any], cutoff: str, metric: str) -> float:
    hits = summary.get("hits_at_k") if isinstance(summary.get("hits_at_k"), dict) else {}
    row = hits.get(cutoff) if isinstance(hits.get(cutoff), dict) else {}
    return round(safe_float(row.get(metric)), 4)


def raw_payload_paths(value: Any, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key) in RAW_PAYLOAD_KEYS:
                paths.append(child_path)
                continue
            paths.extend(raw_payload_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(raw_payload_paths(child, f"{path}[{index}]"))
    return paths


def gate_row(actual: float, minimum: float) -> dict[str, Any]:
    return {
        "ok": actual >= minimum,
        "actual": round(actual, 4),
        "minimum": round(minimum, 4),
    }


def build_gate(
    report_path: str | Path,
    label: str = "beam",
    min_top_k50: float = 0.0,
    min_top_k200: float = 1.0,
    min_mrr50: float = 0.0,
    require_dropped: bool = False,
) -> dict[str, Any]:
    report = load_json(report_path)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    raw_paths = raw_payload_paths(report)
    top_k50 = hit_metric(summary, "50", "rate")
    top_k200 = hit_metric(summary, "200", "rate")
    mrr50 = hit_metric(summary, "50", "mrr")
    dropped_ok = not require_dropped or report.get("dropped") is True
    gates = {
        "top_k50": gate_row(top_k50, min_top_k50),
        "top_k200": gate_row(top_k200, min_top_k200),
        "mrr50": gate_row(mrr50, min_mrr50),
        "dropped": {"ok": dropped_ok, "required": require_dropped, "actual": report.get("dropped") is True},
        "raw_payload": {"ok": not raw_paths, "hit_count": len(raw_paths), "hit_paths": raw_paths[:20]},
    }
    blocked_by = []
    if not gates["top_k50"]["ok"]:
        blocked_by.append("top_k50 below floor")
    if not gates["top_k200"]["ok"]:
        blocked_by.append("top_k200 below floor")
    if not gates["mrr50"]["ok"]:
        blocked_by.append("mrr50 below floor")
    if not gates["dropped"]["ok"]:
        blocked_by.append("isolated schema was not dropped")
    if not gates["raw_payload"]["ok"]:
        blocked_by.append("raw payload keys present")
    return {
        "ok": not blocked_by,
        "mode": "beam-diagnostics-gate",
        "runs_model_calls": False,
        "label": label,
        "report": str(report_path),
        "dataset": report.get("dataset"),
        "blocked_by": blocked_by,
        "summary": {
            "question_count": safe_int(summary.get("question_count")),
            "missing_evidence_count": safe_int(summary.get("missing_evidence_count")),
            "max_first_hit_rank": summary.get("max_first_hit_rank"),
            "top_k50_rate": top_k50,
            "top_k200_rate": top_k200,
            "mrr50": mrr50,
        },
        "gates": gates,
        "notes": [
            "This gate does not call models.",
            "It validates sanitized BEAM rank diagnostic reports against explicit floors.",
            "It is a regression guard for benchmark diagnostics, not a production cutover signal.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate sanitized BEAM diagnostic summaries against regression floors.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--label", default="beam")
    parser.add_argument("--min-top-k50", type=float, default=0.0)
    parser.add_argument("--min-top-k200", type=float, default=1.0)
    parser.add_argument("--min-mrr50", type=float, default=0.0)
    parser.add_argument("--require-dropped", action="store_true")
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_gate(
        args.report,
        label=args.label,
        min_top_k50=args.min_top_k50,
        min_top_k200=args.min_top_k200,
        min_mrr50=args.min_mrr50,
        require_dropped=args.require_dropped,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
