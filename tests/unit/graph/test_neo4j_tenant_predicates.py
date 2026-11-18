"""Tenant predicates and the depth cap, checked against query text and guards.

Cypher cannot run in the default suite, so these tests assert two things
without a server. First, that the depth guard fires before any query is sent.
Second, that every node a traversal names carries the tenant predicate. Both
are properties of the Cypher string or of a guard clause, and neither needs a
database. Behaviour is pinned by the integration suite.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from redstring.graph.adapters import neo4j as adapter
from redstring.ports.graph_store import MAX_TRAVERSAL_DEPTH


class _ExplodingDriver:
    """A driver that fails loudly if anything tries to use it.

    Lets the gate prove a guard clause fires *before* any I/O -- which is both
    the contract and the only reason these tests can run without Neo4j.
    """

    def session(self, **_: object) -> object:
        raise AssertionError("the adapter reached the database before validating")


def _offline_store() -> adapter.Neo4jGraphStore:
    return adapter.Neo4jGraphStore(_ExplodingDriver())  # type: ignore[arg-type]


class TestDepthCapIsEnforcedBeforeAnyQuery:
    """`depth` is formatted into `*1..{depth}`, so the cap guards the Cypher string."""

    async def test_a_depth_above_the_cap_is_rejected_before_any_query(self):
        """The cap guards the Cypher string, so it must fire before `_run`.

        `depth` is formatted into `*1..{depth}`. A value past the cap would be
        the exponential trail enumeration the cap exists to prevent.
        """
        with pytest.raises(ValueError, match="depth"):
            await _offline_store().neighbors(uuid4(), uuid4(), depth=MAX_TRAVERSAL_DEPTH + 1)


class _RecordingStore(adapter.Neo4jGraphStore):
    """Records each query and answers with the minimum the caller reads.

    Cypher cannot execute here, so these tests check the query *text*: that
    every node a traversal names carries the tenant predicate. That is the
    defect shape (a far end nobody constrained) and the only place it can be
    seen without a server. Behaviour is pinned by the integration suite.
    """

    def __init__(self) -> None:
        super().__init__(object())  # type: ignore[arg-type]
        self.queries: list[str] = []

    async def _run(self, query: str, /, **parameters: object) -> list[Any]:
        self.queries.append(query)
        if "UNWIND $entity_ids AS wanted" in query and "AliasRef" in query:
            # `resolve_entity_ids` indexes its answer by each requested id.
            return [
                {"wanted": str(entity_id), "canonical": None, "is_alias": False}
                for entity_id in cast("list[str]", parameters["entity_ids"])
            ]
        return []


class TestTraversalPinsTheTenantOnTheFarEnd:
    """Every node in a traversal pattern carries `tenant_id = $tenant_id`.

    Writes keep both ends of an edge in one tenant, so no path crosses tenants
    today. That is defence in depth: a far end left unconstrained is one bad
    row away from a leak, and nothing fails until the row exists.
    """

    @pytest.mark.parametrize("direction", ["out", "in", "both"])
    async def test_every_direction_constrains_the_far_end(self, direction):
        store = _RecordingStore()
        await store.get_relationships_for([uuid4()], uuid4(), direction=direction)

        (query,) = store.queries
        assert "(o:Entity)" in query
        assert "o.tenant_id = $tenant_id" in query

    async def test_alias_resolution_constrains_every_node_on_the_chain(self):
        """The chain's interior is unnamed, so it is checked through `nodes(p)`.

        The chain end and the outgoing check name their nodes and check them
        directly. A variable-length pattern has no name for its interior, so
        the path variable is the only handle on those nodes.
        """
        store = _RecordingStore()
        await store.resolve_entity_ids([uuid4()], uuid4())

        (query,) = store.queries
        assert "p = (a)-[:ALIAS_OF*1..]->(c:AliasRef)" in query
        assert "c.tenant_id = $tenant_id" in query
        assert "ALL(n IN nodes(p) WHERE n.tenant_id = $tenant_id)" in query
        # The outgoing checks name their far node `x` and constrain it.
        assert query.count("(x:AliasRef)") == 2
        assert query.count("x.tenant_id = $tenant_id") == 2

    async def test_the_alias_query_has_no_doubled_braces(self):
        """Every brace reaches Neo4j single and balanced.

        An f-string writes `{{` as one brace, and a plain string does not. The
        alias query mixes the two, and a `}}` in a plain fragment once reached
        the server as two braces. Neo4j refused it with a syntax error, which
        the assertions above could not see, because the tenant predicates were
        all present in the broken text.
        """
        store = _RecordingStore()
        await store.resolve_entity_ids([uuid4()], uuid4())

        (query,) = store.queries
        assert "{{" not in query
        assert "}}" not in query
        assert query.count("{") == query.count("}")
