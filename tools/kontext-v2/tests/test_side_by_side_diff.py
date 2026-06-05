from kontext_v2.parity import compare_search_results


def test_side_by_side_diff_uses_ids_and_metadata_without_memory_text():
    mem0 = [
        {
            "id": "mem-1",
            "memory": "secret raw text",
            "metadata": {"domains": ["ai"], "memory_type": "project_state"},
        }
    ]
    kontext = [
        {
            "external_mem0_id": "mem-1",
            "text": "secret raw text",
            "metadata": {"domains": ["ai"]},
            "memory_type": "project_state",
        }
    ]

    report = compare_search_results("query-hash", mem0, kontext)
    rendered = str(report)

    assert report["mem0_count"] == 1
    assert report["kontext_count"] == 1
    assert report["shared_external_ids"] == ["mem-1"]
    assert "secret raw text" not in rendered
