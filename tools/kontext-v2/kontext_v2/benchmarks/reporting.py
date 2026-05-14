from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kontext_v2.benchmarks.adapter import stable_hash


def build_predict_only_report(
    dataset: str,
    run_id: str,
    top_k: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "matched": 0})
    questions = []
    latencies = []
    for item in results:
        category = str(item.get("category") or "unknown")
        matched = bool(item.get("matched"))
        latency = round(float(item.get("search_latency_ms") or 0), 1)
        latencies.append(latency)
        categories[category]["total"] += 1
        categories[category]["matched"] += int(matched)
        questions.append(
            {
                "question_id": item.get("question_id"),
                "category": category,
                "matched": matched,
                "search_latency_ms": latency,
                "result_ids": [str(value) for value in item.get("result_ids") or []],
                "result_hashes": [
                    stable_hash(str(row.get("memory") or ""))[:16]
                    for row in item.get("search_results") or []
                ],
            }
        )
    total = len(results)
    matched_total = sum(1 for item in results if item.get("matched"))
    return {
        "dataset": dataset,
        "run_id": run_id,
        "mode": "predict-only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "top_k": int(top_k),
        "total_questions": total,
        "matched_questions": matched_total,
        "match_rate": round(matched_total / total, 4) if total else 0.0,
        "average_search_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "categories": dict(sorted(categories.items())),
        "questions": questions,
    }


def write_report_files(report: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    stem = f"{report['dataset']}-{report['run_id']}-predict-only"
    json_path = target / f"{stem}.json"
    markdown_path = target / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        f"# {report['dataset']} Predict-Only Report",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Total questions: {report['total_questions']}",
        f"- Matched questions: {report['matched_questions']}",
        f"- Match rate: {report['match_rate']}",
        f"- Average search latency ms: {report['average_search_latency_ms']}",
        "",
        "## Categories",
        "",
    ]
    for category, values in sorted((report.get("categories") or {}).items()):
        lines.append(f"- `{category}`: {values['matched']}/{values['total']} matched")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
