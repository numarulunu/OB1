from __future__ import annotations

import re
from typing import Any

from schemas import Grade, clamp_signal_strength, normalize_text

DOMAIN_KEYWORDS = {
    "family_origin": ("family-origin", "family origin", "mother", "father", "parent", "childhood", "family"),
    "relationships": ("relationship", "relationships", "attachment", "trust", "partner", "girlfriend", "boyfriend", "breakup", "mother"),
    "psychology": ("psychology", "identity", "blind spot", "pattern", "nervous system", "emotion", "trauma", "attachment", "motivation", "shadow"),
    "ai": ("ai", "agent", "agents", "claude", "codex", "chatgpt", "gpt", "mem0", "open brain", "ob1", "kontext", "mcp"),
    "systems": ("system", "systems", "workflow", "automation", "pipeline", "settings", "mcp", "backup", "daemon", "operational brain", "memory layer"),
    "business": ("business", "branding", "brand", "offer", "students", "skool", "preply", "stripe", "sales", "marketing"),
    "money_execution": ("finance", "money", "income", "pfa", "investment", "cash", "mrr", "tax", "invoice", "accounting"),
    "opera": ("opera", "audition", "competition", "role", "repertoire", "baritone", "fach", "career"),
    "vocality": ("vocality", "voice", "vocal", "singing", "passaggio", "larynx", "fach", "repertoire", "baritone", "teacher", "mentor"),
}

JUNK_PATTERNS = (
    ("transcript_process_junk", ("speaker diarization", "speaker identification", "canonical names", "conversation cues", "transcript process")),
    ("tool_log_junk", ("stdout", "stderr", "traceback", "stack trace", "exit code", "raw json", "tool call")),
)

GENERIC_VOCAL_TERMS = (
    "pharyngeal",
    "vowel tuning",
    "larynx",
    "breath support",
    "resonance",
    "student exercise",
    "lesson covered",
)

OWN_VOICE_TERMS = (
    "user's own",
    "user's voice",
    "user's vocal",
    "my voice",
    "my vocal",
    "my passaggio",
    "my fach",
    "my role",
    "my teacher",
    "my mentor",
    "opera role",
    "audition",
    "career",
    "baritone",
    "vazquez",
    "important teacher",
    "important mentor",
    "teacher influence",
    "mentor influence",
)

REUSABLE_METHOD_TERMS = (
    "teaching method",
    "vocality method",
    "my method",
    "user's method",
    "core rules",
    "diagnosing",
    "method in a nutshell",
)

EMOTIONAL_INTENSITY_TERMS = (
    "raw",
    "embarrassing",
    "emotionally intense",
    "emotionally raw",
    "furious",
    "anger",
    "shame",
    "injustice",
    "burning fire",
    "changed how",
)

GENERIC_BUSINESS_FINANCE_TERMS = (
    "s&p 500",
    "index tracks",
    "large-cap",
    "dividend reinvestment",
    "compound returns",
    "asset allocation",
    "market capitalization",
)

USER_CONTEXT_TERMS = (
    "user",
    "user's",
    "ionut",
    "my ",
    "i ",
    "me ",
    "pfa",
    "sxr8",
    "vocality",
    "opera",
    "skool",
    "preply",
    "students",
    "income",
    "business engine",
)

CURRENT_TERMS = ("current", "now", "active", "decision", "decided", "use ", "uses ", "workflow", "setting")
FORMATIVE_TERMS = ("formative", "identity-shaping", "identity shaping", "childhood", "family-origin", "family origin", "shaped")


def keyword_matches(text: str, term: str) -> bool:
    if re.search(r"[^a-z0-9]", term):
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(keyword_matches(text, term) for term in terms)

def detect_domains(text: str) -> list[str]:
    lower = text.lower()
    domains = []
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if contains_any(lower, keywords):
            domains.append(domain)
    return domains


def junk_reason(text: str) -> str:
    lower = text.lower()
    for reason, patterns in JUNK_PATTERNS:
        if any(pattern in lower for pattern in patterns):
            return reason
    if contains_any(lower, GENERIC_VOCAL_TERMS) and not (
        contains_any(lower, OWN_VOICE_TERMS) or contains_any(lower, REUSABLE_METHOD_TERMS)
    ):
        return "generic_vocal_lesson_sludge"
    if contains_any(lower, GENERIC_BUSINESS_FINANCE_TERMS) and not contains_any(lower, USER_CONTEXT_TERMS):
        return "generic_business_or_finance_fact"
    return ""


def infer_memory_type(text: str, domains: list[str]) -> str:
    lower = text.lower()
    if contains_any(lower, FORMATIVE_TERMS):
        return "identity_shaping"
    if "shadow" in lower:
        return "shadow_motive"
    if contains_any(lower, ("decision", "decided", "chosen", "architecture decision", "policy decision")):
        return "decision"
    if contains_any(lower, ("preference", "prefers", "wants", "does not want", "likes", "dislikes")):
        return "preference"
    if contains_any(lower, ("current status", "next step", "blocked", "blocker", "pending", "status:")):
        return "project_state"
    if contains_any(lower, ("workflow", "process", "pipeline", "repeatable", "runbook", "apply planner")):
        return "workflow"
    if contains_any(lower, ("ai philosophy", "breakthrough", "memory architecture", "agent architecture")) and "ai" in domains:
        return "ai_breakthrough"
    if "relationships" in domains and contains_any(lower, ("dynamic", "attachment", "trust", "conflict", "partner", "close person")):
        return "relationship"
    if "relationships" in domains:
        return "person"
    return "pattern"


def infer_tier(text: str, domains: list[str], memory_type: str) -> str:
    lower = text.lower()
    high_history = {"family_origin", "relationships", "psychology"}
    if memory_type == "identity_shaping" or high_history.intersection(domains) and contains_any(lower, FORMATIVE_TERMS):
        return "historical"
    if contains_any(lower, CURRENT_TERMS):
        return "active"
    if "resolved" in lower or "old" in lower or "past" in lower:
        return "historical"
    return "active"


def signal_score(text: str, domains: list[str], memory_type: str) -> int:
    lower = text.lower()
    score = 1
    if domains:
        score += 2
    if {"psychology", "relationships", "family_origin"}.intersection(domains):
        score += 3
    if "ai" in domains or "systems" in domains:
        score += 2
    if "opera" in domains or "business" in domains or "money_execution" in domains:
        score += 2
    if "vocality" in domains and contains_any(lower, OWN_VOICE_TERMS):
        score += 2
    if "vocality" in domains and contains_any(lower, REUSABLE_METHOD_TERMS):
        score += 4
    if memory_type == "identity_shaping":
        score += 2
    if memory_type == "identity_shaping" and {"psychology", "relationships", "family_origin"}.intersection(domains):
        score += 1
    if {"psychology", "relationships"}.intersection(domains) and contains_any(lower, EMOTIONAL_INTENSITY_TERMS):
        score += 1
    if contains_any(lower, ("decision", "current", "must", "should", "goal", "preference", "constraint")):
        score += 1
    return clamp_signal_strength(score)


def grade_text(text: str, source_id: str = "") -> Grade:
    cleaned = normalize_text(text)
    if not cleaned or len(cleaned) < 24:
        return Grade(source_id=source_id, keep_candidate=False, signal_strength=1, domains=[], memory_type="", memory_tier="active", drop_reason="too_short")

    reason = junk_reason(cleaned)
    if reason:
        return Grade(source_id=source_id, keep_candidate=False, signal_strength=1, domains=[], memory_type="", memory_tier="active", drop_reason=reason)

    domains = detect_domains(cleaned)
    memory_type = infer_memory_type(cleaned, domains)
    score = signal_score(cleaned, domains, memory_type)
    keep = score >= 5
    tier = infer_tier(cleaned, domains, memory_type)
    reasons = []
    if domains:
        reasons.extend(domains[:4])
    if memory_type == "identity_shaping":
        reasons.append("identity_shaping")
    if re.search(r"\b(decision|decided|preference|constraint|goal)\b", cleaned, re.IGNORECASE):
        reasons.append("explicit_operational_signal")

    return Grade(
        source_id=source_id,
        keep_candidate=keep,
        signal_strength=score,
        domains=domains,
        memory_type=memory_type,
        memory_tier=tier,
        reasons=reasons,
        drop_reason="" if keep else "low_signal",
    )


def grade_rows(rows: list[dict[str, Any]]) -> tuple[list[Any], list[dict[str, Any]]]:
    candidates = []
    drops = []
    for index, row in enumerate(rows, start=1):
        text = str(row.get("content") or row.get("text") or row.get("message") or "")
        source_id = str(row.get("id") or row.get("source_id") or f"row-{index}")
        grade = grade_text(text, source_id=source_id)
        if grade.keep_candidate:
            candidates.append(grade.to_candidate(text))
        else:
            drops.append({"source_id": source_id, "drop_reason": grade.drop_reason})
    return candidates, drops
