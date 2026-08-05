"""Every field that can hold free-form text is covered by the NUL check.

The check itself is one function and easy to get right. The thing that goes
wrong is **coverage**: a field added to `Entity` next year is not in the
validator's list, nothing fails, and the omission surfaces as a rejected write
against a real Postgres event store long afterwards. That is this repo's
recurring shape -- a rule held by a hand-kept list with nothing that fails when
the list falls behind -- so the list is checked by introspection here rather
than trusted.

Modelled on `tests/unit/vector/test_compliance_coverage.py`, including the
part that matters most: **the detector is asserted to find something**, since
a coverage check over an empty set passes vacuously and looks identical to a
working one.
"""

from __future__ import annotations

import typing
from enum import Enum
from typing import Any

import pytest
from pydantic import BaseModel

from redstring.domain.entity import Entity
from redstring.domain.relationship import Relationship

#: The types carrying free-form values into the event log. `VectorRecord` and
#: `VectorMatch` are deliberately absent: their only free-form field is
#: `metadata`, checked by `_HasPortableMetadata` rather than by a per-field
#: list, so there is no list here for this gate to keep honest.
GUARDED_MODELS = [Entity, Relationship]

#: The validator every guarded model applies. Named rather than detected, so
#: renaming it fails here instead of silently emptying the gate.
VALIDATOR = "_reject_nul_in_free_form_text"

#: Fields exempt from the check, with the reason. Empty is the intended state:
#: an entry is a visible decision in review, an absent entry is the omission
#: this module exists to catch (`docs/adr/0014-...`).
EXEMPT: dict[tuple[str, str], str] = {}


def _can_hold_text(annotation: Any) -> bool:
    """Whether a value of this annotation can contain an arbitrary string.

    `str` and `Any` both can. An `Enum` cannot, even a `str`-valued one like
    `ExtractionMethod` -- its members are fixed at definition, so no caller can
    put a NUL in one. A nested `BaseModel` cannot either, because it runs its
    own validators; that is a claim about *those* models, so each is listed in
    `GUARDED_MODELS` in its own right or has no free-form field at all.
    """
    if annotation is Any or annotation is str:
        return True
    if isinstance(annotation, type):
        if issubclass(annotation, (Enum, BaseModel)):
            return False
        return issubclass(annotation, str)
    return any(_can_hold_text(arg) for arg in typing.get_args(annotation))


def _text_fields(model: type[BaseModel]) -> set[str]:
    return {
        name
        for name, field in model.model_fields.items()
        if _can_hold_text(field.annotation) and (model.__name__, name) not in EXEMPT
    }


def _checked_fields(model: type[BaseModel]) -> set[str]:
    validators = model.__pydantic_decorators__.field_validators
    if VALIDATOR not in validators:
        return set()
    return set(validators[VALIDATOR].info.fields)


@pytest.mark.parametrize("model", GUARDED_MODELS, ids=lambda m: m.__name__)
class TestEveryTextFieldIsChecked:
    def test_the_detector_finds_something(self, model: type[BaseModel]):
        """A gate over an empty set passes vacuously.

        If `_can_hold_text` is broken into always returning `False`, every
        other assertion in this module still passes -- which is precisely the
        failure this repo has been bitten by elsewhere.
        """
        assert len(_text_fields(model)) >= 2

    def test_no_text_field_escapes_the_validator(self, model: type[BaseModel]):
        uncovered = _text_fields(model) - _checked_fields(model)
        assert not uncovered, (
            f"{model.__name__} has free-form text fields the NUL check does not "
            f"reach: {sorted(uncovered)}. Add them to the {VALIDATOR} validator, "
            f"or add an entry to EXEMPT here saying why the field cannot carry one."
        )

    def test_the_validator_does_not_outlive_its_fields(self, model: type[BaseModel]):
        """Checked in the other direction too: a validator naming a field the
        model has dropped matches nothing and would pass forever."""
        stale = _checked_fields(model) - set(model.model_fields)
        assert not stale, f"{model.__name__}.{VALIDATOR} names dropped fields: {sorted(stale)}"


class TestTheExemptionListStaysFalsifiable:
    def test_exemptions_carry_a_reason(self):
        for key, reason in EXEMPT.items():
            assert reason.strip(), f"{key} is exempt with no reason given"

    def test_no_exemption_outlives_its_field(self):
        for model_name, field in EXEMPT:
            model = next(m for m in GUARDED_MODELS if m.__name__ == model_name)
            assert field in model.model_fields, f"{model_name}.{field} no longer exists"


class TestTheDetectorItself:
    """`_can_hold_text` decides what the gate covers, so a wrong answer here
    silently shrinks it. These pin both directions."""

    @pytest.mark.parametrize(
        "annotation",
        [str, Any, "str | None", "dict[str, Any]", "list[str]", "frozenset[str] | None"],
    )
    def test_text_bearing_annotations(self, annotation: Any):
        resolved = eval(annotation) if isinstance(annotation, str) else annotation
        assert _can_hold_text(resolved)

    @pytest.mark.parametrize("annotation", [int, float, bool, "UUID", "UUID | None"])
    def test_annotations_that_cannot_hold_text(self, annotation: Any):
        from uuid import UUID  # noqa: F401

        resolved = eval(annotation) if isinstance(annotation, str) else annotation
        assert not _can_hold_text(resolved)

    def test_a_str_enum_is_not_free_form(self):
        """`ExtractionMethod` subclasses `str`, so the naive check counts it.

        Its members are fixed at class definition and pydantic rejects
        anything else, so no caller can route a NUL through it -- and listing
        it in the validator would be noise that implies the opposite.
        """
        from redstring.domain.entity import ExtractionMethod

        assert issubclass(ExtractionMethod, str)
        assert not _can_hold_text(ExtractionMethod)
