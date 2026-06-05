from dedupe import plan_proposal_action
from schemas import MemoryProposal


class FakeMem0:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, query, top_k=5, domains=None, memory_tiers=None):
        self.queries.append((query, top_k, domains, memory_tiers))
        return {"results": self.results}


def proposal(content="Mem0 is the operational brain."):
    return MemoryProposal(
        action="save",
        content=content,
        domains=["ai", "systems"],
        memory_type="decision",
        signal_strength=8,
        current_status="active",
        memory_tier="active",
        source_ids=["row-1"],
        reason="durable",
    )


def test_exact_existing_memory_is_skipped():
    existing = [{"id": "mem-1", "text": "Mem0 is the operational brain.", "metadata": {"domains": ["ai"]}}]
    plan = plan_proposal_action(proposal(), FakeMem0(existing))

    assert plan.action == "skip"
    assert plan.existing_id == "mem-1"
    assert plan.reason == "exact_duplicate"


def test_strong_overlap_updates_existing_memory():
    existing = [{"id": "mem-2", "text": "Mem0 is the operational shared memory brain for AI tools.", "metadata": {"domains": ["ai"]}}]
    plan = plan_proposal_action(proposal("Mem0 is the operational shared memory brain for Codex and Claude."), FakeMem0(existing))

    assert plan.action == "update"
    assert plan.existing_id == "mem-2"
    assert plan.reason == "high_overlap"


def test_no_overlap_saves_new_memory():
    existing = [{"id": "mem-3", "text": "Unrelated finance memory.", "metadata": {"domains": ["finance"]}}]
    fake = FakeMem0(existing)
    plan = plan_proposal_action(proposal("The extractor uses memory tiers for active and historical context."), fake)

    assert plan.action == "save"
    assert plan.existing_id == ""
    assert fake.queries[0][2] == ["ai", "systems"]
