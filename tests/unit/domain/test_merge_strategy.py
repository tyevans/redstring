"""Four strategies resolve; one refuses, and refuses loudly."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.merge_strategy import (
    IMPLEMENTED,
    PropertyClaim,
    PropertyMergePolicy,
    PropertyMergeStrategy,
    _order_key,
    claims_for,
    resolve,
)
from redstring.domain.provenance import ExtractionMethod, Provenance

EARLY = datetime(2026, 1, 1, tzinfo=UTC)
LATE = datetime(2026, 6, 1, tzinfo=UTC)


#: Two ids that bracket every other, so a test can say which claim the
#: *third* component of the order would pick. `uuid4()` cannot: it makes the
#: origin tie-break a coin flip, and a test relying on it passes about half
#: the time -- CLAUDE.md's "ids drawn from `uuid4()`" row, in the one place
#: here where the ordering of two ids decides an assertion.
LOWEST_ID = EntityId(UUID(int=0))
HIGHEST_ID = EntityId(UUID(int=(1 << 128) - 1))


def claim(
    value: object,
    *,
    at: datetime = LATE,
    confidence: float = 0.5,
    origin: EntityId | None = None,
) -> PropertyClaim:
    return PropertyClaim(
        value=value,
        provenance=Provenance(
            observed_at=at,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=confidence,
        ),
        origin=origin if origin is not None else EntityId(uuid4()),
    )


def entity_with(*, at: datetime, **overrides: Any) -> Entity:
    return Entity(
        id=EntityId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name="Ada",
        normalized_name="ada",
        entity_type="person",
        provenance=Provenance(
            observed_at=at,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.5,
        ),
        **overrides,
    )


class TestPreferCanonical:
    def test_the_canonical_value_wins(self):
        assert (
            resolve(
                PropertyMergeStrategy.PREFER_CANONICAL,
                [claim("Ada"), claim("A."), claim("Augusta")],
            )
            == "Ada"
        )

    def test_a_canonical_none_is_a_value_not_an_absence(self):
        """The case a "prefer canonical unless it's empty" implementation gets
        wrong. A description deliberately cleared on the canonical entity must
        not be refilled from an entity being absorbed."""
        assert (
            resolve(PropertyMergeStrategy.PREFER_CANONICAL, [claim(None), claim("a bio")]) is None
        )

    def test_it_does_not_consult_others_at_all(self):
        assert resolve(PropertyMergeStrategy.PREFER_CANONICAL, [claim(1)]) == 1


class TestUnion:
    def test_it_accumulates_distinct_values_canonical_first(self):
        assert resolve(
            PropertyMergeStrategy.UNION, [claim("Ada"), claim("A."), claim("Augusta")]
        ) == [
            "Ada",
            "A.",
            "Augusta",
        ]

    def test_duplicates_collapse_and_first_seen_order_survives(self):
        assert resolve(
            PropertyMergeStrategy.UNION,
            [claim("Ada"), claim("Augusta"), claim("Ada"), claim("A.")],
        ) == ["Ada", "Augusta", "A."]

    def test_it_flattens_one_level(self):
        """Applying `UNION` to an already-unioned value must not nest. A
        projection replays, so the second application has to be a no-op."""
        once = resolve(PropertyMergeStrategy.UNION, [claim("Ada"), claim("A.")])
        twice = resolve(PropertyMergeStrategy.UNION, [claim(once), claim("A.")])

        assert twice == ["Ada", "A."]

    def test_unhashable_values_survive(self):
        """A `set` would raise here, on exactly the nested values `UNION`
        exists to accumulate."""
        assert resolve(
            PropertyMergeStrategy.UNION,
            [
                claim({"wikidata": "Q7259"}),
                claim({"viaf": "12345"}),
                claim({"wikidata": "Q7259"}),
            ],
        ) == [{"wikidata": "Q7259"}, {"viaf": "12345"}]

    def test_values_that_compare_equal_across_types_collapse_once(self):
        """`1 == True` in Python, and `==` is what deduplicates. Pinned so the
        behaviour is a decision on the record rather than a surprise: the
        first-seen value is the one kept."""
        assert resolve(PropertyMergeStrategy.UNION, [claim(1), claim(True)]) == [1]

    @given(
        canonical=st.integers(),
        others=st.lists(st.integers(), max_size=6),
    )
    def test_the_union_holds_every_input_exactly_once(self, canonical, others):
        merged = resolve(
            PropertyMergeStrategy.UNION, [claim(canonical), *(claim(o) for o in others)]
        )

        assert set(merged) == {canonical, *others}
        assert len(merged) == len(set(merged))

    @given(canonical=st.integers(), others=st.lists(st.integers(), max_size=6))
    def test_it_is_idempotent(self, canonical, others):
        """The property a replaying projection needs. Stated over generated
        input because the flattening is what makes it true, and flattening is
        easy to get right for one example and wrong in general."""
        once = resolve(PropertyMergeStrategy.UNION, [claim(canonical), *(claim(o) for o in others)])
        twice = resolve(PropertyMergeStrategy.UNION, [claim(once), *(claim(o) for o in others)])

        assert twice == once


class TestMostRecentlyObserved:
    def test_it_takes_the_later_claim_when_it_is_not_canonical(self) -> None:
        """The canonical claim losing is the case that distinguishes this
        strategy from `PREFER_CANONICAL`. A test where the canonical value
        happens to be the most recent cannot tell the two apart.
        """
        result = resolve(
            PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
            [claim("old", at=EARLY), claim("new", at=LATE)],
        )
        assert result == "new"

    def test_it_takes_the_canonical_claim_when_it_is_later(self) -> None:
        result = resolve(
            PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
            [claim("new", at=LATE), claim("old", at=EARLY)],
        )
        assert result == "new"

    def test_simultaneous_claims_are_broken_by_confidence_not_by_position(self) -> None:
        """Two entities extracted in one batch share an instant exactly. Without
        a tie-break the winner is decided by arrival order, in a replayable log
        -- ADR 0010's rule, applied to a narrower order.

        The origins are pinned so the *unsure* claim is the one `str(origin)`
        would pick. Under `uuid4()` this test passed about half the time
        against an order with no confidence component, which is not a test of
        anything: the surer claim has to be the one every other component
        rejects.
        """
        result = resolve(
            PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
            [
                claim("unsure", at=LATE, confidence=0.2, origin=HIGHEST_ID),
                claim("sure", at=LATE, confidence=0.9, origin=LOWEST_ID),
            ],
        )
        assert result == "sure"

    def test_the_surer_claim_loses_to_the_later_one(self) -> None:
        """Recency outranks confidence, which is the strategy's whole content.
        Without this the order could be `(confidence, observed_at, ...)` and
        every other case here would still pass.
        """
        result = resolve(
            PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
            [
                claim("sure but old", at=EARLY, confidence=0.9),
                claim("new", at=LATE, confidence=0.1),
            ],
        )
        assert result == "new"

    def test_claims_agreeing_on_instant_and_confidence_still_have_one_winner(self) -> None:
        """The third component carries no meaning and exists only so that no two
        distinct claims compare equal. Asserted as determinism across a
        reordering, because *which* one wins is arbitrary and must not be
        pinned.
        """
        first = claim("a", at=LATE, confidence=0.5)
        second = claim("b", at=LATE, confidence=0.5)
        forwards = resolve(PropertyMergeStrategy.MOST_RECENTLY_OBSERVED, [first, second])
        backwards = resolve(PropertyMergeStrategy.MOST_RECENTLY_OBSERVED, [second, first])
        assert forwards == backwards


def claim_strategy() -> st.SearchStrategy[PropertyClaim]:
    """Claims drawn from a *small* set of instants and confidences.

    Deliberately not `redstring.testing.strategies`, which draws instants at
    microsecond resolution over four centuries: under that a tie in the first
    two components effectively never occurs and the totality property passes
    without ever reaching the component it is about. Two instants and two
    confidences make collisions the common case.
    """
    return st.builds(
        PropertyClaim,
        value=st.integers(),
        provenance=st.builds(
            Provenance,
            observed_at=st.sampled_from([EARLY, LATE]),
            extraction_method=st.just(ExtractionMethod.PATTERN),
            confidence=st.sampled_from([0.2, 0.5]),
        ),
        origin=st.builds(EntityId, st.uuids()),
    )


@given(claims=st.lists(claim_strategy(), min_size=2, max_size=8))
def test_the_claim_order_is_total(claims: list[PropertyClaim]) -> None:
    """ADR 0010: a `>` mutated to `>=` is equivalent only when the order really
    is total. Assert the totality rather than labelling the survivor.
    """
    by_key: dict[tuple[object, ...], list[PropertyClaim]] = defaultdict(list)
    for c in claims:
        by_key[_order_key(c)].append(c)
    for sharing in by_key.values():
        for other in sharing[1:]:
            assert other == sharing[0]


class TestPreferMerged:
    def test_it_takes_the_first_absorbed_claim(self) -> None:
        result = resolve(
            PropertyMergeStrategy.PREFER_MERGED,
            [claim("canonical", at=LATE), claim("absorbed", at=EARLY), claim("later", at=EARLY)],
        )
        assert result == "absorbed"

    def test_it_falls_back_to_canonical_when_nothing_was_absorbed(self) -> None:
        result = resolve(PropertyMergeStrategy.PREFER_MERGED, [claim("only", at=LATE)])
        assert result == "only"


def test_resolve_refuses_an_empty_claim_list() -> None:
    with pytest.raises(ValueError, match="at least one claim"):
        resolve(PropertyMergeStrategy.PREFER_CANONICAL, [])


class TestTheDeferredStrategies:
    def test_deep_merge_still_raises_and_still_names_the_backlog_entry(self) -> None:
        with pytest.raises(NotImplementedError, match="B28"):
            resolve(PropertyMergeStrategy.DEEP_MERGE, [claim("x", at=LATE)])

    def test_it_raises_rather_than_falling_back(self):
        """The failure mode this prevents is silent, not loud: a fallback
        writes the canonical value while the caller believes it asked for
        something else, and leaves nothing in the result to show it."""
        with pytest.raises(NotImplementedError) as raised:
            resolve(PropertyMergeStrategy.DEEP_MERGE, [claim("Ada"), claim("A.")])

        assert PropertyMergeStrategy.DEEP_MERGE.name in str(raised.value)
        assert "B28" in str(raised.value)

    def test_the_deferred_set_is_exactly_what_is_not_implemented(self):
        """Derived rather than listed, so adding a strategy to the enum and
        forgetting `resolve` fails here instead of at some caller."""
        for strategy in PropertyMergeStrategy:
            if strategy in IMPLEMENTED:
                resolve(strategy, [claim("x")])
            else:
                with pytest.raises(NotImplementedError):
                    resolve(strategy, [claim("x")])

    def test_implemented_names_the_four_the_docstring_claims(self):
        assert {
            PropertyMergeStrategy.PREFER_CANONICAL,
            PropertyMergeStrategy.UNION,
            PropertyMergeStrategy.PREFER_MERGED,
            PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
        } == IMPLEMENTED


class TestClaimsFor:
    def test_it_skips_entities_that_say_nothing_about_the_property(self) -> None:
        """Silence is not a `None` claim. An entity with no opinion must not be
        able to outvote one with an opinion under MOST_RECENTLY_OBSERVED.
        """
        silent = entity_with(properties={}, at=LATE)
        speaking = entity_with(properties={"role": "engineer"}, at=EARLY)
        claims = claims_for("properties.role", silent, [speaking])
        assert [c.value for c in claims] == ["engineer"]

    def test_it_keeps_an_explicit_none_which_is_not_silence(self) -> None:
        speaking = entity_with(properties={"role": None}, at=LATE)
        assert len(claims_for("properties.role", speaking, [])) == 1

    def test_it_returns_nothing_when_nobody_claims_the_property(self) -> None:
        assert claims_for("properties.absent", entity_with(properties={}, at=LATE), []) == []

    def test_it_puts_the_canonical_claim_first_and_keeps_the_listed_order(self) -> None:
        """`resolve` reads position: `claims[0]` is canonical for
        `PREFER_CANONICAL` and `claims[1]` is the first absorbed one for
        `PREFER_MERGED`. A `claims_for` that reordered would be silent.
        """
        canonical = entity_with(properties={"role": "canonical"}, at=EARLY)
        first = entity_with(properties={"role": "first"}, at=LATE)
        second = entity_with(properties={"role": "second"}, at=LATE)
        claims = claims_for("properties.role", canonical, [first, second])
        assert [c.value for c in claims] == ["canonical", "first", "second"]

    def test_each_claim_carries_its_own_entity_s_provenance_and_id(self) -> None:
        """The whole point of a claim. Building them all from the canonical
        entity's provenance would leave every test above passing and
        `MOST_RECENTLY_OBSERVED` answering with the canonical value always.
        """
        canonical = entity_with(properties={"role": "canonical"}, at=EARLY)
        absorbed = entity_with(properties={"role": "absorbed"}, at=LATE)
        claims = claims_for("properties.role", canonical, [absorbed])
        assert [c.origin for c in claims] == [canonical.id, absorbed.id]
        assert [c.provenance.observed_at for c in claims] == [EARLY, LATE]


class TestPolicyLookup:
    """`strategy_for` resolves exact path, then field default, then default."""

    def test_an_exact_path_wins_over_its_fields_default(self):
        policy = PropertyMergePolicy(
            default=PropertyMergeStrategy.PREFER_CANONICAL,
            overrides={
                "properties": PropertyMergeStrategy.PREFER_MERGED,
                "properties.role": PropertyMergeStrategy.UNION,
            },
        )
        assert policy.strategy_for("properties.role") is PropertyMergeStrategy.UNION

    def test_a_fields_default_covers_a_key_with_no_entry(self):
        policy = PropertyMergePolicy(
            default=PropertyMergeStrategy.PREFER_CANONICAL,
            overrides={"properties": PropertyMergeStrategy.PREFER_MERGED},
        )
        assert policy.strategy_for("properties.role") is PropertyMergeStrategy.PREFER_MERGED

    def test_the_policy_default_covers_a_field_with_no_entry(self):
        policy = PropertyMergePolicy(default=PropertyMergeStrategy.MOST_RECENTLY_OBSERVED)
        assert policy.strategy_for("external_ids.wikidata") is (
            PropertyMergeStrategy.MOST_RECENTLY_OBSERVED
        )

    def test_all_three_tiers_are_consulted_for_one_field(self):
        """The three tiers must be distinguishable at once.

        Asserting them in separate tests leaves an implementation that
        consults only two of them passing every one: each test names a policy
        where the tier below happens to give the same answer. Three distinct
        strategies over three paths of the same field is the input where a
        dropped tier changes an answer.
        """
        policy = PropertyMergePolicy(
            default=PropertyMergeStrategy.PREFER_CANONICAL,
            overrides={
                "properties": PropertyMergeStrategy.PREFER_MERGED,
                "properties.role": PropertyMergeStrategy.UNION,
            },
        )
        assert policy.strategy_for("properties.role") is PropertyMergeStrategy.UNION
        assert policy.strategy_for("properties.era") is PropertyMergeStrategy.PREFER_MERGED
        assert policy.strategy_for("description") is PropertyMergeStrategy.PREFER_CANONICAL

    def test_the_default_policy_prefers_the_canonical_entity(self):
        assert PropertyMergePolicy().strategy_for("properties.role") is (
            PropertyMergeStrategy.PREFER_CANONICAL
        )


class TestPolicyRefusals:
    def test_an_override_naming_no_real_field_is_refused(self):
        """A typo would otherwise be silently inert -- every merge applies the
        default and nothing says the entry did nothing."""
        with pytest.raises(ValidationError, match="properities"):
            PropertyMergePolicy(overrides={"properities.role": PropertyMergeStrategy.UNION})

    def test_a_bare_unknown_field_is_refused_too(self):
        with pytest.raises(ValidationError, match="name"):
            PropertyMergePolicy(overrides={"name": PropertyMergeStrategy.PREFER_MERGED})

    def test_union_is_refused_on_external_ids(self):
        """`external_ids` is `dict[str, str]`; UNION returns a list, so the
        upsert would raise inside a fold with the event already in the log."""
        with pytest.raises(ValidationError, match="UNION"):
            PropertyMergePolicy(overrides={"external_ids": PropertyMergeStrategy.UNION})

    def test_union_is_refused_on_an_external_ids_key(self):
        with pytest.raises(ValidationError, match="UNION"):
            PropertyMergePolicy(overrides={"external_ids.wikidata": PropertyMergeStrategy.UNION})

    def test_union_is_refused_on_description(self):
        with pytest.raises(ValidationError, match="UNION"):
            PropertyMergePolicy(overrides={"description": PropertyMergeStrategy.UNION})

    def test_union_is_refused_as_the_policy_default(self):
        """A UNION default would reach `description` and `external_ids`, which
        is the case the per-path refusals exist to prevent."""
        with pytest.raises(ValidationError, match="UNION"):
            PropertyMergePolicy(default=PropertyMergeStrategy.UNION)

    def test_union_is_allowed_on_properties_and_its_keys(self):
        """The permitting case, so the refusals above are not passing because
        the validator rejects everything."""
        policy = PropertyMergePolicy(
            overrides={
                "properties": PropertyMergeStrategy.UNION,
                "properties.aka": PropertyMergeStrategy.UNION,
            }
        )
        assert policy.strategy_for("properties.aka") is PropertyMergeStrategy.UNION

    def test_the_policy_is_frozen(self):
        policy = PropertyMergePolicy()
        with pytest.raises(ValidationError):
            policy.default = PropertyMergeStrategy.UNION


class TestClaimsForPaths:
    def test_a_description_claim_is_read_from_the_field(self):
        canonical = entity_with(description="the first analyst", at=EARLY)
        absorbed = entity_with(description="a mathematician", at=LATE)

        claims = claims_for("description", canonical, [absorbed])

        assert [c.value for c in claims] == ["the first analyst", "a mathematician"]

    def test_a_none_description_is_silence_and_is_skipped(self):
        """The asymmetry with `properties`, where an explicit `None` is a claim.

        `description` has no present/absent distinction -- `None` *is* the
        absence -- so an entity with no description must not outvote one with a
        description merely by being newer.
        """
        canonical = entity_with(description="the first analyst", at=EARLY)
        silent = entity_with(description=None, at=LATE)

        claims = claims_for("description", canonical, [silent])

        assert [c.value for c in claims] == ["the first analyst"]

    def test_an_explicit_none_property_is_still_a_claim(self):
        """The other half of the asymmetry, asserted beside it so neither can
        be changed without the other failing."""
        canonical = entity_with(properties={"role": None}, at=EARLY)

        claims = claims_for("properties.role", canonical, [])

        assert [c.value for c in claims] == [None]

    def test_an_external_id_claim_is_read_from_external_ids(self):
        canonical = entity_with(external_ids={"wikidata": "Q7259"}, at=EARLY)
        absorbed = entity_with(external_ids={"orcid": "0000-1"}, at=LATE)

        assert [c.value for c in claims_for("external_ids.wikidata", canonical, [absorbed])] == [
            "Q7259"
        ]
        assert [c.value for c in claims_for("external_ids.orcid", canonical, [absorbed])] == [
            "0000-1"
        ]

    def test_a_path_naming_no_real_field_is_refused(self):
        with pytest.raises(ValueError, match="name"):
            claims_for("name", entity_with(at=EARLY), [])

    def test_a_key_under_description_is_refused(self):
        """`description` is a scalar; `description.x` names nothing."""
        with pytest.raises(ValueError, match="description"):
            claims_for("description.x", entity_with(at=EARLY), [])

    def test_a_bare_dict_field_is_refused_as_a_claim_path(self):
        """`properties` is a policy key, not a claim target. Resolving the whole
        dict as one value is not what any strategy means."""
        with pytest.raises(ValueError, match="properties"):
            claims_for("properties", entity_with(properties={"role": "x"}, at=EARLY), [])
