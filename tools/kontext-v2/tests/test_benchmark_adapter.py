from pathlib import Path

from kontext_v2.benchmarks.fixtures import load_locomo_tiny_fixture


FIXTURE = Path(__file__).parent / "fixtures" / "locomo_tiny.json"


def test_load_locomo_tiny_fixture_returns_conversations_and_questions():
    fixture = load_locomo_tiny_fixture(FIXTURE)

    assert fixture["dataset"] == "locomo_tiny"
    assert len(fixture["conversations"]) == 2
    assert fixture["conversations"][0]["conversation_id"] == "tiny-conv-1"
    assert fixture["questions"][0]["question"] == "Where is Alice planning to travel in June?"
    assert fixture["questions"][0]["expected_terms"] == ["berlin", "june"]
