"""Every string enum's member values, written out, because they are persisted.

`ExtractionMethod` says it in its own docstring -- "these values become
persisted event payloads" -- and it is true of most of the others: a
`DatePrecision` reaches a Neo4j property, a `TemporalRelation` reaches an
inferred edge's type, a `PropertyMergeStrategy` reaches a `PropertiesMerged`
payload. Renaming a *member* is a refactor; changing its *value* is a
migration, and nothing else in the tree can tell the two apart.

So the values are written here as literals rather than derived from the enums.
An expectation written as `{m.name: m.value for m in E}` is true for every
possible spelling including the wrong one -- CLAUDE.md's row about an
expectation stated in terms of the thing under test. These have to be typed
out to mean anything.

**The table is checked for completeness**, since a hand-kept list that stops
matching the tree is the failure this repository has already paid for twice
(`docs/adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md`). A new
string enum anywhere under `redstring` fails `test_every_string_enum_is_pinned`
until its values are written down here.

Written for BACKLOG B18: `UP042` is ignored project-wide because rewriting
`class X(str, Enum)` as `enum.StrEnum` changes `str(X.A)` from `"X.A"` to
`"a"`. This file is what makes that migration checkable -- it pins the half
that must *not* move while the `str()` form does.
"""

from __future__ import annotations

import enum
import importlib
import pkgutil

import pytest

import redstring
from redstring.consolidation.policy import MergeDecision
from redstring.domain.blocking import BlockingKeyStrategy
from redstring.domain.entity import ExtractionMethod
from redstring.domain.interval import TemporalRelation
from redstring.domain.merge_strategy import PropertyMergeStrategy
from redstring.domain.temporal import DatePrecision, UncertaintyMarker
from redstring.llm.circuit_breaker import CircuitState

#: Every member of every string enum in the package, by name, with the exact
#: string it serialises as. Typed out on purpose -- see the module docstring.
WIRE_FORMAT: dict[type[enum.Enum], dict[str, str]] = {
    BlockingKeyStrategy: {
        "PREFIX": "prefix",
        "ENTITY_TYPE": "entity_type",
        "SOUNDEX": "soundex",
    },
    CircuitState: {
        "CLOSED": "closed",
        "OPEN": "open",
        "HALF_OPEN": "half_open",
    },
    DatePrecision: {
        "YEAR": "year",
        "MONTH": "month",
        "DAY": "day",
        "HOUR": "hour",
        "MINUTE": "minute",
    },
    ExtractionMethod: {
        "LLM": "llm",
        "PATTERN": "pattern",
        "SCHEMA_ORG": "schema_org",
        "OPEN_GRAPH": "open_graph",
        "HYBRID": "hybrid",
        "MANUAL": "manual",
    },
    MergeDecision: {
        "MERGE": "merge",
        "ADJUDICATE": "adjudicate",
        "REJECT": "reject",
    },
    PropertyMergeStrategy: {
        "PREFER_CANONICAL": "prefer_canonical",
        "UNION": "union",
        "PREFER_MERGED": "prefer_merged",
        "LATEST": "latest",
        "DEEP_MERGE": "deep_merge",
    },
    TemporalRelation: {
        "BEFORE": "before",
        "AFTER": "after",
        "DURING": "during",
        "CONTAINS": "contains",
        "OVERLAPS": "overlaps",
        "EQUALS": "equals",
    },
    UncertaintyMarker: {
        "EXACT": "exact",
        "APPROXIMATE": "approximate",
        "CIRCA": "circa",
        "BEFORE": "before",
        "AFTER": "after",
        "INFERRED": "inferred",
    },
}


def string_enums() -> set[type[enum.Enum]]:
    """Every `str`-valued `Enum` reachable by importing the whole package.

    Imports every submodule rather than reading `redstring.__all__`: the point
    is to catch an enum nobody exported, and most of these are not exported.
    """
    found: set[type[enum.Enum]] = set()
    for info in pkgutil.walk_packages(redstring.__path__, f"{redstring.__name__}."):
        module = importlib.import_module(info.name)
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, enum.Enum)
                and issubclass(value, str)
                and value.__module__.startswith(f"{redstring.__name__}.")
            ):
                found.add(value)
    return found


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    WIRE_FORMAT.items(),
    ids=lambda argument: argument.__name__ if isinstance(argument, type) else "",
)
def test_members_serialise_to_the_pinned_strings(enum_type, expected):
    assert {member.name: member.value for member in enum_type} == expected


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    WIRE_FORMAT.items(),
    ids=lambda argument: argument.__name__ if isinstance(argument, type) else "",
)
def test_a_member_equals_its_wire_value_as_a_plain_string(enum_type, expected):
    """The property callers actually rely on, and the one `str()` does not have.

    `X.A == "a"` holds for both `(str, Enum)` and `StrEnum`, which is what lets
    a member be handed to a driver, a JSON encoder or a dict key without
    unwrapping. `str(X.A)` differs between the two spellings and is pinned
    nowhere on purpose -- nothing may depend on it.
    """
    for name, value in expected.items():
        assert enum_type[name] == value
        assert "".join([enum_type[name]]) == value


def test_every_string_enum_is_pinned():
    """A new string enum is unpinned until someone writes its values down.

    Without this, the table above quietly stops describing the package and
    reads as coverage it does not have.
    """
    assert string_enums() == set(WIRE_FORMAT)
