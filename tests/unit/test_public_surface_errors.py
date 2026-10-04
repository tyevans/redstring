"""An exported error may not hide from a caller.

Ensures every RedstringError subclass is catchable from the public surface or
documented in UNEXPORTED_BECAUSE_THEIR_RAISER_IS.
"""

from __future__ import annotations

import importlib
import pkgutil
from contextlib import suppress

import pytest

import redstring
from redstring.domain.exceptions import RedstringError

EXPORTED = frozenset(redstring.__all__)

#: RedstringError subclasses that are deliberately not exported, and why.
UNEXPORTED_BECAUSE_THEIR_RAISER_IS = {
    "CircuitOpen": "redstring.llm.circuit_breaker is middleware, not exported",
    "RateLimitExceeded": "redstring.llm.rate_limiter is middleware, not exported",
}


def _redstring_error_subclasses() -> list[type]:
    """Every `RedstringError` subclass, with the whole package imported first.

    Importing the package is what makes this complete: `__subclasses__` only
    knows about classes whose module has been executed, so a subclass in a
    module `redstring/__init__.py` does not reach would be invisible and the
    check would pass by not looking.
    """
    for module in pkgutil.walk_packages(redstring.__path__, "redstring."):
        with suppress(ImportError):  # optional extras: neo4j, langchain
            importlib.import_module(module.name)

    found: set[type] = set()

    def descend(klass: type) -> None:
        for subclass in klass.__subclasses__():
            found.add(subclass)
            descend(subclass)

    descend(RedstringError)
    assert len(found) > 10, (
        f"expected the whole hierarchy, found {sorted(c.__name__ for c in found)}"
    )
    return sorted(found, key=lambda c: c.__name__)


@pytest.mark.parametrize(
    "error", _redstring_error_subclasses(), ids=lambda c: f"{c.__module__}.{c.__name__}"
)
def test_every_error_is_catchable_from_the_public_surface(error: type) -> None:
    """`RedstringError` promises to be the base of every deliberate error.

    A promise a caller cannot act on is not one. `RefusedCompletionError` was
    the case that made this: its own docstring argues at length that a caller
    "must" distinguish it from `EmptyCompletionError` -- which was exported
    while it was not, so the distinction needed a dotted path into an internal
    module.
    """
    name = error.__name__
    assert name in EXPORTED or name in UNEXPORTED_BECAUSE_THEIR_RAISER_IS, (
        f"`{name}` ({error.__module__}) is a RedstringError and a caller cannot name it. "
        f"Export it, or add it to UNEXPORTED_BECAUSE_THEIR_RAISER_IS with the capability "
        f"whose export would bring it along."
    )


def test_no_unexported_error_reason_is_stale() -> None:
    live = {error.__name__ for error in _redstring_error_subclasses()}
    stale = sorted(set(UNEXPORTED_BECAUSE_THEIR_RAISER_IS) - live)
    assert not stale, f"UNEXPORTED_BECAUSE_THEIR_RAISER_IS names {stale}, which no longer exist."


def test_an_exported_error_is_not_also_listed_as_unexported() -> None:
    """The two lists must not overlap, or an export is silently excused.

    Without this, exporting an error while leaving its entry in place would
    leave the entry as a false statement that nothing contradicts -- and the
    entry is the only record of *why* something is not exported.
    """
    both = sorted(EXPORTED & set(UNEXPORTED_BECAUSE_THEIR_RAISER_IS))
    assert not both, f"{both} are exported and still listed as deliberately unexported."
