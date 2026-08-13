"""A gate on the `ChunkStore` compliance suite: every read method is covered.

`CLAUDE.md` says to give every store port the same gate `GraphStore` has, and
this is it. The reason is recorded there at length: four read methods shipped
during slice 3 with complete behavioural tests and no mutation-isolation test,
and each time a mutation run -- not review -- found that returning the live
internal object passed everything. A written rule is what failed those four
times, so the rule is executable here.

The read-method list is **derived from the Protocol by introspection**, never
hand-maintained: a hand-kept list needs updating by the same person who forgot
the test.

Like the `VectorStore` gate and unlike `GraphStore`'s, there is no legacy
registry, because this suite is new and every test could simply be named to
the convention. Keep it that way -- add `test_<method>_returns_copies` and
`test_<method>_never_crosses_tenants` alongside the method, and this module
needs no edit at all.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Sequence

from redstring.domain.chunk import ChunkId, StoredChunk
from redstring.domain.chunk_ranking import LexicalCandidates
from redstring.domain.chunk_retrieval import SemanticCandidate
from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.ports.chunk_store import ChunkStore
from redstring.testing.chunk_store import ChunkStoreCompliance

# The port annotates under `if TYPE_CHECKING`, so resolving its hints at
# runtime needs the names supplied explicitly.
_PORT_NAMESPACE = {
    "StoredChunk": StoredChunk,
    "ChunkId": ChunkId,
    "SourceId": SourceId,
    "TenantId": TenantId,
    "EntityId": EntityId,
    "LexicalCandidates": LexicalCandidates,
    "SemanticCandidate": SemanticCandidate,
    "Sequence": Sequence,
}

ISOLATION_CONVENTION = "test_{method}_returns_copies"
TENANT_CONVENTION = "test_{method}_never_crosses_tenants"

#: Read methods deliberately exempt, with the reason. Empty today; an entry
#: here is a visible decision, an absent one is the omission this catches.
ISOLATION_EXEMPT: dict[str, str] = {}


def _mentions(annotation: object, targets: set[type]) -> bool:
    """Whether `annotation` contains any of `targets`, however nested."""
    if annotation in targets:
        return True
    return any(_mentions(argument, targets) for argument in typing.get_args(annotation))


def read_methods() -> set[str]:
    """Port methods handing domain objects back to the caller.

    Derived from return annotations rather than names, so the three methods
    returning `int` drop out automatically and a future read method is
    included automatically.

    `LexicalCandidates` is in the target set alongside `StoredChunk` itself.
    `lexical_candidates` returns `LexicalCandidates`, which *contains* chunks
    (each `LexicalCandidate.chunk` is a `StoredChunk`) without the wrapper's
    own annotation mentioning `StoredChunk` anywhere -- so a return type that
    *contains* domain objects leaks exactly as one that *is* a domain object,
    and `_mentions` cannot see through a type it was not told to look for.
    Omitting it here would silently skip the method this gate exists to
    catch. `SemanticCandidate` is in the set for the same reason:
    `semantic_candidates` returns `list[SemanticCandidate]`, each wrapping a
    `StoredChunk` the same way `LexicalCandidate` does.
    """
    found = set()
    for name, function in inspect.getmembers(ChunkStore, inspect.isfunction):
        if name.startswith("_"):
            continue
        hints = typing.get_type_hints(function, localns=_PORT_NAMESPACE)
        if _mentions(hints.get("return"), {StoredChunk, LexicalCandidates, SemanticCandidate}):
            found.add(name)
    return found


def _uncovered(convention: str, exempt: dict[str, str]) -> set[str]:
    return {
        method
        for method in read_methods()
        if method not in exempt
        and not hasattr(ChunkStoreCompliance, convention.format(method=method))
    }


class TestEveryReadMethodIsCovered:
    def test_the_port_has_read_methods_to_check(self) -> None:
        """Guard the guard: a detector that finds nothing passes vacuously.

        `ChunkId` is `str`, so this is not merely belt and braces: were the
        domain-type set ever written as `{StoredChunk, ChunkId}`, every method
        taking or returning a string would qualify, and were `StoredChunk`
        dropped from it, none would -- and both mistakes leave the two
        coverage tests below green.
        """
        assert read_methods() == {
            "get",
            "get_by_source",
            "get_by_entity",
            "lexical_candidates",
            "semantic_candidates",
        }

    def test_every_read_method_declares_isolation_coverage(self) -> None:
        missing = _uncovered(ISOLATION_CONVENTION, ISOLATION_EXEMPT)
        assert not missing, (
            f"read methods with no mutation-isolation test: {sorted(missing)}. "
            f"Add a test that mutates the result and asserts a later read is "
            f"unaffected, named "
            f"{[ISOLATION_CONVENTION.format(method=m) for m in sorted(missing)]} "
            f"-- or, if the method genuinely cannot leak stored state, add it "
            f"to ISOLATION_EXEMPT with the reason."
        )

    def test_every_read_method_declares_tenant_coverage(self) -> None:
        missing = _uncovered(TENANT_CONVENTION, {})
        assert not missing, (
            f"read methods with no tenant-isolation test: {sorted(missing)}. "
            f"Add "
            f"{[TENANT_CONVENTION.format(method=m) for m in sorted(missing)]}. "
            f"A cross-tenant leak is a data-confidentiality bug; every read "
            f"path needs its own proof, and a new read is a new place to leak."
        )

    def test_the_exemption_list_does_not_outlive_the_port(self) -> None:
        stale = set(ISOLATION_EXEMPT) - read_methods()
        assert not stale, f"ISOLATION_EXEMPT names methods the port no longer has: {sorted(stale)}"

    def test_exemptions_carry_a_reason(self) -> None:
        for method, reason in ISOLATION_EXEMPT.items():
            assert reason.strip(), f"{method!r} is exempt with no reason given"
