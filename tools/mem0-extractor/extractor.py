from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from grading import junk_reason
from schemas import Candidate, ExtractionResult, MemoryProposal, normalize_proposal

TYPE_DECISION_TREE = """
Type decision tree:
- Chosen architecture/policy/tooling direction -> decision.
- Current status, blocker, pending next step, active project state -> project_state.
- Repeatable process, pipeline, runbook, operating procedure -> workflow.
- User preference, instruction, taste, default, likes/dislikes -> preference.
- Generalized takeaway from experience -> lesson.
- Important person, mentor, family member, partner, close/influential contact -> person.
- Relationship dynamic, attachment/trust/conflict pattern with another person -> relationship.
- Formative event or identity-shaping life context -> identity_shaping.
- Deep recurring drive, wound, shame/fury/ambition source -> shadow_motive.
- AI philosophy, memory architecture, agent-system breakthrough -> ai_breakthrough.
- Otherwise use pattern.

Pattern is a last-resort type. Use it only for recurring behavior/system patterns that are not decisions, workflows, preferences, project state, lessons, relationships, people, identity material, or AI breakthroughs.
""".strip()

PROTECTED_DOMAINS = {"psychology", "relationships", "family_origin"}
HARD_GATE_WARNINGS = {"generic_vocal_lesson_sludge", "generic_business_or_finance_fact", "missing_domains", "low_signal_save"}

POLICY_TEXT = f"""
Extract durable memories for Ionut's operational second brain, not raw transcript summaries.
Ultimate rule: keep only information that will improve future advice, project continuity, relationship understanding, self-model accuracy, or daily execution.

Keep aggressively:
- Psychology, relationships, family-origin material, formative events, emotionally intense events, shadow motives, and identity-shaping patterns. Do not flatten psychology; preserve timeline, ambiguity, competing interpretations, and concrete influential people when they shaped identity, attachment, ambition, or trust.
- Current business, branding, money, opera, AI, systems, workflows, settings, and active or dormant-but-important projects.
- Own voice and opera-career context: vocal development, fach, repertoire/role direction, useful vocal or physical characteristics, important teacher/mentor influence, and compact personal method.

Drop aggressively:
- Transcript-processing artifacts, speaker diarization, speaker label rules, raw tool logs, command output, generic Q&A, duplicate phrasing, stale implementation trivia, and generic vocal lesson/anatomy sludge.
- Student-specific vocal lesson content unless it reveals reusable business/method IP.
- Junk should be deleted, not hidden as cold.

Tiers:
- active = current defaults, operating context, live systems, current decisions.
- historical = formative history or older context that still explains current behavior.
- cold = rarely needed background that remains true but should not normally be retrieved.

{TYPE_DECISION_TREE}

Use each candidate's suggested_memory_type as the default. Override it only when the content clearly fits a better type, and explain the override in reason.
Return JSON only with proposals. Use source_ids. Prefer split cards when project facts and emotional context are mixed.
""".strip()


def strip_code_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def build_extraction_prompt(candidates: list[Candidate], session_label: str = "session") -> str:
    candidate_rows = [candidate.to_prompt_dict() for candidate in candidates]
    schema = {
        "proposals": [
            {
                "action": "save|update|skip|ask_user",
                "content": "self-contained durable memory card",
                "domains": ["psychology|relationships|family_origin|ai|systems|business|money_execution|opera|vocality|workflow"],
                "memory_type": "person|relationship|event|lesson|trigger|shadow_motive|identity_shaping|ai_breakthrough|decision|preference|workflow|project|project_state|pattern",
                "signal_strength": "number 1..10",
                "current_status": "active|dormant|resolved|review",
                "memory_tier": "active|historical|cold",
                "source_ids": ["source ids used"],
                "reason": "short non-private reason; include why type differs from suggested_memory_type when overridden",
            }
        ],
        "risk_notes": ["only if needed"],
    }
    return (
        f"{POLICY_TEXT}\n\n"
        f"Session label: {session_label}\n\n"
        f"Required JSON shape:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"Candidate rows:\n{json.dumps(candidate_rows, ensure_ascii=False, indent=2)}\n"
    )


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def inferred_memory_type(content: str, domains: list[str]) -> str:
    lower = content.lower()
    domain_set = set(domains)
    if _has_any(lower, ("identity-shaping", "identity shaping", "formative", "family-origin", "family origin", "childhood")):
        return "identity_shaping"
    if _has_any(lower, ("shadow motive", "shadow", "deep drive", "wound", "shame", "raw anger")):
        return "shadow_motive"
    if re.search(r"\b(decision|decided|chosen|settled|use .* as |keep .* as )\b", lower):
        return "decision"
    if _has_any(lower, ("prefers", "preference", "wants", "does not want", "likes", "dislikes", "default style")):
        return "preference"
    if _has_any(lower, ("current status", "next step", "blocked", "blocker", "pending", "todo", "remains to")):
        return "project_state"
    if _has_any(lower, ("workflow", "pipeline", "process", "runbook", "repeatable", "procedure")):
        return "workflow"
    if _has_any(lower, ("ai philosophy", "agent architecture", "memory architecture", "breakthrough")) or ("ai" in domain_set and "architecture" in lower):
        return "ai_breakthrough"
    if "relationships" in domain_set and _has_any(lower, ("dynamic", "attachment", "trust", "conflict", "relationship", "partner", "close person")):
        return "relationship"
    if "relationships" in domain_set or any(name in lower for name in ("mother", "father", "teacher", "mentor", "partner")):
        return "person"
    if _has_any(lower, ("lesson learned", "takeaway", "learned that")):
        return "lesson"
    return "pattern"


def repair_proposal_type(proposal: MemoryProposal) -> tuple[MemoryProposal, str | None]:
    inferred = inferred_memory_type(proposal.content, proposal.domains)
    if proposal.memory_type == "pattern" and inferred != "pattern":
        return replace(proposal, memory_type=inferred), f"retyped_from_pattern_to_{inferred}"
    return proposal, None


def parse_llm_response(raw: str) -> ExtractionResult:
    cleaned = strip_code_fence(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return ExtractionResult(proposals=[], risk_notes=["parse_error"])
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return ExtractionResult(proposals=[], risk_notes=["parse_error"])
    if not isinstance(parsed, dict):
        return ExtractionResult(proposals=[], risk_notes=["not_object"])
    proposals = []
    risk_notes = [str(note) for note in parsed.get("risk_notes") or [] if str(note).strip()]
    for row in parsed.get("proposals") or []:
        if isinstance(row, dict) and str(row.get("content") or "").strip():
            proposal, note = repair_proposal_type(normalize_proposal(row))
            proposals.append(proposal)
            if note:
                risk_notes.append(note)
    return ExtractionResult(proposals=proposals, risk_notes=risk_notes)


def proposal_quality_warnings(proposal: MemoryProposal) -> tuple[list[str], list[str]]:
    if proposal.action in {"skip", "ask_user"}:
        return [], []

    hard: list[str] = []
    soft: list[str] = []
    if not proposal.domains:
        hard.append("missing_domains")
    if proposal.signal_strength < 4:
        hard.append("low_signal_save")

    reason = junk_reason(proposal.content)
    if reason in HARD_GATE_WARNINGS:
        hard.append(reason)

    if PROTECTED_DOMAINS.intersection(proposal.domains) and proposal.signal_strength < 7:
        soft.append("protected_domain_low_signal")

    return hard, soft


def metadata_quality_report(proposals: list[MemoryProposal], max_pattern_ratio: float = 0.6) -> dict[str, Any]:
    proposal_count = len(proposals)
    pattern_count = sum(1 for proposal in proposals if proposal.memory_type == "pattern")
    pattern_ratio = pattern_count / proposal_count if proposal_count else 0.0
    hard_warnings: list[str] = []
    soft_warnings: list[str] = []
    status = "passed"
    if proposal_count and pattern_ratio > max_pattern_ratio:
        status = "failed"
        hard_warnings.append("pattern_overuse")

    for proposal in proposals:
        hard, soft = proposal_quality_warnings(proposal)
        hard_warnings.extend(hard)
        soft_warnings.extend(soft)

    if hard_warnings:
        status = "failed"

    warnings = sorted(set(hard_warnings + soft_warnings))
    return {
        "status": status,
        "proposal_count": proposal_count,
        "pattern_count": pattern_count,
        "pattern_ratio": round(pattern_ratio, 4),
        "max_pattern_ratio": max_pattern_ratio,
        "hard_warning_count": len(hard_warnings),
        "soft_warning_count": len(soft_warnings),
        "warnings": warnings,
    }


def proposals_from_candidates(candidates: list[Candidate]) -> ExtractionResult:
    proposals = []
    for candidate in candidates:
        proposal, _ = repair_proposal_type(
            normalize_proposal(
                {
                    "action": "save",
                    "content": candidate.text,
                    "domains": candidate.domains,
                    "memory_type": candidate.memory_type,
                    "signal_strength": candidate.signal_strength,
                    "memory_tier": candidate.memory_tier,
                    "current_status": "active" if candidate.memory_tier == "active" else "dormant",
                    "source_ids": [candidate.source_id],
                    "reason": "deterministic candidate; needs LLM distillation before broad apply",
                }
            )
        )
        proposals.append(proposal)
    return ExtractionResult(proposals=proposals)
