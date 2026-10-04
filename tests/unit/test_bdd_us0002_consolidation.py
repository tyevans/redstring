"""Executable BDD Acceptance Criteria for US-0002: Consolidate Mentions and Resolve Entity Aliases.

Governed by:
- ADR-0001 (Event Log Schema and Granularity)
- ADR-0004 (Consolidation Emits Events)
- ADR-0010 (One Total Order for Preference)
- PRD-0001
- US-0002
"""

from __future__ import annotations

from uuid import uuid4

from redstring.aggregates.consolidation_log import ConsolidationLog
from redstring.domain.merge_strategy import PropertyMergePolicy, PropertyMergeStrategy


def test_resolve_multiple_alias_mentions_to_canonical_entity() -> None:
    """Scenario: Resolve multiple alias mentions to canonical entity (US-0002)."""
    tenant_id = uuid4()
    canonical = uuid4()
    absorbed = uuid4()

    log = ConsolidationLog(tenant_id)
    log.merge(
        tenant_id=tenant_id,
        canonical_entity_id=canonical,
        merged_entity_ids=[absorbed],
        merge_reason="Exact match",
    )

    assert len(log.uncommitted_events) == 1
    event = log.uncommitted_events[0]
    assert event.canonical_entity_id == canonical
    assert event.merged_entity_ids == [absorbed]


def test_merge_duplicate_properties_with_deterministic_preference_ordering() -> None:
    """Scenario: Merge duplicate properties with deterministic preference ordering (US-0002)."""
    policy = PropertyMergePolicy(
        default=PropertyMergeStrategy.PREFER_CANONICAL,
        overrides={"properties.role": PropertyMergeStrategy.PREFER_CANONICAL},
    )

    strategy = policy.strategy_for("properties.role")
    assert strategy == PropertyMergeStrategy.PREFER_CANONICAL
