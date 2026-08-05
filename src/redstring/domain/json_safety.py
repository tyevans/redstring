"""What a free-form value may hold if it is to survive a JSON column.

Every durable store behind this library keeps free-form payloads as JSON, and
Postgres `jsonb` **cannot hold a NUL character in text** -- it refuses the
write outright rather than truncating or escaping. A Python `dict` and a
Python `str` both can. So without a check at the domain boundary, an in-memory
adapter accepts a value that the first persistent store to see it refuses,
which is the silent-divergence shape the compliance suites exist to prevent
(`.claude/rules/recurring-defects.md` §1).

**This module exists because the same rule was needed in two places.** It was
first written as a private `_reject_nul` inside `domain/vector.py`, found by
`VectorRecord.metadata`'s round-trip property in slice 5 and correctly fixed
in the domain type rather than in either adapter. `Entity` and `Relationship`
carry free-form values into the *event log* by the same route and had no such
check (BACKLOG B36); the choice was a cross-module private import or a home
both could reach, and this is the home.

Rejecting rather than stripping or escaping is deliberate, and the reasoning
is the same one `VectorRecord` records: silently altering the value would make
every round-trip contract in this repo a lie, and a caller with a NUL in its
data has a bug upstream that is better surfaced than smoothed over.

**Adding the check late is safe in a way a schema change would not be.** It
only refuses values that could never have been persisted, so no event already
in a log becomes invalid by it.
"""

from __future__ import annotations

from typing import TypeVar

#: A value passed through a NUL check unchanged.
#:
#: A `TypeVar` rather than `Any` because the claim is true: these validators
#: return exactly what they were handed, and saying so keeps `ANN401` honest
#: instead of silenced. Contrast `domain/merge_strategy.py::resolve`, where
#: `Any` is correct and the suppression is argued (BACKLOG B42) -- there the
#: output type genuinely differs from the input for one enum member.
Passthrough = TypeVar("Passthrough")

#: What `jsonb` will not hold in text, and therefore what this module is about.
NUL = "\x00"


def reject_nul(value: object, *, what: str = "value") -> None:
    """Raise `ValueError` if any string reachable from `value` contains U+0000.

    Recurses through dicts (keys *and* values), lists, tuples, sets and
    frozensets, because a NUL nested three levels down breaks the write
    exactly as thoroughly as one at the top. `what` names the field in the
    error, so a caller with several free-form fields learns which one.

    Anything else -- numbers, booleans, `None`, a datetime -- is passed over:
    this asks one question about text and is not a general schema check.
    """
    if has_nul(value):
        raise ValueError(f"{what} must not contain a NUL character; JSON storage rejects it")


def has_nul(value: object) -> bool:
    """Whether any string reachable from `value` contains U+0000.

    The predicate half of `reject_nul`, for the one caller that must *not*
    raise. `extraction/mapping.py` takes candidates from a language model,
    which is an untrusted input source: an unusable candidate there is dropped
    and counted, exactly as a blank name is, rather than failing the chunk it
    arrived in. Raising is right at the domain boundary and wrong at that one,
    and the difference is whose bug it is.
    """
    if isinstance(value, str):
        return NUL in value
    if isinstance(value, dict):
        return any(has_nul(key) or has_nul(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(has_nul(item) for item in value)
    return False
