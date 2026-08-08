"""The `VectorStore` port: embeddings and similarity search, in domain terms.

Like `GraphStore`, a `VectorStore` is a **projection**. The event log is the
authority; every write here is idempotent because projection handlers replay.

Every method is tenant-scoped. There is no cross-tenant read, ever.

## Score

`search` returns `VectorMatch`es whose `score` is **cosine similarity mapped
onto 0..1 by `(1 + cosine) / 2`, higher meaning more similar** -- `1.0` for
identical direction, `0.5` for orthogonal, `0.0` for opposite. The scale is
defined once, on `redstring.domain.vector`, and is the same for every
adapter. It is stated here as well because "score" is ambiguous across vector
databases: several report a *distance*, where lower is better, and an adapter
that inverted the sense would return plausible nonsense rather than an error.

`min_score` is read on that same scale. Note that `0.0` is *not* a no-op
filter -- it excludes exactly the antipodal vectors -- and that a threshold
tuned against a raw-cosine store must be halved and shifted to mean the same
thing here.

## Dimension is configuration, and mismatches are rejected

A store is constructed with the dimension of the embedding model that feeds
it, exposed as `dimension`. `upsert`, `upsert_many` and `search` all raise
`DimensionMismatchError` for a vector of any other length, in **every**
adapter -- an in-memory reference that accepted what a real store refuses
would let the compliance suite pass on data that cannot be persisted, and the
adapters would diverge silently.

**Changing embedding model means a new store, not an in-place change.** Two
models' vectors in one collection are not comparable even when their
dimensions happen to agree, and nothing in this port can detect that. Point
the new store at a new table/collection and backfill.

## Precision: single, not double

An adapter may store components at **float32**. pgvector's `vector` type is
float4, and so are most managed vector databases -- doubling the storage of
every embedding to preserve digits an embedding model never produced would be
a poor trade. A caller must therefore not rely on a float64 value surviving
`upsert` then `get` bit-for-bit, and must not compare a retrieved vector to
the written one with `==` unless the components were float32-representable to
begin with.

## Vectors with a zero norm are rejected

Cosine is undefined at the origin. Backends express that incompatibly --
pgvector yields NaN, which sorts unpredictably and would make ranking depend
on the query plan -- so such a vector raises `ValueError` on the way in and on
the way into `search`, rather than being stored and producing a meaningless
score later.

**The test is the norm, and it is taken in float32**, which is a slightly
wider rejection than "every component is zero". Since a stored vector is
float32 (above), a component below about `1e-19` squares to zero there: a
vector of such components has non-zero components, a non-zero float64 norm,
and no direction any backend can compute. Rejecting on the components alone
let the two adapters disagree about exactly that band, which is what
`domain.vector.has_zero_norm` is for. No real embedding is anywhere near it.

## Metadata, and what `entity_types` filters on

`metadata` is opaque to the store with exactly one exception: the key
`entity_type`. `search(entity_types=[...])` keeps a record whose
`metadata["entity_type"]` is one of those values. It is a convention rather
than a field because the alternative -- a typed column for every attribute a
caller might filter on -- puts the caller's schema into the port.

Because it is a convention over free-form JSON, the key can hold **anything**,
and the port has to say what a non-string means rather than leaving each
adapter to find out. It means *not a type name*: a record whose
`entity_type` is absent, `None`, a number, a list or an object matches no type
filter, and asking never raises. Adapters must not decide this for themselves
-- the natural implementations disagree loudly, a `text` column simply cannot
hold a list while a Python `in` against a `set` raises `TypeError` -- so
`entity_type_of` below is the single reading of the convention and every
adapter calls it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from redstring.ports.lifecycle import AsyncClosable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.ids import EntityId, TenantId
    from redstring.domain.vector import VectorMatch, VectorRecord

#: The metadata key `search(entity_types=...)` filters on. See the docstring.
ENTITY_TYPE_KEY = "entity_type"


def entity_type_of(metadata: dict[str, Any]) -> str | None:
    """The entity type a record answers to, or `None` if it has none.

    The single reading of the `entity_type` convention, deliberately living
    with the port rather than in either adapter. `None` unless the metadata
    carries a **string** under `ENTITY_TYPE_KEY`; see the module docstring for
    why a non-string is "no type" rather than an error or a coercion.

    This is not a helper that happened to be shared. The two adapters wrote
    their own readings and diverged: pgvector nulled every non-string, because
    its column is `text`, while the in-memory store compared the raw value
    against a `set` and raised `TypeError: unhashable type: 'list'` for a
    stored `{"entity_type": ["person"]}`. Same call, two outcomes. A rule that
    lives in one function cannot be half-applied.
    """
    value = metadata.get(ENTITY_TYPE_KEY)
    # `str` and not `isinstance(value, str) or ...`: coercing `7` to `"7"`
    # would invent a match for `entity_types=["7"]` that no caller asked for.
    return value if isinstance(value, str) else None


@runtime_checkable
class VectorWriter(AsyncClosable, Protocol):
    """Putting embeddings in. What a projection needs, and all of it."""

    @property
    def dimension(self) -> int:
        """The vector length this store accepts. Fixed at construction."""
        ...

    async def upsert(
        self,
        entity_id: EntityId,
        vector: Sequence[float],
        tenant_id: TenantId,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or replace one embedding, keyed by `(tenant_id, entity_id)`.

        Idempotent, last-write-wins. `metadata` replaces whatever was there
        wholesale rather than merging: a merge would make replay
        order-dependent, since a key removed by a later event would survive.
        `None` means the empty mapping.

        Raises `DimensionMismatchError` if `len(vector) != dimension`, and
        `ValueError` for a vector whose float32 norm is zero.
        """
        ...

    async def upsert_many(self, items: Sequence[VectorRecord]) -> None:
        """Upsert many records. Equivalent to `upsert` per element.

        Records may belong to different tenants; each is keyed by its own
        `tenant_id`. Two records with the same `(tenant_id, entity_id)` in one
        call leave one row holding the later value -- the same last-write-wins
        rule that applies across calls.

        Embedding batches are thousands of rows, so an adapter over a database
        must send this as one statement, not a loop.
        """
        ...


@runtime_checkable
class VectorReader(AsyncClosable, Protocol):
    """Getting embeddings back, by id or by proximity.

    `get` and `search` stay together rather than splitting into a lookup and a
    query protocol, for the reason `ports/graph_store.py` keeps
    `RelationshipStore` whole: the split is by *who calls what*, not by
    symmetry. `CandidateFinder` reads the subject's own vector and then asks
    what is near it, in that order, and neither half is useful to it alone.
    """

    @property
    def dimension(self) -> int:
        """The vector length this store accepts. Fixed at construction."""
        ...

    async def get(self, entity_id: EntityId, tenant_id: TenantId) -> VectorRecord | None:
        """Return the stored record, or `None` if this tenant has no such id.

        An unknown id is not an error. The returned record is the caller's:
        mutating it cannot change stored state.
        """
        ...

    async def search(
        self,
        vector: Sequence[float],
        tenant_id: TenantId,
        *,
        k: int = 10,
        entity_types: Sequence[str] | None = None,
        min_score: float | None = None,
    ) -> list[VectorMatch]:
        """Return this tenant's `k` nearest records to `vector`, best first.

        Never more than `k` results; fewer only when the tenant holds fewer
        matching records. `k=0` yields `[]`; a negative `k` raises
        `ValueError`.

        `entity_types` restricts to records whose `metadata["entity_type"]` is
        in the sequence; `None` means no filter and `[]` means nothing
        matches. `min_score` drops results scoring strictly below it.

        **Filtering happens before `k` is applied.** A store that took the `k`
        nearest and *then* filtered would return fewer than `k` results while
        matching records existed further down the ranking -- correct-looking
        and wrong, and indistinguishable from a small corpus. This is the
        single most important sentence in this port for an adapter over an
        approximate index.

        Ties in score are broken by ascending `entity_id` compared as its
        canonical lowercase hyphenated string, so the result is a total order
        and two adapters agree on it. Without that, `k` cutting through a tie
        would return different members on different backends.

        Raises `DimensionMismatchError` if `len(vector) != dimension`, and
        `ValueError` for a vector whose float32 norm is zero.
        """
        ...


@runtime_checkable
class VectorPurge(AsyncClosable, Protocol):
    """Removing embeddings, one at a time or a whole tenant's worth.

    The only capability here that declares no `dimension`, and the line is the
    port's own: `upsert`, `upsert_many` and `search` are the three methods
    whose contract is stated in terms of it, because they are the three that
    accept or return a vector. Both methods below name ids and nothing else,
    so a caller who can only delete has no vector length to agree about.
    """

    async def delete(self, entity_id: EntityId, tenant_id: TenantId) -> bool:
        """Delete one record; return whether it existed.

        Idempotent: deleting an absent id returns `False` rather than raising,
        so replaying a delete is not an error.
        """
        ...

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        """Delete every record of `tenant_id`; return how many were removed.

        No other tenant is touched.
        """
        ...


@runtime_checkable
class VectorStore(VectorWriter, VectorReader, VectorPurge, Protocol):
    """Storage for entity embeddings, with similarity search.

    The whole port, composed from the three capabilities above. Adapters
    implement this and `tests/compliance/vector_store.py` runs against it.

    **Collaborators should not.** Every first-party consumer is covered
    exactly by one capability, which is what decided where the lines fell --
    `VectorProjection` writes, `Retriever` and `CandidateFinder` read, and
    nothing here purges. `VectorProjection` calls `upsert_many` and nothing
    else, so it is a `StoreProjection[VectorWriter]`, the same narrowing
    `ChunkProjection` got for the same reason.

    `dimension` is on two capabilities rather than one or three, and the rule
    is the port's own: the methods that accept or return a vector -- `upsert`,
    `upsert_many`, `search` -- are exactly the methods whose contract says
    `DimensionMismatchError`, so writing and reading each declare the length
    they agree about and `VectorPurge` does not. `ports/cache.py` records the
    same shape reached the other way round, where `close` belongs to *both*
    halves; the lesson both times is that the answer comes from what the
    methods say, not from a preference for the smallest protocol.

    The reasoning that was tried and dropped: a `VectorSearcher` holding
    `search` alone, on the symmetry of `ChunkStore`'s `LexicalCandidateSource`.
    It does not carry over. `lexical_candidates` is separable because BM25
    ranking genuinely needs recall and statistics from anywhere, chunk store or
    not; `search` here has no such caller, and both of its consumers reach for
    `get` or `dimension` in the same breath. A capability nobody can request is
    the inert-code shape wearing a Protocol.

    Splitting changes nothing for an adapter. `VectorStore` still names every
    method through its bases, `runtime_checkable` still works, and
    `tests/unit/vector/test_compliance_coverage.py` still finds `get` and
    `search`, because `inspect.getmembers` and `typing.get_type_hints` both
    walk the MRO. See `ports/graph_store.py`, which made this move first.
    """
