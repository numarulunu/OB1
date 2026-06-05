from __future__ import annotations

import difflib
import re
from typing import Any

from schemas import ActionPlan, MemoryProposal, normalize_text

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {"and", "the", "for", "with", "that", "this", "from", "into", "memory", "user", "ionut"}


def canonical_text(text: str) -> str:
    return " ".join(TOKEN_RE.findall(str(text or "").lower()))


def tokens(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(str(text or "").lower()) if len(token) > 2 and token not in STOPWORDS}


def overlap_score(a: str, b: str) -> float:
    canonical_a = canonical_text(a)
    canonical_b = canonical_text(b)
    if not canonical_a or not canonical_b:
        return 0.0
    ratio = difflib.SequenceMatcher(a=canonical_a, b=canonical_b).ratio()
    a_tokens = tokens(a)
    b_tokens = tokens(b)
    jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens) if a_tokens and b_tokens else 0.0
    return max(ratio, jaccard)


def memory_text(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("memory") or row.get("content") or row.get("data") or "")


def plan_proposal_action(proposal: MemoryProposal, mem0_client: Any, top_k: int = 5) -> ActionPlan:
    if proposal.action in {"skip", "ask_user"}:
        return ActionPlan(action=proposal.action, proposal=proposal, existing_id=proposal.existing_id, reason=proposal.reason)

    result = mem0_client.search(proposal.content, top_k=top_k, domains=proposal.domains or None, memory_tiers=None)
    rows = result.get("results") if isinstance(result, dict) else []
    best_id = ""
    best_score = 0.0
    for row in rows or []:
        existing_text = memory_text(row)
        existing_id = str(row.get("id") or "")
        if canonical_text(existing_text) == canonical_text(proposal.content):
            return ActionPlan(action="skip", proposal=proposal, existing_id=existing_id, reason="exact_duplicate")
        score = overlap_score(proposal.content, existing_text)
        if score > best_score:
            best_score = score
            best_id = existing_id
    if best_id and best_score >= 0.74:
        return ActionPlan(action="update", proposal=proposal, existing_id=best_id, reason="high_overlap")
    return ActionPlan(action="save", proposal=proposal, existing_id="", reason="new_memory")
