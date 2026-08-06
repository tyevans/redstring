"""What a free-form value may hold if it is to survive a JSON column.

Every durable store behind this library keeps free-form payloads as JSON over
a UTF-8 connection. Two kinds of string cannot make that trip, and a Python
`str` holds both quite happily:

- **A NUL.** Postgres `jsonb` cannot hold U+0000 in text -- it refuses the
  write outright rather than truncating or escaping.
- **An unpaired surrogate.** `"\\ud800"` is a legal Python `str` and has no
  UTF-8 encoding at all, so it cannot cross a wire that speaks UTF-8. It is
  ordinary output from a language model, because `json.loads` will build one
  from an escape without complaint.

So without a check at the domain boundary, an in-memory adapter accepts a
value that the first persistent store to see it refuses, which is the
silent-divergence shape the compliance suites exist to prevent
(`.claude/rules/recurring-defects.md` §1).

**The surrogate half arrives earlier than any store**, which is what makes it
worse than the NUL rather than merely another case of it:
`extraction/mapping.py` derives an entity's id with `uuid5`, which encodes its
argument, so a surrogate in a name raises `UnicodeEncodeError` out of the
mapper and fails the whole chunk before a store is involved.

**This module exists because the same rule was needed in two places.** It was
first written as a private `_reject_nul` inside `domain/vector.py`, found by
`VectorRecord.metadata`'s round-trip property. `Entity` and `Relationship`
carry free-form values into the *event log* by the same route and had no such
check (BACKLOG B36); the choice was a cross-module private import or a home
both could reach, and this is the home.

Rejecting rather than stripping or escaping is deliberate, and the reasoning
is the same one `VectorRecord` records: silently altering the value would make
every round-trip contract in this repo a lie, and a caller with unstorable
text has a bug upstream that is better surfaced than smoothed over.

**Adding the check late is safe in a way a schema change would not be.** It
only refuses values that could never have been persisted, so no event already
in a log becomes invalid by it.
"""

from __future__ import annotations

from typing import TypeVar

#: A value passed through the check unchanged.
#:
#: A `TypeVar` rather than `Any` because the claim is true: these validators
#: return exactly what they were handed, and saying so keeps `ANN401` honest
#: instead of silenced. Contrast `domain/merge_strategy.py::resolve`, where
#: `Any` is correct and the suppression is argued (BACKLOG B42) -- there the
#: output type genuinely differs from the input for one enum member.
Passthrough = TypeVar("Passthrough")

#: What `jsonb` will not hold in text.
NUL = "\x00"


def reject_unstorable_text(value: object, *, what: str = "value") -> None:
    """Raise `ValueError` if any string reachable from `value` cannot be stored.

    Recurses through dicts (keys *and* values), lists, tuples, sets and
    frozensets, because an unstorable string nested three levels down breaks
    the write exactly as thoroughly as one at the top. `what` names the field
    in the error, so a caller with several free-form fields learns which one.

    Anything else -- numbers, booleans, `None`, a datetime -- is passed over:
    this asks one question about text and is not a general schema check.
    """
    if has_unstorable_text(value):
        raise ValueError(
            f"{what} must be storable as UTF-8 JSON text: no NUL character and "
            f"no unpaired surrogate"
        )


def has_unstorable_text(value: object) -> bool:
    """Whether any string reachable from `value` cannot survive a JSON column.

    The predicate half of `reject_unstorable_text`, for the one caller that
    must *not* raise. `extraction/mapping.py` takes candidates from a language
    model, which is an untrusted input source: an unusable candidate there is
    dropped and counted, exactly as a blank name is, rather than failing the
    chunk it arrived in. Raising is right at the domain boundary and wrong at
    that one, and the difference is whose bug it is.
    """
    if isinstance(value, str):
        return NUL in value or not _is_utf8_encodable(value)
    if isinstance(value, dict):
        return any(
            has_unstorable_text(key) or has_unstorable_text(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(has_unstorable_text(item) for item in value)
    return False


def _is_utf8_encodable(value: str) -> bool:
    """Whether `value` has a UTF-8 encoding at all.

    Asked by encoding rather than by inspecting codepoints, because "which
    strings are unencodable" is the codec's rule and restating it here would
    be a second declaration site for a fact CPython already owns.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
