"""Turning a `ChunkingResult` into the passages a `ChunkStore` holds.

Both write paths need this, and they are in different layers -- the extraction
pipeline is a sibling, `index_documents` is above it -- so the shared part
lives here at the bottom of the pair. Written twice, the two paths would
compute chunk ids and offsets separately, and a divergence there is a corpus
where indexing a document and extracting it disagree about what its third
passage *is*.

## The digest is over the split produced, not over the chunker's settings

The plan calls the middle field of a chunking signature `params_digest`, and
the settings are not reachable: `Chunker` exposes `chunker_type` and nothing
else, and `ChunkingResult` carries `overlap_size` but not the chunk size --
which `SlidingWindowChunker` then forces to `0` on the single-chunk path
anyway. A digest over what a chunker *reported* about itself would therefore
call two different chunk sizes the same chunking, which is exactly the case
`index_documents`'s re-index-with-different-settings behaviour turns on.

Digesting the boundaries closes that without touching the protocol, and it is
stronger in the direction that matters: settings that happen to produce an
identical split really are the same chunking, and recording it twice would
write the same rows under a second key for no gain. The cost is that the
signature cannot be computed without chunking first, which no caller wants to
do anyway.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from redstring.domain.chunk import StoredChunk, chunk_id

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from redstring.domain.ids import EntityId, SourceId, TenantId
    from redstring.extraction.chunking import ChunkingResult


def chunking_digest(result: ChunkingResult) -> str:
    """A stable digest of the split `result` describes.

    Each chunk contributes its index, its offsets and its text. The text is
    length-prefixed rather than delimited, because a chunker's raw output has
    not been through `reject_unstorable_text` yet and so may hold any byte a
    delimiter could be -- and two chunkings whose preimages differ only in
    where a delimiter fell would otherwise digest the same.
    """
    digest = hashlib.sha256()
    for chunk in result.chunks:
        encoded = chunk.text.encode("utf-8")
        digest.update(
            f"{chunk.chunk_index}:{chunk.start_char}:{chunk.end_char}:{len(encoded)}:".encode()
        )
        digest.update(encoded)
    return digest.hexdigest()


def stored_chunks(
    result: ChunkingResult,
    *,
    tenant_id: TenantId,
    source_id: SourceId,
    entity_ids_by_index: Mapping[int, Sequence[EntityId]] | None = None,
) -> list[StoredChunk]:
    """The passages of `result`, ready for `DocumentChunked`.

    Args:
        result: What the chunker produced.
        tenant_id: Stamped on every passage. `DocumentChunked` rejects a
            payload that disagrees with the event's tenant, so a mistake here
            is caught rather than stored.
        source_id: The document. Also half of every chunk's content-addressed
            id.
        entity_ids_by_index: What each chunk's index produced, for the path
            that ran a model. Omitted entirely by `index_documents`, and a
            chunk index absent from it gets an empty list -- which
            `StoredChunk` documents as "no entities were extracted from this
            passage", not as "extraction is pending".

    Returns:
        One `StoredChunk` per **distinct** id, in first-seen order.

    A document that repeats a passage verbatim yields two chunks with one
    content-addressed id, and `ChunkStore.upsert_many` keys on `(tenant_id,
    id)` -- so passing both would silently drop one, and which one depends on
    the adapter's write order. They are folded here instead: the first
    occurrence's offsets win, and the entity links are the union, because an
    entity found in either occurrence was found in that text.
    """
    links = entity_ids_by_index if entity_ids_by_index is not None else {}
    by_id: dict[str, StoredChunk] = {}

    for chunk in result.chunks:
        ident = chunk_id(source_id, chunk.text)
        found = list(links.get(chunk.chunk_index, ()))
        seen = by_id.get(ident)
        if seen is None:
            by_id[ident] = StoredChunk(
                id=ident,
                tenant_id=tenant_id,
                source_id=source_id,
                text=chunk.text,
                chunk_index=chunk.chunk_index,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                entity_ids=found,
            )
            continue
        seen.entity_ids.extend(entity_id for entity_id in found if entity_id not in seen.entity_ids)

    return list(by_id.values())
