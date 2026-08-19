"""Greedy modularity optimisation.

Every id here is a fixed literal, never `uuid4()`. CLAUDE.md's table records
three separate defects that survived because random ids happened to sort the
right way about half the time, and this module is exposed to exactly that: the
output order is by id and the tie-break is by id, so a suite built on random
ids cannot tell "sorted by id" from "sorted by whichever community was found
first". The literals below are assigned to the two halves of the barbell
*alternately*, so the id order interleaves the communities and the two
orderings are visibly different things.

The canonical fixture is a barbell rather than a chain for the reason the
table gives about chains: on a chain almost every partitioning algorithm
agrees, so it distinguishes nothing. On a barbell the right answer is two
communities, an implementation that never moves anyone gives eight, and one
that ignores the null model gives one.
"""

from __future__ import annotations

import random
from uuid import UUID

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from redstring.domain.community import Community, detect_communities
from redstring.domain.ids import EntityId

N1 = EntityId(UUID("00000000-0000-4000-8000-000000000001"))
N2 = EntityId(UUID("00000000-0000-4000-8000-000000000002"))
N3 = EntityId(UUID("00000000-0000-4000-8000-000000000003"))
N4 = EntityId(UUID("00000000-0000-4000-8000-000000000004"))
N5 = EntityId(UUID("00000000-0000-4000-8000-000000000005"))
N6 = EntityId(UUID("00000000-0000-4000-8000-000000000006"))
N7 = EntityId(UUID("00000000-0000-4000-8000-000000000007"))
N8 = EntityId(UUID("00000000-0000-4000-8000-000000000008"))
ABSENT = EntityId(UUID("00000000-0000-4000-8000-0000000000ff"))

#: The two lobes interleave in id order: odds one side, evens the other.
LEFT = (N1, N3, N5, N7)
RIGHT = (N2, N4, N6, N8)
BARBELL_NODES = (N1, N2, N3, N4, N5, N6, N7, N8)


def _clique(
    members: tuple[EntityId, ...], weight: float = 1.0
) -> list[tuple[EntityId, EntityId, float]]:
    return [(a, b, weight) for i, a in enumerate(members) for b in members[i + 1 :]]


#: Two 4-cliques joined by one weak edge. The bridge is deliberately far
#: lighter than an internal edge, so the answer is not a matter of taste.
BARBELL_EDGES = [*_clique(LEFT), *_clique(RIGHT), (N1, N2, 0.05)]


def _members(communities: list[Community]) -> list[tuple[EntityId, ...]]:
    return [community.members for community in communities]


class TestThePartition:
    def test_a_barbell_splits_at_the_weak_edge(self) -> None:
        """The fixture that a chain could not provide: three answers differ.

        Two communities is right; one means the null model is not being
        subtracted, eight means nobody ever moved.
        """
        assert _members(detect_communities(BARBELL_NODES, BARBELL_EDGES)) == [LEFT, RIGHT]

    def test_a_hub_keeps_neighbours_that_sort_both_above_and_below_it(self) -> None:
        """The hub's id sits mid-range, so neither id order can be mistaken
        for adjacency: N4's neighbours are N1..N3 and N5..N7, and a partition
        derived from position rather than from edges would cut the hub's star
        in half.
        """
        nodes = (N1, N2, N3, N4, N5, N6, N7)
        edges = [(N4, other, 1.0) for other in (N1, N2, N3, N5, N6, N7)]
        assert _members(detect_communities(nodes, edges)) == [nodes]

    def test_isolated_nodes_are_singletons_beside_a_real_community(self) -> None:
        """A node in no edge, listed *before* nodes that do cluster.

        The bad-element-then-good-element shape: an implementation that
        stopped at the first node with no neighbours would return one
        community and lose the rest.
        """
        nodes = (N1, N2, N3, N4)
        edges = [(N3, N4, 1.0)]
        assert _members(detect_communities(nodes, edges)) == [(N1,), (N2,), (N3, N4)]

    def test_every_node_appears_in_exactly_one_community(self) -> None:
        communities = detect_communities(BARBELL_NODES, BARBELL_EDGES)
        seen = [member for community in communities for member in community.members]
        assert sorted(seen, key=str) == sorted(BARBELL_NODES, key=str)
        assert len(seen) == len(set(seen))

    def test_a_diamond_partitions_without_leaving_anyone_out(self) -> None:
        """A 4-cycle, where no answer is obviously right and every symmetry
        is intact -- so it can only be asserted as an invariant. A chain would
        have made this trivially true for any implementation.
        """
        nodes = (N1, N2, N3, N4)
        edges = [(N1, N2, 1.0), (N1, N3, 1.0), (N2, N4, 1.0), (N3, N4, 1.0)]
        communities = detect_communities(nodes, edges)
        assert sorted((m for c in communities for m in c.members), key=str) == list(nodes)

    def test_no_nodes_is_no_communities(self) -> None:
        assert detect_communities([], []) == []

    def test_one_node_is_one_singleton(self) -> None:
        assert detect_communities([N1], []) == [Community(members=(N1,))]

    def test_two_nodes_one_edge_is_one_community(self) -> None:
        assert _members(detect_communities((N2, N1), [(N1, N2, 1.0)])) == [(N1, N2)]

    def test_a_repeated_node_is_one_node(self) -> None:
        assert _members(detect_communities((N1, N1), [])) == [(N1,)]


class TestOrder:
    def test_communities_are_ordered_by_their_first_member_and_members_ascend(self) -> None:
        """Nodes and edges are supplied in descending id order throughout, so
        an implementation that echoed input order would be visible here.
        """
        nodes = (N8, N6, N4, N2, N1, N3, N5, N7)
        edges = list(reversed(BARBELL_EDGES))
        communities = detect_communities(nodes, edges)
        assert _members(communities) == [LEFT, RIGHT]
        firsts = [c.members[0] for c in communities]
        assert firsts == sorted(firsts, key=str)

    def test_the_id_order_is_not_the_community_order(self) -> None:
        """Guards the fixture rather than the code: if the barbell's lobes
        were contiguous in id order, every assertion above would hold for an
        implementation that sorted by id and never clustered at all.
        """
        by_id = sorted(BARBELL_NODES, key=str)
        communities = detect_communities(BARBELL_NODES, BARBELL_EDGES)
        by_community = [member for community in communities for member in community.members]
        assert by_id != by_community


class TestDeterminism:
    def test_a_shuffled_edge_list_gives_the_identical_partition(self) -> None:
        """The test that catches a dependence on insertion order.

        Twenty shuffles rather than one: a single shuffle can coincide with
        the sorted order, and an order-dependent implementation would then
        pass about as often as not.
        """
        expected = detect_communities(BARBELL_NODES, BARBELL_EDGES)
        rng = random.Random(20260818)
        for _ in range(20):
            shuffled = BARBELL_EDGES[:]
            rng.shuffle(shuffled)
            assert detect_communities(BARBELL_NODES, shuffled) == expected

    def test_two_runs_over_the_same_input_agree(self) -> None:
        once = detect_communities(BARBELL_NODES, BARBELL_EDGES)
        assert once == detect_communities(BARBELL_NODES, BARBELL_EDGES)

    def test_nodes_are_visited_in_ascending_id_order(self) -> None:
        """Greedy local moving is order-sensitive, and this graph is where it
        shows: visited N1-first every node ends in one community, visited
        N4-first it splits into two pairs. Both are stable partitions, so
        neither is "wrong" -- which is exactly why the visiting order has to
        be pinned by a test rather than left to whatever the loop happens to
        do. Without this case, iterating in descending order passes the whole
        module.
        """
        nodes = (N1, N2, N3, N4)
        edges = [(N1, N3, 0.68), (N1, N4, 1.64), (N2, N4, 1.97)]
        assert _members(detect_communities(nodes, edges)) == [(N1, N2, N3, N4)]

    def test_an_equal_gain_resolves_to_the_lower_id(self) -> None:
        """N4 is pulled equally hard by two otherwise identical triangles.

        Both candidate communities offer the same weight and the same degree,
        so only the tie-break decides -- and it must decide by id, not by
        which triangle N4's edges were listed in. The edges to the *higher*
        triangle are listed first, so insertion order and id order disagree.
        """
        nodes = (N1, N2, N3, N4, N5, N6, N7)
        edges = [
            *_clique((N5, N6, N7)),
            *_clique((N1, N2, N3)),
            (N4, N5, 1.0),
            (N4, N1, 1.0),
        ]
        communities = detect_communities(nodes, edges)
        assert (N1, N2, N3, N4) in _members(communities)


class TestResolution:
    def test_a_higher_resolution_splits_the_barbell_further(self) -> None:
        """Two resolutions that give the same answer would test nothing."""
        at_one = detect_communities(BARBELL_NODES, BARBELL_EDGES, resolution=1.0)
        at_three = detect_communities(BARBELL_NODES, BARBELL_EDGES, resolution=3.0)
        assert _members(at_one) == [LEFT, RIGHT]
        assert _members(at_three) == [(node,) for node in sorted(BARBELL_NODES, key=str)]

    def test_resolution_zero_is_legal_and_is_label_propagation(self) -> None:
        """The boundary, pinned as an example because it is where the null
        model term disappears entirely. It does *not* merge the barbell --
        see the module docstring in `domain/community.py`.
        """
        at_zero = detect_communities(BARBELL_NODES, BARBELL_EDGES, resolution=0.0)
        assert _members(at_zero) == [LEFT, RIGHT]

    def test_an_exact_tie_with_staying_put_leaves_the_node_where_it_is(self) -> None:
        """One edge at `resolution=2` makes joining worth exactly zero.

        `gain = w - 2 * w * w / (2w)` is 0, which is precisely what staying in
        a community the node has just vacated is worth -- so nothing about
        modularity decides this and only the tie-break can. The rule is that
        the node's own community is considered first, so an exact tie never
        moves anybody; without this case, a tie-break that preferred the
        other candidate passes the whole module.
        """
        tied = detect_communities((N1, N2), [(N1, N2, 1.0)], resolution=2.0)
        assert _members(tied) == [(N1,), (N2,)]

    def test_a_negative_resolution_is_refused(self) -> None:
        with pytest.raises(ValueError, match="resolution"):
            detect_communities(BARBELL_NODES, BARBELL_EDGES, resolution=-1.0)


class TestEdgeInput:
    def test_an_edge_naming_an_unknown_node_names_it(self) -> None:
        with pytest.raises(ValueError, match=str(ABSENT)):
            detect_communities((N1, N2), [(N1, N2, 1.0), (N1, ABSENT, 1.0)])

    def test_an_unknown_node_is_caught_in_either_position(self) -> None:
        with pytest.raises(ValueError, match=str(ABSENT)):
            detect_communities((N1, N2), [(ABSENT, N1, 1.0)])

    def test_a_bad_edge_after_a_good_one_is_still_caught(self) -> None:
        """The loop shape from CLAUDE.md's table, inverted: the offending
        edge is not first, so an implementation that only validated the head
        of the list would pass every other case here.
        """
        with pytest.raises(ValueError, match=str(ABSENT)):
            detect_communities((N1, N2, N3), [(N1, N2, 1.0), (N2, N3, 1.0), (N3, ABSENT, 1.0)])

    @pytest.mark.parametrize("weight", [0.0, -1.0, float("nan"), float("inf")])
    def test_a_weight_that_is_not_finite_and_positive_is_refused(self, weight: float) -> None:
        with pytest.raises(ValueError, match="weight"):
            detect_communities((N1, N2), [(N1, N2, weight)])

    def test_duplicate_edges_sum(self) -> None:
        """N3 is pulled by two symmetric triangles and the duplicates decide.

        Three separate 0.3 listings towards the left triangle against one 0.5
        towards the right: summed, N3 goes left; under max, last-wins or
        first-wins the 0.3 loses to the 0.5 and N3 goes right. The weights are
        deliberately unequal so the two rules cannot agree, and the
        single-edge case below is the control that shows the difference is
        the summing and not the topology.
        """
        nodes = (N1, N2, N3, N4, N5, N6, N7)
        shared = [*_clique((N1, N5, N7)), *_clique((N2, N4, N6))]
        thrice = [(N3, N1, 0.3), (N1, N3, 0.3), (N3, N1, 0.3), (N3, N2, 0.5)]
        summed = detect_communities(nodes, [*shared, *thrice])
        assert (N1, N3, N5, N7) in _members(summed)

        unsummed = detect_communities(nodes, [*shared, (N3, N1, 0.3), (N3, N2, 0.5)])
        assert (N2, N3, N4, N6) in _members(unsummed)

    def test_a_self_loop_is_permitted_and_only_makes_a_node_heavier(self) -> None:
        """It cannot make a node join anything -- a node is not evidence of
        its own membership -- but it does inflate the degree the null model
        charges for, which is enough to push N3 out of a community it joins
        without the loop.

        The weight is 0.5 rather than something comfortably large, because
        the convention under test is that a self-loop counts *twice* towards
        the degree. At a large weight both conventions split and the test
        would be asserting only that self-loops are accepted; at 0.5 the
        doubled degree splits and a single contribution does not.
        """
        nodes = (N1, N2, N3)
        edges = [(N1, N2, 1.0), (N2, N3, 1.0)]
        assert _members(detect_communities(nodes, edges)) == [(N1, N2, N3)]
        assert _members(detect_communities(nodes, [*edges, (N3, N3, 0.5)])) == [(N1, N2), (N3,)]

    def test_an_edgeless_graph_is_all_singletons(self) -> None:
        assert _members(detect_communities((N2, N1), [])) == [(N1,), (N2,)]


ID_POOL = (N1, N2, N3, N4, N5, N6, N7, N8)


@st.composite
def _graphs(draw: st.DrawFn) -> tuple[tuple[EntityId, ...], list[tuple[EntityId, EntityId, float]]]:
    nodes = tuple(draw(st.lists(st.sampled_from(ID_POOL), min_size=1, max_size=8, unique=True)))
    edges = draw(
        st.lists(
            st.tuples(
                st.sampled_from(nodes),
                st.sampled_from(nodes),
                st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
            ),
            max_size=20,
        )
    )
    return nodes, edges


class TestProperties:
    """A sampler, not a proof -- every boundary above is also an example."""

    @given(_graphs())
    @settings(suppress_health_check=[HealthCheck.too_slow])
    @example(((N1,), []))
    @example((BARBELL_NODES, BARBELL_EDGES))
    def test_every_node_lands_in_exactly_one_community(
        self, graph: tuple[tuple[EntityId, ...], list[tuple[EntityId, EntityId, float]]]
    ) -> None:
        nodes, edges = graph
        communities = detect_communities(nodes, edges)
        seen = [member for community in communities for member in community.members]
        assert len(seen) == len(set(seen))
        assert set(seen) == set(nodes)

    @given(_graphs(), st.randoms(use_true_random=True))
    @settings(suppress_health_check=[HealthCheck.too_slow])
    def test_shuffling_the_input_does_not_change_the_partition(
        self,
        graph: tuple[tuple[EntityId, ...], list[tuple[EntityId, EntityId, float]]],
        rng: random.Random,
    ) -> None:
        nodes, edges = graph
        shuffled_nodes = list(nodes)
        shuffled_edges = list(edges)
        rng.shuffle(shuffled_nodes)
        rng.shuffle(shuffled_edges)
        shuffled = detect_communities(shuffled_nodes, shuffled_edges)
        assert shuffled == detect_communities(nodes, edges)

    @given(_graphs())
    @settings(suppress_health_check=[HealthCheck.too_slow])
    def test_the_output_is_totally_ordered(
        self, graph: tuple[tuple[EntityId, ...], list[tuple[EntityId, EntityId, float]]]
    ) -> None:
        communities = detect_communities(*graph)
        for community in communities:
            assert list(community.members) == sorted(community.members, key=str)
        firsts = [community.members[0] for community in communities]
        assert firsts == sorted(firsts, key=str)
