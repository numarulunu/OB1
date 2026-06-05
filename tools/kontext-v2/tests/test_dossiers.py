from kontext_v2.dossiers import parse_dossier_edit, render_category_dossier


def test_dossier_is_projection_and_edits_become_patch_proposals():
    memories = [
        {
            "external_mem0_id": "mem-1",
            "title": "Architecture",
            "text": "Kontext V2 mirrors Mem0.",
            "memory_type": "project_state",
        }
    ]
    markdown = render_category_dossier("ai-systems", "AI Systems", memories)
    proposals = parse_dossier_edit(
        original_markdown=markdown,
        edited_markdown=markdown + "\n\n<!-- propose:update mem-1 memory_tier=active -->\n",
    )

    assert "# AI Systems" in markdown
    assert "mem-1" in markdown
    assert proposals == [
        {
            "action": "update",
            "external_mem0_id": "mem-1",
            "field": "memory_tier",
            "value": "active",
        }
    ]
