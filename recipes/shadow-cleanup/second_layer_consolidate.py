#!/usr/bin/env python3
"""Second-layer consolidation helpers for OB1 cleanup proposals.

This script never mutates Supabase. It builds proposal packs from first-layer
canonical cards and validates second-layer proposal coverage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


VERSION = "0.1"
DEFAULT_POLICY_VERSION = "human-memory-second-layer-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".local" / "open-brain-cleanup" / "second-layer"
DEFAULT_FIRST_LAYER_ROOT = REPO_ROOT / ".local" / "open-brain-cleanup" / "budgeted"


DOMAIN_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "finance_tax_compliance",
        "Finance, PFA, tax, legal and compliance",
        ("pfa", "tax", "anaf", "vat", "cass", "cas", "invoice", "accounting", "finance", "legal", "contract", "deduct", "vies"),
    ),
    (
        "ai_memory_agents",
        "AI memory, agents, Kontext, OB1 and context systems",
        (
            "open brain",
            "ob1",
            "kontext",
            "memory",
            "retrieval",
            "agent",
            "subagent",
            "codex",
            "claude",
            "mcp",
            "smac",
            "mastermind",
            "context management",
            "token",
            "prompt",
        ),
    ),
    (
        "media_transcription",
        "Media, transcription, diarization and transcript tooling",
        ("transcription", "diarization", "transcript", "speaker", "srt", "subtitle", "audio", "ffmpeg", "video compression", "media cleanup"),
    ),
    (
        "content_marketing_copy",
        "Content, YouTube, marketing, copy and brand voice",
        ("youtube", "content", "copywriting", "marketing", "brand voice", "script", "tweet", "thumbnail", "funnel", "landing page", "conversion"),
    ),
    (
        "voice_opera_career",
        "Ionut voice, opera career, repertoire, Fach and mentors",
        (
            "opera",
            "opera career",
            "repertoire",
            "fach",
            "bass",
            "bass-baritone",
            "voice development",
            "vocal development",
            "singing",
            "vazquez",
            "melocchi",
            "mentor",
            "audition",
            "competition",
        ),
    ),
    (
        "vocality_method_pedagogy",
        "Vocality method, teaching principles and pedagogy",
        (
            "vocal pedagogy",
            "teaching method",
            "vocal technique",
            "curriculum",
            "student",
            "larynx",
            "passaggio",
            "sovt",
            "breath",
            "register",
            "vowel",
            "bel canto",
            "anatomy",
        ),
    ),
    (
        "vocality_business_brand",
        "Vocality business, offer, brand and product strategy",
        ("vocality", "skool", "course", "product", "offer", "pricing", "brand", "business", "students", "sales", "coaching"),
    ),
    (
        "engineering_systems_workflows",
        "Engineering, self-hosting, automation and work systems",
        (
            "workflow",
            "automation",
            "system",
            "systems",
            "self-hosting",
            "n8n",
            "api",
            "supabase",
            "database",
            "testing",
            "security",
            "playwright",
            "browser",
            "frontend",
            "backend",
            "cli",
            "tooling",
            "architecture",
            "reliability",
        ),
    ),
    (
        "psychology_relationships_preferences",
        "Psychology, relationships, preferences and personal operating patterns",
        (
            "psychology",
            "relationship",
            "relationships",
            "preference",
            "communication",
            "blind spot",
            "personal",
            "emotional",
            "identity",
            "motivation",
            "decision-making",
            "life",
        ),
    ),
]


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def clean_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return cleaned[:80] or "missing"


def read_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    if text.startswith("["):
        parsed = json.loads(text)
        return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("proposals"), list):
            return [parsed]
        if isinstance(parsed, dict):
            return [parsed]
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.strip():
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def proposal_dicts_from_file(path: Path) -> list[dict[str, Any]]:
    rows = read_json_or_jsonl(path)
    proposals: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row.get("proposals"), list):
            proposals.extend(item for item in row["proposals"] if isinstance(item, dict))
        elif isinstance(row.get("proposal"), dict):
            proposal = dict(row["proposal"])
            proposal.setdefault("pack_id", row.get("pack_id"))
            proposals.append(proposal)
        elif any(key in row for key in ("canonical_memories", "delete_source_ids", "keep_source_ids", "escalate_source_ids")):
            proposals.append(row)
    return proposals


def route_domain(card: dict[str, Any]) -> str:
    haystack_parts = [str(card.get("type") or "")]
    haystack_parts.extend(str(topic) for topic in card.get("topics") or [])
    haystack_parts.append(str(card.get("content") or ""))
    haystack = "\n".join(haystack_parts).lower()
    for slug, _label, keywords in DOMAIN_RULES:
        if any(keyword in haystack for keyword in keywords):
            return slug
    return "other_projects_context"


def extract_cards(proposal_paths: Iterable[Path], source: str = "claude_history", canonical_row_ids: set[str] | None = None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    ordinal = 0
    seen_source_ids: set[str] = set()
    for path in sorted(proposal_paths, key=lambda p: str(p).lower()):
        for proposal_index, proposal in enumerate(proposal_dicts_from_file(path)):
            pack_id = str(proposal.get("pack_id") or "")
            for memory_index, memory in enumerate(proposal.get("canonical_memories") or []):
                if not isinstance(memory, dict):
                    continue
                source_ids = [str(item) for item in (memory.get("source_ids") or []) if str(item)]
                if canonical_row_ids is not None:
                    source_ids = [source_id for source_id in source_ids if source_id in canonical_row_ids]
                source_ids = [source_id for source_id in source_ids if source_id not in seen_source_ids]
                if not source_ids:
                    continue
                ordinal += 1
                raw_id = stable_hash(
                    {
                        "path": str(path.as_posix()),
                        "proposal_index": proposal_index,
                        "memory_index": memory_index,
                        "pack_id": pack_id,
                        "source_ids": source_ids,
                    }
                )[:16]
                card = {
                    "card_id": f"c2-{raw_id}",
                    "ordinal": ordinal,
                    "source": source,
                    "proposal_file": path.as_posix(),
                    "pack_id": pack_id,
                    "content": str(memory.get("content") or "").strip(),
                    "type": str(memory.get("type") or "context"),
                    "topics": memory.get("topics") if isinstance(memory.get("topics"), list) else [],
                    "confidence": memory.get("confidence"),
                    "reason": str(memory.get("reason") or ""),
                    "source_ids": source_ids,
                    "source_id_count": len(source_ids),
                }
                card["domain"] = route_domain(card)
                cards.append(card)
                for source_id in source_ids:
                    seen_source_ids.add(source_id)
    return cards


def default_proposal_paths(first_layer_root: Path) -> list[Path]:
    patterns = [
        first_layer_root / "waves" / "claude-wave*-proposals-shard-*.json",
        first_layer_root / "proposals" / "claude-pilot-500-20260505.jsonl",
    ]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(item) for item in glob.glob(str(pattern)))
    return sorted(set(paths), key=lambda p: str(p).lower())

def default_cache_paths(first_layer_root: Path) -> list[Path]:
    state_path = first_layer_root / "waves" / "claude-sweep-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        cache_files = state.get("cache_files") if isinstance(state, dict) else []
        if isinstance(cache_files, list):
            return [REPO_ROOT / str(path) if not Path(str(path)).is_absolute() else Path(str(path)) for path in cache_files]
    return sorted((first_layer_root / "cache").glob("row-cache-*.jsonl"), key=lambda p: str(p).lower())


def canonical_ids_from_cache(paths: Iterable[Path]) -> set[str]:
    canonical_ids: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("decision") or "") == "canonical":
                row_id = str(row.get("row_id") or "")
                if row_id:
                    canonical_ids.add(row_id)
    return canonical_ids

def default_snapshot_path(first_layer_root: Path) -> Path | None:
    state_path = first_layer_root / "waves" / "claude-sweep-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        snapshot = state.get("snapshot") if isinstance(state, dict) else None
        if snapshot:
            path = Path(str(snapshot))
            return path if path.is_absolute() else REPO_ROOT / path
    fallback = REPO_ROOT / ".local" / "open-brain-cleanup" / "agent-compaction" / "claude-current-agent-20260505.jsonl"
    return fallback if fallback.exists() else None


def snapshot_rows_by_id(path: Path | None) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if path is None or not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row_id = str(row.get("id") or "")
        if row_id:
            rows[row_id] = row
    return rows


def append_missing_canonical_cards(cards: list[dict[str, Any]], canonical_row_ids: set[str], rows_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    represented = {source_id for card in cards for source_id in card.get("source_ids", [])}
    missing = sorted(canonical_row_ids - represented)
    next_ordinal = max([int(card.get("ordinal") or 0) for card in cards], default=0)
    output = list(cards)
    for source_id in missing:
        row = rows_by_id.get(source_id)
        if not row:
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        next_ordinal += 1
        card = {
            "card_id": f"c2-missing-{stable_hash(source_id)[:16]}",
            "ordinal": next_ordinal,
            "source": str(meta.get("source") or "claude_history"),
            "proposal_file": "synthetic-from-active-canonical-cache",
            "pack_id": "synthetic-missing-canonical-cache",
            "content": str(row.get("content") or "").strip(),
            "type": str(meta.get("type") or "context"),
            "topics": meta.get("topics") if isinstance(meta.get("topics"), list) else [],
            "confidence": None,
            "reason": "Active canonical cache row was not represented in first-layer proposal card text.",
            "source_ids": [source_id],
            "source_id_count": 1,
        }
        card["domain"] = route_domain(card)
        output.append(card)
    return output


def split_domain_cards(cards: list[dict[str, Any]], max_cards_per_pack: int) -> list[dict[str, Any]]:
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        by_domain.setdefault(str(card.get("domain") or "other_projects_context"), []).append(card)
    packs: list[dict[str, Any]] = []
    for domain in sorted(by_domain):
        domain_cards = sorted(by_domain[domain], key=lambda card: int(card.get("ordinal") or 0))
        chunk_count = max(1, (len(domain_cards) + max_cards_per_pack - 1) // max_cards_per_pack)
        for index in range(chunk_count):
            chunk = domain_cards[index * max_cards_per_pack : (index + 1) * max_cards_per_pack]
            pack_id = f"c2-{clean_slug(domain)}-{index + 1:02d}-of-{chunk_count:02d}"
            packs.append(
                {
                    "pack_id": pack_id,
                    "policy_version": DEFAULT_POLICY_VERSION,
                    "domain": domain,
                    "domain_label": domain_label(domain),
                    "chunk": index + 1,
                    "chunk_count": chunk_count,
                    "card_count": len(chunk),
                    "source_id_count": sum(int(card.get("source_id_count") or 0) for card in chunk),
                    "cards": chunk,
                }
            )
    return packs


def domain_label(slug: str) -> str:
    for rule_slug, label, _keywords in DOMAIN_RULES:
        if slug == rule_slug:
            return label
    return "Other project context"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8", newline="\n")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")
            count += 1
    return count


def policy_text() -> str:
    return """# GPT-5.5 OB1 Second-Layer Consolidation Policy

Work only in C:\\Tools\\OB1. This is proposal-only Open Brain cleanup.

Hard constraints:
- Do not call OpenRouter or any external model API.
- Do not read env/secrets.
- Do not mutate Supabase or any database.
- Do not print raw memory/chat contents in final output.
- Write only the assigned output file.

Policy version: human-memory-second-layer-v1.

Goal:
- Collapse first-layer canonical cards into durable human-memory cards.
- Mimic useful human memory: compact, current, goal-driven, lossy, pattern-based.
- Keep only current truths, reusable systems, project/business context, close relationships/mentor influence, psychology/personal operating patterns, finance/legal anchors that need review, and voice/opera-career context.
- Drop redundant, stale, implementation-trivia, lesson-sludge, transcript-process, diarization/speaker-labeling, generic vocal anatomy, and student-specific material unless it becomes a reusable current pattern.
- Voice material should stay only if it is about Ionut's voice/career, Fach/roles/repertoire, current technical focus, important mentor influence, or compact Vocality IP/business method.
- Prefer fewer stronger cards over many detailed cards. Compress aggressively.

Output strict JSON shape:
{
  "agent": "gpt55-claude-second-layer-...",
  "policy_version": "human-memory-second-layer-v1",
  "domain": "...",
  "input_pack_id": "...",
  "memory_cards": [
    {"content":"compact durable memory", "source_card_ids":["..."], "source_ids":["..."], "type":"goal|context|decision|preference|project|workflow|pattern|relationship|lesson", "topics":["..."], "status":"current|inactive_but_important|review", "confidence":0.0, "reason":"short reason, no raw quotes"}
  ],
  "drop_card_ids": ["..."],
  "escalate_card_ids": ["..."],
  "review_questions": ["short direct questions for low-confidence cards"],
  "risk_notes": ["short notes, no raw quotes"],
  "counts": {"input_cards":0, "memory_cards":0, "represented_cards":0, "drop_cards":0, "escalate_cards":0}
}

Coverage rule:
- Every input card_id must appear exactly once across memory_cards.source_card_ids, drop_card_ids, or escalate_card_ids.
- If unsure whether a detail is current or high-impact, escalate the card instead of silently keeping noisy detail.

Final chat answer: output path, counts, and review questions only. Do not include raw memory contents.
"""


def cmd_build_packs(args: argparse.Namespace) -> int:
    output_root: Path = args.output_root
    paths = [Path(item) for item in args.proposals] if args.proposals else default_proposal_paths(args.first_layer_root)
    cache_paths = [Path(item) for item in args.cache_files] if args.cache_files else default_cache_paths(args.first_layer_root)
    canonical_row_ids = canonical_ids_from_cache(cache_paths)
    cards = extract_cards(paths, source=args.source, canonical_row_ids=canonical_row_ids)
    snapshot_path = Path(args.snapshot) if args.snapshot else default_snapshot_path(args.first_layer_root)
    cards = append_missing_canonical_cards(cards, canonical_row_ids, snapshot_rows_by_id(snapshot_path))
    packs = split_domain_cards(cards, max_cards_per_pack=args.max_cards_per_pack)
    card_index = output_root / "claude-second-layer-card-index-20260506.jsonl"
    pack_dir = output_root / "packs"
    policy_file = output_root / "claude-second-layer-policy-human-memory-v1.md"
    manifest_file = output_root / "claude-second-layer-manifest-20260506.json"
    state_file = output_root / "claude-second-layer-state.json"

    write_jsonl(card_index, cards)
    for pack in packs:
        write_json(pack_dir / f"{pack['pack_id']}.json", pack)
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(policy_text(), encoding="utf-8", newline="\n")
    domain_counts = Counter(str(card.get("domain") or "other_projects_context") for card in cards)
    manifest = {
        "version": VERSION,
        "policy_version": DEFAULT_POLICY_VERSION,
        "source": args.source,
        "proposal_files": len(paths),
        "card_count": len(cards),
        "source_id_count": sum(int(card.get("source_id_count") or 0) for card in cards),
        "unique_source_id_count": len({source_id for card in cards for source_id in card.get("source_ids", [])}),
        "canonical_cache_ids": len(canonical_row_ids),
        "pack_count": len(packs),
        "max_cards_per_pack": args.max_cards_per_pack,
        "domain_counts": dict(sorted(domain_counts.items())),
        "card_index": card_index.as_posix(),
        "policy_file": policy_file.as_posix(),
        "pack_files": [(pack_dir / f"{pack['pack_id']}.json").as_posix() for pack in packs],
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    write_json(manifest_file, manifest)
    write_json(
        state_file,
        {
            "status": "packs_ready",
            "stage": "ready",
            "policy_version": DEFAULT_POLICY_VERSION,
            "manifest_file": manifest_file.as_posix(),
            "policy_file": policy_file.as_posix(),
            "pack_files": manifest["pack_files"],
            "completed_packs": [],
            "active_agents": {},
            "db_apply": False,
            "updated_at": dt.datetime.now(dt.UTC).isoformat(),
        },
    )
    print(f"card_count={manifest['card_count']}")
    print(f"source_id_count={manifest['source_id_count']}")
    print(f"unique_source_id_count={manifest['unique_source_id_count']}")
    print(f"pack_count={manifest['pack_count']}")
    print("domain_counts=" + json.dumps(manifest["domain_counts"], sort_keys=True))
    print(f"manifest_file={manifest_file}")
    return 0


def output_rows_from_file(path: Path) -> list[dict[str, Any]]:
    rows = read_json_or_jsonl(path)
    if len(rows) == 1 and isinstance(rows[0].get("memory_cards"), list):
        return rows
    return [row for row in rows if isinstance(row.get("memory_cards"), list)]


def decisions_for_output(row: dict[str, Any]) -> list[tuple[str, str]]:
    decisions: list[tuple[str, str]] = []
    for card in row.get("memory_cards") or []:
        if isinstance(card, dict):
            for card_id in card.get("source_card_ids") or []:
                decisions.append((str(card_id), "memory"))
    for field, decision in (("drop_card_ids", "drop"), ("escalate_card_ids", "escalate")):
        for card_id in row.get(field) or []:
            decisions.append((str(card_id), decision))
    return decisions


def validate_outputs(pack_paths: list[Path], output_paths: list[Path]) -> dict[str, Any]:
    expected_cards: dict[str, str] = {}
    for pack_path in pack_paths:
        pack = json.loads(pack_path.read_text(encoding="utf-8-sig"))
        for card in pack.get("cards") or []:
            card_id = str(card.get("card_id") or "")
            if card_id:
                expected_cards[card_id] = str(pack.get("pack_id") or pack_path.stem)
    assignments: list[tuple[str, str, str]] = []
    memory_cards = 0
    review_questions = 0
    risk_notes = 0
    for output_path in output_paths:
        for row in output_rows_from_file(output_path):
            memory_cards += len(row.get("memory_cards") or [])
            review_questions += len(row.get("review_questions") or [])
            risk_notes += len(row.get("risk_notes") or [])
            for card_id, decision in decisions_for_output(row):
                assignments.append((card_id, decision, output_path.name))
    counts = Counter(card_id for card_id, _decision, _file in assignments)
    expected_ids = set(expected_cards)
    assigned_ids = set(counts)
    errors: list[dict[str, Any]] = []
    missing = sorted(expected_ids - assigned_ids)
    extra = sorted(assigned_ids - expected_ids)
    duplicate = sorted(card_id for card_id, count in counts.items() if count != 1)
    if missing:
        errors.append({"type": "missing_card_ids", "count": len(missing), "sample": missing[:10]})
    if extra:
        errors.append({"type": "extra_card_ids", "count": len(extra), "sample": extra[:10]})
    if duplicate:
        errors.append({"type": "duplicate_card_ids", "count": len(duplicate), "sample": duplicate[:10]})
    decision_counts = Counter(decision for _card_id, decision, _file in assignments)
    return {
        "status": "pass" if not errors else "fail",
        "expected_cards": len(expected_cards),
        "assigned_cards": len(assignments),
        "unique_assigned_cards": len(assigned_ids),
        "memory_cards": memory_cards,
        "decision_counts": dict(sorted(decision_counts.items())),
        "review_questions": review_questions,
        "risk_notes": risk_notes,
        "errors": errors,
    }


def cmd_validate(args: argparse.Namespace) -> int:
    report = validate_outputs([Path(item) for item in args.packs], [Path(item) for item in args.outputs])
    if args.report:
        write_json(args.report, report)
    print(json.dumps({key: report[key] for key in ("status", "expected_cards", "assigned_cards", "unique_assigned_cards", "memory_cards", "decision_counts", "review_questions", "risk_notes")}, indent=2))
    if report["errors"]:
        print("errors=" + json.dumps(report["errors"][:5], indent=2))
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Second-layer OB1 cleanup consolidation helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-packs")
    build.add_argument("--first-layer-root", type=Path, default=DEFAULT_FIRST_LAYER_ROOT)
    build.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    build.add_argument("--source", default="claude_history")
    build.add_argument("--max-cards-per-pack", type=int, default=90)
    build.add_argument("--proposals", nargs="*", default=[])
    build.add_argument("--cache-files", nargs="*", default=[])
    build.add_argument("--snapshot")
    build.set_defaults(func=cmd_build_packs)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--packs", nargs="+", required=True)
    validate.add_argument("--outputs", nargs="+", required=True)
    validate.add_argument("--report", type=Path)
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
