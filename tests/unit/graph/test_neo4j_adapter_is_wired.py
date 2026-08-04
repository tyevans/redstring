"""Default-gate checks on the Neo4j adapter that need no Neo4j.

**Why this file exists.** The adapter's behavioural tests are
`integration`-marked, so `addopts` deselects all 106 of them and the whole
module goes unexecuted in the commit gate. A cosmic-ray run left a mutant in
`graph/adapters/neo4j.py` --

    -    if limit is not None and limit < 0:
    +    if not limit is not None and limit < 0:

-- and the full suite passed with it applied, 2026 tests green. Corrupt source
in an integration-only module is invisible to the gate, and that is a property
of the marking decision rather than of that mutant.

These tests run every part of the adapter that does not need a server, which
is more of it than the marking implied:

- **Argument validation.** Every guard clause raises before the first query,
  so an `_ExplodingDriver` proves both the rejection and that no I/O happened.
  This kills the escaped mutant above.
- **Encoding.** `_entity_row`/`_entity_from` and their relationship
  counterparts are pure functions, and they are where the adapter does its
  real work -- the JSON round trip that keeps nested `properties` intact.
- **Structure.** Importing the module at all means a syntax error, a bad
  name, or an import cycle fails the default run; signatures are checked
  against the port; and Cypher is checked not to have leaked out of the
  adapter.

What still needs the container is everything that requires Cypher to actually
execute: the queries themselves, the schema DDL, tenant isolation, traversal
and the query plans. That is
`tests/integration/graph/test_neo4j_store.py`. It is **not** duplicated here
with mocks, which would only assert what the mock was told to say.

See BACKLOG B10a for the combined coverage run that would report the
remaining gap honestly rather than leaving it to be inferred.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from uuid import uuid4

import pytest

from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.relationship import Relationship
from kg_builder.graph.adapters import neo4j as adapter
from kg_builder.ports.graph_store import GraphStore

SOURCE_ROOT = Path(adapter.__file__).parent.parent.parent

#: Cypher keywords distinctive enough that finding one outside the adapter
#: means a query has leaked. "MATCH" alone would be too common in prose.
CYPHER_MARKERS = ("MERGE (", "MATCH (", "DETACH DELETE", "UNWIND $", "CREATE INDEX")

#: The pre-rewrite Neo4j layer, condemned by the migration plan and deleted in
#: slices 7 and 9. Exempt because they are already on the way out, not because
#: the rule does not apply to them.
#:
#: **This list may only shrink.** Delete an entry in the commit that deletes
#: its module; `test_the_exemption_list_has_no_stale_entries` fails if an
#: entry names a file that is gone, and the main test fails if a *new* module
#: grows Cypher. Between them, the only way to add Cypher outside the adapter
#: is to edit this list, which is a visible decision in review.
LEGACY_CYPHER = frozenset(
    {
        "graph/client.py",
        "graph/queries.py",
        "services/neo4j.py",
        "services/neo4j_schema.py",
        "services/neo4j_queries.py",
        "services/neo4j_tenant.py",
        "services/consolidation/graph_similarity.py",
    }
)

#: The adapter itself, which is where Cypher is supposed to be.
ADAPTER = "graph/adapters/neo4j.py"


class TestTheAdapterImplementsThePort:
    """Structural conformance, checkable without a connection."""

    def test_it_satisfies_the_protocol_without_being_instantiated(self):
        """`runtime_checkable` checks methods, and a class has them already.

        Instantiating would need a driver; the point here is that the
        *shape* is right, which is what a caller type-checks against.
        """
        assert isinstance(adapter.Neo4jGraphStore, type)
        for name in _port_methods():
            assert hasattr(adapter.Neo4jGraphStore, name), f"missing {name}"

    def test_every_port_method_has_a_matching_signature(self):
        """A renamed keyword argument is a break no integration run reaches.

        The compliance suite calls these by keyword, so it would catch it --
        but only when Neo4j is up. This catches it in the gate.
        """
        for name in _port_methods():
            port_signature = inspect.signature(getattr(GraphStore, name))
            adapter_signature = inspect.signature(getattr(adapter.Neo4jGraphStore, name))
            assert list(adapter_signature.parameters) == list(port_signature.parameters), (
                f"{name} takes {list(adapter_signature.parameters)}, "
                f"the port declares {list(port_signature.parameters)}"
            )

    def test_it_implements_nothing_the_port_does_not_declare(self):
        """A public method the port has no name for is a leak of backend detail.

        `connect`, `close` and `ensure_schema` are lifecycle, which the port
        deliberately says nothing about; anything else means a caller could
        come to depend on Neo4j-shaped API.
        """
        lifecycle = {"connect", "close", "ensure_schema"}
        public = {
            name
            for name, _ in inspect.getmembers(adapter.Neo4jGraphStore, inspect.isfunction)
            if not name.startswith("_")
        }
        assert public - lifecycle == _port_methods()


class TestCypherNeverLeavesTheAdapter:
    """A hard requirement of the slice, and one nothing else checks.

    The port speaks domain types; a caller must never need to know a graph
    database is involved. This is a property of the whole source tree, so no
    test inside the adapter could state it.
    """

    def test_no_module_outside_the_adapter_contains_cypher(self):
        offenders = {
            relative: found
            for relative, found in _modules_containing_cypher().items()
            if relative != ADAPTER and relative not in LEGACY_CYPHER
        }
        assert not offenders, (
            f"Cypher found outside {ADAPTER}: {offenders}. The port speaks "
            f"domain types -- a caller must never need to know a graph "
            f"database is involved. Move the query into the adapter."
        )

    def test_the_exemption_list_has_no_stale_entries(self):
        """An exemption must die with the module it covers.

        Without this, deleting `services/neo4j_queries.py` in slice 9 would
        leave a permanent hole that the next stray query could fall through.
        """
        stale = {relative for relative in LEGACY_CYPHER if not (SOURCE_ROOT / relative).exists()}
        assert not stale, (
            f"LEGACY_CYPHER exempts modules that no longer exist: "
            f"{sorted(stale)}. Delete the entries."
        )

    def test_every_exempted_module_actually_contains_cypher(self):
        """An exemption for a module with no Cypher is a hole, not a waiver."""
        with_cypher = set(_modules_containing_cypher())
        pointless = LEGACY_CYPHER - with_cypher
        assert not pointless, (
            f"LEGACY_CYPHER exempts modules with no Cypher in them: "
            f"{sorted(pointless)}. Delete the entries."
        )

    def test_the_detector_would_find_cypher_if_there_were_any(self):
        """Guard the guard: a marker list that matches nothing passes vacuously."""
        text = Path(adapter.__file__).read_text()
        assert [marker for marker in CYPHER_MARKERS if marker in text] == list(CYPHER_MARKERS)


class TestSchemaStatementsAreWellFormed:
    """The DDL runs only on connect, so a typo in it needs a server to find."""

    def test_every_statement_is_idempotent(self):
        """Re-running `ensure_schema` on every start must not raise."""
        for statement in adapter._SCHEMA:
            assert "IF NOT EXISTS" in statement, statement

    def test_the_uniqueness_constraint_is_composite(self):
        """On `id` alone it would reject two tenants holding the same id."""
        constraint = next(s for s in adapter._SCHEMA if "CONSTRAINT" in s)
        assert "(e.tenant_id, e.id) IS UNIQUE" in constraint

    def test_every_entity_index_leads_with_the_tenant(self):
        """There is no cross-tenant read, so no useful index starts elsewhere."""
        for statement in adapter._SCHEMA:
            if "FOR (e:Entity)" in statement:
                assert "(e.tenant_id" in statement, statement

    def test_no_statement_uses_apoc(self):
        """Requiring a plugin narrows which Neo4j deployments can host this."""
        assert not any("apoc" in statement.lower() for statement in adapter._SCHEMA)


class TestNoUnparameterisedInterpolation:
    """`depth` is the only value formatted into a query string.

    Everything else is a bound parameter. This is asserted by parsing the
    module rather than by reading it, because "someone adds an f-string to a
    query" is exactly the change that looks harmless in review.
    """

    def test_depth_is_the_only_interpolated_query_value(self):
        tree = ast.parse(Path(adapter.__file__).read_text())
        interpolated = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literal = "".join(part.value for part in node.values if isinstance(part, ast.Constant))
            if not any(marker in literal for marker in CYPHER_MARKERS):
                continue
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    interpolated.add(ast.unparse(part.value))

        # EDGE and the direction patterns are module constants, not caller
        # input; `depth` is the only value that reaches here from an argument,
        # and `neighbors` proves it is an int before formatting it.
        assert interpolated <= {"EDGE", "depth", "_TENANT_SEEK", "_PATTERNS[direction]"}, (
            f"unexpected interpolation into Cypher: {sorted(interpolated)}"
        )


class _ExplodingDriver:
    """A driver that fails loudly if anything tries to use it.

    Lets the gate prove a guard clause fires *before* any I/O -- which is both
    the contract and the only reason these tests can run without Neo4j.
    """

    def session(self, **_: object) -> object:
        raise AssertionError("the adapter reached the database before validating")


def _offline_store() -> adapter.Neo4jGraphStore:
    return adapter.Neo4jGraphStore(_ExplodingDriver())  # type: ignore[arg-type]


class TestArgumentValidationNeedsNoDatabase:
    """The guard clauses, run in the default gate.

    These are the branches a cosmic-ray mutant hides in most comfortably, and
    they need no server: each raises before the first query. The mutant that
    escaped the gate --

        -    if limit is not None and limit < 0:
        +    if not limit is not None and limit < 0:

    -- is killed by `test_a_negative_limit_is_rejected` below. The compliance
    suite already asserts all of this against Neo4j; the point of repeating it
    here is that here it actually runs.
    """

    async def test_a_negative_limit_is_rejected(self):
        with pytest.raises(ValueError, match="limit"):
            await _offline_store().find_entities(uuid4(), limit=-1)

    async def test_a_zero_limit_is_not_rejected(self):
        """`limit=0` is a legal empty page, so the guard must be `< 0`.

        Without this, `<= 0` passes the negative case and silently breaks a
        caller paging with a computed limit.
        """
        with pytest.raises(AssertionError, match="reached the database"):
            await _offline_store().find_entities(uuid4(), limit=0)

    async def test_a_negative_depth_is_rejected(self):
        with pytest.raises(ValueError, match="depth"):
            await _offline_store().neighbors(uuid4(), uuid4(), depth=-1)

    async def test_depth_zero_answers_without_a_query(self):
        """`*1..0` is not a legal pattern, and the answer is [] anyway."""
        assert await _offline_store().neighbors(uuid4(), uuid4(), depth=0) == []

    @pytest.mark.parametrize("bad", ["1", "1 OR 1=1", 1.0, True, None])
    async def test_a_non_integer_depth_is_rejected(self, bad):
        """`depth` is the one value formatted into a query string.

        `True` is in the list because `bool` is an `int` subclass that would
        render as `True`, and `1.0` because a float would render as `1.0`.
        """
        with pytest.raises(TypeError, match="depth"):
            await _offline_store().neighbors(uuid4(), uuid4(), depth=bad)

    @pytest.mark.parametrize("direction", ["sideways", "OUT", "", None])
    async def test_an_unknown_direction_is_rejected(self, direction):
        with pytest.raises(ValueError, match="direction"):
            await _offline_store().get_relationships(uuid4(), uuid4(), direction=direction)

    @pytest.mark.parametrize("direction", ["out", "in", "both"])
    async def test_every_documented_direction_is_accepted(self, direction):
        """Guard the guard: a validator rejecting everything passes the above."""
        with pytest.raises(AssertionError, match="reached the database"):
            await _offline_store().get_relationships(uuid4(), uuid4(), direction=direction)

    async def test_empty_inputs_short_circuit_without_a_query(self):
        """An empty batch is a no-op, not a query with an empty UNWIND."""
        store = _offline_store()
        tenant = uuid4()
        assert await store.get_entities([], tenant) == []
        assert await store.find_by_blocking_keys([], tenant) == {}
        assert await store.get_relationships_for([], tenant) == []
        assert await store.upsert_entities([]) is None
        assert await store.upsert_relationships([]) is None


class TestEncodingIsPureAndReversible:
    """`Entity` <-> Neo4j row, tested in the gate.

    This is where the adapter does its real work -- a Neo4j property is a
    primitive or a homogeneous array, so nested dicts, mixed arrays, empty
    containers and big integers all have to survive a JSON round trip. None
    of that needs a database: `_entity_row` and `_entity_from` are pure.
    """

    @pytest.mark.parametrize(
        "properties",
        [
            {},
            {"nested": {"deep": {"deeper": [1, 2, {"x": None}]}}},
            {"empty_dict": {}, "empty_list": []},
            {"mixed": [1, "two", None, True, {"k": []}]},
            {"bool": True, "int": 1, "float": 1.0},
            {"big": 2**70},
            {"": "empty key", "unicode": "é中\U0001f600"},
        ],
    )
    def test_properties_survive_the_round_trip_exactly(self, properties):
        entity = _entity(properties=properties)
        decoded = adapter._entity_from(_as_node(adapter._entity_row(entity)))

        assert decoded.properties == properties
        # `==` on a dict does not tell True from 1, so types are checked too.
        assert [type(v) for v in decoded.properties.values()] == [
            type(v) for v in properties.values()
        ]

    @pytest.mark.parametrize(
        "blocking_keys", [None, frozenset(), frozenset({"A430"}), frozenset({"a", "b"})]
    )
    def test_blocking_keys_distinguish_absent_from_empty(self, blocking_keys):
        """Setting a Neo4j property to null removes it, so `None` and
        `frozenset()` collapse together unless encoded apart."""
        entity = _entity(blocking_keys=blocking_keys)
        decoded = adapter._entity_from(_as_node(adapter._entity_row(entity)))

        assert decoded.blocking_keys == blocking_keys

    def test_a_whole_entity_round_trips(self):
        entity = _entity(
            description="",
            source_text="text",
            external_ids={"wikidata": "Q7259"},
            properties={"a": [1, {"b": None}]},
            blocking_keys=frozenset({"A430"}),
        )
        assert adapter._entity_from(_as_node(adapter._entity_row(entity))) == entity

    def test_an_entity_with_every_optional_field_unset_round_trips(self):
        entity = _entity()
        assert entity.description is None
        assert adapter._entity_from(_as_node(adapter._entity_row(entity))) == entity

    def test_a_relationship_round_trips(self):
        relationship = Relationship(
            id=uuid4(),
            tenant_id=uuid4(),
            source_entity_id=uuid4(),
            target_entity_id=uuid4(),
            relationship_type="knows",
            properties={"since": 1999, "nested": {"k": [None, True]}},
            confidence=0.5,
        )
        row = adapter._relationship_row(relationship)
        assert adapter._relationship_from(_as_node(row)) == relationship

    def test_the_row_holds_only_values_neo4j_can_store(self):
        """The whole reason the JSON columns exist.

        A row value must be a primitive or a homogeneous array of primitives;
        anything else is rejected by the driver at write time, which is a
        failure only an integration run would see.
        """
        row = adapter._entity_row(
            _entity(properties={"nested": {"a": [1, "two"]}}, blocking_keys=frozenset({"k"}))
        )
        for key, value in row.items():
            if isinstance(value, list):
                assert len({type(item) for item in value}) <= 1, f"{key} is heterogeneous"
                assert all(isinstance(item, str) for item in value), key
            else:
                assert value is None or isinstance(value, str | int | float | bool), (
                    f"{key} is a {type(value).__name__}, which Neo4j cannot store"
                )


def _entity(**overrides: object) -> Entity:
    fields: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "extraction_method": ExtractionMethod.MANUAL,
        "confidence": 1.0,
    }
    fields.update(overrides)
    return Entity(**fields)  # type: ignore[arg-type]


def _as_node(row: dict[str, object]) -> dict[str, object]:
    """A stand-in for a `neo4j.graph.Node`.

    Neo4j drops properties written as null, so a decoder must cope with the
    key being *absent* rather than present-and-None. Stripping them here is
    what makes this a faithful stand-in rather than a friendlier dictionary --
    without it, `.get()` would be tested against a shape the database never
    produces.
    """
    return {key: value for key, value in row.items() if value is not None}


def _modules_containing_cypher() -> dict[str, list[str]]:
    """Every module under `src/kg_builder` holding a Cypher marker."""
    found = {}
    for path in SOURCE_ROOT.rglob("*.py"):
        markers = [marker for marker in CYPHER_MARKERS if marker in path.read_text()]
        if markers:
            found[str(path.relative_to(SOURCE_ROOT))] = markers
    return found


def _port_methods() -> set[str]:
    """Every method the `GraphStore` protocol declares."""
    return {
        name
        for name, _ in inspect.getmembers(GraphStore, inspect.isfunction)
        if not name.startswith("_")
    }
