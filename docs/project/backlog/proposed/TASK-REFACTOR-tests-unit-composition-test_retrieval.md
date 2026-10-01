---
id: REFACTOR-tests-unit-composition-test_retrieval
title: Refactor and Decompose Legacy File test_retrieval.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-composition-test_retrieval: Refactor Legacy File test_retrieval.py

## Summary
The grandfathered debt file `tests/unit/composition/test_retrieval.py` contains 851 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_retrieval_the.py, test_retrieval_lexical.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/composition/test_retrieval/` with submodules:
- `test_retrieval_the.py`: test_two_tenants_holding_the_same_entity_id_never_cross, test_two_tenants_holding_the_same_entity_id_never_cross_in_semantic_mode, test_a_vector_match_whose_entity_the_graph_lacks_is_skipped, test_the_lexical_channel_scores_a_candidate_after_a_duplicate_one, test_entity_types_filters_the_lexical_channel_before_k_is_applied, test_overfetch_is_configurable_and_one_restores_the_narrow_behaviour, test_the_semantic_channel_embeds_the_query_as_a_query, test_the_query_reaches_the_provider_unprefixed_by_the_caller, test_a_lexical_only_retriever_defaults_to_the_lexical_mode, _entity, _retriever, _store_vector, test_an_exact_name_is_retrieved, test_a_skipped_dangling_match_is_not_backfilled, test_empty_entity_types_matches_nothing, test_a_result_reports_both_component_scores_when_both_channels_ranked, test_entities_are_compared_by_equality_not_identity, test_mutating_a_result_cannot_change_what_a_later_retrieve_returns, test_a_blank_query_raises, test_k_zero_returns_nothing_and_a_negative_k_raises, test_more_results_than_k_are_truncated, test_a_provider_and_store_of_different_dimensions_are_refused, _RecordingVectorStore, test_each_channel_is_asked_for_more_candidates_than_k, test_an_overfetch_below_one_is_refused, test_rank_fusion_promotes_a_consistent_runner_up, _asymmetric, test_a_full_retriever_still_defaults_to_hybrid
- `test_retrieval_lexical.py`: test_equal_lexical_scores_are_broken_by_ascending_id_not_by_store_order, test_a_semantic_only_mode_leaves_lexical_none, test_a_lexical_only_mode_makes_no_embedding_call, test_a_lexical_only_retriever_needs_no_embedding_provider, test_a_lexical_only_retriever_refuses_a_mode_needing_a_vector, test_a_lexical_only_retriever_refuses_an_overfetch_below_one

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/composition/test_retrieval.py (851 lines):
  Submodule 'test_retrieval_the.py' (~565 lines):
    - [function] test_two_tenants_holding_the_same_entity_id_never_cross (lines 122-142)
    - [function] test_two_tenants_holding_the_same_entity_id_never_cross_in_semantic_mode (lines 145-177)
    - [function] test_a_vector_match_whose_entity_the_graph_lacks_is_skipped (lines 185-202)
    - [function] test_the_lexical_channel_scores_a_candidate_after_a_duplicate_one (lines 250-299)
    - [function] test_entity_types_filters_the_lexical_channel_before_k_is_applied (lines 332-350)
    - [function] test_overfetch_is_configurable_and_one_restores_the_narrow_behaviour (lines 621-635)
    - [function] test_the_semantic_channel_embeds_the_query_as_a_query (lines 690-722)
    - [function] test_the_query_reaches_the_provider_unprefixed_by_the_caller (lines 725-755)
    - [function] test_a_lexical_only_retriever_defaults_to_the_lexical_mode (lines 783-802)
    - [function] _entity (lines 50-79)
    - [function] _retriever (lines 82-91)
    - [function] _store_vector (lines 94-102)
    - [function] test_an_exact_name_is_retrieved (lines 110-119)
    - [function] test_a_skipped_dangling_match_is_not_backfilled (lines 205-242)
    - [function] test_empty_entity_types_matches_nothing (lines 353-364)
    - [function] test_a_result_reports_both_component_scores_when_both_channels_ranked (lines 372-392)
    - [function] test_entities_are_compared_by_equality_not_identity (lines 456-498)
    - [function] test_mutating_a_result_cannot_change_what_a_later_retrieve_returns (lines 501-513)
    - [function] test_a_blank_query_raises (lines 517-520)
    - [function] test_k_zero_returns_nothing_and_a_negative_k_raises (lines 523-540)
    - [function] test_more_results_than_k_are_truncated (lines 543-551)
    - [function] test_a_provider_and_store_of_different_dimensions_are_refused (lines 554-561)
    - [class] _RecordingVectorStore (lines 569-589)
    - [function] test_each_channel_is_asked_for_more_candidates_than_k (lines 592-618)
    - [function] test_an_overfetch_below_one_is_refused (lines 639-648)
    - [function] test_rank_fusion_promotes_a_consistent_runner_up (lines 651-667)
    - [function] _asymmetric (lines 682-687)
    - [function] test_a_full_retriever_still_defaults_to_hybrid (lines 826-844)
  Submodule 'test_retrieval_lexical.py' (~120 lines):
    - [function] test_equal_lexical_scores_are_broken_by_ascending_id_not_by_store_order (lines 302-329)
    - [function] test_a_semantic_only_mode_leaves_lexical_none (lines 395-411)
    - [function] test_a_lexical_only_mode_makes_no_embedding_call (lines 414-448)
    - [function] test_a_lexical_only_retriever_needs_no_embedding_provider (lines 763-780)
    - [function] test_a_lexical_only_retriever_refuses_a_mode_needing_a_vector (lines 806-823)
    - [function] test_a_lexical_only_retriever_refuses_an_overfetch_below_one (lines 848-851)
  Suggested barrel exports:
    from .test_retrieval_the import test_two_tenants_holding_the_same_entity_id_never_cross, test_two_tenants_holding_the_same_entity_id_never_cross_in_semantic_mode, test_a_vector_match_whose_entity_the_graph_lacks_is_skipped, test_the_lexical_channel_scores_a_candidate_after_a_duplicate_one, test_entity_types_filters_the_lexical_channel_before_k_is_applied, test_overfetch_is_configurable_and_one_restores_the_narrow_behaviour, test_the_semantic_channel_embeds_the_query_as_a_query, test_the_query_reaches_the_provider_unprefixed_by_the_caller, test_a_lexical_only_retriever_defaults_to_the_lexical_mode, _entity, _retriever, _store_vector, test_an_exact_name_is_retrieved, test_a_skipped_dangling_match_is_not_backfilled, test_empty_entity_types_matches_nothing, test_a_result_reports_both_component_scores_when_both_channels_ranked, test_entities_are_compared_by_equality_not_identity, test_mutating_a_result_cannot_change_what_a_later_retrieve_returns, test_a_blank_query_raises, test_k_zero_returns_nothing_and_a_negative_k_raises, test_more_results_than_k_are_truncated, test_a_provider_and_store_of_different_dimensions_are_refused, _RecordingVectorStore, test_each_channel_is_asked_for_more_candidates_than_k, test_an_overfetch_below_one_is_refused, test_rank_fusion_promotes_a_consistent_runner_up, _asymmetric, test_a_full_retriever_still_defaults_to_hybrid
    from .test_retrieval_lexical import test_equal_lexical_scores_are_broken_by_ascending_id_not_by_store_order, test_a_semantic_only_mode_leaves_lexical_none, test_a_lexical_only_mode_makes_no_embedding_call, test_a_lexical_only_retriever_needs_no_embedding_provider, test_a_lexical_only_retriever_refuses_a_mode_needing_a_vector, test_a_lexical_only_retriever_refuses_an_overfetch_below_one

    __all__ = ["test_two_tenants_holding_the_same_entity_id_never_cross", "test_two_tenants_holding_the_same_entity_id_never_cross_in_semantic_mode", "test_a_vector_match_whose_entity_the_graph_lacks_is_skipped", "test_the_lexical_channel_scores_a_candidate_after_a_duplicate_one", "test_entity_types_filters_the_lexical_channel_before_k_is_applied", "test_overfetch_is_configurable_and_one_restores_the_narrow_behaviour", "test_the_semantic_channel_embeds_the_query_as_a_query", "test_the_query_reaches_the_provider_unprefixed_by_the_caller", "test_a_lexical_only_retriever_defaults_to_the_lexical_mode", "_entity", "_retriever", "_store_vector", "test_an_exact_name_is_retrieved", "test_a_skipped_dangling_match_is_not_backfilled", "test_empty_entity_types_matches_nothing", "test_a_result_reports_both_component_scores_when_both_channels_ranked", "test_entities_are_compared_by_equality_not_identity", "test_mutating_a_result_cannot_change_what_a_later_retrieve_returns", "test_a_blank_query_raises", "test_k_zero_returns_nothing_and_a_negative_k_raises", "test_more_results_than_k_are_truncated", "test_a_provider_and_store_of_different_dimensions_are_refused", "_RecordingVectorStore", "test_each_channel_is_asked_for_more_candidates_than_k", "test_an_overfetch_below_one_is_refused", "test_rank_fusion_promotes_a_consistent_runner_up", "_asymmetric", "test_a_full_retriever_still_defaults_to_hybrid", "test_equal_lexical_scores_are_broken_by_ascending_id_not_by_store_order", "test_a_semantic_only_mode_leaves_lexical_none", "test_a_lexical_only_mode_makes_no_embedding_call", "test_a_lexical_only_retriever_needs_no_embedding_provider", "test_a_lexical_only_retriever_refuses_a_mode_needing_a_vector", "test_a_lexical_only_retriever_refuses_an_overfetch_below_one"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
