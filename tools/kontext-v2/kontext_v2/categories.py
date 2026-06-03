from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from kontext_v2.models import MemoryRecord


@dataclass(frozen=True)
class CategorySpec:
    slug: str
    name: str
    description: str = ""
    domains: tuple[str, ...] = ()
    memory_types: tuple[str, ...] = ()
    tiers: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()


DEFAULT_CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec(
        slug="identity",
        name="Identity",
        description="Stable identity, preferences, communication style, and personal context.",
        domains=("identity", "personal", "psychology", "relationships", "opera"),
        memory_types=("identity", "preference", "psychology", "relationship"),
        terms=("identity", "bio", "preference", "psychology", "relationship", "opera", "voice"),
    ),
    CategorySpec(
        slug="projects",
        name="Projects",
        description="Current project state, decisions, plans, and roadmap memory.",
        domains=("project", "projects", "business"),
        memory_types=("project_state", "decision", "plan"),
        terms=("project", "roadmap", "status", "plan", "implementation", "benchmark"),
    ),
    CategorySpec(
        slug="systems",
        name="Systems",
        description="Infrastructure, AI systems, deployments, memory systems, and tooling.",
        domains=("ai", "systems", "memory", "infrastructure"),
        memory_types=("system", "infrastructure"),
        terms=("kontext", "mem0", "mcp", "vps", "deploy", "database", "postgres", "pgvector", "docker"),
    ),
    CategorySpec(
        slug="design",
        name="Design",
        description="Brand, UI, frontend, and product design context.",
        domains=("design", "brand", "frontend", "ui"),
        memory_types=("design",),
        terms=("design", "brand", "frontend", "dashboard", "ui", "visual"),
    ),
    CategorySpec(
        slug="workflow",
        name="Workflow",
        description="Agent workflow, CLI behavior, hooks, and operating rules.",
        domains=("workflow",),
        memory_types=("workflow", "operating_rule"),
        terms=("workflow", "codex", "claude", "agent", "hook", "cli", "instruction"),
    ),
    CategorySpec(
        slug="archive",
        name="Archive",
        description="Historical, cold, dormant, or resolved memory kept for reference.",
        tiers=("historical", "cold", "archive"),
        statuses=("historical", "resolved", "dormant", "inactive"),
        terms=("historical", "archive", "old", "cold"),
    ),
)


def normalize_category_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:80] or "category"


def default_category_rows() -> list[dict[str, str]]:
    return [
        {"slug": item.slug, "name": item.name, "description": item.description}
        for item in DEFAULT_CATEGORIES
    ]


def _string_list(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, dict):
        values = list(value.values())
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    return {str(item).strip().lower() for item in values if str(item).strip()}


def infer_category_slugs(memory: MemoryRecord) -> list[str]:
    metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
    domains = _string_list(metadata.get("domains"))
    tags = _string_list(metadata.get("tags")) | _string_list(metadata.get("categories"))
    memory_type = str(memory.memory_type or metadata.get("memory_type") or "").strip().lower()
    tier = str(memory.memory_tier or metadata.get("memory_tier") or "").strip().lower()
    status = str(memory.current_status or metadata.get("current_status") or "").strip().lower()
    haystack = " ".join(
        str(value or "").lower()
        for value in [memory.title, memory.text, memory_type, tier, status, " ".join(domains), " ".join(tags)]
    )

    slugs: list[str] = []
    for spec in DEFAULT_CATEGORIES:
        matched = False
        if domains.intersection(spec.domains):
            matched = True
        if tags and tags.intersection({spec.slug, spec.name.lower(), *spec.domains, *spec.terms}):
            matched = True
        if memory_type and memory_type in spec.memory_types:
            matched = True
        if tier and tier in spec.tiers:
            matched = True
        if status and status in spec.statuses:
            matched = True
        if spec.terms and any(term in haystack for term in spec.terms):
            matched = True
        if matched:
            slugs.append(spec.slug)

    return list(dict.fromkeys(slugs or ["archive"]))
