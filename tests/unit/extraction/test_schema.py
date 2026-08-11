"""What `Extraction.model_json_schema()` promises a grammar compiler, not what pydantic promises.

The two diverge on purpose here: `Extraction()` and `Extraction(entities=[...])`
are used throughout this suite and in `gleaning.py`, so the pydantic-level
`default_factory` on `entities`/`relationships` stays -- and pydantic's own
rule, "a field with a default is not required", would otherwise leave the
JSON Schema's root `required` empty. Fed to a real grammar compiler that makes
`{}` a legal completion: a model can legally answer "found nothing" without
having looked, and nothing downstream can tell that apart from a document that
genuinely held nothing.
"""

from __future__ import annotations

from redstring.extraction.constrained import constrained_extraction
from redstring.extraction.domains.registry import get_domain_schema
from redstring.extraction.schema import Extraction


def test_entities_and_relationships_are_required_in_the_json_schema():
    schema = Extraction.model_json_schema()

    assert schema["required"] == ["entities", "relationships"]


def test_an_empty_list_for_either_key_is_still_a_legal_value():
    """The key must be present; what it holds is unconstrained by this fix.

    Constructing `Extraction()` proves the Python-level default is untouched
    -- if `default_factory` had been dropped instead of patching the schema,
    this and every other bare `Extraction()`/`Extraction(entities=...)` call
    across the suite would raise `ValidationError` instead.
    """
    assert Extraction().model_dump() == {"entities": [], "relationships": []}
    assert Extraction(entities=[]).relationships == []


def test_a_domain_constrained_subclass_keeps_the_same_two_required_keys():
    """`constrained_extraction` narrows two field *types*; it must not narrow `required` too.

    A domain-constrained subclass only overrides `entities` and
    `relationships` with narrower list item types -- see
    `redstring.extraction.constrained` -- so its root schema should require
    exactly the same two keys as the base class, no more and no fewer.
    """
    domain = get_domain_schema("news_journalism")

    schema = constrained_extraction(domain).model_json_schema()

    assert schema["required"] == ["entities", "relationships"]
