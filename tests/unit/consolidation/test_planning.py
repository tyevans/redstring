"""The merge plan: what moves, what is dropped, and which duplicate survives."""

from __future__ import annotations

import itertools
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from redstring.consolidation.planning import duplicate_preference, plan_redirections

from .conftest import edge

#: Two ids whose canonical strings sort one way whatever order they are built
#: in, so a test can say which is "lower" without depending on `uuid4`.
LOW = UUID("00000000-0000-4000-8000-00000000000a")
HIGH = UUID("ffffffff-0000-4000-8000-00000000000f")


def _plan(tenant, canonical, absorbed, relationships):
    return plan_redirections(
        canonical_entity_id=canonical,
        merged_entity_ids=absorbed,
        relationships=relationships,
    )


class TestMoving:
    def test_an_absorbed_entitys_edge_moves_onto_the_canonical(self):
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        moved = edge(tenant, source=absorbed, target=outsider)

        [redirection] = _plan(tenant, canonical, [absorbed], [moved])

        assert redirection.before == moved
        assert redirection.after is not None
        assert redirection.after.source_entity_id == canonical
        assert redirection.after.target_entity_id == outsider
        # Same id, or applying the redirection would create a second edge and
        # leave the original in place.
        assert redirection.after.id == moved.id

    def test_an_incoming_edge_moves_too(self):
        """Direction is not a filter here: an edge *into* an absorbed entity
        is just as much its edge."""
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        incoming = edge(tenant, source=outsider, target=absorbed)

        [redirection] = _plan(tenant, canonical, [absorbed], [incoming])

        assert redirection.after is not None
        assert redirection.after.target_entity_id == canonical
        assert redirection.after.source_entity_id == outsider

    def test_the_canonical_entitys_own_edges_are_untouched(self):
        """No redirection at all, not a redirection to itself. A no-op
        redirection is a permanent event's worth of nothing."""
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        own = edge(tenant, source=canonical, target=outsider, kind="wrote")

        assert _plan(tenant, canonical, [absorbed], [own]) == []

    def test_an_edge_touching_neither_is_ignored(self):
        """The caller may pass a superset; it must not have to filter first."""
        tenant, canonical, absorbed = (uuid4() for _ in range(3))
        elsewhere = edge(tenant, source=uuid4(), target=uuid4())

        assert _plan(tenant, canonical, [absorbed], [elsewhere]) == []

    def test_every_absorbed_entity_is_redirected_not_just_the_first(self):
        """A loop that stopped early would pass every single-entity test."""
        tenant, canonical, outsider = uuid4(), uuid4(), uuid4()
        first, second = uuid4(), uuid4()
        edges = [
            edge(tenant, source=first, target=outsider, kind="a"),
            edge(tenant, source=second, target=outsider, kind="b"),
        ]

        plan = _plan(tenant, canonical, [first, second], edges)

        assert [r.after.source_entity_id for r in plan] == [canonical, canonical]


class TestDropping:
    def test_an_edge_between_two_absorbed_entities_is_dropped(self):
        """It would be a self-loop on the canonical entity, which
        `Relationship` refuses to construct at all."""
        tenant, canonical, first, second = (uuid4() for _ in range(4))
        internal = edge(tenant, source=first, target=second)

        [redirection] = _plan(tenant, canonical, [first, second], [internal])

        assert redirection.before == internal
        assert redirection.after is None

    def test_an_edge_between_the_canonical_and_an_absorbed_entity_is_dropped(self):
        """The commonest case of all -- consolidation frequently merges two
        entities an extractor had already linked."""
        tenant, canonical, absorbed = (uuid4() for _ in range(3))
        linking = edge(tenant, source=canonical, target=absorbed, kind="same_as")

        [redirection] = _plan(tenant, canonical, [absorbed], [linking])

        assert redirection.after is None

    def test_a_dropped_edge_keeps_its_whole_before(self):
        """Undo recreates from `before`, so type, confidence and properties
        have to survive -- endpoint ids alone would restore a different edge.
        """
        tenant, canonical, first, second = (uuid4() for _ in range(4))
        internal = edge(
            tenant,
            source=first,
            target=second,
            kind="collaborated_with",
            confidence=0.42,
            properties={"since": 1843},
        )

        [redirection] = _plan(tenant, canonical, [first, second], [internal])

        assert redirection.before == internal


class TestDeduplication:
    def test_a_redirected_duplicate_is_dropped(self):
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        existing = edge(tenant, source=canonical, target=outsider, confidence=0.9)
        duplicate = edge(tenant, source=absorbed, target=outsider, confidence=0.1)

        plan = _plan(tenant, canonical, [absorbed], [existing, duplicate])

        assert [(r.before.id, r.after) for r in plan] == [(duplicate.id, None)]

    def test_the_direction_of_the_edge_is_part_of_the_claim(self):
        """`A -> X` and `X -> A` of the same type are different claims. An
        unordered signature would collapse "Ada wrote the Notes" and "the Notes
        were written by Ada" into whichever was seen first."""
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        outgoing = edge(tenant, source=canonical, target=outsider, kind="knows")
        incoming = edge(tenant, source=outsider, target=absorbed, kind="knows")

        [redirection] = _plan(tenant, canonical, [absorbed], [outgoing, incoming])

        assert redirection.after is not None, "the reversed edge is not a duplicate"

    def test_the_relationship_type_is_part_of_the_claim(self):
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        wrote = edge(tenant, source=canonical, target=outsider, kind="wrote")
        knows = edge(tenant, source=absorbed, target=outsider, kind="knows")

        [redirection] = _plan(tenant, canonical, [absorbed], [wrote, knows])

        assert redirection.after is not None

    def test_the_more_confident_duplicate_survives(self):
        """The order's first component, and the only one anybody would design
        deliberately."""
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        weak = edge(tenant, source=canonical, target=outsider, confidence=0.1)
        strong = edge(tenant, source=absorbed, target=outsider, confidence=0.9)

        plan = _plan(tenant, canonical, [absorbed], [weak, strong])

        dropped = [r for r in plan if r.after is None]
        kept = [r for r in plan if r.after is not None]
        assert [r.before.id for r in dropped] == [weak.id]
        assert [r.before.id for r in kept] == [strong.id]

    def test_an_edge_the_canonical_already_had_can_be_the_one_dropped(self):
        """Stated separately because "the winner's edge always wins" is the
        natural implementation and is wrong: the surviving edge should be the
        better description of the claim, not the one that happened to belong to
        the surviving entity."""
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        existing = edge(tenant, source=canonical, target=outsider, confidence=0.2)
        better = edge(tenant, source=absorbed, target=outsider, confidence=0.8)

        plan = _plan(tenant, canonical, [absorbed], [existing, better])

        assert [r.before.id for r in plan if r.after is None] == [existing.id]

    def test_a_tie_on_confidence_is_broken_by_the_id_not_by_arrival(self):
        """The failure this exists to prevent. `get_relationships_for` promises
        no order, so two adapters would emit different payloads for one graph.

        The two edges are given deliberately equal confidence and properties --
        the common case, since every relationship a model declines to score
        carries the same default -- so only the id can separate them.
        """
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        low = edge(tenant, source=canonical, target=outsider, confidence=0.5)
        high = edge(tenant, source=absorbed, target=outsider, confidence=0.5)
        object.__setattr__(low, "id", LOW)
        object.__setattr__(high, "id", HIGH)

        for ordering in itertools.permutations([low, high]):
            plan = _plan(tenant, canonical, [absorbed], list(ordering))
            assert [r.before.id for r in plan if r.after is None] == [LOW], (
                "the surviving duplicate depends on the order the store "
                "returned, which the port does not promise"
            )

    def test_three_duplicates_leave_exactly_one(self):
        tenant, canonical, outsider = uuid4(), uuid4(), uuid4()
        first, second = uuid4(), uuid4()
        edges = [
            edge(tenant, source=canonical, target=outsider, confidence=0.1),
            edge(tenant, source=first, target=outsider, confidence=0.2),
            edge(tenant, source=second, target=outsider, confidence=0.3),
        ]

        plan = _plan(tenant, canonical, [first, second], edges)

        assert len([r for r in plan if r.after is None]) == 2
        assert len([r for r in plan if r.after is not None]) == 1


class TestTheShapeOfThePlan:
    def test_redirections_are_ordered_by_the_original_edge_id(self):
        """The payload lands in a permanent log, and the store promises no
        order -- so the plan has to impose one or two adapters disagree."""
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        edges = [
            edge(tenant, source=absorbed, target=outsider, kind=str(index)) for index in range(6)
        ]

        for ordering in (edges, list(reversed(edges))):
            plan = _plan(tenant, canonical, [absorbed], ordering)
            assert [str(r.before.id) for r in plan] == sorted(str(r.before.id) for r in plan)

    def test_an_empty_edge_set_plans_nothing(self):
        tenant, canonical, absorbed = (uuid4() for _ in range(3))

        assert _plan(tenant, canonical, [absorbed], []) == []

    @given(data=st.data())
    def test_the_plan_does_not_depend_on_the_order_of_its_input(self, data):
        """The property the store's silence about order makes necessary.

        Permuting the edges must not change the plan at all -- not which edges
        move, not which are dropped, not the order they are listed in.
        """
        tenant, canonical, outsider, other = (uuid4() for _ in range(4))
        first, second = uuid4(), uuid4()
        confidences = data.draw(st.lists(st.floats(0.0, 1.0), min_size=5, max_size=5))
        edges = [
            edge(tenant, source=canonical, target=outsider, confidence=confidences[0]),
            edge(tenant, source=first, target=outsider, confidence=confidences[1]),
            edge(tenant, source=second, target=outsider, confidence=confidences[2]),
            edge(tenant, source=first, target=second, confidence=confidences[3]),
            edge(tenant, source=other, target=outsider, confidence=confidences[4]),
        ]
        permutation = data.draw(st.permutations(edges))

        assert _plan(tenant, canonical, [first, second], edges) == _plan(
            tenant, canonical, [first, second], list(permutation)
        )

    @given(data=st.data())
    def test_every_edge_of_the_group_is_accounted_for(self, data):
        """Nothing is silently left behind. Every edge touching an absorbed
        entity either moves or is recorded as dropped -- an omission would be
        an edge undo could never restore.
        """
        tenant, canonical, outsider = uuid4(), uuid4(), uuid4()
        first, second = uuid4(), uuid4()
        kinds = data.draw(st.lists(st.sampled_from(["a", "b", "c"]), min_size=4, max_size=4))
        group_edges = [
            edge(tenant, source=first, target=outsider, kind=kinds[0]),
            edge(tenant, source=outsider, target=second, kind=kinds[1]),
            edge(tenant, source=first, target=second, kind=kinds[2]),
            edge(tenant, source=canonical, target=first, kind=kinds[3]),
        ]

        plan = _plan(tenant, canonical, [first, second], group_edges)

        assert {r.before.id for r in plan} == {e.id for e in group_edges}


class TestDuplicatePreference:
    def test_it_is_total_over_edges_sharing_a_signature(self):
        """The claim the module docstring argues, checked rather than assumed.

        Two edges competing for one signature agree on endpoints, type and
        tenant by construction, so only confidence, properties and id can
        differ. No two distinct competitors may compare equal, or the `max`
        falls through to arrival order.
        """
        tenant, source, target = uuid4(), uuid4(), uuid4()
        competitors = [
            edge(tenant, source=source, target=target, confidence=c, properties=p)
            for c in (0.5, 0.9)
            for p in ({}, {"a": 1}, {"b": 2})
        ]

        keys = [duplicate_preference(competitor) for competitor in competitors]
        assert len(set(keys)) == len(keys)

    def test_two_edges_alike_in_everything_but_id_still_compare_unequal(self):
        """The component `relationship_preference` alone does not have, and
        the reason this order is not simply that one."""
        tenant, source, target = uuid4(), uuid4(), uuid4()
        one = edge(tenant, source=source, target=target, confidence=0.5)
        two = edge(tenant, source=source, target=target, confidence=0.5)

        assert duplicate_preference(one) != duplicate_preference(two)

    def test_confidence_dominates_the_id(self):
        """The id is a tie-break, not a criterion. If it outranked confidence
        the merge would keep whichever edge sorted highest, which is nonsense
        dressed as determinism."""
        tenant, source, target = uuid4(), uuid4(), uuid4()
        strong = edge(tenant, source=source, target=target, confidence=0.9)
        weak = edge(tenant, source=source, target=target, confidence=0.1)
        object.__setattr__(strong, "id", LOW)
        object.__setattr__(weak, "id", HIGH)

        assert duplicate_preference(strong) > duplicate_preference(weak)

    @given(
        confidence=st.floats(0.0, 1.0),
        # NUL excluded: `Relationship` refuses one in `properties`, since no
        # JSON-backed store can hold it (`domain/json_safety.py`). This
        # property is about the preference order, not about that guard.
        properties=st.dictionaries(
            st.text(alphabet=st.characters(codec="utf-8", exclude_characters="\x00"), max_size=4),
            st.integers(),
            max_size=3,
        ),
    )
    def test_it_is_reflexive_and_deterministic(self, confidence, properties):
        tenant, source, target = uuid4(), uuid4(), uuid4()
        one = edge(
            tenant,
            source=source,
            target=target,
            confidence=confidence,
            properties=properties,
        )

        assert duplicate_preference(one) == duplicate_preference(one)

    def test_it_is_comparable_across_arbitrary_property_bags(self):
        """`properties` holds whatever an extraction found, and a `TypeError`
        from deep inside a `max` would be an appalling way to discover that two
        bags were not mutually orderable."""
        tenant, source, target = uuid4(), uuid4(), uuid4()
        edges = [
            edge(tenant, source=source, target=target, properties=bag)
            for bag in ({"a": [1, {"b": None}]}, {"a": "text"}, {}, {"z": 2**70})
        ]

        assert max(edges, key=duplicate_preference) in edges


class TestNoOpRedirectionsAreNotEmitted:
    def test_a_group_that_moves_nothing_plans_nothing(self):
        tenant, canonical, absorbed = (uuid4() for _ in range(3))
        unrelated = [edge(tenant, source=uuid4(), target=uuid4()) for _ in range(3)]

        assert _plan(tenant, canonical, [absorbed], unrelated) == []

    def test_a_redirection_never_has_after_equal_to_before(self):
        tenant, canonical, outsider = uuid4(), uuid4(), uuid4()
        first, second = uuid4(), uuid4()
        edges = [
            edge(tenant, source=canonical, target=outsider),
            edge(tenant, source=first, target=outsider, kind="other"),
            edge(tenant, source=first, target=second, kind="third"),
        ]

        for redirection in _plan(tenant, canonical, [first, second], edges):
            assert redirection.after != redirection.before


class TestTheMergeEventAcceptsThePlan:
    def test_a_plan_is_a_valid_redirections_payload(self):
        """`EntitiesMerged` rejects redirections carrying a foreign tenant, and
        `RelationshipRedirection` rejects an `after` that is a different edge.
        A plan must satisfy both without the caller adjusting it."""
        from redstring.events.merge import EntitiesMerged

        tenant, canonical, outsider = uuid4(), uuid4(), uuid4()
        first, second = uuid4(), uuid4()
        edges = [
            edge(tenant, source=first, target=outsider),
            edge(tenant, source=first, target=second, kind="internal"),
        ]

        plan = _plan(tenant, canonical, [first, second], edges)

        event = EntitiesMerged(
            aggregate_id=tenant,
            tenant_id=tenant,
            canonical_entity_id=canonical,
            merged_entity_ids=[first, second],
            redirections=plan,
        )
        assert len(event.redirections) == 2

    def test_a_plan_never_moves_an_edge_across_tenants(self):
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        moved = edge(tenant, source=absorbed, target=outsider)

        [redirection] = _plan(tenant, canonical, [absorbed], [moved])

        assert redirection.after.tenant_id == tenant


class TestArgumentHandling:
    def test_merging_nothing_plans_nothing(self):
        """Not an error: `EntitiesMerged` is what refuses an empty merge, and
        the planner is not the place to duplicate that rule."""
        tenant, canonical, outsider = uuid4(), uuid4(), uuid4()
        own = edge(tenant, source=canonical, target=outsider)

        assert _plan(tenant, canonical, [], [own]) == []

    def test_a_repeated_absorbed_id_changes_nothing(self):
        tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
        moved = edge(tenant, source=absorbed, target=outsider)

        once = _plan(tenant, canonical, [absorbed], [moved])
        twice = _plan(tenant, canonical, [absorbed, absorbed], [moved])

        assert once == twice


def test_planning_is_pure() -> None:
    """No store, no clock, no provider -- and no mutation of its arguments.

    A planner that edited the relationships it was handed would corrupt the
    caller's view of the pre-merge graph, which is exactly what undo needs.
    """
    tenant, canonical, absorbed, outsider = (uuid4() for _ in range(4))
    moved = edge(tenant, source=absorbed, target=outsider)
    pristine = moved.model_copy(deep=True)

    _plan(tenant, canonical, [absorbed], [moved])

    assert moved == pristine


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
