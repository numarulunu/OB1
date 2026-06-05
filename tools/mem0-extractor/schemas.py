from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VALID_MEMORY_TIERS = {"active", "historical", "cold"}
VALID_ACTIONS = {"save", "update", "skip", "ask_user"}
VALID_MEMORY_TYPES = {
    "pattern",
    "person",
    "relationship",
    "event",
    "lesson",
    "trigger",
    "shadow_motive",
    "identity_shaping",
    "ai_breakthrough",
    "decision",
    "preference",
    "workflow",
    "project",
    "project_state",
}


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_memory_tier(value: Any) -> str:
    tier = str(value or "active").strip().lower()
    return tier if tier in VALID_MEMORY_TIERS else "active"


def normalize_memory_type(value: Any) -> str:
    memory_type = normalize_text(value).lower()
    return memory_type if memory_type in VALID_MEMORY_TYPES else "pattern"


def normalize_action(value: Any) -> str:
    action = str(value or "save").strip().lower()
    return action if action in VALID_ACTIONS else "save"


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    cleaned: list[str] = []
    for item in raw:
        text = str(item or "").strip().lower()
        if text:
            cleaned.append(text)
    return cleaned


def normalize_source_ids(value: Any) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    cleaned: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def clamp_signal_strength(value: Any, default: int = 1) -> int:
    if isinstance(value, bool):
        return default
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        score = default
    return max(1, min(10, score))


@dataclass(frozen=True)
class Candidate:
    source_id: str
    text: str
    domains: list[str] = field(default_factory=list)
    signal_strength: int = 1
    memory_type: str = "pattern"
    memory_tier: str = "active"
    reasons: list[str] = field(default_factory=list)

    def to_report_dict(self, preview_chars: int = 320) -> dict[str, Any]:
        preview = normalize_text(self.text)
        if len(preview) > preview_chars:
            preview = preview[: preview_chars - 3].rstrip() + "..."
        return {
            "source_id": self.source_id,
            "domains": self.domains,
            "signal_strength": self.signal_strength,
            "memory_type": self.memory_type,
            "suggested_memory_type": self.memory_type,
            "memory_tier": self.memory_tier,
            "reasons": self.reasons,
            "text_preview": preview,
        }

    def to_prompt_dict(self, max_chars: int = 1200) -> dict[str, Any]:
        text = normalize_text(self.text)
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        return {
            "source_id": self.source_id,
            "text": text,
            "domains": self.domains,
            "signal_strength": self.signal_strength,
            "memory_type": self.memory_type,
            "suggested_memory_type": self.memory_type,
            "memory_tier": self.memory_tier,
            "reasons": self.reasons,
        }


@dataclass(frozen=True)
class Grade:
    source_id: str
    keep_candidate: bool
    signal_strength: int
    domains: list[str]
    memory_type: str
    memory_tier: str
    reasons: list[str] = field(default_factory=list)
    drop_reason: str = ""

    def to_candidate(self, text: str) -> Candidate:
        return Candidate(
            source_id=self.source_id,
            text=text,
            domains=self.domains,
            signal_strength=self.signal_strength,
            memory_type=self.memory_type,
            memory_tier=self.memory_tier,
            reasons=self.reasons,
        )


@dataclass(frozen=True)
class MemoryProposal:
    action: str
    content: str
    domains: list[str]
    memory_type: str
    signal_strength: int
    current_status: str
    memory_tier: str
    source_ids: list[str]
    reason: str = ""
    existing_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionResult:
    proposals: list[MemoryProposal]
    risk_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActionPlan:
    action: str
    proposal: MemoryProposal
    existing_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["proposal"] = self.proposal.to_dict()
        return row


def normalize_proposal(row: dict[str, Any]) -> MemoryProposal:
    return MemoryProposal(
        action=normalize_action(row.get("action")),
        content=normalize_text(row.get("content")),
        domains=normalize_list(row.get("domains")),
        memory_type=normalize_memory_type(row.get("memory_type")),
        signal_strength=clamp_signal_strength(row.get("signal_strength")),
        current_status=normalize_text(row.get("current_status")) or "active",
        memory_tier=normalize_memory_tier(row.get("memory_tier")),
        source_ids=normalize_source_ids(row.get("source_ids")),
        reason=normalize_text(row.get("reason")),
        existing_id=normalize_text(row.get("existing_id")),
    )
