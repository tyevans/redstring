"""Data models and constants for corpus theme extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from redstring.domain.ids import EntityId


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SYSTEM_PROMPT",
    "MAX_PAGES",
    "CommunityReport",
    "PromptContext",
    "Theme",
    "ThemeReport",
]

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


@dataclass(frozen=True, slots=True)
class PromptContext:
    """The text one community's call carries, and what went into it."""

    text: str
    members_shown: int
    passages_shown: int
