from report_repair import repair_source_ids


def test_repair_source_ids_resolves_prefixes_and_removes_invalid_ids():
    report = {
        "summary": {},
        "candidates": [
            {"source_id": "7ca71592-a70a-4918-85af-c9f83cf50baa"},
            {"source_id": "f750880a-f5aa-4875-b38f-63781f9f1ac8"},
        ],
        "proposals": [
            {
                "action": "save",
                "content": "Durable memory.",
                "source_ids": ["7ca71592", "fe18f825-b44f-4cd5-9378-1f9f1ac8"],
            }
        ],
    }

    repaired, counts = repair_source_ids(report)

    assert repaired["proposals"][0]["source_ids"] == ["7ca71592-a70a-4918-85af-c9f83cf50baa"]
    assert counts["resolved_prefixes"] == 1
    assert counts["removed_invalid_source_ids"] == 1
    assert repaired["proposals"][0]["action"] == "save"


def test_repair_source_ids_marks_orphaned_save_for_user_review():
    report = {
        "summary": {},
        "candidates": [{"source_id": "7ca71592-a70a-4918-85af-c9f83cf50baa"}],
        "proposals": [{"action": "save", "content": "Durable memory.", "source_ids": ["missing-source"], "reason": "test"}],
    }

    repaired, counts = repair_source_ids(report)

    assert repaired["proposals"][0]["source_ids"] == []
    assert repaired["proposals"][0]["action"] == "ask_user"
    assert counts["proposal_actions_changed_to_ask_user"] == 1
