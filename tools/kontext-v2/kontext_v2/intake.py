from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from kontext_v2.models import MemoryRecord
from kontext_v2.retention import (
    cleanup_flag_blocked_for_protected_history,
    is_protected_autobiographical_history,
)


@dataclass(frozen=True)
class IngestionMessage:
    role: str
    content: str


@dataclass(frozen=True)
class GateDecision:
    keep: bool
    reason: str
    domains: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IngestionProposal:
    action: str
    content: str
    domains: list[str]
    memory_type: str
    signal_strength: int
    current_status: str
    memory_tier: str
    confidence: float
    reason: str
    existing_id: str = ""
    flag_type: str = ""
    protected_override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


VALID_ACTIONS = {"save", "update", "skip", "flag"}
VALID_FLAG_TYPES = {"delete_candidate", "merge_candidate", "conflict_candidate", "stale_candidate"}
VALID_MEMORY_TIERS = {"active", "historical", "cold"}
DESTRUCTIVE_ACTION_FLAGS = {
    "delete": "delete_candidate",
    "merge": "merge_candidate",
    "conflict": "conflict_candidate",
    "stale": "stale_candidate",
}

DOMAIN_KEYWORDS = {
    "ai": ("ai", "agent", "agents", "chatgpt", "claude", "codex", "mem0", "open brain", "ob1", "kontext", "mcp", "qwen"),
    "systems": (
        "system",
        "systems",
        "workflow",
        "automation",
        "pipeline",
        "settings",
        "hook",
        "backup",
        "self-hosted",
        "architecture",
        "vps",
        "mirror",
        "write path",
    ),
    "business": ("business", "brand", "branding", "offer", "students", "skool", "preply", "sales", "marketing"),
    "money_execution": ("money", "finance", "income", "pfa", "investment", "tax", "invoice", "funding", "cash"),
    "opera": ("opera", "audition", "competition", "role", "repertoire", "fach", "baritone", "career"),
    "vocality": ("vocality", "voice", "vocal", "singing", "passaggio", "larynx", "vazquez", "teacher", "mentor"),
    "relationships": ("relationship", "relationships", "mother", "father", "family", "partner", "attachment", "trust"),
    "psychology": ("psychology", "identity", "nervous system", "pattern", "blind spot", "trauma", "shadow", "emotion"),
}

JUNK_TERMS = (
    "stdout",
    "stderr",
    "traceback",
    "stack trace",
    "raw json",
    "tool call",
    "speaker diarization",
    "speaker identification",
    "canonical names",
    "conversation cues",
    "transcript process",
)

GENERIC_VOCAL_TERMS = ("larynx", "breath support", "resonance", "vowel tuning", "pharyngeal", "lesson covered")
OWN_VOICE_TERMS = (
    "my voice",
    "my vocal",
    "my fach",
    "my role",
    "my teacher",
    "my mentor",
    "opera",
    "audition",
    "career",
    "vocality method",
    "my method",
)
KEEP_TERMS = (
    "decided",
    "decision",
    "prefer",
    "preference",
    "want",
    "goal",
    "current",
    "workflow",
    "architecture",
    "should",
    "must",
    "remember",
    "save this",
)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_messages(messages: list[dict[str, Any]] | list[IngestionMessage] | None) -> list[IngestionMessage]:
    normalized: list[IngestionMessage] = []
    for message in messages or []:
        if isinstance(message, IngestionMessage):
            role = message.role
            content = message.content
        else:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
        role = role.strip().lower() or "user"
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        content = normalize_text(content)
        if content:
            normalized.append(IngestionMessage(role=role, content=content))
    return normalized


def source_hash(messages: list[IngestionMessage]) -> str:
    payload = [{"role": message.role, "content": message.content} for message in messages]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def preview_messages(messages: list[IngestionMessage], max_chars: int = 500) -> str:
    preview = " | ".join(f"{message.role}: {message.content}" for message in messages)
    preview = normalize_text(preview)
    if len(preview) <= max_chars:
        return preview
    return preview[: max_chars - 3].rstrip() + "..."


def contains_term(text: str, term: str) -> bool:
    if re.search(r"[^a-z0-9]", term):
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def detect_domains(text: str) -> list[str]:
    lower = text.lower()
    domains = []
    for domain, terms in DOMAIN_KEYWORDS.items():
        if any(contains_term(lower, term) for term in terms):
            domains.append(domain)
    return domains


def gate_exchange(messages: list[IngestionMessage]) -> GateDecision:
    text = normalize_text(" ".join(message.content for message in messages))
    lower = text.lower()
    if not text:
        return GateDecision(False, "empty_exchange", [])
    if any(term in lower for term in JUNK_TERMS):
        return GateDecision(False, "transcript_or_tool_junk", [])
    if len(text) < 24 and not any(term in lower for term in KEEP_TERMS):
        return GateDecision(False, "low_signal_chatter", [])
    if any(term in lower for term in GENERIC_VOCAL_TERMS) and not any(term in lower for term in OWN_VOICE_TERMS):
        return GateDecision(False, "generic_vocal_lesson_sludge", ["vocality"])
    domains = detect_domains(text)
    if domains:
        return GateDecision(True, "domain_signal", domains)
    if any(term in lower for term in KEEP_TERMS):
        return GateDecision(True, "durable_language", [])
    return GateDecision(False, "no_domain_signal", [])


def normalize_list(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else [value] if value else []
    return [normalize_text(item).lower() for item in raw if normalize_text(item)]


def clamp_int(value: Any, default: int = 5) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(1, min(10, number))


def clamp_float(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def normalize_tier(value: Any) -> str:
    tier = normalize_text(value).lower()
    return tier if tier in VALID_MEMORY_TIERS else "active"


def normalize_flag_type(value: Any) -> str:
    flag_type = normalize_text(value).lower()
    return flag_type if flag_type in VALID_FLAG_TYPES else "delete_candidate"


def normalize_action(value: Any) -> tuple[str, str]:
    action = normalize_text(value).lower() or "save"
    if action in DESTRUCTIVE_ACTION_FLAGS:
        return "flag", DESTRUCTIVE_ACTION_FLAGS[action]
    if action in VALID_ACTIONS:
        return action, ""
    return "skip", ""


def normalize_proposals(raw: str | dict[str, Any]) -> list[IngestionProposal]:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid extraction JSON") from exc
    else:
        payload = raw
    proposals = payload.get("proposals") if isinstance(payload, dict) else None
    if not isinstance(proposals, list):
        raise ValueError("proposals field is required")

    normalized: list[IngestionProposal] = []
    for item in proposals:
        if not isinstance(item, dict):
            continue
        action, mapped_flag_type = normalize_action(item.get("action"))
        flag_type = mapped_flag_type
        if action == "flag" and not flag_type:
            flag_type = normalize_flag_type(item.get("flag_type"))
        normalized.append(
            IngestionProposal(
                action=action,
                content=normalize_text(item.get("content")),
                domains=normalize_list(item.get("domains")),
                memory_type=normalize_text(item.get("memory_type")) or "note",
                signal_strength=clamp_int(item.get("signal_strength")),
                current_status=normalize_text(item.get("current_status")) or "active",
                memory_tier=normalize_tier(item.get("memory_tier")),
                confidence=clamp_float(item.get("confidence")),
                reason=normalize_text(item.get("reason")),
                existing_id=normalize_text(item.get("existing_id") or item.get("id")),
                flag_type=flag_type,
                protected_override=bool(item.get("protected_override")),
            )
        )
    return normalized

HIGH_CONFIDENCE_THRESHOLD = 0.75


def proposal_content_hash(proposal: IngestionProposal) -> str:
    return hash_text(normalize_text(proposal.content))


def local_memory_id(proposal: IngestionProposal) -> str:
    explicit_id = normalize_text(proposal.existing_id)
    if explicit_id:
        return explicit_id
    return f"kontext-local-{proposal_content_hash(proposal)[:24]}"


def proposal_source_hash(proposal: IngestionProposal, source_hash: str) -> str:
    payload = {"source_hash": source_hash, "proposal": proposal.to_dict()}
    return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _proposal_metadata(proposal: IngestionProposal, source_hash: str, origin: str) -> dict[str, Any]:
    return {
        "domains": proposal.domains,
        "memory_type": proposal.memory_type,
        "signal_strength": proposal.signal_strength,
        "current_status": proposal.current_status,
        "memory_tier": proposal.memory_tier,
        "source": "kontext-local-ingestion",
        "source_hash": source_hash,
        "ingestion_origin": origin,
        "ingestion_action": proposal.action,
        "ingestion_confidence": proposal.confidence,
        "ingestion_reason_hash": hash_text(proposal.reason),
    }


def _memory_from_proposal(
    proposal: IngestionProposal,
    *,
    external_id: str,
    source_hash: str,
    origin: str,
) -> MemoryRecord:
    content = normalize_text(proposal.content)
    return MemoryRecord(
        external_mem0_id=external_id,
        title=content[:80],
        text=content,
        metadata=_proposal_metadata(proposal, source_hash, origin),
        memory_type=proposal.memory_type,
        current_status=proposal.current_status,
        memory_tier=proposal.memory_tier,
        signal_strength=float(proposal.signal_strength),
        source_hash=proposal_source_hash(proposal, source_hash),
    )


def _protected_update_blocked(proposal: IngestionProposal, existing: Any) -> bool:
    return is_protected_autobiographical_history(existing) and not proposal.protected_override


def _protected_skip_result(external_id: str) -> dict[str, Any]:
    return {
        "action": "skipped_protected",
        "id": external_id,
        "reason": "protected_autobiographical_history",
    }


def apply_intake_proposals(
    repo: Any,
    *,
    proposals: list[IngestionProposal],
    source_hash: str,
    origin: str,
    apply: bool = False,
) -> dict[str, Any]:
    counts = {"saved": 0, "updated": 0, "skipped": 0, "flagged": 0, "errors": []}
    results: list[dict[str, Any]] = []
    writes_applied = 0

    for proposal in proposals:
        action = proposal.action
        content_hash = proposal_content_hash(proposal)
        try:
            if action == "skip" or not normalize_text(proposal.content):
                counts["skipped"] += 1
                results.append({"action": "skip", "reason": proposal.reason or "skip"})
                continue

            if action == "flag":
                flag_id = normalize_text(proposal.existing_id) or repo.find_exact_text_id(proposal.content) or f"proposal-{content_hash[:24]}"
                flag_type = proposal.flag_type or "delete_candidate"
                existing = repo.fetch_by_external_id(flag_id) if flag_id else None
                if cleanup_flag_blocked_for_protected_history(flag_type, existing):
                    counts["skipped"] += 1
                    results.append({"action": "protected", "id": flag_id, "flag_type": flag_type, "protected": True})
                    continue
                counts["flagged"] += 1
                result = {"action": "flag", "id": flag_id, "flag_type": flag_type}
                if apply:
                    repo.record_memory_flag(
                        external_mem0_id=flag_id,
                        flag_type=result["flag_type"],
                        reason_hash=hash_text(proposal.reason),
                        confidence=proposal.confidence,
                        origin=origin,
                        status="pending",
                        metadata={"source_hash": source_hash, "content_hash": content_hash},
                    )
                    writes_applied += 1
                results.append(result)
                continue

            if action == "update":
                existing_id = normalize_text(proposal.existing_id)
                existing = repo.fetch_by_external_id(existing_id) if existing_id else None
                if existing is None:
                    exact_id = repo.find_exact_text_id(proposal.content)
                    existing = repo.fetch_by_external_id(exact_id) if exact_id else None
                    existing_id = exact_id
                if existing is None:
                    counts["flagged"] += 1
                    result = {"action": "flag", "flag_type": "conflict_candidate", "reason": "no_exact_update_match"}
                    if apply:
                        repo.record_memory_flag(
                            external_mem0_id=f"proposal-{content_hash[:24]}",
                            flag_type="conflict_candidate",
                            reason_hash=hash_text(proposal.reason or "no exact update match"),
                            confidence=proposal.confidence,
                            origin=origin,
                            status="pending",
                            metadata={"source_hash": source_hash, "content_hash": content_hash},
                        )
                        writes_applied += 1
                    results.append(result)
                    continue
                if _protected_update_blocked(proposal, existing):
                    counts["skipped"] += 1
                    results.append(_protected_skip_result(existing_id))
                    continue
                counts["updated"] += 1
                result = {"action": "update", "id": existing_id}
                if apply:
                    repo.upsert_memory(
                        _memory_from_proposal(proposal, external_id=existing_id, source_hash=source_hash, origin=origin),
                        version_source="kontext_ingestion",
                    )
                    writes_applied += 1
                results.append(result)
                continue

            if action == "save":
                explicit_id = normalize_text(proposal.existing_id)
                existing = repo.fetch_by_external_id(explicit_id) if explicit_id else None
                if existing is not None and _protected_update_blocked(proposal, existing):
                    counts["skipped"] += 1
                    results.append(_protected_skip_result(explicit_id))
                    continue
                duplicate_id = repo.find_exact_text_id(proposal.content)
                if duplicate_id:
                    counts["skipped"] += 1
                    results.append({"action": "skip", "existing_id": duplicate_id, "reason": "exact duplicate"})
                    continue
                if proposal.confidence < HIGH_CONFIDENCE_THRESHOLD:
                    counts["skipped"] += 1
                    results.append({"action": "skip", "reason": "low_confidence"})
                    continue
                external_id = local_memory_id(proposal)
                counts["saved"] += 1
                result = {"action": "save", "id": external_id}
                if apply:
                    repo.upsert_memory(
                        _memory_from_proposal(proposal, external_id=external_id, source_hash=source_hash, origin=origin),
                        version_source="kontext_ingestion",
                    )
                    writes_applied += 1
                results.append(result)
                continue

            counts["skipped"] += 1
            results.append({"action": "skip", "reason": "unknown action"})
        except Exception as exc:
            counts["errors"].append(str(exc))
            results.append({"action": "error", "reason": str(exc)})

    return {
        "mode": "apply" if apply else "dry_run",
        "apply": apply,
        "writes_applied": writes_applied,
        "source_hash": source_hash,
        "origin": origin,
        "counts": counts,
        "results": results,
    }
