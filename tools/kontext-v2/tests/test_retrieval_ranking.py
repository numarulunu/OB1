import json

import kontext_v2.retrieval as retrieval
from kontext_v2.retrieval import expand_query, normalize_domain_values, prepare_score_row, query_domain_hints, score_row, search_memories


def row(memory_id, text, rank=0.0, **metadata):
    return {
        "external_mem0_id": memory_id,
        "title": text[:90],
        "text": text,
        "rank": rank,
        "metadata": metadata,
        "memory_type": metadata.get("memory_type"),
        "current_status": metadata.get("current_status"),
        "memory_tier": metadata.get("memory_tier"),
        "signal_strength": metadata.get("signal_strength"),
    }


def ranked_ids(query, rows):
    scored = [(index, score_row(query, item, requested_domains=set(), requested_tiers=set()), item) for index, item in enumerate(rows)]
    scored.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    return [item["external_mem0_id"] for _, _, item in scored]


def test_score_row_prefers_recent_active_memory_over_older_historical_conflict():
    rows = [
        row(
            "older-conflict",
            "Relationship state: Ionut and Luiza are fully together and should be treated as current.",
            rank=0.75,
            domains=["relationships", "psychology"],
            memory_type="relationship_pattern",
            signal_strength=8,
            current_status="active",
            memory_tier="historical",
            effective_at="2025-11-01T00:00:00Z",
            updated_at="2025-11-01T00:00:00Z",
        ),
        row(
            "current-state",
            "Relationship state: Ionut and Luiza are paused, with practical logistics treated as current.",
            rank=0.62,
            domains=["relationships", "psychology"],
            memory_type="relationship_pattern",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            effective_at="2026-05-17T00:00:00Z",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    assert ranked_ids("current relationship state Luiza logistics", rows)[0] == "current-state"


def test_score_row_keeps_protected_history_retrievable_for_sensitive_queries():
    rows = [
        row(
            "protected-history",
            "Past family-origin history shaped attachment, trust, and relationship patterns.",
            rank=0.12,
            domains=["family_origin", "psychology", "relationships"],
            memory_type="formative_event",
            signal_strength=9,
            current_status="dormant",
            memory_tier="cold",
            effective_at="2018-01-01T00:00:00Z",
            updated_at="2018-01-01T00:00:00Z",
        ),
        row(
            "active-workflow",
            "Current workflow preference for project notes and dashboard filters.",
            rank=0.88,
            domains=["ai", "systems", "workflow"],
            memory_type="project_state",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            effective_at="2026-05-26T00:00:00Z",
            updated_at="2026-05-26T00:00:00Z",
        ),
    ]

    assert ranked_ids("childhood trauma psychology relationship trust pattern", rows)[0] == "protected-history"


def test_score_row_keeps_protected_history_retrievable_for_autobiographical_when_queries():
    rows = [
        row(
            "protected-history",
            "Past family-origin history shaped attachment, trust, and relationship patterns.",
            rank=0.12,
            domains=["family_origin", "psychology", "relationships"],
            memory_type="formative_event",
            signal_strength=9,
            current_status="dormant",
            memory_tier="cold",
            effective_at="2018-01-01T00:00:00Z",
            updated_at="2018-01-01T00:00:00Z",
        ),
        row(
            "active-workflow",
            "Current workflow preference for project notes and dashboard filters.",
            rank=0.88,
            domains=["ai", "systems", "workflow"],
            memory_type="project_state",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            effective_at="2026-05-26T00:00:00Z",
            updated_at="2026-05-26T00:00:00Z",
        ),
    ]

    assert ranked_ids("when did childhood trauma shape my relationship trust pattern", rows)[0] == "protected-history"
    explanation = retrieval.explain_score_row(
        "when did childhood trauma shape my relationship trust pattern",
        rows[0],
        requested_domains=set(),
        requested_tiers=set(),
    )
    feature_names = {feature["name"] for feature in explanation["features"]}
    assert "protected_history_cold_tier" in feature_names


def test_prepare_score_row_preserves_score_and_caches_text_features():
    query = "Where is the deployment checklist after the latest project handoff?"
    raw = row(
        "benchmark-row",
        "user: The deployment checklist moved from the amber notebook to the project vault on 2026-05-20.",
        rank=0.2,
        domains=["systems", "workflow"],
        memory_type="benchmark_observation",
        signal_strength=5,
        current_status="benchmark",
        memory_tier="cold",
    )
    raw["context_text"] = "assistant: Earlier notes still mentioned the amber notebook."
    prepared = {**raw, "metadata": dict(raw["metadata"])}

    expected_score = score_row(query, raw, requested_domains=set(), requested_tiers={"cold"})
    prepare_score_row(prepared)

    assert "_score_text_cache" in prepared
    assert score_row(query, prepared, requested_domains=set(), requested_tiers={"cold"}) == expected_score


def test_prepare_score_row_reuses_cross_request_cache(monkeypatch):
    retrieval.clear_prepared_row_cache()
    raw = row(
        "cached-row",
        "Kontext latency optimization should prepare this text only once.",
        domains=["ai", "systems"],
        memory_type="project_state",
        signal_strength=8,
        current_status="active",
        memory_tier="active",
        updated_at="2026-05-22T00:00:00Z",
    )
    raw["updated_at"] = "2026-05-22T00:00:00Z"

    prepare_score_row(dict(raw))

    def fail_lexical_tokens(_text):
        raise AssertionError("row text was tokenized again instead of using the prepared cache")

    monkeypatch.setattr(retrieval, "lexical_tokens", fail_lexical_tokens)
    prepared = prepare_score_row(dict(raw))

    assert prepared["_score_text_cache"]["own_token_count"] > 0


def test_prepare_score_row_bounds_oversized_text_for_latency():
    retrieval.clear_prepared_row_cache()
    oversized = "head-marker " + ("filler " * 8000) + "tail-marker"
    raw = row(
        "oversized-row",
        oversized,
        domains=["ai", "systems"],
        memory_type="project_state",
        signal_strength=8,
        current_status="active",
        memory_tier="active",
        updated_at="2026-05-22T00:00:00Z",
    )
    raw["title"] = "Important title survives"
    raw["updated_at"] = "2026-05-22T00:00:00Z"

    prepared = prepare_score_row(raw)
    own_text = prepared["_score_text_cache"]["own_text"]

    assert len(own_text) <= retrieval.MAX_SCORING_TEXT_CHARS + len(raw["title"]) + 2
    assert "Important title survives" in own_text
    assert "head-marker" in own_text
    assert "tail-marker" in own_text


def test_search_memories_reuses_query_context(monkeypatch):
    class ManyRowsRepo:
        def list_memory_rows(self, limit=1000):
            return [
                row(
                    f"row-{index}",
                    "Kontext Mem0 project memory architecture pipeline observer latency.",
                    domains=["ai", "systems"],
                    memory_type="project_state",
                    signal_strength=8,
                    current_status="active",
                    memory_tier="active",
                    updated_at="2026-05-22T00:00:00Z",
                )
                for index in range(25)
            ]

    calls = {"expand": 0}
    original_expand = retrieval.expand_query

    def counted_expand(query):
        calls["expand"] += 1
        return original_expand(query)

    monkeypatch.setattr(retrieval, "expand_query", counted_expand)

    search_memories(
        ManyRowsRepo(),
        "Kontext Mem0 project memory architecture latency",
        top_k=5,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert calls["expand"] <= 2


def test_search_memories_uses_db_assisted_candidates_for_deep_project_queries(monkeypatch):
    monkeypatch.setattr(retrieval, "MIN_DB_ASSISTED_CANDIDATES", 1)

    class SearchRowsRepo:
        def __init__(self):
            self.search_calls = []
            self.list_limits = []

        def search_rows(self, query, top_k, domains, memory_types, memory_tiers, current_statuses):
            self.search_calls.append(
                {
                    "query": query,
                    "top_k": top_k,
                    "domains": domains,
                    "memory_types": memory_types,
                    "memory_tiers": memory_tiers,
                    "current_statuses": current_statuses,
                }
            )
            return [
                row(
                    "db-hit",
                    "Kontext Mem0 project memory architecture latency root cause lives in Python scoring.",
                    domains=["ai", "systems", "infrastructure"],
                    memory_type="project_state",
                    signal_strength=9,
                    current_status="active",
                    memory_tier="active",
                    updated_at="2026-05-22T00:00:00Z",
                )
            ]

        def list_memory_rows(self, limit=1000):
            self.list_limits.append(limit)
            return [
                row(
                    "fallback-hit",
                    "Kontext fallback high signal project state.",
                    domains=["ai", "systems"],
                    memory_type="project_state",
                    signal_strength=8,
                    current_status="active",
                    memory_tier="active",
                    updated_at="2026-05-22T00:00:00Z",
                )
            ]

    repo = SearchRowsRepo()

    results = search_memories(
        repo,
        "Kontext Mem0 project memory architecture latency",
        top_k=2,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert repo.search_calls[0]["top_k"] == retrieval.DB_ASSISTED_CANDIDATE_LIMIT
    assert repo.search_calls[0]["domains"] == []
    assert repo.list_limits == [retrieval.HIGH_SIGNAL_FALLBACK_LIMIT]
    assert [item["external_mem0_id"] for item in results] == ["db-hit", "fallback-hit"]


def test_score_row_penalizes_superseded_memory_below_current_successor():
    rows = [
        row(
            "superseded",
            "AI memory architecture: Kontext Mem0 source of truth says use old local-only Kontext as the main database.",
            rank=0.95,
            domains=["ai", "systems", "infrastructure"],
            memory_type="architecture_decision",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            superseded_by="current",
            superseded_at="2026-05-16T12:00:00Z",
            updated_at="2026-05-10T00:00:00Z",
        ),
        row(
            "current",
            "AI memory architecture: Mem0 remains source of truth while Kontext V2 mirrors in shadow mode.",
            rank=0.42,
            domains=["ai", "systems", "infrastructure"],
            memory_type="architecture_decision",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            effective_at="2026-05-17T00:00:00Z",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    assert ranked_ids("AI memory architecture Kontext Mem0 source of truth", rows)[0] == "current"


def test_score_row_uses_effective_or_updated_at_as_close_score_tiebreaker():
    rows = [
        row("older", "Current OB1 memory status: pipeline healthy.", rank=0.5, domains=["ai"], memory_type="project_state", signal_strength=7, current_status="active", memory_tier="active", updated_at="2025-01-01T00:00:00Z"),
        row("newer", "Current OB1 memory status: pipeline healthy.", rank=0.5, domains=["ai"], memory_type="project_state", signal_strength=7, current_status="active", memory_tier="active", effective_at="2026-05-17T00:00:00Z"),
    ]

    assert ranked_ids("current OB1 memory status pipeline", rows)[0] == "newer"


class FakeRepo:
    def __init__(self):
        self.requested_limit = None

    def list_memory_rows(self, limit=1000):
        self.requested_limit = limit
        return []


def test_memory_cleanup_policy_query_expands_to_project_domains():
    expanded = expand_query("What is the policy for keeping, deleting, and compacting my second-brain memories?")

    assert {"systems", "workflow"} <= query_domain_hints(expanded)


def test_search_memories_uses_deep_candidate_pool_for_memory_policy_queries():
    repo = FakeRepo()

    search_memories(
        repo,
        "What is the policy for keeping, deleting, and compacting my second-brain memories?",
        top_k=5,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert repo.requested_limit == 10000


def test_score_row_boosts_decisions_for_memory_cleanup_policy_queries():
    rows = [
        row(
            "generic-pattern",
            "Workflow psychology note about policy, keeping, deleting, compacting, and second-brain memories.",
            domains=["workflow", "psychology", "business", "vocality"],
            memory_type="pattern",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "policy-decision",
            "Memory cleanup policy: keep durable memories, flag stale items, compact and archive low-priority context.",
            domains=["ai", "systems", "memory", "workflow"],
            memory_type="decision",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    assert ranked_ids("What is the policy for keeping, deleting, and compacting my second-brain memories?", rows)[0] == "policy-decision"

def test_score_row_boosts_project_state_for_memory_policy_queries():
    rows = [
        row(
            "generic-decision",
            "Memory cleanup policy decision mentions cleanup and compacting but not the current project-state details.",
            domains=["ai", "systems", "memory", "workflow"],
            memory_type="decision",
            signal_strength=5,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "project-state",
            "Project state: agent-first memory writes use direct MCP save/update and dream cleanup is CLI-first with approval.",
            domains=["ai", "systems", "memory", "workflow"],
            memory_type="project_state",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-21T00:00:00Z",
        ),
    ]

    assert ranked_ids("What is the agent-first memory write policy for Codex and Claude?", rows)[0] == "project-state"


def test_ai_systems_domain_alias_expands_to_ai_and_systems():
    assert {"ai", "systems", "ai_systems"} <= normalize_domain_values(["ai_systems"])


def test_lexical_tokens_keeps_short_ai_token_and_strips_diacritics():
    assert "ai" in retrieval.lexical_tokens("ai memory architecture")
    assert retrieval.lexical_tokens("traum\u0103 ata\u0219ament \u00eencredere") == ["trauma", "atasament", "incredere"]


def test_query_domain_hints_cover_short_ai_and_romanian_sensitive_terms():
    assert "ai" in query_domain_hints("ai status")
    assert "family" in query_domain_hints("mama tata copilarie")
    assert "psychology" in query_domain_hints("traum\u0103 ata\u0219ament \u00eencredere")
    assert "relationships" in query_domain_hints("relatie cu partenerul")


def test_romanian_protected_history_query_ranks_protected_memory_first():
    rows = [
        row(
            "active-workflow",
            "Workflow architecture next steps for agents and systems.",
            rank=0.6,
            domains=["ai", "systems"],
            memory_type="workflow_state",
            current_status="active",
            memory_tier="active",
            signal_strength=5,
        ),
        row(
            "protected-history",
            "Cum m-a influentat mama in copilarie si tiparul meu de atasament.",
            rank=0.4,
            domains=["family", "psychology"],
            memory_type="formative_event",
            current_status="historical",
            memory_tier="cold",
            signal_strength=7,
            is_protected_autobiographical_history=True,
        ),
    ]

    assert ranked_ids("cum m-a influentat copilaria si relatia cu mama", rows)[0] == "protected-history"


def test_english_father_query_counts_as_sensitive_protected_history():
    rows = [
        row(
            "active-workflow",
            "Agent architecture decision for memory systems.",
            rank=0.6,
            domains=["ai", "systems"],
            memory_type="architecture_decision",
            current_status="active",
            memory_tier="active",
            signal_strength=5,
        ),
        row(
            "protected-history",
            "Father attachment pattern shaped trust and abandonment.",
            rank=0.4,
            domains=["family", "psychology"],
            memory_type="formative_event",
            current_status="historical",
            memory_tier="cold",
            signal_strength=7,
            is_protected_autobiographical_history=True,
        ),
    ]

    assert ranked_ids("how did my father shape my trust pattern", rows)[0] == "protected-history"


def test_expand_query_uses_whole_word_triggers_not_substrings():
    invoice = expand_query("create invoice for client")
    compactification = expand_query("compactification of memory")

    assert "opera" not in invoice
    assert "repertoire" not in invoice
    assert "archive" not in compactification
    assert "cleanup" not in compactification


def test_search_memories_allows_top_k_50_for_parity_sweeps():
    class ManyRowsRepo:
        def search_rows(self, *args, **kwargs):
            return []

        def list_memory_rows(self, limit=1000):
            return [
                row(
                    f"memory-{index:02d}",
                    f"AI memory architecture candidate {index:02d}",
                    rank=1.0 - (index / 100.0),
                    domains=["ai", "systems"],
                    memory_type="project_state",
                    signal_strength=8,
                    current_status="active",
                    memory_tier="active",
                )
                for index in range(60)
            ]

    results = search_memories(
        ManyRowsRepo(),
        "ai memory architecture",
        top_k=50,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert len(results) == 50


def test_ai_memory_architecture_prefers_project_decision_over_loose_pattern():
    rows = [
        row(
            "loose-pattern",
            "Current architecture language appears in a personal workflow pattern, but it is not an AI memory system decision.",
            domains=["vocality", "design", "workflow", "branding"],
            memory_type="pattern",
            signal_strength=10,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "memory-architecture",
            "AI memory architecture: Mem0 is the source of truth while Kontext mirrors and evaluates retrieval parity.",
            domains=["ai", "systems", "infrastructure"],
            memory_type="decision",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    assert ranked_ids("What is the current architecture of my AI memory system?", rows)[0] == "memory-architecture"


def test_ai_agent_preferences_prefers_project_state_over_personal_pattern():
    rows = [
        row(
            "personal-pattern",
            "AI agent preferences are mentioned inside a psychology pattern, but this is not the operating preference record.",
            domains=["ai", "systems", "psychology"],
            memory_type="shadow_motive",
            signal_strength=10,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "agent-preference",
            "AI agent preference: use focused memory retrieval, direct MCP writes, and safe automation workflows.",
            domains=["ai_systems", "workflow"],
            memory_type="project_state",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    assert ranked_ids("What are my preferences for AI agents, memory, and automation systems?", rows)[0] == "agent-preference"


def test_project_boost_does_not_override_psychology_execution_query():
    rows = [
        row(
            "ai-workflow-decision",
            "Execution and systems appear in this AI automation decision, but it is not the psychology pattern.",
            domains=["business", "ai_systems", "marketing", "workflow"],
            memory_type="decision",
            signal_strength=10,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "execution-pattern",
            "Psychology execution pattern: blocked execution, avoidance, and workflow friction.",
            domains=["psychology", "workflow"],
            memory_type="psychology_pattern",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    assert ranked_ids("What are my psychology execution patterns and workflow blocks?", rows)[0] == "execution-pattern"


def test_sensitive_agent_context_query_prefers_careful_handling_decision_over_project_state():
    rows = [
        row(
            "project-state",
            "AI agents systems workflow project state for automation preferences and current operating notes.",
            domains=["ai_systems", "workflow"],
            memory_type="project_state",
            signal_strength=10,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "careful-decision",
            "Decision for agents: handle psychology and relationship context carefully in workflows.",
            domains=["ai_systems", "workflows"],
            memory_type="decision",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]
    query = "What psychology and relationship context should agents handle carefully right now?"

    assert ranked_ids(query, rows)[0] == "careful-decision"
    explanation = retrieval.explain_score_row(query, rows[1], requested_domains=set(), requested_tiers=set())
    assert {feature["name"] for feature in explanation["features"]} >= {"sensitive_agent_context_type"}
    assert "workflow" in normalize_domain_values(["workflows"])


def test_project_boost_does_not_override_finance_context_query():
    rows = [
        row(
            "systems-money-decision",
            "Money execution systems decision for automation and workflow operations.",
            domains=["money_execution", "systems"],
            memory_type="decision",
            signal_strength=10,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "finance-context",
            "Finance PFA context: taxes, business money, income handling, and fiscal planning.",
            domains=["finance", "business"],
            memory_type="finance_context",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    assert ranked_ids("What is my finance PFA context for business money?", rows)[0] == "finance-context"


def test_finance_app_query_prefers_explicit_finance_project_state():
    rows = [
        row(
            "cross-domain-workflow",
            "2026 current finance app accounting workflow state for Vocality business psychology execution workflow.",
            domains=["business", "psychology", "vocality", "workflow"],
            memory_type="workflow",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "finance-project-state",
            "2026 current finance app accounting project state for tax compliance.",
            domains=["finance", "systems", "tax compliance"],
            memory_type="project_state",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    query = "What is the current state of the finance app accounting workflow?"

    assert ranked_ids(query, rows)[0] == "finance-project-state"
    explanation = retrieval.explain_score_row(query, rows[1], requested_domains=set(), requested_tiers=set())
    assert {feature["name"] for feature in explanation["features"]} >= {"finance_domain_focus", "finance_state_type"}


def test_opera_funding_goal_does_not_overfocus_finance_context():
    rows = [
        row(
            "finance-context",
            "2026 opera funding context, business finance paperwork, and money planning.",
            domains=["business", "finance", "opera"],
            memory_type="finance_context",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
        row(
            "opera-goal",
            "2026 opera funding goal and career context decision for auditions and workflow.",
            domains=["ai", "business", "opera", "workflow"],
            memory_type="goal",
            signal_strength=9,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-17T00:00:00Z",
        ),
    ]

    query = "What is my opera funding goal and career context?"

    assert ranked_ids(query, rows)[0] == "opera-goal"
    explanation = retrieval.explain_score_row(query, rows[0], requested_domains=set(), requested_tiers=set())
    assert "finance_domain_focus" not in {feature["name"] for feature in explanation["features"]}


def test_named_topic_domain_hints_include_melocchi_branding_and_accounting():
    assert {"opera", "vocality", "workflow"} <= query_domain_hints("Melocchi session extraction pipeline")
    assert {"business", "vocality"} <= query_domain_hints("Vocality branding and design preferences")
    assert "finance" in query_domain_hints("Accounting workflow state")


def test_explain_score_row_matches_score_and_omits_raw_memory_text():
    private_text = "AI memory architecture private raw details that must never appear in explanations."
    item = row(
        "rank-explain-memory",
        private_text,
        rank=0.42,
        domains=["ai", "systems", "infrastructure"],
        memory_type="architecture_decision",
        signal_strength=9,
        current_status="active",
        memory_tier="active",
        updated_at="2026-05-18T00:00:00Z",
    )

    explanation = retrieval.explain_score_row(
        "What is the current architecture of my AI memory system?",
        item,
        requested_domains=set(),
        requested_tiers=set(),
    )

    expected_score = score_row(
        "What is the current architecture of my AI memory system?",
        item,
        requested_domains=set(),
        requested_tiers=set(),
    )
    assert abs(explanation["total"] - expected_score) < 0.001
    assert explanation["domains"] == ["ai", "infrastructure", "systems"]
    assert "systems" in explanation["matched_domains"]
    assert explanation["memory_type"] == "architecture_decision"
    assert explanation["tier"] == "active"
    assert explanation["status"] == "active"
    assert {feature["name"] for feature in explanation["features"]} >= {
        "base_rank",
        "lexical_own",
        "domain_hint",
        "project_domain",
        "project_type",
        "signal_strength",
        "tier_active",
        "status_active",
    }
    rendered = json.dumps(explanation).lower()
    assert private_text.lower() not in rendered
    assert "text" not in explanation
    assert "memory" not in explanation
    assert "content" not in explanation


def test_search_memories_can_attach_sanitized_score_explanations():
    private_text = "Kontext Mem0 mirror private ranking source text that should not leak into score_explanation."

    class RowsRepo:
        def list_memory_rows(self, limit=1000):
            return [
                row(
                    "explain-search-memory",
                    private_text,
                    rank=0.3,
                    domains=["ai", "systems"],
                    memory_type="project_state",
                    signal_strength=8,
                    current_status="active",
                    memory_tier="active",
                    updated_at="2026-05-18T00:00:00Z",
                )
            ]

    query = "How is Kontext wired into my AI memory system?"
    results = search_memories(
        RowsRepo(),
        query,
        top_k=1,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
        include_explanations=True,
    )

    assert results[0]["external_mem0_id"] == "explain-search-memory"
    expected_score = score_row(
        query,
        results[0],
        requested_domains=query_domain_hints(expand_query(query)),
        requested_tiers=set(),
    )
    assert abs(results[0]["score_explanation"]["total"] - expected_score) < 0.001
    assert private_text.lower() not in json.dumps(results[0]["score_explanation"]).lower()


def test_project_shadow_mode_query_does_not_prefer_psychology_shadow_patterns():
    rows = [
        row(
            "psychology-shadow",
            "Psychology shadow pattern: identity, trauma, attachment, and relationship trust context.",
            domains=["psychology", "relationships"],
            memory_type="psychology_pattern",
            signal_strength=10,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-18T00:00:00Z",
        ),
        row(
            "kontext-shadow",
            "Mem0 remains source of truth while Kontext V2 mirrors in read-only mode on the VPS.",
            domains=["ai", "systems", "infrastructure", "workflow"],
            memory_type="project_state",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-18T00:00:00Z",
        ),
    ]

    assert ranked_ids("What is Kontext V2 shadow read-only mode synced from Mem0?", rows)[0] == "kontext-shadow"


def test_shadow_telemetry_query_domains_are_project_observability():
    context = retrieval.build_score_query_context("retrieval shadow telemetry scorer version result metrics")

    assert context.project_shadow_mode is True
    assert context.query_domains & {"ai", "systems", "infrastructure"}
    assert "psychology" not in context.query_domains


def test_shadow_telemetry_query_prefers_project_observability_over_psychology():
    rows = [
        row(
            "psychology-shadow",
            "Psychology shadow pattern: identity, trauma, attachment, and relationship trust context.",
            domains=["psychology", "relationships"],
            memory_type="psychology_pattern",
            signal_strength=10,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-18T00:00:00Z",
        ),
        row(
            "retrieval-telemetry",
            "Retrieval shadow telemetry tracks scorer version, result metrics, and quality gates.",
            domains=["ai", "systems", "infrastructure", "workflow"],
            memory_type="workflow",
            signal_strength=8,
            current_status="active",
            memory_tier="active",
            updated_at="2026-05-18T00:00:00Z",
        ),
    ]

    assert ranked_ids("retrieval shadow telemetry scorer version result metrics", rows)[0] == "retrieval-telemetry"


def test_sensitive_query_fallback_caps_memory_row_scan_to_2500():
    class CapturingRepo:
        def __init__(self):
            self.requested_limit = None

        def search_rows(self, *args, **kwargs):
            return []

        def list_memory_rows(self, limit=1000):
            self.requested_limit = limit
            return [
                row(
                    "sensitive-cap-row",
                    "Relationship psychology context should still be retrievable inside the bounded scan.",
                    domains=["relationships", "psychology"],
                    memory_type="relationship_pattern",
                    signal_strength=8,
                    current_status="active",
                    memory_tier="active",
                    updated_at="2026-05-18T00:00:00Z",
                )
            ]

    repo = CapturingRepo()

    results = search_memories(
        repo,
        "relationship psychology memory context",
        top_k=1,
        domains=[],
        memory_types=[],
        memory_tiers=[],
        current_statuses=[],
    )

    assert repo.requested_limit == 2500
    assert results[0]["external_mem0_id"] == "sensitive-cap-row"
