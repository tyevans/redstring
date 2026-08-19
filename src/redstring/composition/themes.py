"""What a corpus is *about*, one report per cluster of the graph.

The thematic read surface ADR 0042 decided on, and the one function behind it.
`Retriever` ranks individual entities against a query string; this answers a
question no query string asks -- "what is in here" -- by partitioning the
tenant's graph and asking the model to describe each part.

It joins `graph`, `llm` and `chunks`, three siblings in the layer contract
that may not import each other, so no lower layer can hold all three. That is
the admission test `pyproject.toml` sets for this layer, and it is the same
argument `retrieval.py` passes for a different triple.

It holds the *narrowest* ports it uses: `EntityReader`, `RelationshipStore`
and `ChunkReader`, never the composed `GraphStore` or `ChunkStore`. A theme
summariser that could wipe a tenant is a fact worth keeping out of a
signature, and this one reads and nothing else.

## Nothing is written, and that is the decision rather than an omission

No event, no store, no `CommunityId`. A community is a fact about a partition
of a graph that the next `DocumentExtracted` replaces, so a stored one is
stale before it is read and there is no invalidation smaller than "recompute
everything" -- ADR 0042 argues it at length, and B147 holds the harder
question a caller wanting stable themes across calls actually needs answered.
The reports come back to the caller, who keeps them for as long as they are
useful. Same shape as `PipelineResult`: computed, returned, written nowhere.

## The topology arrives through the capabilities that already exist

`find_entities` pages over a total order with a resumable cursor and
`get_relationships_for` takes a batch of ids, so the whole tenant arrives
without widening `GraphStore` (ADR 0016, and B148 for what would justify a
bulk read).

**An edge spanning two pages comes back twice**, once per page whose ids
touch it, so edges are deduplicated by `Relationship.id` before they reach
the clustering. Without that, every cross-page edge would be weighted double
and the partition would depend on `page_size` -- a tuning knob silently
deciding the answer. A corpus that fits in one page cannot show this, which
is why the test for it uses more entities than the page size.

**An edge naming an entity no page returned is dropped and counted.** The
store is a live projection: a write landing between two pages can produce an
edge whose other endpoint was in a page already read, or in one not read yet.
`detect_communities` refuses an edge naming an unknown node, correctly -- so
the choice here is between failing a whole tenant's report on ordinary
concurrent writing and skipping the edge. It is the same choice
`Retriever` makes for a dangling vector match, for the same reason, and
`ThemeReport.dangling_edges` is what keeps it from being silent.

## Every edge weighs one

There is no corpus-level edge weight in this library yet (B144), and
`Relationship.confidence` is not one: it is the model's confidence in a single
assertion, so weighting by it would let one hesitantly-stated edge count for a
fraction of a confidently-stated one when both are the same claim, and a
confidence of `0.0` -- legal on the type -- would be refused outright by
`detect_communities`, turning one cautious extraction into a raised exception
for the whole tenant. So each surviving edge contributes `1.0`, and parallel
edges between one pair sum, which is `detect_communities`'s documented
treatment of duplicates and is the right one here: two documents asserting a
relationship is more evidence for it.

## Small communities do not get a model call

`min_size` defaults to 2. A singleton's report would be the entity's own name
and description restated at the price of a call, and a partition of a sparse
graph is mostly singletons -- an entity mentioned in one document and linked
to nothing is its own community by construction. They are counted
(`ThemeReport.too_small`) rather than hidden, because "this corpus is 900
disconnected entities" is the single most useful thing the count can say.

## A failed report aborts, unless you say otherwise

Default `skip_failed=False`: an `LlmProviderError` propagates and the run
produces nothing. That is the same default `build_graph` takes for
`skip_failed_chunks`, and for the same reason -- a partial answer that does
not announce itself is worse than no answer, and here it is worse still,
because a missing theme looks exactly like a corpus that does not contain it.
With `skip_failed=True` the failure is skipped and counted in
`ThemeReport.failed`.

## What the model is shown

Always the members: name, type, and description where there is one. With a
`ChunkReader` supplied, also the stored passages those members were extracted
from -- GraphRAG's fast pipeline summarises a community from its source text
units rather than from entity descriptions, and ADR 0023 is what makes that
variant available here. It is optional because a caller may hold no chunk
corpus; B117's description-quality problem is exactly why it is worth
supplying one.

Both lists are bounded. `max_members_shown` caps membership at a size that
fits a prompt, and **the members shown are the highest-degree ones**, ties
broken by id ascending -- degree within the community, which is the only
ranking available that says anything about how central a member is to the
theme being described. Id order would be a hash. The reported `Theme.members`
is the *whole* community regardless; only the prompt is capped, and
`Theme.members_shown` says how much of it the summary actually saw.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field

from redstring.domain.community import detect_communities
from redstring.domain.exceptions import LlmProviderError
from redstring.domain.limiter import CallLimiter
from redstring.temporal.query import CursorStalledError

if TYPE_CHECKING:
    from redstring.domain.chunk import StoredChunk
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, RelationshipId, TenantId
    from redstring.domain.relationship import Relationship
    from redstring.ports.chunk_store import ChunkReader
    from redstring.ports.graph_store import EntityReader, RelationshipStore
    from redstring.ports.llm_provider import LlmProvider

#: Entities per `find_entities` round trip. A tuning knob and not a limit on
#: the answer -- the scan pages until the tenant is exhausted.
DEFAULT_PAGE_SIZE: Final = 500

#: How many pages before the scan gives up. The exit condition is a short page,
#: which is adapter-supplied data, and an unbounded loop over a cursor that
#: fails to advance hangs rather than fails -- which in CI reads as
#: infrastructure trouble and gets retried instead of investigated. Same bound
#: and same `CursorStalledError` as `TemporalQuery`, deliberately: two paged
#: scans over one port giving two different diagnoses would be a second
#: mechanism to keep in step with the first.
MAX_PAGES: Final = 10_000

DEFAULT_SYSTEM_PROMPT: Final = (
    "You are summarising one cluster of a knowledge graph. You are given the "
    "entities in the cluster and, where available, passages of the documents "
    "they were extracted from. Write a short title naming what this cluster is "
    "about, and a summary of what connects its members. Describe only what the "
    "material supports; do not speculate about entities that are not listed."
)


class CommunityReport(BaseModel):
    """What the model is asked for, per community.

    Two fields on purpose. Every field here is a claim the model must ground
    in the material, and an ungrounded one is not merely absent but plausible
    and wrong -- so the schema carries the two a caller cannot assemble
    itself. A "key entities" list would be the third obvious field and is
    deliberately not here: the membership is already known exactly, and asking
    the model to restate a subset of it invites a name that is not in the
    cluster at all. `Theme.members` is the answer to that question, and it is
    not a model output.
    """

    title: str = Field(description="A short noun phrase naming what this cluster is about.")
    summary: str = Field(description="What connects the members of this cluster.")


@dataclass(frozen=True, slots=True)
class Theme:
    """One community of the graph, as the model described it."""

    title: str
    summary: str
    #: Every member of the community, ascending by id -- not only the ones the
    #: prompt showed. Whether the summary is about all of them is what
    #: `members_shown` answers.
    members: tuple[EntityId, ...]
    #: How many members the prompt actually carried. Equal to `len(members)`
    #: unless `max_members_shown` bit, and the field exists so that a summary
    #: written from 25 of 4,000 entities does not read like one written from
    #: all of them.
    members_shown: int
    #: How many stored passages the prompt carried. Zero when no `ChunkReader`
    #: was supplied, which is the default -- and zero *with* one means the
    #: members have no chunks, which is what an entity graph built by
    #: `build_graph` without a chunk store looks like.
    passages_shown: int


@dataclass(frozen=True, slots=True)
class ThemeReport:
    """What one `summarize_themes` call found, and what it did not summarise."""

    #: Best first, where "best" is largest: descending by member count, ties
    #: broken by first member id ascending so two runs over one graph return
    #: the identical order. The clustering is deterministic (ADR 0042) and
    #: this keeps the report so.
    themes: tuple[Theme, ...]
    #: Communities the partition contained, before `min_size` or any failure.
    communities: int
    #: Communities below `min_size`, which cost no model call. On a sparse
    #: graph this is most of them, and it is the number that says so.
    too_small: int
    #: Communities whose model call failed and were skipped. Non-zero only
    #: with `skip_failed`.
    failed: int
    #: Edges dropped because an endpoint was not among the entities scanned.
    #: Ordinary under concurrent writes; a large number means something else.
    dangling_edges: int


async def summarize_themes(
    tenant_id: TenantId,
    *,
    graph: EntityReader,
    relationships: RelationshipStore,
    provider: LlmProvider,
    chunks: ChunkReader | None = None,
    resolution: float = 1.0,
    min_size: int = 2,
    max_members_shown: int = 25,
    max_passages_shown: int = 10,
    page_size: int = DEFAULT_PAGE_SIZE,
    concurrency: int = 1,
    limiter: CallLimiter | None = None,
    skip_failed: bool = False,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> ThemeReport:
    """Cluster this tenant's graph and describe each cluster.

    Reads the whole tenant, partitions it, and pays for one model call per
    community above `min_size`. The cost scales with the corpus's *structure*
    rather than its length, which is the reason this exists at all.

    **Writes nothing anywhere.** See the module docstring and ADR 0042.

    Args:
        tenant_id: The only tenant read. Every call this function makes is
            scoped to it, so no cross-tenant edge can enter the partition and
            `detect_communities` never sees a `TenantId` at all.
        graph: Where entities are read from. `find_entities` is the only
            method called.
        relationships: Where edges are read from. `get_relationships_for` is
            the only method called.
        provider: What writes each report.
        chunks: Where source passages come from, or `None` to summarise from
            entity names, types and descriptions alone. `get_by_entity` is
            the only method called.
        resolution: Passed to `detect_communities`. Larger yields more,
            smaller communities.
        min_size: Communities smaller than this get no model call and no
            theme. Defaults to 2; see the module docstring for why a
            singleton is not worth a call.
        max_members_shown: How many members of a community the prompt
            carries, highest degree first.
        max_passages_shown: How many stored passages the prompt carries.
            Ignored when `chunks` is `None`.
        page_size: Entities per round trip while scanning.
        concurrency: How many model calls may be in flight, when no `limiter`
            is given. Ignored when one is.
        limiter: The endpoint ceiling, shared with whatever else is calling
            the same server. Pass the one `build_graph` was given rather than
            letting two ceilings be no ceiling -- see
            `redstring.domain.limiter`.
        skip_failed: Continue past a community whose model call failed,
            counting it in `ThemeReport.failed`. Off by default.
        system_prompt: Instructions for the model.

    Returns:
        A `ThemeReport`. `themes` may be empty -- an empty tenant, a tenant of
        singletons, or every call failing under `skip_failed` all produce one,
        and the counters are how a caller tells them apart.

    Raises:
        ValueError: `min_size`, `max_members_shown`, `max_passages_shown` or
            `page_size` is below its floor, or `resolution` is negative.
        CursorStalledError: The entity scan did not finish in `MAX_PAGES`.
        LlmProviderError: A model call failed and `skip_failed` is off.
    """
    if page_size < 1:
        raise ValueError(f"page_size must be at least 1, got {page_size}")
    if min_size < 1:
        raise ValueError(f"min_size must be at least 1, got {min_size}")
    if max_members_shown < 1:
        raise ValueError(f"max_members_shown must be at least 1, got {max_members_shown}")
    if max_passages_shown < 0:
        raise ValueError(f"max_passages_shown must not be negative, got {max_passages_shown}")

    entities, edges, dangling = await _read_topology(tenant_id, graph, relationships, page_size)

    communities = detect_communities(
        list(entities),
        [(edge.source_entity_id, edge.target_entity_id, 1.0) for edge in edges],
        resolution=resolution,
    )
    degrees = _degrees(edges)

    large = [community for community in communities if len(community.members) >= min_size]
    # Largest first, ties by first member id -- the members are already
    # ascending, so `_key` on the first one is a total order over communities
    # of equal size and two runs cannot disagree.
    large.sort(key=lambda community: (-len(community.members), str(community.members[0])))

    prompts = [
        await _prompt_for(
            community.members,
            entities,
            degrees,
            chunks,
            tenant_id,
            max_members_shown,
            max_passages_shown,
        )
        for community in large
    ]

    ceiling = limiter if limiter is not None else CallLimiter(concurrency)
    results = await asyncio.gather(
        *(_describe(provider, prompt.text, system_prompt, ceiling) for prompt in prompts),
        return_exceptions=True,
    )

    themes: list[Theme] = []
    failed = 0
    for community, prompt, result in zip(large, prompts, results, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, LlmProviderError) or not skip_failed:
                raise result
            failed += 1
            continue
        themes.append(
            Theme(
                title=result.title,
                summary=result.summary,
                members=community.members,
                members_shown=prompt.members_shown,
                passages_shown=prompt.passages_shown,
            )
        )

    return ThemeReport(
        themes=tuple(themes),
        communities=len(communities),
        too_small=len(communities) - len(large),
        failed=failed,
        dangling_edges=dangling,
    )


async def _read_topology(
    tenant_id: TenantId,
    graph: EntityReader,
    relationships: RelationshipStore,
    page_size: int,
) -> tuple[dict[EntityId, Entity], list[Relationship], int]:
    """This tenant's whole topology, one page of entities at a time.

    Edges are keyed by `Relationship.id` as they arrive, because an edge whose
    endpoints fall on two different pages is returned by both pages' reads.
    Deduplicating after the fact would be the same thing; doing it here is
    what makes the weight independent of `page_size`.
    """
    entities: dict[EntityId, Entity] = {}
    by_id: dict[RelationshipId, Relationship] = {}
    cursor: EntityId | None = None
    finished = False

    for _ in range(MAX_PAGES):
        page = await graph.find_entities(tenant_id, limit=page_size, after=cursor)
        for entity in page:
            entities[entity.id] = entity
        if page:
            found = await relationships.get_relationships_for(
                [entity.id for entity in page], tenant_id
            )
            for edge in found:
                by_id[edge.id] = edge
        if len(page) < page_size:
            finished = True
            break
        cursor = page[-1].id

    if not finished:
        raise CursorStalledError(tenant_id, MAX_PAGES)

    kept = [
        edge
        for edge in by_id.values()
        if edge.source_entity_id in entities and edge.target_entity_id in entities
    ]
    return entities, kept, len(by_id) - len(kept)


def _degrees(edges: list[Relationship]) -> dict[EntityId, int]:
    """How many edges touch each entity. A self-loop counts twice."""
    degrees: dict[EntityId, int] = {}
    for edge in edges:
        for endpoint in (edge.source_entity_id, edge.target_entity_id):
            degrees[endpoint] = degrees.get(endpoint, 0) + 1
    return degrees


@dataclass(frozen=True, slots=True)
class _Prompt:
    """The text one community's call carries, and what went into it."""

    text: str
    members_shown: int
    passages_shown: int


async def _prompt_for(
    members: tuple[EntityId, ...],
    entities: dict[EntityId, Entity],
    degrees: dict[EntityId, int],
    chunks: ChunkReader | None,
    tenant_id: TenantId,
    max_members_shown: int,
    max_passages_shown: int,
) -> _Prompt:
    """Render one community for the model, capped both ways.

    Members are ordered by degree descending, ties by id ascending -- the
    central members of a cluster are the ones a summary of it should be
    written from, and the tie-break makes the choice reproducible rather than
    dependent on which order the store happened to return.
    """
    shown = sorted(members, key=lambda member: (-degrees.get(member, 0), str(member)))[
        :max_members_shown
    ]

    lines = [f"Cluster of {len(members)} entities."]
    if len(shown) < len(members):
        lines.append(f"The {len(shown)} most connected are listed.")
    lines.append("")
    lines.append("Entities:")
    for member in shown:
        entity = entities[member]
        described = f" -- {entity.description}" if entity.description else ""
        lines.append(f"- {entity.name} ({entity.entity_type}){described}")

    passages = (
        await _passages(shown, chunks, tenant_id, max_passages_shown) if chunks is not None else []
    )
    if passages:
        lines.append("")
        lines.append("Passages:")
        for passage in passages:
            lines.append("---")
            lines.append(passage.text)
        lines.append("---")

    return _Prompt(text="\n".join(lines), members_shown=len(shown), passages_shown=len(passages))


async def _passages(
    shown: list[EntityId],
    chunks: ChunkReader,
    tenant_id: TenantId,
    limit: int,
) -> list[StoredChunk]:
    """Up to `limit` stored passages the shown members were extracted from.

    Taken in the order the members are shown -- most connected first -- so the
    passages that survive the cap are the ones about the cluster's centre. A
    chunk mentioning two members appears once. Within one member the store's
    own order (`chunk_index`, then `id`) is kept, so the selection is
    reproducible without a second sort.
    """
    seen: dict[str, StoredChunk] = {}
    for member in shown:
        if len(seen) >= limit:
            break
        for chunk in await chunks.get_by_entity(member, tenant_id):
            if chunk.id not in seen:
                seen[chunk.id] = chunk
            if len(seen) >= limit:
                break
    return list(seen.values())


async def _describe(
    provider: LlmProvider,
    text: str,
    system_prompt: str,
    limiter: CallLimiter,
) -> CommunityReport:
    """One model call, holding a slot of the endpoint ceiling for its duration."""
    async with limiter:
        return await provider.extract(text, CommunityReport, system_prompt=system_prompt)
