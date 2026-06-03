#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


MRR_KEYS = {"mrr", "first_satisfying_mrr", "mean_reciprocal_rank"}
LATENCY_KEYS = {"avg_latency_ms", "avg_search_latency_ms", "average_latency_ms", "latency_ms_avg"}
FLAG_KEYS = {"flags", "enabled_flags", "non_default_flags", "benchmark_flags"}


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _numbers_for_keys(payload: dict[str, Any], keys: set[str]) -> list[float]:
    values: list[float] = []
    for key, value in _walk(payload):
        if str(key) not in keys:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _flag_labels(label: str, payload: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key, value in _walk(payload):
        if str(key) not in FLAG_KEYS:
            continue
        if isinstance(value, dict):
            labels.extend(f"{label}:{name}" for name, enabled in value.items() if bool(enabled))
        elif isinstance(value, list):
            labels.extend(f"{label}:{item}" for item in value if str(item).strip())
    return sorted(set(labels))


def _safe_summary(label: str, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    mrr_values = _numbers_for_keys(payload, MRR_KEYS)
    latency_values = _numbers_for_keys(payload, LATENCY_KEYS)
    return {
        "label": label,
        "path": str(path),
        "ok": bool(payload.get("ok", True)),
        "mode": str(payload.get("mode") or ""),
        "mrr": max(mrr_values) if mrr_values else None,
        "latency_ms": max(latency_values) if latency_values else None,
        "benchmark_flags": _flag_labels(label, payload),
    }


def evaluate_reports(
    inputs: list[tuple[str, Path]],
    *,
    min_mrr: float = 0.99,
    max_latency_ms: float = 900.0,
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    blockers: list[str] = []
    all_flags: list[str] = []

    for label, path in inputs:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            summaries.append({"label": label, "path": str(path), "ok": False, "error": type(exc).__name__})
            blockers.append(f"{label} could not be read")
            continue
        if not isinstance(payload, dict):
            summaries.append({"label": label, "path": str(path), "ok": False, "error": "not_object"})
            blockers.append(f"{label} is not a JSON object")
            continue

        summary = _safe_summary(label, path, payload)
        summaries.append(summary)
        all_flags.extend(summary["benchmark_flags"])

        if summary["ok"] is not True:
            blockers.append(f"{label} report ok=false")
        if summary["mrr"] is not None and summary["mrr"] < min_mrr:
            blockers.append(f"{label} mrr {summary['mrr']:.4f} below {min_mrr:.4f}")
        if summary["latency_ms"] is not None and summary["latency_ms"] > max_latency_ms:
            blockers.append(f"{label} latency {summary['latency_ms']:.1f}ms above {max_latency_ms:.1f}ms")

    labels = sorted(set(all_flags))
    return {
        "ok": not blockers,
        "mode": "kontext-regression-canary",
        "reports_checked": len(inputs),
        "thresholds": {"min_mrr": min_mrr, "max_latency_ms": max_latency_ms},
        "benchmark_flags": {"active": bool(labels), "labels": labels},
        "blockers": blockers,
        "reports": summaries,
    }


def _parse_input(value: str) -> tuple[str, Path]:
    if "=" in value:
        label, raw_path = value.split("=", 1)
        label = label.strip()
    else:
        raw_path = value
        label = Path(raw_path).stem
    path = Path(raw_path).expanduser()
    if not label:
        raise argparse.ArgumentTypeError("input label cannot be empty")
    return label, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate sanitized Kontext regression reports.")
    parser.add_argument("--input", action="append", type=_parse_input, required=True, help="LABEL=path to a sanitized JSON report")
    parser.add_argument("--output", help="Optional JSON output path")
    parser.add_argument("--min-mrr", type=float, default=0.99)
    parser.add_argument("--max-latency-ms", type=float, default=900.0)
    args = parser.parse_args(argv)

    result = evaluate_reports(args.input, min_mrr=args.min_mrr, max_latency_ms=args.max_latency_ms)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).expanduser().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
