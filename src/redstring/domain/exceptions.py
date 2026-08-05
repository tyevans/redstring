"""Domain-level errors.

These are raised by ports and adapters when a domain invariant cannot be
satisfied. They carry the identifiers involved so a caller can act on them
without parsing a message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from redstring.domain.ids import EntityId, TenantId


class RedstringError(Exception):
    """Base class for every error this library raises deliberately."""


class MissingEntityError(RedstringError):
    """A referenced entity does not exist in the given tenant.

    Raised when writing a relationship whose endpoint is absent: dangling
    edges are not permitted in a `GraphStore`.
    """

    def __init__(self, *, entity_id: EntityId, tenant_id: TenantId) -> None:
        self.entity_id = entity_id
        self.tenant_id = tenant_id
        super().__init__(f"entity {entity_id} does not exist in tenant {tenant_id}")


class DimensionMismatchError(RedstringError):
    """A vector's length does not match the store's configured dimension.

    A store is built for one embedding model and one dimension. Accepting a
    vector of a different length is a silent correctness catastrophe: it does
    not surface as an exception but as mediocre search results, which read as
    a mediocre embedding model rather than as a bug. Changing model therefore
    means a new store, not an in-place write of differently-shaped vectors.
    """

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected a vector of dimension {expected}, got {actual}")


class AliasCycleError(RedstringError):
    """Resolving an entity through its aliases did not terminate.

    Unreachable through legal history: a cycle needs some merge to name an
    entity that is already an alias as its canonical, and `ConsolidationLog`
    refuses precisely that. It is raised anyway because resolution is a walk
    over adapter-supplied data, and the alternative to a bounded walk is a
    hang -- which in CI reads as infrastructure trouble and gets retried
    rather than investigated. A loud error naming the id is the cheap half of
    that trade.
    """

    def __init__(self, *, entity_id: EntityId, tenant_id: TenantId) -> None:
        self.entity_id = entity_id
        self.tenant_id = tenant_id
        super().__init__(
            f"alias resolution for entity {entity_id} in tenant {tenant_id} did not "
            f"terminate: the alias graph has a cycle, which no legal merge history "
            f"can produce"
        )


class UnknownDomainError(RedstringError):
    """No domain schema by that id.

    A `RedstringError` rather than the `KeyError` the registry raises, because
    `domain_system_prompt` is public and `RedstringError` is documented as the
    base of every error this library raises deliberately. A typo in a domain
    id is the overwhelmingly likely cause, so the message lists the ids that
    do exist -- "unknown domain" alone does not help fix it.
    """

    def __init__(self, domain_id: str, available: list[str]) -> None:
        self.domain_id = domain_id
        self.available = available
        super().__init__(
            f"Unknown domain {domain_id!r}. Available: {', '.join(available) or 'none'}"
        )


class LlmProviderError(RedstringError):
    """An `LlmProvider` could not produce a validated extraction.

    Deliberately *not* recoverable-by-default. Every alternative to raising
    here degrades to "the document contained nothing", which is a legitimate
    answer that the caller cannot distinguish from a failure, and which erodes
    a knowledge graph silently rather than stopping a run.
    """

    def __init__(self, message: str, *, model: str) -> None:
        self.model = model
        super().__init__(f"[{model}] {message}")


class EmptyCompletionError(LlmProviderError):
    """The model returned no usable content.

    The reference deployment's reasoning model reaches this by spending its
    whole token budget on `reasoning_content` before `content` begins, and
    still answering HTTP 200. `finish_reason` is carried when the transport
    reported one, because "length" and "stop" call for different fixes: raise
    the token budget, or look at the prompt.
    """

    def __init__(self, *, model: str, finish_reason: str | None = None) -> None:
        self.finish_reason = finish_reason
        detail = "" if finish_reason is None else f" (finish_reason={finish_reason!r})"
        super().__init__(f"returned empty content{detail}", model=model)


class RefusedCompletionError(LlmProviderError):
    """The model declined to answer, and its safety layer said so.

    A sibling of `EmptyCompletionError` rather than a subclass, because the
    two call for opposite responses. A truncation is a configuration problem
    that a larger token budget fixes, and retrying is the right move. A
    refusal is a permanent property of *this content*: retrying spends tokens
    to be refused again, and the useful reaction is to record which document
    could not be extracted and move on.

    Collapsing them would make that distinction unavailable at exactly the
    point a caller extracting from clinical or legal text needs it most.
    """

    def __init__(self, *, model: str) -> None:
        super().__init__("refused to answer (content filter)", model=model)


class MalformedCompletionError(LlmProviderError):
    """Content came back, but did not validate against the requested schema."""

    def __init__(self, *, model: str, schema: str, cause: str) -> None:
        self.schema = schema
        self.cause = cause
        super().__init__(f"returned content that is not a valid {schema}: {cause}", model=model)


class ConsolidationInvariantError(RedstringError):
    """A merge or undo would violate a rule the consolidation log enforces.

    These three rules used to be enforced by nothing, and each one corrupts a
    graph quietly rather than loudly: a merge into an alias leaves a chain
    nothing resolves, a double merge gives one entity two canonical parents,
    and an undo of a merge that never happened restores edges that were never
    displaced.
    """


class MergeIntoAliasError(ConsolidationInvariantError):
    """The proposed canonical entity has itself been merged into another."""

    def __init__(self, *, alias_entity_id: EntityId, canonical_entity_id: EntityId) -> None:
        self.alias_entity_id = alias_entity_id
        self.canonical_entity_id = canonical_entity_id
        super().__init__(
            f"cannot merge into {alias_entity_id}: it is already an alias of {canonical_entity_id}"
        )


class DoubleMergeError(ConsolidationInvariantError):
    """An entity in this merge has already been merged into another."""

    def __init__(self, *, entity_id: EntityId, canonical_entity_id: EntityId) -> None:
        self.entity_id = entity_id
        self.canonical_entity_id = canonical_entity_id
        super().__init__(f"entity {entity_id} has already been merged into {canonical_entity_id}")


class UnknownMergeError(ConsolidationInvariantError):
    """No merge in effect matches the event id an undo refers to.

    Covers both "never happened" and "already undone". The two are one case
    from the caller's point of view -- there is no merge to reverse -- and
    distinguishing them in the type would invite handling only one.
    """

    def __init__(self, *, merge_event_id: UUID) -> None:
        self.merge_event_id = merge_event_id
        super().__init__(f"no merge in effect with event id {merge_event_id}")
