"""The provenance check `replace_source` makes, in one place.

Both adapters raise `ValueError` on a chunk carrying another source's or
another tenant's provenance, and the port says so. Written twice, the two
messages drift -- and the shared compliance suite matches on the *contents* of
the message (`pytest.raises(..., match=str(tenant))`), so a divergence there is
a divergence in what each adapter promises a caller who is reading the error.

That is `recurring-defects.md` §2 rather than §1: one fact, one declaration
site. The check itself is cheap; agreeing on it is the part that is not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import StoredChunk
    from redstring.domain.ids import SourceId, TenantId


def reject_foreign_chunks(
    chunks: Sequence[StoredChunk], source_id: SourceId, tenant_id: TenantId
) -> None:
    """Raise unless every chunk carries exactly this provenance.

    Every element is inspected, not the first: the suite's stray is the
    *second* element precisely because a check that stops early passes on a
    well-formed head. Silently rewriting a chunk's provenance is how one
    document's entity links end up on another's passage, and the tenant half
    of it is a confidentiality bug rather than a correctness one.
    """
    strays = [
        chunk for chunk in chunks if chunk.source_id != source_id or chunk.tenant_id != tenant_id
    ]
    if strays:
        raise ValueError(
            f"every chunk must carry source_id={source_id!r} and "
            f"tenant_id={tenant_id}; found "
            f"{sorted({(c.source_id, str(c.tenant_id)) for c in strays})}"
        )
