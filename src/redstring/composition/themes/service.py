"""Orchestration service for knowledge graph community theme summarization."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from redstring.domain.community import detect_communities
from redstring.domain.exceptions import LlmProviderError
from redstring.domain.limiter import CallLimiter

from .models import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SYSTEM_PROMPT,
    Theme,
    ThemeReport,
)
from .ranking import describe_community, prompt_for
from .topology import node_degrees, read_topology

if TYPE_CHECKING:
    from redstring.domain.ids import TenantId
    from redstring.ports.chunk_store import ChunkReader
    from redstring.ports.graph_store import EntityReader, RelationshipStore
    from redstring.ports.llm_provider import LlmProvider


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

    entities, edges, dangling = await read_topology(tenant_id, graph, relationships, page_size)

    communities = detect_communities(
        list(entities),
        [(edge.source_entity_id, edge.target_entity_id, 1.0) for edge in edges],
        resolution=resolution,
    )
    degrees = node_degrees(edges)

    large = [community for community in communities if len(community.members) >= min_size]
    # Largest first, ties by first member id -- the members are already
    # ascending, so `_key` on the first one is a total order over communities
    # of equal size and two runs cannot disagree.
    large.sort(key=lambda community: (-len(community.members), str(community.members[0])))

    prompts = [
        await prompt_for(
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
        *(describe_community(provider, prompt.text, system_prompt, ceiling) for prompt in prompts),
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
