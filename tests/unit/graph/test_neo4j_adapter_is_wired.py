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
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from redstring.domain.alias import Alias
from redstring.domain.entity import Entity
from redstring.domain.exceptions import MissingEntityError
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.graph.adapters import neo4j as adapter
from redstring.ports.graph_store import GraphStore

SOURCE_ROOT = Path(adapter.__file__).parent.parent.parent

#: Cypher keywords distinctive enough that finding one outside the adapter
#: means a query has leaked. "MATCH" alone would be too common in prose.
#:
#: `"CREATE INDEX"` was here and is not any more: slice 5's pgvector adapter
#: creates a *SQL* index and tripped this test, which is a false positive
#: rather than a leak. `"IF NOT EXISTS FOR "` is the Cypher-only form of the
#: same DDL -- `CREATE INDEX ... IF NOT EXISTS FOR (e:Entity) ON (...)` -- and
#: `"CREATE CONSTRAINT"` has no SQL spelling with that syntax either.
CYPHER_MARKERS = (
    "MERGE (",
    "MATCH (",
    "DETACH DELETE",
    "UNWIND $",
    "IF NOT EXISTS FOR ",
    "CREATE CONSTRAINT",
)

#: **Empty, and staying that way.** This exempted the pre-rewrite Neo4j layer
#: while it was on its way out; slices 7 and 9 deleted every module in it, and
#: as of slice 9 the adapter is the only module in the library containing
#: Cypher -- which is the property the port was for.
#:
#: The list is kept rather than removed along with its last entry, because it
#: is the seam the rule is enforced at: with it empty,
#: `test_no_module_outside_the_adapter_contains_cypher` admits no exceptions
#: at all, and adding one means adding a name here, which is a visible
#: decision in review rather than a query that quietly appeared in a service.
LEGACY_CYPHER: frozenset[str] = frozenset()

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

        `connect` and `ensure_schema` are lifecycle the port deliberately says
        nothing about -- one takes a URI and the other is a Neo4j DDL concern;
        anything else means a caller could come to depend on Neo4j-shaped API.

        `close` was in this set until ADR 0028 and is not any more, because
        the port now declares it (with `__aenter__`/`__aexit__`, through
        `AsyncClosable`), so it arrives via `_port_methods()` instead of being
        excused. That is the assertion doing its job in the direction nobody
        writes it for: exempting a name the port has since adopted would leave
        the exemption matching something it no longer needs to.
        """
        lifecycle = {"connect", "ensure_schema"}
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

        # The label and edge-type constants and the direction patterns are
        # module constants, not caller input; `depth` is the only value that
        # reaches here from an argument, and `neighbors` proves it is an int
        # before formatting it.
        assert interpolated <= {
            "EDGE",
            "ALIAS_NODE",
            "ALIAS_EDGE",
            "KEY_NODE",
            "KEY_EDGE",
            "depth",
            "_TENANT_SEEK",
            "_PATTERNS[direction]",
        }, f"unexpected interpolation into Cypher: {sorted(interpolated)}"


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


class _ScriptedStore(adapter.Neo4jGraphStore):
    """A store whose `_run` answers from a script instead of a database.

    Used only to drive `upsert_relationships` through the state its two
    round trips make possible: the endpoint check passes, and by the time the
    write runs the endpoint is gone. Single-threaded tests cannot *produce*
    that interleaving, but they can put the write path into the state it
    leaves behind -- a write that matched fewer rows than it was given.
    """

    def __init__(self, present: set[tuple[str, str]], *, written: int) -> None:
        super().__init__(object())  # type: ignore[arg-type]
        self._present = present
        self._written = written
        self.queries: list[str] = []

    async def _run(self, query: str, /, **parameters: object) -> list[Any]:
        self.queries.append(query)
        if "RETURN e.tenant_id AS tenant_id" in query:
            return [
                {"tenant_id": tenant_id, "id": entity_id} for tenant_id, entity_id in self._present
            ]
        rows = cast("list[dict[str, Any]]", parameters["rows"])
        # Both keys, because that is what the write query returns: a
        # relationship is identified by (tenant_id, id), not by id.
        return [{"tenant_id": row["tenant_id"], "id": row["id"]} for row in rows[: self._written]]


def _edge(tenant: UUID, source: UUID, target: UUID, edge_id: UUID | None = None) -> Relationship:
    return Relationship(
        id=edge_id or uuid4(),
        tenant_id=tenant,
        source_entity_id=source,
        target_entity_id=target,
        relationship_type="knows",
        confidence=1.0,
    )


class TestTheWriteReportsWhatItFailedToWrite:
    """`upsert_relationships` is write-or-raise, across two round trips.

    The check and the write are separate implicit transactions, so an endpoint
    deleted between them leaves the write's `MATCH` matching nothing -- and
    Cypher drops a non-matching row *silently*. Without the write reporting how
    many rows it matched, the caller is told a batch succeeded that was never
    written.
    """

    async def test_a_short_write_raises_rather_than_reporting_success(self):
        tenant, source, target = uuid4(), uuid4(), uuid4()
        # The check sees both endpoints; the write matches neither.
        store = _ScriptedStore({(str(tenant), str(source)), (str(tenant), str(target))}, written=0)

        with pytest.raises(MissingEntityError):
            await store.upsert_relationships([_edge(tenant, source, target)])

    async def test_a_partly_short_write_raises(self):
        """One row of a batch lost is still a lost write, not a success."""
        tenant, a, b, c = uuid4(), uuid4(), uuid4(), uuid4()
        store = _ScriptedStore(
            {(str(tenant), str(x)) for x in (a, b, c)},
            written=1,
        )

        with pytest.raises(MissingEntityError):
            await store.upsert_relationships([_edge(tenant, a, b), _edge(tenant, b, c)])

    async def test_one_tenants_write_does_not_vouch_for_anothers(self):
        """A relationship is `(tenant_id, id)`, so the check must be too.

        Every other test here draws ids from `uuid4()`, which means the two
        tenants never collide and a check keyed on `id` alone agrees with one
        keyed on the pair for every input in the suite -- the third row of
        CLAUDE.md's table. Pinning the id makes them disagree: tenant A's row
        writes, tenant B's identically-numbered row is dropped, and an
        id-keyed check finds B's id in `written` and reports success for a
        write that never happened.
        """
        shared_id = uuid4()
        tenant_a, tenant_b = uuid4(), uuid4()
        source, target = uuid4(), uuid4()
        store = _ScriptedStore(
            {(str(t), str(e)) for t in (tenant_a, tenant_b) for e in (source, target)},
            # A writes, B does not.
            written=1,
        )

        with pytest.raises(MissingEntityError):
            await store.upsert_relationships(
                [
                    _edge(tenant_a, source, target, edge_id=shared_id),
                    _edge(tenant_b, source, target, edge_id=shared_id),
                ]
            )

    async def test_a_complete_write_does_not_raise(self):
        """Guard the guard: a check that always raises would pass the above."""
        tenant, source, target = uuid4(), uuid4(), uuid4()
        store = _ScriptedStore({(str(tenant), str(source)), (str(tenant), str(target))}, written=1)

        assert await store.upsert_relationships([_edge(tenant, source, target)]) is None
        assert len(store.queries) == 2


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

    @pytest.mark.parametrize(
        "merged_at",
        [
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 6, 1, 12, 30, 45, 123456, tzinfo=UTC),
            datetime(2026, 6, 1, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            datetime(2026, 6, 1, tzinfo=timezone(timedelta(hours=-8))),
        ],
    )
    def test_an_alias_round_trips_with_its_offset_intact(self, merged_at):
        """`merged_at` is ISO text, not a native Neo4j `DateTime`.

        The driver's `neo4j.time.DateTime` converts back to a `datetime` whose
        `tzinfo` is a fixed offset even when the value was written as UTC, and
        `Alias` is compared for equality by the port -- so a native column
        would fail for `+05:30` and pass for `+00:00`, which is the shape of
        bug that ships. The parametrisation is what separates them.
        """
        alias = Alias(
            id=uuid4(),
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_entity_id=uuid4(),
            alias_name="A. Lovelace",
            alias_normalized_name="a. lovelace",
            merged_at=merged_at,
            merge_reason="jaro-winkler 0.94",
        )
        row = adapter._alias_row(alias)
        decoded = adapter._alias_from(_as_node(row))

        assert decoded == alias
        assert decoded.merged_at.utcoffset() == merged_at.utcoffset()

    def test_an_alias_with_no_name_round_trips(self):
        """The fold writes these: `EntitiesMerged` carries ids, not names, so
        an entity whose extraction has not been folded yet has none to find.
        Neo4j drops a null property, so the decoder sees the key *absent*."""
        alias = Alias(
            id=uuid4(),
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_entity_id=uuid4(),
            merged_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert adapter._alias_from(_as_node(adapter._alias_row(alias))) == alias

    def test_the_alias_row_holds_only_values_neo4j_can_store(self):
        alias = Alias(
            id=uuid4(),
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_entity_id=uuid4(),
            merged_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for key, value in adapter._alias_row(alias).items():
            assert value is None or isinstance(value, str | int | float | bool), (
                f"{key} is a {type(value).__name__}, which Neo4j cannot store"
            )

    @pytest.mark.parametrize(
        ("blocking_keys", "creates_edges"),
        [
            (None, False),
            (frozenset(), False),
            (frozenset({"A430"}), True),
            (frozenset({"A430", "person:ad"}), True),
        ],
    )
    def test_only_rows_with_keys_reach_the_edge_write(self, blocking_keys, creates_edges):
        """`None` and `frozenset()` differ to the decoder and not here.

        The property keeps "no keys known" and "known to have none" apart,
        because an edge set cannot express that difference -- but neither
        creates an edge, so the filter must collapse them. Parametrised over
        both because an implementation testing `is not None` passes for one
        and fails for the other.
        """
        rows = [adapter._entity_row(_entity(blocking_keys=blocking_keys))]
        assert bool(adapter.rows_carrying_keys(rows)) is creates_edges

    def test_a_mixed_batch_keeps_only_the_keyed_rows(self):
        keyed = _entity(blocking_keys=frozenset({"A430"}))
        rows = [
            adapter._entity_row(_entity(blocking_keys=None)),
            adapter._entity_row(keyed),
            adapter._entity_row(_entity(blocking_keys=frozenset())),
        ]
        assert [row["id"] for row in adapter.rows_carrying_keys(rows)] == [str(keyed.id)]

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
        # Offset-bearing, non-midnight and non-whole-minute on purpose: the
        # row encoder writes `observed_at` as ISO text precisely because the
        # driver's `DateTime` round-trip is lossy for offsets, and a midnight
        # UTC value would agree with the lossy encoding too.
        "provenance": Provenance(
            observed_at=datetime(2026, 3, 1, 14, 45, 30, tzinfo=timezone(timedelta(hours=-5))),
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
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
    """Every module under `src/redstring` holding a Cypher marker."""
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
