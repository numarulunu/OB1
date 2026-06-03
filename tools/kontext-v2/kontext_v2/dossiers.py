from __future__ import annotations

import re
from typing import Any


def render_category_dossier(slug: str, name: str, memories: list[dict[str, Any]]) -> str:
    lines = [f"# {name}", "", f"Category: `{slug}`", "", "## Memories"]
    for memory in memories:
        lines.extend(
            [
                "",
                f"### {memory.get('title') or memory.get('external_mem0_id')}",
                f"- ID: `{memory.get('external_mem0_id')}`",
                f"- Type: `{memory.get('memory_type', '')}`",
                f"- Text: {memory.get('text', '')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Edit Protocol",
            "Add HTML comments like `<!-- propose:update mem-id field=value -->` to request database patches.",
        ]
    )
    return "\n".join(lines)


def parse_dossier_edit(original_markdown: str, edited_markdown: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"<!--\s*propose:update\s+(\S+)\s+([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)\s*-->"
    )
    original_commands = {match.group(0) for match in pattern.finditer(original_markdown)}
    proposals = []
    for match in pattern.finditer(edited_markdown):
        if match.group(0) in original_commands:
            continue
        proposals.append(
            {
                "action": "update",
                "external_mem0_id": match.group(1),
                "field": match.group(2),
                "value": match.group(3),
            }
        )
    return proposals
