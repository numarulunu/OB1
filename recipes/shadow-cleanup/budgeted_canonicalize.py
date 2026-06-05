#!/usr/bin/env python3
"""Budgeted canonical rewrite pass for OB1 thoughts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

VERSION = "0.1"
DEFAULT_POLICY_VERSION = "human-memory-patterns-v2"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / ".local" / "open-brain-cleanup" / "budgeted"
LOG_PATH = Path(__file__).resolve().parent / "_budgeted-canonicalize.log"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

SUPABASE_URL = ""
SUPABASE_SERVICE_ROLE_KEY = ""
OPENROUTER_API_KEY = ""

SOURCE_BUDGETS: dict[str, dict[str, float | int]] = {
    "claude_history": {"target_rows": 1800, "max_pack_rows": 50, "max_canonicals_per_full_pack": 3, "min_delete_ratio": 0.70},
    "shadow_cleanup": {"target_rows": 600, "max_pack_rows": 50, "max_canonicals_per_full_pack": 5, "min_delete_ratio": 0.55},
    "kontext": {"target_rows": 850, "max_pack_rows": 50, "max_canonicals_per_full_pack": 8, "min_delete_ratio": 0.30},
    "gemini": {"target_rows": 250, "max_pack_rows": 40, "max_canonicals_per_full_pack": 4, "min_delete_ratio": 0.60},
    "whatsapp": {"target_rows": 200, "max_pack_rows": 40, "max_canonicals_per_full_pack": 3, "min_delete_ratio": 0.70},
    "chatgpt": {"target_rows": 150, "max_pack_rows": 40, "max_canonicals_per_full_pack": 3, "min_delete_ratio": 0.60},
    "missing": {"target_rows": 100, "max_pack_rows": 40, "max_canonicals_per_full_pack": 2, "min_delete_ratio": 0.75},
}

TEMP_MARKERS = ("\\temp", "/tmp", "appdata\\local\\temp", "codex", "claude-code")


class BudgetedCanonicalizeError(RuntimeError):
    """Plain-language CLI error."""


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)


def clean_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return cleaned[:80] or "missing"


def source_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", str(value or "").lower()).strip("_")
    return cleaned[:80] or "missing"


def metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata")
    return value if isinstance(value, dict) else {}


def source_of(record: dict[str, Any]) -> str:
    return source_key(str(metadata(record).get("source") or "missing"))


def project_key(record: dict[str, Any]) -> str:
    meta = metadata(record)
    value = str(meta.get("claude_project") or meta.get("cwd") or meta.get("project") or "missing")
    lower = value.lower()
    if any(marker in lower for marker in TEMP_MARKERS):
        return "temp-working-dir"
    return clean_key(value)


def topic_key(record: dict[str, Any]) -> str:
    topics = metadata(record).get("topics")
    if isinstance(topics, list) and topics:
        return clean_key(str(topics[0]))
    return "missing"


def bucket_key(record: dict[str, Any]) -> str:
    meta = metadata(record)
    return (
        f"source:{source_of(record)}|"
        f"project:{project_key(record)}|"
        f"type:{clean_key(str(meta.get('type') or 'missing'))}|"
        f"topic:{topic_key(record)}"
    )


def canonical_limit_for_bucket(source: str, row_count: int) -> int:
    budget = SOURCE_BUDGETS.get(source_key(source), SOURCE_BUDGETS["missing"])
    if row_count <= 0:
        return 0
    max_pack_rows = int(budget["max_pack_rows"])
    max_canonicals = int(budget["max_canonicals_per_full_pack"])
    scaled = math.ceil(row_count / max(1, max_pack_rows) * max_canonicals)
    return max(1, min(max_canonicals, scaled))


def ensure_output_dirs(root: Path) -> None:
    for name in ("snapshots", "packs", "proposals", "verified", "backups", "raw-failures", "apply", "reports", "junk", "cache", "waves"):
        (root / name).mkdir(parents=True, exist_ok=True)


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, dict) else default


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(path)


def text_hash(text: str) -> str:
    normalized = " ".join(str(text or "").lower().strip().split())
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def metadata_hash(record: dict[str, Any]) -> str:
    return stable_hash(metadata(record))


def row_cache_key(record: dict[str, Any], policy_version: str = DEFAULT_POLICY_VERSION) -> str:
    return stable_hash(
        {
            "policy_version": policy_version,
            "content_hash": text_hash(str(record.get("content") or "")),
            "metadata_hash": metadata_hash(record),
        }
    )


def pack_cache_key(pack: dict[str, Any], policy_version: str = DEFAULT_POLICY_VERSION) -> str:
    compact_items = []
    for item in pack.get("items") or []:
        if not isinstance(item, dict):
            continue
        compact_items.append(
            {
                "id": str(item.get("id") or ""),
                "content_hash": text_hash(str(item.get("content") or "")),
                "source": item.get("source"),
                "type": item.get("type"),
                "topics": item.get("topics") if isinstance(item.get("topics"), list) else [],
                "project": item.get("project"),
            }
        )
    return stable_hash(
        {
            "policy_version": policy_version,
            "cluster_key": pack.get("cluster_key"),
            "source": pack.get("source"),
            "canonical_limit": pack.get("canonical_limit"),
            "items": compact_items,
        }
    )


def pack_id_for(cluster_key: str, items: list[dict[str, Any]], canonical_limit: int) -> str:
    ids = [str(item.get("id") or "") for item in items]
    raw = json.dumps([cluster_key, ids, canonical_limit], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def compact_item(record: dict[str, Any]) -> dict[str, Any]:
    meta = metadata(record)
    return {
        "id": record.get("id"),
        "content": record.get("content") or "",
        "source": meta.get("source"),
        "type": meta.get("type"),
        "topics": meta.get("topics") if isinstance(meta.get("topics"), list) else [],
        "project": meta.get("claude_project") or meta.get("cwd") or meta.get("project"),
    }


def build_rewrite_packs(records: list[dict[str, Any]], source: str = "all", limit_rows: int = 0, max_pack_rows: int = 0) -> list[dict[str, Any]]:
    selected = [row for row in records if source == "all" or source_of(row) == source_key(source)]
    selected.sort(key=lambda row: bucket_key(row))
    if limit_rows:
        selected = selected[:limit_rows]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in selected:
        grouped.setdefault(bucket_key(row), []).append(row)

    packs: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        source_name = source_of(rows[0]) if rows else "missing"
        budget = SOURCE_BUDGETS.get(source_name, SOURCE_BUDGETS["missing"])
        budget_max_pack_rows = int(budget["max_pack_rows"])
        max_pack_rows = min(budget_max_pack_rows, max_pack_rows) if max_pack_rows else budget_max_pack_rows
        for start in range(0, len(rows), max_pack_rows):
            chunk = rows[start : start + max_pack_rows]
            canonical_limit = canonical_limit_for_bucket(source_name, len(chunk))
            items = [compact_item(row) for row in chunk]
            packs.append(
                {
                    "pack_id": pack_id_for(key, items, canonical_limit),
                    "kind": "budgeted_canonicalize_v1",
                    "cluster_key": key,
                    "source": source_name,
                    "record_count": len(items),
                    "canonical_limit": canonical_limit,
                    "min_delete_ratio": float(budget["min_delete_ratio"]),
                    "items": items,
                }
            )
    packs.sort(key=lambda pack: (-int(pack.get("record_count") or 0), str(pack.get("cluster_key") or ""), str(pack.get("pack_id") or "")))
    return packs


def build_agent_wave_packs(records: list[dict[str, Any]], source: str = "all", limit_rows: int = 0, max_pack_rows: int = 0) -> list[dict[str, Any]]:
    selected = [row for row in records if source == "all" or source_of(row) == source_key(source)]
    selected.sort(key=lambda row: (source_of(row), clean_key(str(metadata(row).get("type") or "missing")), topic_key(row), project_key(row), str(row.get("id") or "")))
    if limit_rows:
        selected = selected[:limit_rows]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in selected:
        grouped.setdefault(source_of(row), []).append(row)

    packs: list[dict[str, Any]] = []
    for source_name, rows in sorted(grouped.items()):
        budget = SOURCE_BUDGETS.get(source_name, SOURCE_BUDGETS["missing"])
        budget_max_pack_rows = int(budget["max_pack_rows"])
        pack_size = min(budget_max_pack_rows, max_pack_rows) if max_pack_rows else budget_max_pack_rows
        for index, start in enumerate(range(0, len(rows), pack_size), start=1):
            chunk = rows[start : start + pack_size]
            canonical_limit = canonical_limit_for_bucket(source_name, len(chunk))
            items = [compact_item(row) for row in chunk]
            cluster_key = f"agent-wave:{source_name}:{index:05d}"
            packs.append(
                {
                    "pack_id": pack_id_for(cluster_key, items, canonical_limit),
                    "kind": "budgeted_agent_wave_v1",
                    "cluster_key": cluster_key,
                    "source": source_name,
                    "record_count": len(items),
                    "canonical_limit": canonical_limit,
                    "min_delete_ratio": float(budget["min_delete_ratio"]),
                    "items": items,
                }
            )
    return packs



def build_budgeted_prompt(pack: dict[str, Any]) -> str:
    public_pack = {
        "pack_id": pack.get("pack_id"),
        "source": pack.get("source"),
        "cluster_key": pack.get("cluster_key"),
        "canonical_limit": pack.get("canonical_limit"),
        "min_delete_ratio": pack.get("min_delete_ratio"),
        "items": pack.get("items") or [],
    }
    limit = int(pack.get("canonical_limit") or 1)
    return f"""
You are rebuilding a second-brain memory database under a hard row budget.

Goal: replace raw, redundant, stale, or implementation-level rows with a tiny set of durable canonical memories.

Return strict JSON only. Do not include prose.

Hard rules:
- Create at most {limit} canonical_memories.
- Every input id must appear exactly once across canonical_memories.source_ids, delete_source_ids, keep_source_ids, or escalate_source_ids.
- Prefer canonical memories over raw keeps.
- Keep max 2 raw rows unless the pack contains critical current identity, money, legal/fiscal, relationship, or active project constraints.
- Escalate max 1 row unless the pack is genuinely unsafe.
- Delete implementation detail, process noise, obsolete choices, repeated facts, and raw chatter.
- For code/project/workflow material, keep the purpose and current architecture, not function names, line numbers, test status, flags, or logs.
- For psychology/relationships, keep stable current patterns and important close/influential people, not one-off incidents.
- For voice/vocal material, keep only current Vocality/brand/method IP or reusable teaching assets; delete raw lesson chatter.
- If a row is important, compress it into a canonical memory and delete the raw original unless the exact wording must remain as a reference.

Schema:
{{
  "pack_id": "same pack id",
  "bucket_status": "complete|partial|unsafe",
  "canonical_memories": [
    {{"content": "current durable memory", "source_ids": ["ids"], "type": "decision|preference|context|learning|reference|goal|project|workflow|pattern", "topics": ["short topics"], "confidence": 0.0, "reason": "short non-private reason"}}
  ],
  "delete_source_ids": ["ids safe to delete with no canonical because they are pure junk"],
  "keep_source_ids": ["ids that must remain raw"],
  "escalate_source_ids": ["ids too risky for automated handling"],
  "risk_notes": ["short notes, no raw quotes"]
}}

Pack:
{json.dumps(public_pack, ensure_ascii=False)}
""".strip()


def build_human_memory_prompt(pack: dict[str, Any]) -> str:
    public_pack = {
        "pack_id": pack.get("pack_id"),
        "source": pack.get("source"),
        "cluster_key": pack.get("cluster_key"),
        "canonical_limit": pack.get("canonical_limit"),
        "min_delete_ratio": pack.get("min_delete_ratio"),
        "items": pack.get("items") or [],
    }
    limit = int(pack.get("canonical_limit") or 1)
    return f"""
You are compacting a second-brain database to mimic human memory.

Human memory is lossy, gist-based, current-goal-focused, and aggressively forgets raw detail after extracting the durable pattern.

Return strict JSON only. Do not include prose.

Hard rules:
- Create at most {limit} canonical_memories.
- Every input id must appear exactly once across canonical_memories.source_ids, delete_source_ids, keep_source_ids, or escalate_source_ids.
- Store schemas, patterns, current truths, preferences, goals, systems, relationships, lessons, and project state. Do not store transcript fragments.
- Current truth beats history. Delete outdated decisions unless the change itself reveals a recurring blind spot or prevents future confusion.
- A memory survives only if it changes future advice, project continuity, relationship understanding, or user-model accuracy.
- Compress many examples into one durable rule whenever possible.
- Ask the user by using escalate_source_ids when confidence is low, facts conflict, or deletion could erase an important current self/project/relationship signal.
- Keep raw rows only as rare anchors: legal/financial constraints, exact commitments, relationship-defining events, or operational settings that must remain exact.
- For code/project/workflow rows, keep purpose, current architecture, and next direction; delete code snippets, flags, test logs, implementation trivia, and status noise.
- Delete transcript-cleanup, diarization, speaker-labeling, and formatting-process rows by default; keep them only when they are current operating settings for an active reusable tool/product.
- For psychology/relationships, keep stable patterns and close/influential people; delete one-off chatter.
- For voice/vocal rows, default-delete student lessons, generic pedagogy, anatomy trivia, and other people's vocal/health context.
- Keep vocal rows only when they preserve the user's own voice/career context: current vocal development, role/Fach/repertoire direction, vocal or physical characteristics useful for opera-career planning, current technical focus, personal method in a nutshell, or active constraints affecting singing/career.
- Keep important teacher/mentor context only when it explains technical influence, personal influence, or a close career-shaping relationship; do not keep the mentor's unrelated problems as user context.
- Keep the core Vocality teaching method/guidelines/rules/approach only as compact reusable IP or business context. Do not preserve individual student lesson chatter unless it reveals a reusable Vocality asset or business pattern.
- Treat Rigoletto/Sparafucile-style role opportunities as historical/delete unless the row clearly says the opportunity is current.

Memory buckets to prefer:
- north_star
- self_model
- project
- system
- business_brand
- person_relationship
- preference_setting
- lesson_pattern

Schema:
{{
  "pack_id": "same pack id",
  "bucket_status": "complete|partial|unsafe",
  "canonical_memories": [
    {{"content": "compact current human-memory card", "source_ids": ["ids"], "type": "goal|context|decision|preference|project|workflow|pattern|relationship|lesson", "topics": ["short topics"], "confidence": 0.0, "reason": "short non-private reason"}}
  ],
  "delete_source_ids": ["ids safely forgotten after extraction or pure junk"],
  "keep_source_ids": ["rare exact anchors that must remain raw"],
  "escalate_source_ids": ["ids needing user review / brainstorm"],
  "risk_notes": ["short notes, no raw quotes"]
}}

Pack:
{json.dumps(public_pack, ensure_ascii=False)}
""".strip()



def build_pack_prompt(pack: dict[str, Any], policy: str = "human") -> str:
    if policy == "human":
        return build_human_memory_prompt(pack)
    if policy == "budgeted":
        return build_budgeted_prompt(pack)
    raise BudgetedCanonicalizeError(f"Unknown prompt policy: {policy}")



def build_verifier_prompt(pack: dict[str, Any], proposal: dict[str, Any]) -> str:
    public_pack = {
        "pack_id": pack.get("pack_id"),
        "source": pack.get("source"),
        "cluster_key": pack.get("cluster_key"),
        "items": pack.get("items") or [],
    }
    public_proposal = {
        "pack_id": proposal.get("pack_id"),
        "bucket_status": proposal.get("bucket_status"),
        "canonical_memories": proposal.get("canonical_memories") or [],
        "delete_source_ids": proposal.get("delete_source_ids") or [],
        "keep_source_ids": proposal.get("keep_source_ids") or [],
        "escalate_source_ids": proposal.get("escalate_source_ids") or [],
    }
    return f"""
You are the safety verifier for a second-brain cleanup proposal.

Check whether the canonical memories preserve all important current/useful facts from the original rows that would be deleted.

Do not approve deletion if any important current fact, preference, project decision, workflow purpose, relationship pattern, business/finance constraint, or psychology/self-model signal would be lost.

Return strict JSON only:
{{
  "pack_id": "same pack id",
  "coverage_ok": true,
  "unsafe_delete_ids": ["ids that should not be deleted"],
  "missing_facts": ["short non-private descriptions of important missing facts"],
  "risk_notes": ["short notes, no raw quotes"]
}}

Original pack:
{json.dumps(public_pack, ensure_ascii=False)}

Cleanup proposal:
{json.dumps(public_proposal, ensure_ascii=False)}
""".strip()


def build_repair_prompt(pack: dict[str, Any], rejected_row: dict[str, Any]) -> str:
    proposal = rejected_row.get("proposal") if isinstance(rejected_row.get("proposal"), dict) else {}
    verification = rejected_row.get("verification") if isinstance(rejected_row.get("verification"), dict) else {}
    public_pack = {
        "pack_id": pack.get("pack_id"),
        "source": pack.get("source"),
        "cluster_key": pack.get("cluster_key"),
        "canonical_limit": pack.get("canonical_limit"),
        "min_delete_ratio": pack.get("min_delete_ratio"),
        "items": pack.get("items") or [],
    }
    public_proposal = {
        "pack_id": proposal.get("pack_id"),
        "bucket_status": proposal.get("bucket_status"),
        "canonical_memories": proposal.get("canonical_memories") or [],
        "delete_source_ids": proposal.get("delete_source_ids") or [],
        "keep_source_ids": proposal.get("keep_source_ids") or [],
        "escalate_source_ids": proposal.get("escalate_source_ids") or [],
    }
    public_feedback = {
        "coverage_ok": verification.get("coverage_ok"),
        "unsafe_delete_ids": verification.get("unsafe_delete_ids") or [],
        "missing_facts": verification.get("missing_facts") or [],
        "risk_notes": verification.get("risk_notes") or [],
    }
    limit = int(pack.get("canonical_limit") or 1)
    return f"""
Repair the cleanup proposal for a second-brain memory database.

The previous proposal failed verifier coverage. Produce a corrected proposal that fixes the verifier feedback without loosening the cleanup policy.

Return strict JSON only. Do not include prose.

Hard rules:
- Create at most {limit} canonical_memories.
- Every input id must appear exactly once across canonical_memories.source_ids, delete_source_ids, keep_source_ids, or escalate_source_ids.
- Preserve every important missing fact from verifier feedback, either in a canonical memory or by keeping/escalating the relevant source row.
- Any verifier unsafe_delete_ids must not be deleted directly. Put them in canonical_memories.source_ids only if the repaired canonical fully preserves their important facts; otherwise put them in keep_source_ids or escalate_source_ids.
- Prefer canonical memories over raw keeps, but do not sacrifice current useful facts to hit the row budget.
- Delete implementation detail, process noise, obsolete choices, repeated facts, and raw chatter.
- For code/project/workflow material, keep the purpose and current architecture, not function names, line numbers, test status, flags, or logs.
- For psychology/relationships, keep stable current patterns and important close/influential people, not one-off incidents.
- For voice/vocal material, keep only current Vocality/brand/method IP or reusable teaching assets; delete raw lesson chatter.

Schema:
{{
  "pack_id": "same pack id",
  "bucket_status": "complete|partial|unsafe",
  "canonical_memories": [
    {{"content": "current durable memory", "source_ids": ["ids"], "type": "decision|preference|context|learning|reference|goal|project|workflow|pattern", "topics": ["short topics"], "confidence": 0.0, "reason": "short non-private reason"}}
  ],
  "delete_source_ids": ["ids safe to delete with no canonical because they are pure junk"],
  "keep_source_ids": ["ids that must remain raw"],
  "escalate_source_ids": ["ids too risky for automated handling"],
  "risk_notes": ["short notes, no raw quotes"]
}}

Original pack:
{json.dumps(public_pack, ensure_ascii=False)}

Rejected cleanup proposal:
{json.dumps(public_proposal, ensure_ascii=False)}

Verifier feedback to fix:
{json.dumps(public_feedback, ensure_ascii=False)}
""".strip()



def parse_model_json(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise BudgetedCanonicalizeError("Model response was not valid JSON.")
        parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise BudgetedCanonicalizeError("Model response had an unexpected shape.")
    return parsed


def valid_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if UUID_RE.match(str(item).strip())]


def validate_proposal(pack: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    input_ids = [str(item.get("id") or "") for item in pack.get("items") or []]
    input_set = set(input_ids)
    canonical_ids: list[str] = []
    memories = proposal.get("canonical_memories") or []
    if not isinstance(memories, list):
        memories = []
    for memory in memories:
        if isinstance(memory, dict):
            canonical_ids.extend(valid_id_list(memory.get("source_ids")))
    delete_ids = valid_id_list(proposal.get("delete_source_ids"))
    keep_ids = valid_id_list(proposal.get("keep_source_ids"))
    escalate_ids = valid_id_list(proposal.get("escalate_source_ids"))
    all_ids = canonical_ids + delete_ids + keep_ids + escalate_ids
    counts: dict[str, int] = {}
    for thought_id in all_ids:
        counts[thought_id] = counts.get(thought_id, 0) + 1
    duplicate_ids = sorted([thought_id for thought_id, count in counts.items() if count > 1])
    unknown_ids = sorted([thought_id for thought_id in counts if thought_id not in input_set])
    missing_ids = sorted([thought_id for thought_id in input_set if thought_id not in counts])
    canonical_limit = int(pack.get("canonical_limit") or 0)
    canonical_limit_exceeded = len(memories) > canonical_limit
    ok = not duplicate_ids and not unknown_ids and not missing_ids and not canonical_limit_exceeded
    return {
        "ok": ok,
        "duplicate_ids": duplicate_ids,
        "unknown_ids": unknown_ids,
        "missing_ids": missing_ids,
        "canonical_limit_exceeded": canonical_limit_exceeded,
        "canonical_count": len(memories),
        "delete_count": len(delete_ids),
        "keep_count": len(keep_ids),
        "escalate_count": len(escalate_ids),
    }


def validate_verification(pack: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    input_set = {str(item.get("id") or "") for item in pack.get("items") or []}
    unsafe_ids = valid_id_list(verification.get("unsafe_delete_ids"))
    unknown_ids = sorted([thought_id for thought_id in unsafe_ids if thought_id not in input_set])
    ok = not unknown_ids and isinstance(verification.get("coverage_ok"), bool)
    return {"ok": ok, "unknown_ids": unknown_ids, "unsafe_delete_ids": unsafe_ids, "coverage_ok": verification.get("coverage_ok") is True}


def build_apply_plan(proposal_rows: list[dict[str, Any]], require_verified: bool = True) -> dict[str, Any]:
    canonicals: list[dict[str, Any]] = []
    delete_ids: list[str] = []
    keep_or_escalate: set[str] = set()
    verifier_unsafe: set[str] = set()
    skipped_unverified = 0
    accepted_rows: list[dict[str, Any]] = []

    for row in proposal_rows:
        verification = row.get("verification") if isinstance(row.get("verification"), dict) else None
        if require_verified and not (verification and verification.get("coverage_ok") is True):
            skipped_unverified += 1
            continue
        if verification:
            verifier_unsafe.update(valid_id_list(verification.get("unsafe_delete_ids")))
        accepted_rows.append(row)
        proposal = row.get("proposal") or {}
        keep_or_escalate.update(valid_id_list(proposal.get("keep_source_ids")))
        keep_or_escalate.update(valid_id_list(proposal.get("escalate_source_ids")))

    for row in accepted_rows:
        proposal = row.get("proposal") or {}
        for memory in proposal.get("canonical_memories") or []:
            if not isinstance(memory, dict) or not str(memory.get("content") or "").strip():
                continue
            canonicals.append({"row": row, "memory": memory})
            for thought_id in valid_id_list(memory.get("source_ids")):
                if thought_id not in keep_or_escalate and thought_id not in verifier_unsafe and thought_id not in delete_ids:
                    delete_ids.append(thought_id)
        for thought_id in valid_id_list(proposal.get("delete_source_ids")):
            if thought_id not in keep_or_escalate and thought_id not in verifier_unsafe and thought_id not in delete_ids:
                delete_ids.append(thought_id)

    proposed_delete_ids = [
        thought_id
        for row in accepted_rows
        for thought_id in valid_id_list((row.get("proposal") or {}).get("delete_source_ids"))
    ]
    blocked_keep = sorted([thought_id for thought_id in proposed_delete_ids if thought_id in keep_or_escalate])
    blocked_verifier = sorted([thought_id for thought_id in proposed_delete_ids if thought_id in verifier_unsafe])
    return {
        "canonical_count": len(canonicals),
        "canonicals": canonicals,
        "delete_ids": delete_ids,
        "blocked_keep_or_escalate_ids": blocked_keep,
        "blocked_verifier_ids": blocked_verifier,
        "skipped_unverified": skipped_unverified,
    }


def junk_label_for_row(record: dict[str, Any]) -> dict[str, Any]:
    meta = metadata(record)
    content = str(record.get("content") or "")
    lower = " ".join(content.lower().split())
    if str(meta.get("created_by") or "") == "budgeted_canonicalize_v1" or str(meta.get("import_mode") or "") == "budgeted_canonicalize_v1":
        return {"ok": False, "reason": "protected_canonical", "confidence": 0.0}
    if "speaker diarization" in lower and ("inferred" in lower or "conversational cues" in lower or "successfully" in lower):
        return {"ok": True, "reason": "speaker_diarization_clutter", "confidence": 0.99}
    if "speaker identification" in lower and "aliases" in lower and "canonical names" in lower:
        return {"ok": True, "reason": "speaker_identity_alias_clutter", "confidence": 0.99}
    if re.search(r"\bstderr\s+empty\b", lower):
        return {"ok": True, "reason": "import_log_status", "confidence": 0.99}
    if re.search(r"\bsync\s+reached\s+\d+\b", lower):
        return {"ok": True, "reason": "import_log_status", "confidence": 0.99}
    if re.search(r"\b(processed|generated|ingested)\s+\d+\s+(sessions|thoughts|rows|packs)\b", lower):
        return {"ok": True, "reason": "import_log_status", "confidence": 0.98}
    if "proposal_records_written=" in lower or "verification_records_written=" in lower or "apply_plan proposal_rows=" in lower:
        return {"ok": True, "reason": "tool_output_status", "confidence": 0.99}
    return {"ok": False, "reason": "no_rule", "confidence": 0.0}



def build_junk_proposal(record: dict[str, Any], label: dict[str, Any]) -> dict[str, Any]:
    meta = metadata(record)
    return {
        "kind": "deterministic_junk_v1",
        "id": str(record.get("id") or ""),
        "source": source_of(record),
        "type": meta.get("type"),
        "topics": meta.get("topics") if isinstance(meta.get("topics"), list) else [],
        "reason": str(label.get("reason") or "unknown"),
        "confidence": float(label.get("confidence") or 0.0),
        "content_hash": text_hash(str(record.get("content") or "")),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }



def audit_junk_records(records: list[dict[str, Any]], source: str = "all", min_confidence: float = 0.98, limit_rows: int = 0) -> list[dict[str, Any]]:
    selected = [row for row in records if source == "all" or source_of(row) == source_key(source)]
    if limit_rows:
        selected = selected[:limit_rows]
    proposals: list[dict[str, Any]] = []
    for row in selected:
        label = junk_label_for_row(row)
        if label.get("ok") is True and float(label.get("confidence") or 0.0) >= min_confidence:
            proposals.append(build_junk_proposal(row, label))
    return proposals



def build_junk_apply_plan(proposal_rows: list[dict[str, Any]], min_confidence: float = 0.98) -> dict[str, Any]:
    delete_ids: list[str] = []
    skipped_low_confidence = 0
    skipped_invalid_id = 0
    reason_counts: dict[str, int] = {}
    for row in proposal_rows:
        thought_id = str(row.get("id") or "").strip()
        if not UUID_RE.match(thought_id):
            skipped_invalid_id += 1
            continue
        confidence = float(row.get("confidence") or 0.0)
        if confidence < min_confidence:
            skipped_low_confidence += 1
            continue
        if thought_id not in delete_ids:
            delete_ids.append(thought_id)
            reason = str(row.get("reason") or "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "delete_ids": delete_ids,
        "reason_counts": reason_counts,
        "skipped_low_confidence": skipped_low_confidence,
        "skipped_invalid_id": skipped_invalid_id,
    }



def load_junk_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") == "deterministic_junk_v1":
                rows.append(row)
    return rows



def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalize_supabase_url(url: str) -> str:
    value = str(url or "").strip().rstrip("/")
    if value.endswith("/rest/v1"):
        value = value[: -len("/rest/v1")]
    return value.rstrip("/")


def refresh_env() -> None:
    global SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENROUTER_API_KEY
    SUPABASE_URL = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


def require_supabase_env() -> None:
    missing = [name for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not os.environ.get(name)]
    if missing:
        raise BudgetedCanonicalizeError("Missing required environment variables: " + ", ".join(missing))


def require_openrouter_env() -> None:
    if not OPENROUTER_API_KEY:
        raise BudgetedCanonicalizeError("Missing required environment variable: OPENROUTER_API_KEY")


def supabase_headers(prefer: str = "return=representation") -> dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def fetch_thoughts(source: str, page_size: int, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    offset = 0
    while True:
        if limit is not None and len(rows) >= limit:
            break
        take = page_size if limit is None else min(page_size, limit - len(rows))
        params = {"select": "id,content,metadata,created_at,updated_at"}
        if source != "all":
            params["metadata->>source"] = f"eq.{source}"
        response = requests.get(base, headers={**supabase_headers(), "Range": f"{offset}-{offset + take - 1}"}, params=params, timeout=120)
        if response.status_code not in (200, 206):
            raise BudgetedCanonicalizeError(f"Supabase snapshot request failed with HTTP {response.status_code}.")
        batch = response.json()
        if not isinstance(batch, list):
            raise BudgetedCanonicalizeError("Supabase snapshot response had an unexpected shape.")
        rows.extend(batch)
        if len(batch) < take:
            break
        offset += take
    return rows


def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_thoughts_by_ids(ids: list[str], chunk_size: int = 100) -> list[dict[str, Any]]:
    valid_ids = [thought_id for thought_id in dict.fromkeys(ids) if UUID_RE.match(thought_id)]
    rows: list[dict[str, Any]] = []
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    for batch_ids in chunked(valid_ids, chunk_size):
        response = requests.get(base, headers=supabase_headers(), params={"select": "id,content,metadata,created_at,updated_at", "id": f"in.({','.join(batch_ids)})"}, timeout=120)
        if response.status_code != 200:
            raise BudgetedCanonicalizeError(f"Supabase fetch-by-id failed with HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, list):
            raise BudgetedCanonicalizeError("Supabase fetch-by-id response had an unexpected shape.")
        rows.extend(payload)
    return rows


def delete_thoughts_by_ids(ids: list[str], chunk_size: int = 100) -> int:
    valid_ids = [thought_id for thought_id in dict.fromkeys(ids) if UUID_RE.match(thought_id)]
    deleted = 0
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    for batch_ids in chunked(valid_ids, chunk_size):
        response = requests.delete(base, headers=supabase_headers(prefer="return=representation"), params={"select": "id", "id": f"in.({','.join(batch_ids)})"}, timeout=120)
        if response.status_code not in (200, 204):
            raise BudgetedCanonicalizeError(f"Supabase delete failed with HTTP {response.status_code}.")
        if response.status_code == 204 or not response.text.strip():
            deleted += len(batch_ids)
        else:
            payload = response.json()
            deleted += len(payload) if isinstance(payload, list) else 0
    return deleted


def generate_embedding(text: str) -> list[float]:
    response = requests.post(
        f"{OPENROUTER_BASE}/embeddings",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        json={"model": EMBEDDING_MODEL, "input": str(text or "")[:8000]},
        timeout=120,
    )
    if response.status_code != 200:
        raise BudgetedCanonicalizeError(f"Embedding request failed with HTTP {response.status_code}.")
    try:
        embedding = response.json()["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise BudgetedCanonicalizeError("Embedding response had an unexpected shape.") from exc
    if not isinstance(embedding, list):
        raise BudgetedCanonicalizeError("Embedding response had an unexpected shape.")
    return embedding


def insert_canonical_memory(content: str, metadata: dict[str, Any]) -> str:
    embedding = generate_embedding(content)
    response = requests.post(
        f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts?select=id",
        headers=supabase_headers(prefer="return=representation"),
        json={"content": content, "embedding": embedding, "metadata": metadata},
        timeout=120,
    )
    if response.status_code not in (200, 201):
        raise BudgetedCanonicalizeError(f"Canonical insert failed with HTTP {response.status_code}.")
    return str(response.json()[0]["id"])


def openrouter_chat(model: str, prompt: str, max_tokens: int, temperature: float) -> str:
    response = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "Return strict JSON only for database canonicalization."},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        },
        timeout=240,
    )
    if response.status_code != 200:
        raise BudgetedCanonicalizeError(f"OpenRouter request failed with HTTP {response.status_code}.")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise BudgetedCanonicalizeError("OpenRouter response had an unexpected shape.") from exc
    if not isinstance(content, str) or not content.strip():
        raise BudgetedCanonicalizeError("OpenRouter response was empty.")
    return content


def retry_operation(operation: Callable[[], Any], retries: int, retry_delay: float, label: str) -> Any:
    attempts = max(1, int(retries) + 1)
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= attempts:
                raise
            logging.warning("%s attempt %s/%s failed: %s", label, attempt, attempts, exc)
            if retry_delay > 0:
                time.sleep(retry_delay)
    raise BudgetedCanonicalizeError(f"{label} failed unexpectedly.")



def save_raw_failure(root: Path, pack_id: str, stage: str, content: str) -> Path:
    failure_dir = root / "raw-failures"
    failure_dir.mkdir(parents=True, exist_ok=True)
    path = failure_dir / f"{stage}-{pack_id}-{utc_stamp()}.txt"
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def processed_pack_ids(path: Path, require_verification: bool = False) -> set[str]:
    processed: set[str] = set()
    if not path.exists():
        return processed
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("error"):
            continue
        pack_id = row.get("pack_id")
        if isinstance(pack_id, str) and isinstance(row.get("proposal"), dict):
            if not require_verification or isinstance(row.get("verification"), dict):
                processed.add(pack_id)
    return processed


def valid_proposal_by_id(paths: list[Path]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            pack_id = row.get("pack_id")
            if row.get("error") or not isinstance(row.get("proposal"), dict) or not isinstance(pack_id, str):
                continue
            rows[pack_id] = row
    return rows


def repairable_rows_by_id(paths: list[Path]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            pack_id = row.get("pack_id")
            verification = row.get("verification") if isinstance(row.get("verification"), dict) else None
            if row.get("error") or row.get("verification_error") or not isinstance(row.get("proposal"), dict) or not isinstance(pack_id, str):
                continue
            if verification and verification.get("coverage_ok") is False:
                rows[pack_id] = row
    return rows



def load_valid_proposal_rows(paths: list[Path]) -> list[dict[str, Any]]:
    return list(valid_proposal_by_id(paths).values())


def normalize_proposal_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict) or row.get("error"):
        return None
    if isinstance(row.get("proposal"), dict) and isinstance(row.get("pack_id"), str):
        return row
    if isinstance(row.get("pack_id"), str):
        return {"pack_id": row.get("pack_id"), "source": row.get("source"), "cluster_key": row.get("cluster_key"), "proposal": row}
    return None


def load_proposal_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if not text.strip():
            continue
        stripped = text.lstrip()
        parsed_items: list[Any]
        if stripped.startswith("{") or stripped.startswith("["):
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("proposals"), list):
                parsed_items = parsed["proposals"]
            elif isinstance(parsed, list):
                parsed_items = parsed
            else:
                parsed_items = [parsed]
        else:
            parsed_items = [json.loads(line) for line in text.splitlines() if line.strip()]
        for item in parsed_items:
            normalized = normalize_proposal_row(item) if isinstance(item, dict) else None
            if normalized:
                rows.append(normalized)
    return rows


def source_decisions_for_proposal(proposal: dict[str, Any]) -> dict[str, str]:
    decisions: dict[str, str] = {}
    for memory in proposal.get("canonical_memories") or []:
        if isinstance(memory, dict):
            for thought_id in valid_id_list(memory.get("source_ids")):
                decisions[thought_id] = "canonical"
    for field, decision in (("delete_source_ids", "delete"), ("keep_source_ids", "keep"), ("escalate_source_ids", "escalate")):
        for thought_id in valid_id_list(proposal.get(field)):
            decisions[thought_id] = decision
    return decisions


def build_decision_cache_records(records: list[dict[str, Any]], proposal_rows: list[dict[str, Any]], policy_version: str = DEFAULT_POLICY_VERSION) -> list[dict[str, Any]]:
    rows_by_id = {str(row.get("id") or ""): row for row in records if UUID_RE.match(str(row.get("id") or ""))}
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for proposal_row in proposal_rows:
        proposal = proposal_row.get("proposal") or {}
        pack_id = str(proposal_row.get("pack_id") or proposal.get("pack_id") or "")
        for thought_id, decision in source_decisions_for_proposal(proposal).items():
            row = rows_by_id.get(thought_id)
            if not row:
                continue
            key = row_cache_key(row, policy_version)
            marker = (key, thought_id)
            if marker in seen:
                continue
            seen.add(marker)
            output.append(
                {
                    "cache_key": key,
                    "row_id": thought_id,
                    "policy_version": policy_version,
                    "decision": decision,
                    "source": source_of(row),
                    "content_hash": text_hash(str(row.get("content") or "")),
                    "metadata_hash": metadata_hash(row),
                    "pack_id": pack_id,
                }
            )
    return output


def load_cache_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if path.exists():
            records.extend(read_jsonl(path))
    return records


def build_wave_plan(
    records: list[dict[str, Any]],
    source: str = "all",
    policy_version: str = DEFAULT_POLICY_VERSION,
    row_cache_records: list[dict[str, Any]] | None = None,
    max_pack_rows: int = 0,
    limit_rows: int = 0,
    exclude_junk: bool = True,
) -> dict[str, Any]:
    selected = [row for row in records if source == "all" or source_of(row) == source_key(source)]
    cached_keys = {
        str(row.get("cache_key") or "")
        for row in (row_cache_records or [])
        if str(row.get("policy_version") or "") == policy_version and str(row.get("cache_key") or "")
    }
    candidates: list[dict[str, Any]] = []
    junk_records: list[dict[str, Any]] = []
    cached_rows = 0
    for row in selected:
        if row_cache_key(row, policy_version) in cached_keys:
            cached_rows += 1
            continue
        junk = junk_label_for_row(row) if exclude_junk else {"ok": False}
        if junk.get("ok") is True:
            junk_records.append({"id": row.get("id"), "reason": junk.get("reason"), "confidence": junk.get("confidence"), "source": source_of(row)})
            continue
        candidates.append(row)
    packs = build_agent_wave_packs(candidates, source=source, limit_rows=limit_rows, max_pack_rows=max_pack_rows)
    for pack in packs:
        pack["policy_version"] = policy_version
        pack["pack_cache_key"] = pack_cache_key(pack, policy_version)
    manifest = {
        "version": VERSION,
        "policy_version": policy_version,
        "source": source,
        "total_rows": len(selected),
        "cached_rows": cached_rows,
        "deterministic_junk_rows": len(junk_records),
        "candidate_rows": len(candidates[:limit_rows] if limit_rows else candidates),
        "pack_count": len(packs),
        "pack_rows": sum(int(pack.get("record_count") or 0) for pack in packs),
        "row_cache_records": len(row_cache_records or []),
        "exclude_junk": exclude_junk,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    return {"manifest": manifest, "packs": packs, "deterministic_junk": junk_records}


def canonical_metadata(row: dict[str, Any], memory: dict[str, Any], batch: str) -> dict[str, Any]:
    return {
        "source": "shadow_cleanup",
        "import_mode": "budgeted_canonicalize_v1",
        "type": memory.get("type") or "context",
        "topics": memory.get("topics") if isinstance(memory.get("topics"), list) else [],
        "budgeted_pack_id": row.get("pack_id"),
        "budgeted_source": row.get("source"),
        "budgeted_cluster_key": row.get("cluster_key"),
        "budgeted_source_ids": valid_id_list(memory.get("source_ids")),
        "budgeted_confidence": memory.get("confidence"),
        "budgeted_reason": memory.get("reason", ""),
        "budgeted_batch": batch,
        "created_by": "budgeted_canonicalize_v1",
    }


def cmd_audit_junk(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    if args.snapshot:
        rows = read_jsonl(args.snapshot)
    else:
        rows = fetch_thoughts(args.source, args.page_size, args.limit_rows or None)
    proposals = audit_junk_records(rows, source=args.source, min_confidence=args.min_confidence, limit_rows=args.limit_rows if args.snapshot else 0)
    output = args.output or args.output_root / "junk" / f"junk-{args.source}-{utc_stamp()}.jsonl"
    count = write_jsonl(output, proposals)
    reason_counts: dict[str, int] = {}
    for row in proposals:
        reason = str(row.get("reason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    logging.info("audit-junk source=%s rows=%s candidates=%s output=%s", args.source, len(rows), count, output)
    print(f"junk_candidates={count}")
    print("reason_counts=" + json.dumps(reason_counts, sort_keys=True))
    print(f"junk_file={output}")
    return 0



def cmd_apply_junk(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    rows = load_junk_rows(args.proposals)
    plan = build_junk_apply_plan(rows, min_confidence=args.min_confidence)
    delete_ids = plan["delete_ids"]
    fetched = fetch_thoughts_by_ids(delete_ids)
    fetched_ids = {str(row.get("id")) for row in fetched}
    live_delete_ids = [thought_id for thought_id in delete_ids if thought_id in fetched_ids]
    backup_file = args.backup_output or args.output_root / "backups" / f"junk-backup-{utc_stamp()}.jsonl"
    if args.max_delete and len(live_delete_ids) > args.max_delete:
        raise BudgetedCanonicalizeError(f"Junk apply plan has {len(live_delete_ids)} live deletes, above --max-delete {args.max_delete}.")
    deleted = 0
    if args.apply:
        backed_up = write_jsonl(backup_file, fetched)
        if backed_up != len(fetched):
            raise BudgetedCanonicalizeError("Backup row count did not match fetched delete rows.")
        deleted = delete_thoughts_by_ids(live_delete_ids)
    print(
        f"junk_apply_plan proposal_rows={len(rows)} live_delete_ids={len(live_delete_ids)} "
        f"skipped_low_confidence={plan['skipped_low_confidence']} skipped_invalid_id={plan['skipped_invalid_id']} "
        f"reason_counts={json.dumps(plan['reason_counts'], sort_keys=True)} deleted={deleted} "
        f"apply={args.apply} backup_file={backup_file if args.apply else 'none'}"
    )
    return 0



def cmd_cache_index(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    records = read_jsonl(args.snapshot)
    proposal_rows = load_proposal_rows(args.proposals)
    cache_rows = build_decision_cache_records(records, proposal_rows, args.policy_version)
    output = args.output or args.output_root / "cache" / f"row-cache-{args.policy_version}-{utc_stamp()}.jsonl"
    count = write_jsonl(output, cache_rows)
    decision_counts: dict[str, int] = {}
    for row in cache_rows:
        decision = str(row.get("decision") or "unknown")
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
    print(f"cache_records={count}")
    print("decision_counts=" + json.dumps(decision_counts, sort_keys=True))
    print(f"cache_file={output}")
    return 0


def cmd_wave(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    records = read_jsonl(args.snapshot)
    cache_records = load_cache_records(args.row_cache)
    plan = build_wave_plan(
        records,
        source=args.source,
        policy_version=args.policy_version,
        row_cache_records=cache_records,
        max_pack_rows=args.max_pack_rows,
        limit_rows=args.limit_rows,
        exclude_junk=not args.include_junk,
    )
    stamp = utc_stamp()
    packs_output = args.packs_output or args.output_root / "waves" / f"wave-packs-{args.source}-{args.policy_version}-{stamp}.jsonl"
    manifest_output = args.manifest_output or args.output_root / "waves" / f"wave-manifest-{args.source}-{args.policy_version}-{stamp}.json"
    junk_output = args.junk_output or args.output_root / "waves" / f"wave-junk-{args.source}-{args.policy_version}-{stamp}.jsonl"
    pack_count = write_jsonl(packs_output, plan["packs"])
    write_json(manifest_output, plan["manifest"])
    junk_count = write_jsonl(junk_output, plan["deterministic_junk"])
    manifest = plan["manifest"]
    print(
        f"wave total_rows={manifest['total_rows']} cached_rows={manifest['cached_rows']} "
        f"deterministic_junk_rows={manifest['deterministic_junk_rows']} candidate_rows={manifest['candidate_rows']} "
        f"pack_count={pack_count} pack_rows={manifest['pack_rows']}"
    )
    print(f"packs_file={packs_output}")
    print(f"manifest_file={manifest_output}")
    print(f"junk_file={junk_output} junk_records={junk_count}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    rows = fetch_thoughts(args.source, args.page_size, args.limit_rows)
    output = args.output or args.output_root / "snapshots" / f"{args.source}-{utc_stamp()}.jsonl"
    count = write_jsonl(output, rows)
    logging.info("snapshot source=%s rows=%s output=%s", args.source, count, output)
    print(f"snapshot_rows={count}")
    print(f"snapshot_file={output}")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    records = read_jsonl(args.snapshot)
    packs = build_rewrite_packs(records, source=args.source, limit_rows=args.limit_rows, max_pack_rows=args.max_pack_rows)
    output = args.output or args.output_root / "packs" / f"packs-{args.source}-{utc_stamp()}.jsonl"
    count = write_jsonl(output, packs)
    logging.info("pack source=%s records=%s packs=%s output=%s", args.source, len(records), count, output)
    print(f"pack_count={count}")
    print(f"pack_file={output}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    packs = read_jsonl(args.pack_file)
    output = args.output or args.output_root / "proposals" / f"proposals-{args.model.replace('/', '-')}-{utc_stamp()}.jsonl"
    processed = processed_pack_ids(output)
    written = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for pack in packs:
            if args.limit_packs and written >= args.limit_packs:
                break
            pack_id = str(pack.get("pack_id") or "")
            if pack_id in processed:
                continue
            def make_proposal_row() -> dict[str, Any]:
                content = openrouter_chat(args.model, build_pack_prompt(pack, args.prompt_policy), args.max_tokens, args.temperature)
                try:
                    proposal = parse_model_json(content)
                except Exception:
                    failure_path = save_raw_failure(args.output_root, pack_id, "proposal", content)
                    raise BudgetedCanonicalizeError(f"Proposal JSON parse failed; raw response saved to {failure_path.name}")
                validation = validate_proposal(pack, proposal)
                if not validation["ok"]:
                    raise BudgetedCanonicalizeError("Proposal failed validation: " + json.dumps(validation, sort_keys=True))
                return {
                    "pack_id": pack_id,
                    "pack_kind": pack.get("kind"),
                    "source": pack.get("source"),
                    "cluster_key": pack.get("cluster_key"),
                    "record_count": pack.get("record_count"),
                    "model": args.model,
                    "created_at": dt.datetime.now(dt.UTC).isoformat(),
                    "proposal": proposal,
                }

            try:
                row = retry_operation(make_proposal_row, args.retries, args.retry_delay, f"proposal pack_id={pack_id}")
            except Exception as exc:
                logging.exception("run failed pack_id=%s", pack_id)
                row = {"pack_id": pack_id, "pack_kind": pack.get("kind"), "source": pack.get("source"), "cluster_key": pack.get("cluster_key"), "record_count": pack.get("record_count"), "model": args.model, "created_at": dt.datetime.now(dt.UTC).isoformat(), "error": str(exc)}
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            written += 1
            time.sleep(args.sleep_seconds)
    print(f"proposal_records_written={written}")
    print(f"proposal_file={output}")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    packs = {str(pack.get("pack_id")): pack for pack in read_jsonl(args.pack_file)}
    rejected_rows = repairable_rows_by_id(args.verified)
    output = args.output or args.output_root / "proposals" / f"repairs-{args.model.replace('/', '-')}-{utc_stamp()}.jsonl"
    processed = processed_pack_ids(output)
    written = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for pack_id, rejected_row in rejected_rows.items():
            if args.limit_packs and written >= args.limit_packs:
                break
            if pack_id in processed:
                continue
            pack = packs.get(pack_id)
            if not pack:
                out_row = {"pack_id": pack_id, "error": "Pack was not found for repair."}
            else:
                def make_repair_row() -> dict[str, Any]:
                    content = openrouter_chat(args.model, build_repair_prompt(pack, rejected_row), args.max_tokens, args.temperature)
                    try:
                        proposal = parse_model_json(content)
                    except Exception:
                        failure_path = save_raw_failure(args.output_root, pack_id, "repair", content)
                        raise BudgetedCanonicalizeError(f"Repair JSON parse failed; raw response saved to {failure_path.name}")
                    validation = validate_proposal(pack, proposal)
                    if not validation["ok"]:
                        raise BudgetedCanonicalizeError("Repair failed validation: " + json.dumps(validation, sort_keys=True))
                    verification = rejected_row.get("verification") if isinstance(rejected_row.get("verification"), dict) else {}
                    return {
                        "pack_id": pack_id,
                        "pack_kind": pack.get("kind"),
                        "source": pack.get("source"),
                        "cluster_key": pack.get("cluster_key"),
                        "record_count": pack.get("record_count"),
                        "model": args.model,
                        "created_at": dt.datetime.now(dt.UTC).isoformat(),
                        "repair_of_model": rejected_row.get("model"),
                        "repair_feedback": {
                            "unsafe_delete_ids": verification.get("unsafe_delete_ids") or [],
                            "missing_facts": verification.get("missing_facts") or [],
                            "risk_notes": verification.get("risk_notes") or [],
                        },
                        "proposal": proposal,
                    }

                try:
                    out_row = retry_operation(make_repair_row, args.retries, args.retry_delay, f"repair pack_id={pack_id}")
                except Exception as exc:
                    logging.exception("repair failed pack_id=%s", pack_id)
                    out_row = {"pack_id": pack_id, "pack_kind": pack.get("kind"), "source": pack.get("source"), "cluster_key": pack.get("cluster_key"), "record_count": pack.get("record_count"), "model": args.model, "created_at": dt.datetime.now(dt.UTC).isoformat(), "error": str(exc)}
            handle.write(json.dumps(out_row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            written += 1
            time.sleep(args.sleep_seconds)
    print(f"repair_records_written={written}")
    print(f"repair_file={output}")
    return 0



def cmd_verify(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    packs = {str(pack.get("pack_id")): pack for pack in read_jsonl(args.pack_file)}
    proposal_rows = valid_proposal_by_id(args.proposals)
    output = args.output or args.output_root / "verified" / f"verified-{args.model.replace('/', '-')}-{utc_stamp()}.jsonl"
    processed = processed_pack_ids(output, require_verification=True)
    written = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for pack_id, row in proposal_rows.items():
            if args.limit_packs and written >= args.limit_packs:
                break
            if pack_id in processed:
                continue
            pack = packs.get(pack_id)
            if not pack:
                out_row = {**row, "error": "Pack was not found for verification."}
            else:
                def make_verified_row() -> dict[str, Any]:
                    content = openrouter_chat(args.model, build_verifier_prompt(pack, row["proposal"]), args.max_tokens, args.temperature)
                    try:
                        verification = parse_model_json(content)
                    except Exception:
                        failure_path = save_raw_failure(args.output_root, pack_id, "verification", content)
                        raise BudgetedCanonicalizeError(f"Verification JSON parse failed; raw response saved to {failure_path.name}")
                    validation = validate_verification(pack, verification)
                    if not validation["ok"]:
                        raise BudgetedCanonicalizeError("Verification failed validation: " + json.dumps(validation, sort_keys=True))
                    return {**row, "verification": verification, "verification_model": args.model, "verified_at": dt.datetime.now(dt.UTC).isoformat()}

                try:
                    out_row = retry_operation(make_verified_row, args.retries, args.retry_delay, f"verification pack_id={pack_id}")
                except Exception as exc:
                    logging.exception("verify failed pack_id=%s", pack_id)
                    out_row = {**row, "verification_error": str(exc)}
            handle.write(json.dumps(out_row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            written += 1
            time.sleep(args.sleep_seconds)
    print(f"verification_records_written={written}")
    print(f"verified_file={output}")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    rows = []
    errors = 0
    verification_errors = 0
    verified_ok = 0
    verified_not_ok = 0
    for path in args.proposals:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("error"):
                errors += 1
            elif row.get("verification_error"):
                verification_errors += 1
                rows.append(row)
            elif isinstance(row.get("proposal"), dict):
                if isinstance(row.get("verification"), dict):
                    if row["verification"].get("coverage_ok") is True:
                        verified_ok += 1
                    else:
                        verified_not_ok += 1
                rows.append(row)
    canonical = sum(len((row.get("proposal") or {}).get("canonical_memories") or []) for row in rows)
    delete_ids = len(set(source_id for row in rows for source_id in valid_id_list((row.get("proposal") or {}).get("delete_source_ids"))))
    canonical_source_ids = len(
        set(
            source_id
            for row in rows
            for memory in ((row.get("proposal") or {}).get("canonical_memories") or [])
            if isinstance(memory, dict)
            for source_id in valid_id_list(memory.get("source_ids"))
        )
    )
    keep_ids = len(set(source_id for row in rows for source_id in valid_id_list((row.get("proposal") or {}).get("keep_source_ids"))))
    escalate_ids = len(set(source_id for row in rows for source_id in valid_id_list((row.get("proposal") or {}).get("escalate_source_ids"))))
    print(
        f"summary proposal_rows={len(rows)} errors={errors} verification_errors={verification_errors} "
        f"verified_ok={verified_ok} verified_not_ok={verified_not_ok} canonical_memories={canonical} "
        f"canonical_source_ids={canonical_source_ids} delete_ids={delete_ids} keep_ids={keep_ids} escalate_ids={escalate_ids}"
    )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    rows = load_valid_proposal_rows(args.proposals)
    if args.limit_packs:
        rows = rows[: args.limit_packs]
    plan = build_apply_plan(rows, require_verified=not args.allow_unverified)
    fetched = fetch_thoughts_by_ids(plan["delete_ids"])
    fetched_ids = {str(row.get("id")) for row in fetched}
    live_delete_ids = [thought_id for thought_id in plan["delete_ids"] if thought_id in fetched_ids]
    backup_file = args.backup_output or args.output_root / "backups" / f"budgeted-backup-{utc_stamp()}.jsonl"
    if args.max_delete and len(live_delete_ids) > args.max_delete:
        raise BudgetedCanonicalizeError(f"Apply plan has {len(live_delete_ids)} live deletes, above --max-delete {args.max_delete}.")
    inserted = 0
    reused = 0
    deleted = 0
    if args.apply:
        backed_up = write_jsonl(backup_file, fetched)
        if backed_up != len(fetched):
            raise BudgetedCanonicalizeError("Backup row count did not match fetched delete rows.")
        sync = read_json(args.sync_log, {"version": VERSION, "canonical_hashes": {}, "batches": {}})
        hashes = sync.setdefault("canonical_hashes", {})
        batch = utc_stamp()
        for item in plan["canonicals"]:
            row = item["row"]
            memory = item["memory"]
            content = str(memory.get("content") or "").strip()
            digest = text_hash(content)
            if digest in hashes:
                reused += 1
                continue
            thought_id = insert_canonical_memory(content, canonical_metadata(row, memory, batch))
            hashes[digest] = thought_id
            inserted += 1
            write_json(args.sync_log, sync)
        deleted = delete_thoughts_by_ids(live_delete_ids)
        sync.setdefault("batches", {})[batch] = {"canonical_inserted": inserted, "canonical_reused": reused, "source_deleted": deleted, "backup_file": str(backup_file), "applied_at": dt.datetime.now(dt.UTC).isoformat()}
        write_json(args.sync_log, sync)
    print(
        f"apply_plan proposal_rows={len(rows)} canonicals={plan['canonical_count']} live_delete_ids={len(live_delete_ids)} "
        f"blocked_keep_or_escalate={len(plan['blocked_keep_or_escalate_ids'])} blocked_verifier={len(plan['blocked_verifier_ids'])} "
        f"skipped_unverified={plan['skipped_unverified']} inserted={inserted} reused={reused} deleted={deleted} "
        f"apply={args.apply} backup_file={backup_file if args.apply else 'none'}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Budgeted OB1 canonical rewrite pipeline.")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--version", action="version", version=f"budgeted-canonicalize {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_junk = subparsers.add_parser("audit-junk")
    audit_junk.add_argument("--source", default="all")
    audit_junk.add_argument("--snapshot", type=Path)
    audit_junk.add_argument("--limit-rows", type=int, default=0)
    audit_junk.add_argument("--page-size", type=int, default=1000)
    audit_junk.add_argument("--min-confidence", type=float, default=0.98)
    audit_junk.add_argument("--output", type=Path)
    audit_junk.set_defaults(func=cmd_audit_junk)

    apply_junk = subparsers.add_parser("apply-junk")
    apply_junk.add_argument("proposals", nargs="+", type=Path)
    apply_junk.add_argument("--apply", action="store_true")
    apply_junk.add_argument("--min-confidence", type=float, default=0.98)
    apply_junk.add_argument("--max-delete", type=int, default=500)
    apply_junk.add_argument("--backup-output", type=Path)
    apply_junk.set_defaults(func=cmd_apply_junk)

    cache_index = subparsers.add_parser("cache-index")
    cache_index.add_argument("--snapshot", required=True, type=Path)
    cache_index.add_argument("proposals", nargs="+", type=Path)
    cache_index.add_argument("--policy-version", default=DEFAULT_POLICY_VERSION)
    cache_index.add_argument("--output", type=Path)
    cache_index.set_defaults(func=cmd_cache_index)

    wave = subparsers.add_parser("wave")
    wave.add_argument("--snapshot", required=True, type=Path)
    wave.add_argument("--source", default="all")
    wave.add_argument("--row-cache", nargs="*", type=Path, default=[])
    wave.add_argument("--policy-version", default=DEFAULT_POLICY_VERSION)
    wave.add_argument("--limit-rows", type=int, default=0)
    wave.add_argument("--max-pack-rows", type=int, default=0)
    wave.add_argument("--include-junk", action="store_true", help="Include deterministic junk candidates in GPT wave packs instead of filtering them out.")
    wave.add_argument("--packs-output", type=Path)
    wave.add_argument("--manifest-output", type=Path)
    wave.add_argument("--junk-output", type=Path)
    wave.set_defaults(func=cmd_wave)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--source", default="all")
    snapshot.add_argument("--limit-rows", type=int)
    snapshot.add_argument("--page-size", type=int, default=1000)
    snapshot.add_argument("--output", type=Path)
    snapshot.set_defaults(func=cmd_snapshot)

    pack = subparsers.add_parser("pack")
    pack.add_argument("--snapshot", required=True, type=Path)
    pack.add_argument("--source", default="all")
    pack.add_argument("--limit-rows", type=int, default=0)
    pack.add_argument("--max-pack-rows", type=int, default=0, help="Optional cap below the source budget for smaller model-safe packs.")
    pack.add_argument("--output", type=Path)
    pack.set_defaults(func=cmd_pack)

    run = subparsers.add_parser("run")
    run.add_argument("--pack-file", required=True, type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--limit-packs", type=int, default=0)
    run.add_argument("--max-tokens", type=int, default=3500)
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--prompt-policy", choices=["human", "budgeted"], default="human")
    run.add_argument("--sleep-seconds", type=float, default=0.5)
    run.add_argument("--retries", type=int, default=2)
    run.add_argument("--retry-delay", type=float, default=3.0)
    run.set_defaults(func=cmd_run)

    repair = subparsers.add_parser("repair")
    repair.add_argument("--pack-file", required=True, type=Path)
    repair.add_argument("verified", nargs="+", type=Path)
    repair.add_argument("--output", type=Path)
    repair.add_argument("--model", default=DEFAULT_MODEL)
    repair.add_argument("--limit-packs", type=int, default=0)
    repair.add_argument("--max-tokens", type=int, default=4500)
    repair.add_argument("--temperature", type=float, default=0.0)
    repair.add_argument("--sleep-seconds", type=float, default=0.5)
    repair.add_argument("--retries", type=int, default=2)
    repair.add_argument("--retry-delay", type=float, default=3.0)
    repair.set_defaults(func=cmd_repair)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--pack-file", required=True, type=Path)
    verify.add_argument("proposals", nargs="+", type=Path)
    verify.add_argument("--output", type=Path)
    verify.add_argument("--model", default=DEFAULT_MODEL)
    verify.add_argument("--limit-packs", type=int, default=0)
    verify.add_argument("--max-tokens", type=int, default=2000)
    verify.add_argument("--temperature", type=float, default=0.0)
    verify.add_argument("--sleep-seconds", type=float, default=0.5)
    verify.add_argument("--retries", type=int, default=2)
    verify.add_argument("--retry-delay", type=float, default=3.0)
    verify.set_defaults(func=cmd_verify)

    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("proposals", nargs="+", type=Path)
    summarize.set_defaults(func=cmd_summarize)

    apply = subparsers.add_parser("apply")
    apply.add_argument("proposals", nargs="+", type=Path)
    apply.add_argument("--apply", action="store_true")
    apply.add_argument("--allow-unverified", action="store_true")
    apply.add_argument("--limit-packs", type=int, default=0)
    apply.add_argument("--max-delete", type=int, default=1000)
    apply.add_argument("--backup-output", type=Path)
    apply.add_argument("--sync-log", type=Path, default=OUTPUT_ROOT / "apply" / "budgeted-sync-log.json")
    apply.set_defaults(func=cmd_apply)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    setup_logging()
    env_commands = {"audit-junk", "apply-junk", "snapshot", "run", "repair", "verify", "apply"}
    if args.command in env_commands:
        load_env_file(REPO_ROOT / ".env.local")
        load_env_file(REPO_ROOT / ".local" / "model-bakeoff" / ".env")
        refresh_env()
    if args.command in {"audit-junk", "apply-junk", "snapshot", "apply"}:
        require_supabase_env()
    if args.command in {"run", "repair", "verify", "apply"}:
        require_openrouter_env()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BudgetedCanonicalizeError as exc:
        logging.error("failed %s", exc)
        print(f"error={exc}", file=sys.stderr)
        raise SystemExit(1)
