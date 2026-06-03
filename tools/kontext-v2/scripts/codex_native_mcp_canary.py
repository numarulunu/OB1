from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


REQUIRED_MARKERS = (
    "MEM0_CANARY_OK",
    "MEM0_TEMP_DELETED",
    "KONTEXT_CANARY_OK",
    "KONTEXT_WRITES_DRY_RUN_OK",
)


def build_prompt(nonce: str) -> str:
    return f"""Run a native MCP canary and keep the final answer sanitized.

Do not print secrets, tokens, URLs with tokens, raw memory text, raw chats, raw logs, or raw MCP payloads.
Use only aggregate booleans in the final answer.

Canary nonce: {nonce}

Steps:
1. Call mem0.ingestion_status with recent_limit=1 and verify it returns aggregate health.
2. Use mem0.save to create exactly one temporary memory with this content: "Kontext native Codex MCP canary {nonce}".
   Metadata should be low priority: domains=["ai","systems","memory"], memory_type="project_state", memory_tier="cold", current_status="resolved", signal_strength=1.
3. If mem0.save returns an id, call mem0.fetch for that exact id, then mem0.delete for that exact id with reason="temporary native Codex MCP canary cleanup".
4. Call mem0_kontext_canary.ingestion_status if that MCP is available.
5. Call mem0_kontext_canary.save with the same canary content if that MCP is available. It must remain dry-run: mode should be dry_run and writes_applied should be 0.

Final answer must be a single JSON object and nothing else, with exactly these keys:
{{
  "MEM0_CANARY_OK": true_or_false,
  "MEM0_TEMP_DELETED": true_or_false,
  "KONTEXT_CANARY_OK": true_or_false,
  "KONTEXT_WRITES_DRY_RUN_OK": true_or_false
}}
"""


def build_codex_command(codex_bin: str, workdir: Path, output_path: Path) -> list[str]:
    return [
        codex_bin,
        "-a",
        "never",
        "exec",
        "--sandbox",
        "danger-full-access",
        "--ephemeral",
        "-C",
        str(workdir),
        "-o",
        str(output_path),
        "-",
    ]


def resolve_codex_bin(codex_bin: str) -> str:
    resolved = shutil.which(codex_bin)
    if resolved:
        return resolved

    candidate = Path(codex_bin)
    if candidate.exists():
        return str(candidate)

    if codex_bin == "codex" and os.name == "nt":
        roots = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "npm")
        roots.append(Path.home() / "AppData" / "Roaming" / "npm")
        for root in roots:
            for name in ("codex.cmd", "codex.exe", "codex"):
                possible = root / name
                if possible.exists():
                    return str(possible)

    raise FileNotFoundError(f"Could not find Codex binary: {codex_bin}. Pass --codex-bin with the full path.")


def parse_canary_result(text: str) -> dict[str, bool]:
    decoder = json.JSONDecoder()
    parsed: dict[str, bool] | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            parsed = {key: bool(candidate.get(key)) for key in REQUIRED_MARKERS}
    if parsed is not None:
        return parsed

    return {key: (key in text and "false" not in text.lower()) for key in REQUIRED_MARKERS}


def _summary(ok: bool, markers: dict[str, bool], output_path: Path, log_path: Path | None, exit_code: int | None) -> dict[str, Any]:
    return {
        "ok": ok,
        "exit_code": exit_code,
        "markers": markers,
        "output_path": str(output_path),
        "log_path": str(log_path) if log_path else None,
    }


def _sanitized_preview(text: str, *, max_lines: int = 12, max_chars: int = 1800) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "body:" in line and len(line) > 220:
            line = line.split("body:", 1)[0] + "body:<omitted>"
        line = re.sub(r"(?i)(api[_-]?key|authorization|password|token|secret)([=:]\s*)[^\s,;]+", r"\1\2<redacted>", line)
        line = re.sub(r"https?://\S+", "<url>", line)
        lines.append(line[:300])
        if len(lines) >= max_lines:
            break
    preview = json.dumps(lines)
    while len(preview) > max_chars and lines:
        lines.pop()
        preview = json.dumps(lines)
    return lines


def run_native_canary(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%dT%H%M%S")
    nonce = args.nonce or f"native-codex-canary-{stamp}"
    output_path = output_dir / f"kontext-native-codex-canary-{stamp}.final.txt"
    log_path = output_dir / f"kontext-native-codex-canary-{stamp}.log"
    prompt = build_prompt(nonce)
    codex_bin = resolve_codex_bin(args.codex_bin)
    command = build_codex_command(codex_bin, workdir, output_path)

    if not args.execute:
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "dry_run",
                    "command": subprocess.list2cmdline(command),
                    "note": "Re-run with --execute to call nested Codex and perform one temp Mem0 save/delete canary.",
                },
                indent=2,
            )
        )
        return 0

    completed: subprocess.CompletedProcess[str] | None = None
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=args.timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        completed = subprocess.CompletedProcess(command, 124, exc.stdout or "", exc.stderr or "")

    final_text = output_path.read_text(encoding="utf-8", errors="replace") if output_path.exists() else ""
    markers = parse_canary_result(final_text)
    ok = (completed.returncode == 0) and all(markers.values()) and not timed_out

    log_payload = {
        "command": subprocess.list2cmdline(command),
        "exit_code": completed.returncode,
        "timed_out": timed_out,
        "stdout_bytes": len(completed.stdout or ""),
        "stderr_bytes": len(completed.stderr or ""),
        "stderr_preview": _sanitized_preview(completed.stderr or ""),
        "output_bytes": len(final_text),
        "markers": markers,
    }
    log_path.write_text(json.dumps(log_payload, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(_summary(ok, markers, output_path, log_path, completed.returncode), indent=2))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a sanitized nested-Codex MCP canary for Mem0 plus the Kontext dry-run canary."
    )
    parser.add_argument("--execute", action="store_true", help="Actually run nested Codex. Default only prints the command.")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--output-dir", default="test-results")
    parser.add_argument("--timeout-sec", type=int, default=420)
    parser.add_argument("--nonce")
    return run_native_canary(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
