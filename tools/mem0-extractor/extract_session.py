#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from extractor import build_extraction_prompt, metadata_quality_report, parse_llm_response, proposals_from_candidates
from grading import grade_rows
from llm_client import LLMConfig, extract_with_cache

DEFAULT_OUTPUT_ROOT = Path(".local") / "mem0-extractor"
DEFAULT_CACHE_PATH = DEFAULT_OUTPUT_ROOT / "extraction-cache.jsonl"
PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
}
PROVIDER_KEY_ENVS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def read_input(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        rows = []
        for index, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
            else:
                rows.append({"id": f"row-{index}", "content": str(row)})
        return rows
    if path.suffix.lower() == ".json":
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [row if isinstance(row, dict) else {"content": str(row)} for row in parsed]
        if isinstance(parsed, dict):
            for key in ("messages", "rows", "items", "conversation"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [row if isinstance(row, dict) else {"content": str(row)} for row in value]
            return [parsed]
    return [{"id": path.name, "content": text}]


def default_output_path() -> Path:
    return DEFAULT_OUTPUT_ROOT / f"dry-run-{utc_stamp()}.json"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_env_file(path: str) -> None:
    if not path:
        return
    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise SystemExit(f"Env file not found: {env_path}")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def llm_config_from_args(args: argparse.Namespace) -> LLMConfig:
    provider = args.llm_provider
    api_key_env = args.api_key_env or PROVIDER_KEY_ENVS[provider]
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"Missing API key env var: {api_key_env}")
    model = args.model.strip()
    if not model:
        raise SystemExit("--model is required when not using --deterministic-only")
    return LLMConfig(
        base_url=args.base_url or PROVIDER_BASE_URLS[provider],
        api_key=api_key,
        model=model,
        timeout=args.timeout,
    )

def build_report(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).expanduser()
    rows = read_input(input_path)
    candidates, drops = grade_rows(rows)
    if args.max_candidates and args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]
    prompt = build_extraction_prompt(candidates, session_label=input_path.name)
    if args.deterministic_only:
        extraction = proposals_from_candidates(candidates)
    else:
        load_env_file(args.env_file)
        raw_response = extract_with_cache(prompt, llm_config_from_args(args), args.cache)
        extraction = parse_llm_response(raw_response)
    report = {
        "summary": {
            "input": str(input_path),
            "input_rows": len(rows),
            "candidate_count": len(candidates),
            "drop_count": len(drops),
            "proposal_count": len(extraction.proposals),
            "metadata_quality": metadata_quality_report(extraction.proposals),
            "dry_run": bool(args.dry_run),
            "deterministic_only": bool(args.deterministic_only),
        },
        "candidates": [candidate.to_report_dict() for candidate in candidates],
        "drops": drops,
        "proposals": [proposal.to_dict() for proposal in extraction.proposals],
        "llm_prompt_preview": prompt if args.include_prompt else "",
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run Mem0 extraction proposal builder for session files.")
    parser.add_argument("--input", required=True, help="Input .jsonl, .json, or .txt session file.")
    parser.add_argument("--output", default="", help="Report path. Defaults under .local/mem0-extractor/.")
    parser.add_argument("--dry-run", action="store_true", help="Write proposals only; do not mutate Mem0.")
    parser.add_argument("--deterministic-only", action="store_true", help="Use deterministic grading only; no LLM call.")
    parser.add_argument("--include-prompt", action="store_true", help="Include the LLM prompt preview in the local report.")
    parser.add_argument("--max-candidates", type=int, default=80)
    parser.add_argument("--llm-provider", choices=["openrouter", "openai"], default="openrouter")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--model", default="qwen/qwen3-235b-a22b-2507")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.dry_run:
        print("error=Only --dry-run is enabled in extractor v1; live apply comes after review.", file=sys.stderr)
        return 2
    output = Path(args.output) if args.output else default_output_path()
    report = build_report(args)
    write_json(output, report)
    print(json.dumps({"output": str(output), **report["summary"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
