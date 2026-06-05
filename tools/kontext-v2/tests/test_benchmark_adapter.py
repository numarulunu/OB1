from pathlib import Path

import os

import psycopg

from kontext_v2.benchmarks.adapter import (
    BenchmarkMessage,
    CrossEncoderRerankConfig,
    KontextBenchmarkAdapter,
    SemanticRerankConfig,
    _BENCHMARK_MODEL_CACHE,
    _BENCHMARK_MODEL_CACHE_MAX_SIZE,
    benchmark_candidate_score,
    benchmark_candidate_score_for_category,
    benchmark_candidate_sort_key,
    clear_benchmark_model_cache_for_tests,
    fuse_rankings_rrf,
    rank_benchmark_candidates,
    rerank_benchmark_candidates_semantically,
    rerank_benchmark_candidates_with_cross_encoder,
)
from kontext_v2.benchmarks.adapter import _compact_context_for_index
from kontext_v2.benchmarks.fixtures import load_locomo_real_fixture, load_locomo_tiny_fixture
from kontext_v2.retrieval import expand_query, lexical_tokens, prepare_score_row, score_row
from kontext_v2.schema import apply_schema


FIXTURE = Path(__file__).parent / "fixtures" / "locomo_tiny.json"
REAL_FIXTURE = Path(__file__).parent / "fixtures" / "locomo_real_shape.json"


def test_benchmark_candidate_sort_key_uses_source_span_only_for_close_low_scores():
    narrow = {
        "memory_type": "benchmark_observation",
        "metadata": {"observation_kind": "session", "source_ids": [str(value) for value in range(100, 260)]},
    }
    wide = {
        "memory_type": "benchmark_observation",
        "metadata": {"observation_kind": "session", "source_ids": [str(value) for value in range(7000, 7326)]},
    }

    close_low = [(0, 43.7, narrow), (1, 43.0, wide)]
    close_low.sort(key=lambda item: benchmark_candidate_sort_key(*item), reverse=True)
    assert close_low[0][2] is wide

    different_bucket = [(0, 44.0, narrow), (1, 43.9, wide)]
    different_bucket.sort(key=lambda item: benchmark_candidate_sort_key(*item), reverse=True)
    assert different_bucket[0][2] is narrow

    high_confidence = [(0, 58.8, narrow), (1, 56.4, wide)]
    high_confidence.sort(key=lambda item: benchmark_candidate_sort_key(*item), reverse=True)
    assert high_confidence[0][2] is narrow


def test_benchmark_candidate_score_boosts_strong_adjacent_phrase_evidence():
    query = "atlas beacon copper drift ember fabric granite harbor"
    query_tokens = lexical_tokens(expand_query(query))
    distractor = {
        "external_mem0_id": "token-coverage-distractor",
        "title": "Benchmark observation",
        "text": "assistant: atlas spacer beacon spacer copper spacer drift spacer ember spacer fabric spacer granite spacer harbor",
        "metadata": {"observation_kind": "session", "source_ids": ["1", "2"], "domains": ["benchmark"]},
        "memory_type": "benchmark_observation",
        "current_status": "benchmark",
        "memory_tier": "cold",
        "signal_strength": 5,
        "rank": 0.0,
    }
    evidence = {
        "external_mem0_id": "adjacent-evidence",
        "title": "Benchmark observation",
        "text": "assistant: atlas beacon spacer copper drift spacer ember fabric spacer granite",
        "metadata": {"observation_kind": "session", "source_ids": ["3", "4"], "domains": ["benchmark"]},
        "memory_type": "benchmark_observation",
        "current_status": "benchmark",
        "memory_tier": "cold",
        "signal_strength": 5,
        "rank": 0.0,
    }
    prepare_score_row(distractor)
    prepare_score_row(evidence)

    assert score_row(query, distractor, requested_domains=set(), requested_tiers={"cold"}) > score_row(
        query, evidence, requested_domains=set(), requested_tiers={"cold"}
    )
    assert benchmark_candidate_score(
        query_tokens,
        evidence,
        score_row(query, evidence, requested_domains=set(), requested_tiers={"cold"}),
    ) > benchmark_candidate_score(
        query_tokens,
        distractor,
        score_row(query, distractor, requested_domains=set(), requested_tiers={"cold"}),
    )


def test_rank_benchmark_candidates_preserves_top20_head_while_reranking_tail():
    head = [
        (index, 100.0 - index, {"external_mem0_id": f"head-{index}", "metadata": {}})
        for index in range(20)
    ]
    tail_distractor = (20, 53.0, {"external_mem0_id": "tail-distractor", "metadata": {}})
    tail_evidence = (21, 52.0, {"external_mem0_id": "tail-evidence", "metadata": {}})

    def adjusted(_tokens, row, base_score):
        if row["external_mem0_id"] == "tail-evidence":
            return base_score + 2.0
        return base_score

    ranked = rank_benchmark_candidates([], [tail_evidence, *head, tail_distractor], score_func=adjusted)

    assert [item[2]["external_mem0_id"] for item in ranked[:20]] == [f"head-{index}" for index in range(20)]
    assert ranked[20][2]["external_mem0_id"] == "tail-evidence"


def test_benchmark_state_category_rerank_can_promote_user_preference_turn():
    head = [
        (
            index,
            40.0,
            {
                "external_mem0_id": f"head-{index}",
                "text": f"assistant: dashboard preference distractor {index}",
                "memory_type": "benchmark_observation",
                "metadata": {"observation_kind": "turn"},
            },
        )
        for index in range(20)
    ]
    target = (
        21,
        39.0,
        {
            "external_mem0_id": "target-user-preference",
            "text": "user: I prefer the dashboard compact rows instead",
            "memory_type": "benchmark_observation",
            "metadata": {"observation_kind": "turn"},
        },
    )
    query_tokens = lexical_tokens(expand_query("What dashboard style does the user prefer?"))

    ranked = rank_benchmark_candidates(
        query_tokens,
        [*head, target],
        score_func=lambda tokens, row, base: benchmark_candidate_score_for_category(
            tokens,
            row,
            base,
            "preference_following",
        ),
        locked_head_size=0,
    )

    assert ranked[0][2]["external_mem0_id"] == "target-user-preference"


def test_semantic_rerank_locks_head_and_moves_tail_candidate():
    class FakeModel:
        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False, batch_size=128):
            vectors = []
            for text in texts:
                value = str(text)
                if "needle" in value:
                    vectors.append([1.0, 0.0])
                else:
                    vectors.append([0.0, 1.0])
            return vectors

    head = [
        (index, 100.0 - index, {"external_mem0_id": f"head-{index}", "text": "head row"})
        for index in range(40)
    ]
    tail_distractor = (40, 50.0, {"external_mem0_id": "tail-distractor", "text": "ordinary row"})
    tail_evidence = (41, 49.0, {"external_mem0_id": "tail-evidence", "text": "needle row"})
    config = SemanticRerankConfig(
        enabled=True,
        locked_head_size=40,
        candidate_window=200,
        score_weight=5.0,
        fusion_rrf_k=0,
    )

    ranked = rerank_benchmark_candidates_semantically(
        "needle query",
        [*head, tail_distractor, tail_evidence],
        config,
        FakeModel(),
    )

    assert [item[2]["external_mem0_id"] for item in ranked[:40]] == [f"head-{index}" for index in range(40)]
    assert ranked[40][2]["external_mem0_id"] == "tail-evidence"


def test_semantic_rerank_config_reads_env_values():
    config = SemanticRerankConfig.from_env(
        {
            "KONTEXT_BENCHMARK_SEMANTIC_RERANK": "true",
            "KONTEXT_BENCHMARK_SEMANTIC_LOCKED_HEAD": "41",
            "KONTEXT_BENCHMARK_SEMANTIC_WINDOW": "180",
            "KONTEXT_BENCHMARK_SEMANTIC_WEIGHT": "12.5",
            "KONTEXT_BENCHMARK_SEMANTIC_MAX_CHARS": "1600",
            "KONTEXT_BENCHMARK_SEMANTIC_BATCH_SIZE": "64",
            "KONTEXT_BENCHMARK_SEMANTIC_RRF_K": "30",
            "KONTEXT_BENCHMARK_SEMANTIC_QUERY_FOCUSED_TEXT": "true",
        }
    )

    assert config.enabled is True
    assert config.locked_head_size == 41
    assert config.candidate_window == 180
    assert config.score_weight == 12.5
    assert config.max_chars == 1600
    assert config.batch_size == 64
    assert config.fusion_rrf_k == 30
    assert config.query_focused_text is True


def test_cross_encoder_rerank_config_reads_env_values():
    config = CrossEncoderRerankConfig.from_env(
        {
            "KONTEXT_BENCHMARK_CROSS_ENCODER_RERANK": "true",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_MODEL": "cross-encoder/custom",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_LOCKED_HEAD": "12",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_WINDOW": "80",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_WEIGHT": "3.5",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_MAX_CHARS": "900",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_BATCH_SIZE": "16",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_RRF_K": "8",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_QUERY_FOCUSED_TEXT": "true",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_TEXT_WINDOWS": "4",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_TEXT_WINDOW_OVERLAP": "30",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_LINE_WINDOWS": "3",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_SOURCE_COUNT_PENALTY": "0.2",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_ADJACENT_PENALTY": "5.0",
            "KONTEXT_BENCHMARK_CROSS_ENCODER_NORMALIZATION_QUESTION_TYPES": "instruction_following, preference_following",
        }
    )

    assert config.enabled is True
    assert config.model_name == "cross-encoder/custom"
    assert config.locked_head_size == 12
    assert config.candidate_window == 80
    assert config.score_weight == 3.5
    assert config.max_chars == 900
    assert config.batch_size == 16
    assert config.fusion_rrf_k == 8
    assert config.query_focused_text is True
    assert config.text_windows == 4
    assert config.text_window_overlap == 30
    assert config.line_windows == 3
    assert config.source_count_penalty_weight == 0.2
    assert config.adjacent_penalty_weight == 5.0
    assert config.normalization_question_types == ("instruction_following", "preference_following")
    assert CrossEncoderRerankConfig.from_env({}).enabled is False


def test_cross_encoder_rerank_config_uses_tuned_defaults():
    config = CrossEncoderRerankConfig.from_env({'KONTEXT_BENCHMARK_CROSS_ENCODER_RERANK': '1'})

    assert config.enabled is True
    assert config.candidate_window == 100
    assert config.score_weight == 10.0
    assert config.batch_size == 64
    assert config.max_chars == 1800
    assert config.fusion_rrf_k == 0
    assert config.query_focused_text is False
    assert config.text_windows == 1
    assert config.line_windows == 0
    assert config.source_count_penalty_weight == 0.0
    assert config.adjacent_penalty_weight == 0.0
    assert config.normalization_question_types == ()


def test_cross_encoder_rerank_locks_head_and_batches_pair_scores():
    class FakeCrossEncoder:
        def __init__(self):
            self.calls = []

        def predict(self, pairs, batch_size=32, show_progress_bar=False):
            self.calls.append((pairs, batch_size, show_progress_bar))
            return [2.0 if "needle" in pair[1] else 0.1 for pair in pairs]

    head = [
        (index, 100.0 - index, {"external_mem0_id": f"head-{index}", "text": "head row"})
        for index in range(2)
    ]
    tail_distractor = (2, 50.0, {"external_mem0_id": "tail-distractor", "text": "ordinary row"})
    tail_evidence = (3, 49.0, {"external_mem0_id": "tail-evidence", "text": "needle row"})
    config = CrossEncoderRerankConfig(
        enabled=True,
        locked_head_size=2,
        candidate_window=4,
        score_weight=2.0,
        batch_size=7,
        fusion_rrf_k=0,
    )
    model = FakeCrossEncoder()

    ranked = rerank_benchmark_candidates_with_cross_encoder(
        "needle query",
        [*head, tail_distractor, tail_evidence],
        config,
        model,
    )

    assert [item[2]["external_mem0_id"] for item in ranked[:2]] == ["head-0", "head-1"]
    assert ranked[2][2]["external_mem0_id"] == "tail-evidence"
    assert model.calls == [(
        [("needle query", "ordinary row"), ("needle query", "needle row")],
        7,
        False,
    )]


def test_cross_encoder_rerank_can_penalize_broad_adjacent_session_chunks():
    class FakeCrossEncoder:
        def predict(self, pairs, batch_size=32, show_progress_bar=False):
            return [1.0 for _ in pairs]

    broad_distractor = (
        0,
        20.0,
        {
            "external_mem0_id": "broad-distractor",
            "memory_type": "benchmark_observation",
            "metadata": {
                "observation_kind": "session",
                "source_ids": [str(index) for index in range(250)],
            },
            "text": "needle query " * 20,
        },
    )
    narrower_evidence = (
        1,
        19.0,
        {
            "external_mem0_id": "narrower-evidence",
            "memory_type": "benchmark_observation",
            "metadata": {
                "observation_kind": "session",
                "source_ids": ["source-1"],
            },
            "text": "needle evidence",
        },
    )
    config = CrossEncoderRerankConfig(
        enabled=True,
        candidate_window=2,
        score_weight=10.0,
        source_count_penalty_weight=0.2,
        adjacent_penalty_weight=5.0,
    )

    ranked = rerank_benchmark_candidates_with_cross_encoder(
        "needle query",
        [broad_distractor, narrower_evidence],
        config,
        FakeCrossEncoder(),
    )

    assert ranked[0][2]["external_mem0_id"] == "narrower-evidence"


def test_cross_encoder_normalization_can_be_scoped_to_question_category():
    class FakeCrossEncoder:
        def predict(self, pairs, batch_size=32, show_progress_bar=False):
            return [1.0 for _ in pairs]

    broad_distractor = (
        0,
        20.0,
        {
            "external_mem0_id": "broad-distractor",
            "memory_type": "benchmark_observation",
            "metadata": {
                "observation_kind": "session",
                "source_ids": [str(index) for index in range(250)],
            },
            "text": "needle query " * 20,
        },
    )
    narrower_evidence = (
        1,
        19.0,
        {
            "external_mem0_id": "narrower-evidence",
            "memory_type": "benchmark_observation",
            "metadata": {
                "observation_kind": "session",
                "source_ids": ["source-1"],
            },
            "text": "needle evidence",
        },
    )
    config = CrossEncoderRerankConfig(
        enabled=True,
        candidate_window=2,
        score_weight=10.0,
        source_count_penalty_weight=0.2,
        adjacent_penalty_weight=5.0,
        normalization_question_types=("instruction_following",),
    )

    preference_ranked = rerank_benchmark_candidates_with_cross_encoder(
        "needle query",
        [broad_distractor, narrower_evidence],
        config,
        FakeCrossEncoder(),
        question_category="preference_following",
    )
    instruction_ranked = rerank_benchmark_candidates_with_cross_encoder(
        "needle query",
        [broad_distractor, narrower_evidence],
        config,
        FakeCrossEncoder(),
        question_category="instruction_following",
    )

    assert preference_ranked[0][2]["external_mem0_id"] == "broad-distractor"
    assert instruction_ranked[0][2]["external_mem0_id"] == "narrower-evidence"


def test_cross_encoder_rerank_uses_query_focused_text_for_long_candidates():
    class CapturingCrossEncoder:
        def __init__(self):
            self.pairs = []

        def predict(self, pairs, batch_size=32, show_progress_bar=False):
            self.pairs.extend(pairs)
            return [1.0 for _ in pairs]

    long_prefix = "noise " * 160
    row = {
        "external_mem0_id": "late-query-match",
        "text": long_prefix + "invoice approval workflow moved to the project vault",
        "context_text": "",
    }
    ranked = [(0, 10.0, row)]
    config = CrossEncoderRerankConfig(
        enabled=True,
        locked_head_size=0,
        candidate_window=1,
        max_chars=120,
        query_focused_text=True,
    )
    model = CapturingCrossEncoder()

    rerank_benchmark_candidates_with_cross_encoder(
        "invoice approval workflow",
        ranked,
        config,
        model,
    )

    assert model.pairs
    assert any("invoice approval workflow" in pair[1] for pair in model.pairs)
    assert all(len(pair[1]) <= config.max_chars for pair in model.pairs)


def test_cross_encoder_rerank_defaults_to_prefix_text_for_long_candidates():
    class CapturingCrossEncoder:
        def __init__(self):
            self.pairs = []

        def predict(self, pairs, batch_size=32, show_progress_bar=False):
            self.pairs.extend(pairs)
            return [1.0 for _ in pairs]

    long_prefix = "noise " * 160
    row = {
        "external_mem0_id": "late-query-match",
        "text": long_prefix + "invoice approval workflow moved to the project vault",
        "context_text": "",
    }
    config = CrossEncoderRerankConfig(
        enabled=True,
        locked_head_size=0,
        candidate_window=1,
        max_chars=120,
    )
    model = CapturingCrossEncoder()

    rerank_benchmark_candidates_with_cross_encoder(
        "invoice approval workflow",
        [(0, 10.0, row)],
        config,
        model,
    )

    assert model.pairs
    assert model.pairs[0][1].startswith("noise ")
    assert "invoice approval workflow" not in model.pairs[0][1]
    assert len(model.pairs[0][1]) <= config.max_chars


def test_cross_encoder_rerank_can_score_multiple_text_windows_per_candidate():
    class WindowScoringCrossEncoder:
        def __init__(self):
            self.pairs = []

        def predict(self, pairs, batch_size=32, show_progress_bar=False):
            self.pairs.extend(pairs)
            return [5.0 if "late local evidence" in pair[1] else 0.0 for pair in pairs]

    evidence = (
        0,
        10.0,
        {
            "external_mem0_id": "late-local-evidence",
            "text": ("noise " * 60) + "late local evidence",
            "context_text": "",
        },
    )
    distractor = (1, 11.0, {"external_mem0_id": "distractor", "text": "ordinary row"})
    config = CrossEncoderRerankConfig(
        enabled=True,
        locked_head_size=0,
        candidate_window=2,
        max_chars=80,
        score_weight=1.0,
        text_windows=4,
        text_window_overlap=10,
    )
    model = WindowScoringCrossEncoder()

    ranked = rerank_benchmark_candidates_with_cross_encoder(
        "instruction query",
        [distractor, evidence],
        config,
        model,
    )

    assert ranked[0][2]["external_mem0_id"] == "late-local-evidence"
    assert len(model.pairs) > len([distractor, evidence])
    assert any("late local evidence" in pair[1] for pair in model.pairs)
    assert all(len(pair[1]) <= config.max_chars for pair in model.pairs)


def test_cross_encoder_rerank_can_score_query_ranked_line_windows():
    class LineScoringCrossEncoder:
        def __init__(self):
            self.pairs = []

        def predict(self, pairs, batch_size=32, show_progress_bar=False):
            self.pairs.extend(pairs)
            return [5.0 if "invoice approval workflow" in pair[1] else 0.0 for pair in pairs]

    evidence = (
        0,
        10.0,
        {
            "external_mem0_id": "line-local-evidence",
            "text": "\n".join(
                [
                    *(f"user: generic opening {index}" for index in range(10)),
                    "user: invoice approval workflow moved to the project vault",
                    "assistant: unrelated closing",
                ]
            ),
            "context_text": "",
        },
    )
    distractor = (1, 11.0, {"external_mem0_id": "distractor", "text": "ordinary row"})
    config = CrossEncoderRerankConfig(
        enabled=True,
        locked_head_size=0,
        candidate_window=2,
        max_chars=90,
        score_weight=1.0,
        line_windows=2,
    )
    model = LineScoringCrossEncoder()

    ranked = rerank_benchmark_candidates_with_cross_encoder(
        "invoice approval workflow",
        [distractor, evidence],
        config,
        model,
    )

    assert ranked[0][2]["external_mem0_id"] == "line-local-evidence"
    assert any("invoice approval workflow" in pair[1] for pair in model.pairs)
    assert "invoice approval workflow" not in model.pairs[0][1]
    assert all(len(pair[1]) <= config.max_chars for pair in model.pairs)


def test_fuse_rankings_rrf_combines_base_and_semantic_order():
    base = [
        (0, 10.0, {"external_mem0_id": "base-first"}),
        (1, 9.0, {"external_mem0_id": "shared"}),
        (2, 8.0, {"external_mem0_id": "base-third"}),
    ]
    semantic = [
        (1, 11.0, {"external_mem0_id": "shared"}),
        (3, 7.0, {"external_mem0_id": "semantic-only"}),
        (0, 10.0, {"external_mem0_id": "base-first"}),
    ]

    fused = fuse_rankings_rrf([base, semantic], k=10)

    assert fused[0][2]["external_mem0_id"] == "shared"


def test_semantic_rerank_reuses_candidate_embedding_cache():
    class CountingModel:
        def __init__(self):
            self.encoded_texts = []

        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False, batch_size=128):
            self.encoded_texts.extend(texts)
            vectors = []
            for text in texts:
                value = str(text)
                vectors.append([1.0, 0.0] if "needle" in value else [0.0, 1.0])
            return vectors

    ranked = [
        (index, 100.0 - index, {"external_mem0_id": f"head-{index}", "text": "head row"})
        for index in range(40)
    ]
    ranked.extend(
        [
            (40, 50.0, {"external_mem0_id": "tail-distractor", "text": "ordinary row"}),
            (41, 49.0, {"external_mem0_id": "tail-evidence", "text": "needle row"}),
        ]
    )
    config = SemanticRerankConfig(enabled=True, locked_head_size=40, candidate_window=200, score_weight=5.0)
    cache = {}
    model = CountingModel()

    rerank_benchmark_candidates_semantically("needle query", ranked, config, model, cache)
    rerank_benchmark_candidates_semantically("needle query", ranked, config, model, cache)

    assert model.encoded_texts.count("ordinary row") == 1
    assert model.encoded_texts.count("needle row") == 1


def test_benchmark_adapter_reuses_semantic_model_loader_across_instances():
    clear_benchmark_model_cache_for_tests()
    loaded = []

    def load_model(model_name):
        model = object()
        loaded.append((model_name, model))
        return model

    config = SemanticRerankConfig(enabled=True, model_name='semantic-test-model', model_loader=load_model)
    first = KontextBenchmarkAdapter(conn=object(), dataset='test', run_id='cache-a', semantic_rerank_config=config)
    second = KontextBenchmarkAdapter(conn=object(), dataset='test', run_id='cache-b', semantic_rerank_config=config)

    assert first._load_semantic_model() is second._load_semantic_model()
    assert [name for name, _ in loaded] == ['semantic-test-model']


def test_benchmark_adapter_reuses_cross_encoder_loader_across_instances():
    clear_benchmark_model_cache_for_tests()
    loaded = []

    def load_model(model_name):
        model = object()
        loaded.append((model_name, model))
        return model

    config = CrossEncoderRerankConfig(enabled=True, model_name='cross-test-model', model_loader=load_model)
    first = KontextBenchmarkAdapter(conn=object(), dataset='test', run_id='cache-a', cross_encoder_rerank_config=config)
    second = KontextBenchmarkAdapter(conn=object(), dataset='test', run_id='cache-b', cross_encoder_rerank_config=config)

    assert first._load_cross_encoder_model() is second._load_cross_encoder_model()
    assert [name for name, _ in loaded] == ['cross-test-model']


def test_benchmark_model_cache_is_bounded():
    clear_benchmark_model_cache_for_tests()

    for index in range(_BENCHMARK_MODEL_CACHE_MAX_SIZE + 2):
        config = SemanticRerankConfig(
            enabled=True,
            model_name=f'semantic-test-model-{index}',
            model_loader=lambda model_name: object(),
        )
        adapter = KontextBenchmarkAdapter(conn=object(), dataset='test', run_id=f'cache-{index}', semantic_rerank_config=config)
        adapter._load_semantic_model()

    assert len(_BENCHMARK_MODEL_CACHE) == _BENCHMARK_MODEL_CACHE_MAX_SIZE
    clear_benchmark_model_cache_for_tests()


def test_load_locomo_tiny_fixture_returns_conversations_and_questions():
    fixture = load_locomo_tiny_fixture(FIXTURE)

    assert fixture["dataset"] == "locomo_tiny"
    assert len(fixture["conversations"]) == 2
    assert fixture["conversations"][0]["conversation_id"] == "tiny-conv-1"
    assert fixture["questions"][0]["question"] == "Where is Alice planning to travel in June?"
    assert fixture["questions"][0]["expected_terms"] == ["berlin", "june"]


def test_load_locomo_real_fixture_normalizes_sessions_and_questions():
    fixture = load_locomo_real_fixture(
        REAL_FIXTURE,
        conversation_indices=[0],
        max_questions=1,
    )

    assert fixture["dataset"] == "locomo10"
    assert len(fixture["conversations"]) == 1
    assert fixture["conversations"][0]["conversation_id"] == "sample-0"
    assert len(fixture["conversations"][0]["sessions"]) == 2
    assert fixture["conversations"][0]["sessions"][0]["session_id"] == "session_1"
    assert fixture["conversations"][0]["sessions"][0]["date"] == "2023-05-07"
    assert fixture["conversations"][0]["sessions"][0]["source_ids"] == ["D1:1", "D1:2"]
    assert fixture["conversations"][0]["sessions"][0]["messages"][0]["role"] == "user"
    assert fixture["conversations"][0]["sessions"][0]["messages"][0]["source_id"] == "D1:1"
    assert fixture["questions"][0]["question_id"] == "sample-0-q-1"
    assert fixture["questions"][0]["category"] == "temporal"
    assert fixture["questions"][0]["evidence"] == ["D1:1"]


def _adapter(run_id: str) -> KontextBenchmarkAdapter:
    conn = psycopg.connect(os.environ["KONTEXT_V2_DATABASE_URL"])
    apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM memories
            WHERE metadata->>'source' = 'benchmark'
              AND metadata->>'benchmark_run_id' = %s
            """,
            (run_id,),
        )
    conn.commit()
    return KontextBenchmarkAdapter(conn=conn, dataset="locomo_tiny", run_id=run_id)


def test_adapter_add_messages_writes_isolated_benchmark_memories():
    adapter = _adapter("unit-adapter-add")
    try:
        added = adapter.add(
            messages=[BenchmarkMessage("user", "Alice: Berlin trip in June.")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-06-01",
            source_ids=["D1:1"],
        )

        memory = adapter.repo.fetch_by_external_id(added.results[0]["id"])
        assert memory is not None
        assert memory.metadata["source"] == "benchmark"
        assert memory.metadata["benchmark_run_id"] == "unit-adapter-add"
        assert memory.metadata["profile"] == "benchmark"
        assert memory.metadata["is_live_memory"] is False
        assert memory.metadata["source_ids"] == ["D1:1"]
        assert memory.metadata["timestamp"] == "2024-06-01"
    finally:
        adapter.close()


def test_adapter_search_returns_mem0_like_results_without_raw_debug():
    adapter = _adapter("unit-adapter-search")
    try:
        adapter.add(
            messages=[BenchmarkMessage("user", "Alice: Berlin trip in June.")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-06-01",
            source_ids=["D1:1"],
        )

        results = adapter.search(
            "Where is Alice traveling in June?",
            "benchmark-locomo-tiny-1",
            top_k=5,
        )

        assert results
        assert results[0]["id"].startswith("benchmark:locomo_tiny:unit-adapter-search:")
        assert "memory" in results[0]
        assert isinstance(results[0]["score"], float)
        assert results[0]["metadata"]["source_ids"] == ["D1:1"]
        assert results[0]["metadata"]["timestamp"] == "2024-06-01"
        assert "query_debug" not in results[0]
    finally:
        adapter.close()

def test_adapter_search_filters_user_before_top_k_cap():
    adapter = _adapter("unit-adapter-user-filter")
    try:
        adapter.add(
            messages=[BenchmarkMessage("user", "Target: orchard lantern detail.")],
            user_id="target-user",
            conversation_id="target-conv",
            session_id="session_1",
            source_ids=["D1:1"],
        )
        for index in range(25):
            adapter.add(
                messages=[BenchmarkMessage("user", "Noise: orchard lantern detail.")],
                user_id=f"noise-user-{index}",
                conversation_id=f"noise-conv-{index}",
                session_id="session_1",
                source_ids=[f"N{index}"],
            )

        results = adapter.search("orchard lantern detail", "target-user", top_k=5)

        assert [row["metadata"]["source_ids"] for row in results] == [["D1:1"]]
    finally:
        adapter.close()

def test_adapter_search_uses_session_context_for_multi_hop_questions():
    adapter = _adapter("unit-adapter-session-context")
    try:
        adapter.add(
            messages=[BenchmarkMessage("user", "Caroline went to the LGBTQ support group.")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-05-07",
            source_ids=["D1:1"],
        )
        adapter.add(
            messages=[BenchmarkMessage("assistant", "It was on May 7, 2023.")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-05-07",
            source_ids=["D1:2"],
        )

        results = adapter.search(
            "When did Caroline go to the LGBTQ support group?",
            "benchmark-locomo-tiny-1",
            top_k=5,
        )

        assert results
        assert results[0]["metadata"]["source_ids"] == ["D1:2"]
    finally:
        adapter.close()

def test_adapter_search_prefers_session_observation_when_scores_tie():
    adapter = _adapter("unit-adapter-session-kind-boost")
    try:
        messages = [BenchmarkMessage("user", "Riley archive permit code blue.")]
        adapter.add(
            messages=messages,
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-05-07",
            source_ids=["D1:1", "D1:2"],
            observation_kind="turn",
        )
        adapter.add(
            messages=messages,
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_1",
            timestamp="2024-05-07",
            source_ids=["D1:1", "D1:2"],
            observation_kind="session",
        )

        results = adapter.search(
            "Riley archive permit code blue",
            "benchmark-locomo-tiny-1",
            top_k=2,
        )

        assert results
        assert results[0]["metadata"]["observation_kind"] == "session"
        assert results[0]["metadata"]["source_ids"] == ["D1:1", "D1:2"]
    finally:
        adapter.close()


def test_adapter_search_prioritizes_benchmark_session_context_over_turn_distractors():
    adapter = _adapter("unit-adapter-session-context-boost")
    try:
        for index in range(12):
            adapter.add(
                messages=[BenchmarkMessage("user", f"Riley archive permit blue distractor {index}")],
                user_id="benchmark-locomo-tiny-1",
                conversation_id="tiny-conv-1",
                session_id=f"session_distractor_{index}",
                timestamp="2024-05-07",
                source_ids=[f"D{index}:1"],
                observation_kind="turn",
            )
        adapter.add(
            messages=[BenchmarkMessage("user", "Riley archive permit")],
            user_id="benchmark-locomo-tiny-1",
            conversation_id="tiny-conv-1",
            session_id="session_target",
            timestamp="2024-05-07",
            source_ids=["TARGET"],
            observation_kind="session",
        )

        results = adapter.search("Riley archive permit blue", "benchmark-locomo-tiny-1", top_k=5)

        assert results
        assert results[0]["metadata"]["observation_kind"] == "session"
        assert results[0]["metadata"]["source_ids"] == ["TARGET"]
    finally:
        adapter.close()


def test_adapter_search_reuses_candidate_rows_for_same_user():
    adapter = KontextBenchmarkAdapter.__new__(KontextBenchmarkAdapter)
    adapter.dataset = "unit"
    adapter.run_id = "cache"
    adapter._candidate_cache = {}
    load_calls = []
    rows = [
        {
            "external_mem0_id": "row-1",
            "title": "row",
            "text": "user: The amber notebook holds the deploy checklist.",
            "metadata": {"source_ids": ["A1"]},
            "memory_type": "benchmark_observation",
            "current_status": "benchmark",
            "memory_tier": "cold",
            "signal_strength": 5,
            "rank": 0.0,
        }
    ]

    def load(user_id: str):
        load_calls.append(user_id)
        return [dict(row) for row in rows]

    adapter._load_candidate_rows = load

    first = adapter.search("Where is the deploy checklist?", "benchmark-user", top_k=1)
    second = adapter.search("Where is the checklist?", "benchmark-user", top_k=1)

    assert [item["id"] for item in first] == ["row-1"]
    assert [item["id"] for item in second] == ["row-1"]
    assert load_calls == ["benchmark-user"]


def test_adapter_default_candidate_limit_covers_large_beam_sessions():
    class Cursor:
        def __init__(self):
            self.params = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _sql, params):
            self.params = params
            return self

        def fetchall(self):
            return []

    class Conn:
        def __init__(self):
            self.cursor_obj = Cursor()

        def cursor(self, row_factory=None):
            return self.cursor_obj

    conn = Conn()
    adapter = KontextBenchmarkAdapter(conn, "beam_10M", "candidate-limit")

    adapter._load_candidate_rows("benchmark-user")

    assert conn.cursor_obj.params[-1] >= 20000


def test_adapter_candidate_limit_can_be_overridden_for_diagnostics():
    class Cursor:
        def __init__(self):
            self.params = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _sql, params):
            self.params = params
            return self

        def fetchall(self):
            return []

    class Conn:
        def __init__(self):
            self.cursor_obj = Cursor()

        def cursor(self, row_factory=None):
            return self.cursor_obj

    conn = Conn()
    adapter = KontextBenchmarkAdapter(conn, "beam_10M", "candidate-limit", candidate_limit=25000)

    adapter._load_candidate_rows("benchmark-user")

    assert conn.cursor_obj.params[-1] == 25000


def test_adapter_prepares_score_cache_for_loaded_candidates():
    class Cursor:
        def __init__(self):
            self.params = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _sql, params):
            self.params = params
            return self

        def fetchall(self):
            return [
                {
                    "external_mem0_id": "row-1",
                    "title": "deploy row",
                    "text": "user: The deploy checklist is in the vault.",
                    "metadata": {
                        "conversation_id": "conv-1",
                        "session_id": "session-1",
                        "source_ids": ["1"],
                    },
                    "memory_type": "benchmark_observation",
                    "current_status": "benchmark",
                    "memory_tier": "cold",
                    "signal_strength": 5,
                    "updated_at": 1,
                    "rank": 0.0,
                }
            ]

    class Conn:
        def __init__(self):
            self.cursor_obj = Cursor()

        def cursor(self, row_factory=None):
            return self.cursor_obj

    adapter = KontextBenchmarkAdapter(Conn(), "beam_10M", "score-cache")

    rows = adapter._load_candidate_rows("benchmark-user")

    assert "_score_text_cache" in rows[0]


def test_compact_context_keeps_nearby_turns_without_copying_whole_large_session():
    texts = [f"turn {index}" for index in range(30)]

    context = _compact_context_for_index(texts, 15, neighbor_window=2, max_chars=1000)

    assert "turn 13" in context
    assert "turn 14" in context
    assert "turn 16" in context
    assert "turn 17" in context
    assert "turn 0" not in context
    assert "turn 29" not in context
