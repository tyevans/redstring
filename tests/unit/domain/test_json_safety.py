"""A NUL anywhere in a free-form value is refused at the domain boundary.

The rule itself is one function; what these tests are really pinning is that
**every type carrying free-form data into the event log applies it**. That is
the part that was missing (BACKLOG B36): `VectorRecord` had the check and
`Entity` and `Relationship` did not, so the same value was accepted or refused
depending on which type it arrived in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.domain.entity import Entity
from redstring.domain.json_safety import reject_unstorable_text
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship

#: Every nesting a free-form value can take. A NUL three levels down breaks a
#: `jsonb` write exactly as thoroughly as one at the top, and a check that
#: recursed only into dicts would pass all but the first two of these.
SURROGATE = "\ud800"

NESTINGS = [
    pytest.param({"\x00": "v"}, id="in-a-key"),
    pytest.param({"k": "a\x00b"}, id="in-a-value"),
    pytest.param({"k": {"deeper": "\x00"}}, id="nested-dict"),
    pytest.param({"k": ["fine", "\x00"]}, id="nested-list"),
    pytest.param({"k": [{"deeper": "\x00"}]}, id="list-of-dicts"),
    pytest.param({"k": ("in", "a\x00tuple")}, id="nested-tuple"),
    pytest.param({"k": {"in", "a\x00set"}}, id="nested-set"),
    pytest.param({"k": "lone " + SURROGATE}, id="surrogate-in-a-value"),
    pytest.param({SURROGATE: "v"}, id="surrogate-in-a-key"),
    pytest.param({"k": ["fine", SURROGATE]}, id="surrogate-nested"),
]


#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 13, 11, 7, tzinfo=UTC)

#: The free-form fields that live on `Provenance` rather than on `Entity`.
#: `_entity` routes an override for one of these into the nested model, so the
#: parametrised test below can keep naming a flat field per case -- the
#: question it asks ("can a NUL reach the log through this field") is
#: unchanged by which model happens to hold it.
ON_PROVENANCE = {"source_id", "source_text", "model", "extraction_method", "confidence"}


def _entity(**overrides: object) -> Entity:
    provenance: dict[str, object] = {
        "observed_at": OBSERVED,
        "extraction_method": ExtractionMethod.PATTERN,
        "confidence": 1.0,
    }
    provenance.update({k: overrides.pop(k) for k in list(overrides) if k in ON_PROVENANCE})
    fields: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "Ada",
        "normalized_name": "ada",
        "entity_type": "person",
        "provenance": Provenance(**provenance),  # type: ignore[arg-type]
    }
    return Entity(**{**fields, **overrides})  # type: ignore[arg-type]


def _relationship(**overrides: object) -> Relationship:
    fields: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "source_entity_id": uuid4(),
        "target_entity_id": uuid4(),
        "relationship_type": "knows",
        "confidence": 1.0,
    }
    return Relationship(**{**fields, **overrides})  # type: ignore[arg-type]


class TestRejectNul:
    @pytest.mark.parametrize("value", NESTINGS)
    def test_a_nul_at_any_depth_is_refused(self, value: object):
        with pytest.raises(ValueError, match="NUL"):
            reject_unstorable_text(value)

    def test_a_lone_surrogate_is_refused_even_though_json_will_escape_it(self):
        """The half that is not about `jsonb`.

        `json.dumps` of a lone surrogate succeeds -- it emits an escape -- so a check
        written by round-tripping through `json` would pass this. The string
        has no UTF-8 encoding at all, which is what actually stops it: it
        cannot cross a connection, and `uuid5` raises `UnicodeEncodeError` on
        it long before a store is involved.
        """
        import json

        assert json.dumps(SURROGATE)  # the check a reasonable person writes first
        with pytest.raises(ValueError, match="surrogate"):
            reject_unstorable_text(SURROGATE)

    def test_the_error_names_the_field(self):
        """Several free-form fields per type, so 'somewhere in this entity'
        is not a useful thing to tell a caller."""
        with pytest.raises(ValueError, match="external_ids"):
            reject_unstorable_text({"a": "\x00"}, what="external_ids")

    @pytest.mark.parametrize(
        "value",
        [
            {},
            {"k": "no nul here"},
            {"k": {"nested": [1, None, True, {"x": []}]}},
            {"k": "unicode is fine: é中\U0001f600"},
            # U+0000 is the only codepoint jsonb refuses. The neighbours are
            # ordinary text, and a check written with a `<` comparison or a
            # `.isprintable()` would wrongly take these too.
            {"k": "\x01\x02\x1f"},
            {"k": "tabs\tand\nnewlines"},
        ],
    )
    def test_everything_else_passes(self, value: object):
        reject_unstorable_text(value)

    @pytest.mark.parametrize("value", [None, 42, 3.5, True, [1, 2, 3]])
    def test_a_non_string_is_not_its_business(self, value: object):
        """It asks one question about text; it is not a schema check."""
        reject_unstorable_text(value)


class TestEntityRefusesIt:
    @pytest.mark.parametrize("value", NESTINGS)
    def test_in_properties(self, value: object):
        with pytest.raises(ValueError, match="NUL"):
            _entity(properties=value)

    def test_in_external_ids(self):
        with pytest.raises(ValueError, match="NUL"):
            _entity(external_ids={"wikidata": "Q\x00937"})

    @pytest.mark.parametrize(
        "field",
        [
            "name",
            "normalized_name",
            "entity_type",
            "original_entity_type",
            "description",
            "source_id",
            "source_text",
            "model",
        ],
    )
    def test_in_any_free_form_string(self, field: str):
        """Not just the dicts B36 named.

        A NUL in `description` breaks the same write as one in `properties`,
        and fixing only the dicts would leave the next person to rediscover
        half of this. `model` is included because it is the field whose values
        are meant to answer 're-extract everything the old model touched' off
        the log -- an unwritable one answers nothing.

        `source_id` is here because the coverage gate in
        `test_nul_rejection_covers_every_text_field.py` found it on its first
        run: `SourceId` is an alias for `str`, not a UUID like the two ids
        beside it, so it reads as typed and is as free-form as `description`.
        """
        with pytest.raises(ValueError, match="NUL"):
            _entity(**{field: "bad\x00value"})

    def test_in_a_blocking_key(self):
        """A `frozenset` of strings, and it reaches the store as data."""
        with pytest.raises(ValueError, match="NUL"):
            _entity(blocking_keys=frozenset({"per:ada", "per:a\x00da"}))

    def test_a_clean_entity_is_untouched(self):
        """The value is passed through, not stripped -- so a caller cannot
        mistake a rejection policy for a sanitising one."""
        entity = _entity(properties={"note": "clean"}, description="fine")
        assert entity.properties == {"note": "clean"}
        assert entity.description == "fine"


class TestRelationshipRefusesIt:
    @pytest.mark.parametrize("value", NESTINGS)
    def test_in_properties(self, value: object):
        with pytest.raises(ValueError, match="NUL"):
            _relationship(properties=value)

    def test_in_the_relationship_type(self):
        with pytest.raises(ValueError, match="NUL"):
            _relationship(relationship_type="kno\x00ws")

    def test_a_clean_relationship_is_untouched(self):
        assert _relationship(properties={"since": 1970}).properties == {"since": 1970}
