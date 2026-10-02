"""Prompt construction, passage extraction, and model interrogation for themes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import CommunityReport, PromptContext

if TYPE_CHECKING:
    from redstring.domain.chunk import StoredChunk
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.domain.limiter import CallLimiter
    from redstring.ports.chunk_store import ChunkReader
    from redstring.ports.llm_provider import LlmProvider


async def prompt_for(
    members: tuple[EntityId, ...],
    entities: dict[EntityId, Entity],
    degrees: dict[EntityId, int],
    chunks: ChunkReader | None,
    tenant_id: TenantId,
    max_members_shown: int,
    max_passages_shown: int,
) -> PromptContext:
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
        await extract_passages(shown, chunks, tenant_id, max_passages_shown)
        if chunks is not None
        else []
    )
    if passages:
        lines.append("")
        lines.append("Passages:")
        for passage in passages:
            lines.append("---")
            lines.append(passage.text)
        lines.append("---")

    return PromptContext(
        text="\n".join(lines), members_shown=len(shown), passages_shown=len(passages)
    )


async def extract_passages(
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


async def describe_community(
    provider: LlmProvider,
    text: str,
    system_prompt: str,
    limiter: CallLimiter,
) -> CommunityReport:
    """One model call, holding a slot of the endpoint ceiling for its duration."""
    async with limiter:
        return await provider.extract(text, CommunityReport, system_prompt=system_prompt)
