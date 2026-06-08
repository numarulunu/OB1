from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


RAW_PAYLOAD_KEYS = {
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
    "beam_state_ledger",
    "beam_state_verifier",
    "beam_state_verifier_response",
    "beam_deterministic_state",
}
SAFE_BOOLEAN_FLAG_KEYS = {
    "beam_state_ledger",
    "beam_state_verifier",
}
PRIVATE_PATH_RE = re.compile(r"/opt/kontext/private/[^\s\"';]+")
SECRET_SHAPED_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|OPENROUTER_API_KEY)\s*="),
]


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def raw_payload_hits(value: Any) -> list[str]:
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = f"{path}/{key}" if path else str(key)
                if key in RAW_PAYLOAD_KEYS and not (key in SAFE_BOOLEAN_FLAG_KEYS and isinstance(child, bool)):
                    hits.append(child_path)
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(value, "")
    return hits


def string_scan_counts(text: str) -> dict[str, int]:
    return {
        "private_path_hits": len(PRIVATE_PATH_RE.findall(text)),
        "secret_shaped_hits": sum(len(pattern.findall(text)) for pattern in SECRET_SHAPED_PATTERNS),
    }


def scan_file(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    raw_hits = raw_payload_hits(payload) if payload is not None else []
    string_counts = string_scan_counts(text)
    return {
        "path_name": target.name,
        "raw_payload_hits": len(raw_hits),
        "raw_payload_hit_paths": raw_hits[:20],
        **string_counts,
    }


def build_report(paths: list[str]) -> dict[str, Any]:
    files = [scan_file(path) for path in paths]
    totals = {
        "raw_payload_hits": sum(int(row["raw_payload_hits"]) for row in files),
        "private_path_hits": sum(int(row["private_path_hits"]) for row in files),
        "secret_shaped_hits": sum(int(row["secret_shaped_hits"]) for row in files),
    }
    return {
        "ok": all(value == 0 for value in totals.values()),
        "mode": "public-artifact-scan",
        "files_scanned": len(files),
        "totals": totals,
        "files": files,
        "notes": [
            "This scanner reports counts and raw key paths only; it never echoes raw payload values.",
            "Safe boolean feature flags are not counted as raw payload even when their names overlap internal raw debug keys.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan public artifacts for raw-payload keys, private paths, and secret-shaped strings.")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.paths)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
