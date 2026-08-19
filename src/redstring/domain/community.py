"""Greedy modularity optimisation over a weighted undirected graph.

Pure. Node ids and weighted edges in, a partition out; no store, no model, no
`TenantId` -- ADR 0042 argues each of those absences. Isolation is enforced by
the reads that build the arguments, so a graph reaching this function is
already one tenant's; a second mechanism here could only disagree with the
first.

## What this is

The *local-moving* phase of Louvain, iterated to convergence: every node is
repeatedly offered to each of its neighbours' communities and moves to the one
that most increases modularity, until a whole pass moves nobody. There is no
aggregation phase, so communities are never coarsened -- the result is the
partition that greedy local moving reaches, which is the same *kind* of answer
Leiden gives and differs from it on quality at scale (ADR 0042, B149).

The gain of moving node `i` into community `C`, scaled by the total weight
`m` so the comparison needs no division per candidate::

    gain(i, C) = w(i, C) - resolution * deg(i) * tot(C) / (2m)

`w(i, C)` is the weight from `i` to `C`'s members, `tot(C)` the summed degree
of `C` with `i` already removed. `resolution` scales only the second term --
the null model -- so a larger value makes joining an already-heavy community
more expensive and yields more, smaller communities; push it far enough and
every node is a singleton.

`resolution=0` is legal and is the boundary worth knowing, because it is not
what it looks like. The null model vanishes, so each node simply joins
whichever neighbouring community it links to most heavily -- label propagation,
not modularity. It does **not** collapse a connected graph into one community:
local moving has no way to merge two dense clusters joined by a weak edge,
since no single node on either side gains by crossing. That is a property of
the phase implemented here rather than of the resolution.

## Determinism is contract, not luck

Modularity optimisation is order-sensitive: the same graph visited in two
orders gives two partitions, and a clustering that varies run to run makes
every downstream report unreproducible. So nodes are visited in ascending id
order, candidate communities are considered in ascending order of their
smallest member, and equal gains resolve to the smaller. The community the
node is currently leaving sorts first among the candidates, so an exact tie
between moving and staying never moves anybody. Nothing here reads
the order the caller happened to list edges or nodes in -- a shuffled edge
list returns the identical partition.

## What the inputs must be

- Every endpoint of every edge must appear in `nodes`; an edge naming anything
  else raises `ValueError` naming the id. Silently inventing the node would
  make a typo look like an isolated cluster.
- Weights must be strictly positive and finite. Zero is refused rather than
  ignored, because an edge carrying no weight is a claim of a connection that
  the evidence does not support, and the caller is better placed to decide
  whether to omit it. Negative weights are not merely unsupported -- they make
  modularity's null model meaningless.
- Duplicate edges, including a pair listed in both directions, are **summed**.
  Two documents each asserting the same relationship is more evidence for it,
  not the same evidence twice; a caller who disagrees can deduplicate before
  calling, which a caller of a refusing implementation could not undo.
- A self-loop is permitted and contributes twice its weight to that node's
  degree, the usual undirected convention. It can therefore only make the node
  look heavier to the null model; it never appears in `w(i, C)`, because a
  node is not evidence of its own membership anywhere.
- Repeated ids in `nodes` are one node.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.ids import EntityId

#: How many full local-moving passes to attempt before returning what we have.
#: Each accepted move strictly increases modularity, which bounds the loop in
#: exact arithmetic -- but the gain is a float, and a bound that rests on an
#: argument about arithmetic is inferred rather than enforced. Convergence on
#: real graphs takes a handful of passes; this only ever fires on a cycle
#: float rounding created.
MAX_PASSES: Final = 100


@dataclass(frozen=True, slots=True)
class Community:
    """One cluster of the graph, with no identity of its own.

    Deliberately just its members: ADR 0042 refuses community identity, since
    a community is a fact about a partition of a graph that the next document
    replaces. Anything stable enough to name would be a claim this function
    cannot make.

    `members` is ascending by the canonical lowercase hyphenated id string --
    the same total order `find_entities` pages over.
    """

    members: tuple[EntityId, ...]


def _key(entity_id: EntityId) -> str:
    """The canonical lowercase hyphenated form, which is the total order."""
    return str(entity_id)


def detect_communities(
    nodes: Sequence[EntityId],
    edges: Sequence[tuple[EntityId, EntityId, float]],
    *,
    resolution: float = 1.0,
) -> list[Community]:
    """Partition `nodes` by greedy modularity optimisation over `edges`.

    Every node appears in exactly one community; a node in no edge is its own
    singleton. Communities are ordered by their first member and members
    ascend within, so two equal partitions compare equal.

    See the module docstring for the treatment of self-loops, duplicate edges,
    weights and `resolution`.

    Raises:
        ValueError: if `resolution` is negative, an edge weight is not a
            finite positive number, or an edge names an id absent from
            `nodes`.
    """
    if resolution < 0:
        message = f"resolution must not be negative, got {resolution}"
        raise ValueError(message)

    ordered = sorted(set(nodes), key=_key)
    if not ordered:
        return []

    index = {node: position for position, node in enumerate(ordered)}
    adjacency: list[dict[int, float]] = [defaultdict(float) for _ in ordered]
    degree = [0.0] * len(ordered)
    total_weight = 0.0

    for source, target, weight in edges:
        if not isfinite(weight) or weight <= 0:
            message = (
                f"edge weight must be finite and positive, got {weight} for {source} -- {target}"
            )
            raise ValueError(message)
        for endpoint in (source, target):
            if endpoint not in index:
                message = f"edge names {endpoint}, which is not among the nodes"
                raise ValueError(message)
        left, right = index[source], index[target]
        total_weight += weight
        if left == right:
            # A self-loop is one edge but two endpoint-incidences.
            degree[left] += 2 * weight
            continue
        adjacency[left][right] += weight
        adjacency[right][left] += weight
        degree[left] += weight
        degree[right] += weight

    if total_weight == 0:
        return [Community(members=(node,)) for node in ordered]

    community = list(range(len(ordered)))
    members: list[set[int]] = [{position} for position in range(len(ordered))]
    community_degree = list(degree)

    for _ in range(MAX_PASSES):
        moved = _one_pass(
            adjacency, degree, community, members, community_degree, total_weight, resolution
        )
        if not moved:
            break

    grouped = [sorted(group) for group in members if group]
    grouped.sort(key=lambda group: group[0])
    return [Community(members=tuple(ordered[position] for position in group)) for group in grouped]


def _one_pass(
    adjacency: list[dict[int, float]],
    degree: list[float],
    community: list[int],
    members: list[set[int]],
    community_degree: list[float],
    total_weight: float,
    resolution: float,
) -> bool:
    """Offer every node to its neighbours' communities once. True if any moved.

    Nodes are visited in ascending index order, and the index order *is* the
    ascending id order established by the caller.
    """
    moved = False
    for position in range(len(degree)):
        current = community[position]
        members[current].discard(position)
        community_degree[current] -= degree[position]

        links: dict[int, float] = defaultdict(float)
        links[current] += 0.0
        for neighbour, weight in adjacency[position].items():
            links[community[neighbour]] += weight

        # Candidates in ascending order of their smallest member, so a tie in
        # gain resolves to the smaller id rather than to whichever community
        # this node's edges happened to be listed in. An emptied community has
        # no smallest member; it can only be `current`, and staying put is the
        # baseline, so it sorts first.
        candidates = sorted(links, key=lambda label: min(members[label], default=-1))
        best, best_gain = current, float("-inf")
        for candidate in candidates:
            null_model = resolution * degree[position] * community_degree[candidate]
            gain = links[candidate] - null_model / (2 * total_weight)
            if gain > best_gain:
                best, best_gain = candidate, gain

        members[best].add(position)
        community_degree[best] += degree[position]
        community[position] = best
        moved = moved or best != current
    return moved
